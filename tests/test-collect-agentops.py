"""
TDD tests for collect-agentops.py — AgentOps ledger auto-collection.

Run: pytest tests/test-collect-agentops.py -v
"""

import json
import os
import re
import sys
import tempfile
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add repo root to path so we can import the script
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# We'll import after writing the module; for now define expected interfaces


# =============================================================
# Test Suite 1: Docker Compose PS parsing
# =============================================================


class TestParseContainerStatus:
    """Parse docker compose ps JSON output into structured data."""

    def test_parse_running_container(self):
        """Running container with uptime should return running state."""
        from scripts.collect_agentops import parse_container_status

        ps_data = [
            {
                "Name": "hermes",
                "State": "running",
                "Status": "Up 3 days",
                "RunningFor": "3 days ago",
            }
        ]
        result = parse_container_status(ps_data)
        assert len(result) == 1
        assert result[0]["name"] == "hermes"
        assert result[0]["state"] == "running"
        assert result[0]["status"] == "Up 3 days"

    def test_parse_container_with_health(self):
        """Container with healthcheck should capture health status."""
        from scripts.collect_agentops import parse_container_status

        ps_data = [
            {
                "Name": "openclaw-gateway",
                "State": "running",
                "Status": "Up 3 days (healthy)",
            }
        ]
        result = parse_container_status(ps_data)
        assert result[0]["healthy"] is True

    def test_parse_container_unhealthy(self):
        """Container with unhealthy status should be flagged."""
        from scripts.collect_agentops import parse_container_status

        ps_data = [
            {
                "Name": "openclaw-gateway",
                "State": "running",
                "Status": "Up 10 seconds (unhealthy)",
            }
        ]
        result = parse_container_status(ps_data)
        assert result[0]["healthy"] is False

    def test_parse_container_no_healthcheck(self):
        """Container without healthcheck should have healthy=None."""
        from scripts.collect_agentops import parse_container_status

        ps_data = [
            {
                "Name": "backup-cron",
                "State": "running",
                "Status": "Up 3 days",
            }
        ]
        result = parse_container_status(ps_data)
        assert result[0]["healthy"] is None

    def test_parse_exited_container(self):
        """Exited container should be flagged as not running."""
        from scripts.collect_agentops import parse_container_status

        ps_data = [
            {
                "Name": "openclaw-cli",
                "State": "exited",
                "Status": "Exited (0) 2 hours ago",
            }
        ]
        result = parse_container_status(ps_data)
        assert result[0]["state"] == "exited"


# =============================================================
# Test Suite 2: Restart detection
# =============================================================


class TestDetectRestarts:
    """Detect recently restarted containers."""

    def test_recent_restart_detected(self):
        """Container running < 2 hours should be flagged."""
        from scripts.collect_agentops import detect_restarts

        containers = [
            {"name": "hermes", "state": "running", "status": "Up 30 minutes"}
        ]
        with patch("scripts.collect_agentops._parse_running_time") as mock_parse:
            mock_parse.return_value = timedelta(minutes=30)
            result = detect_restarts(containers, threshold_hours=2)
            assert len(result) == 1
            assert "hermes" in result[0]["title"]
            assert "重启" in result[0]["title"]

    def test_long_running_not_flagged(self):
        """Container running > 2 hours should NOT be flagged."""
        from scripts.collect_agentops import detect_restarts

        containers = [
            {"name": "hermes", "state": "running", "status": "Up 3 days"}
        ]
        with patch("scripts.collect_agentops._parse_running_time") as mock_parse:
            mock_parse.return_value = timedelta(days=3)
            result = detect_restarts(containers, threshold_hours=2)
            assert len(result) == 0

    def test_multiple_restarts(self):
        """Multiple recently restarted containers should all be detected."""
        from scripts.collect_agentops import detect_restarts

        containers = [
            {"name": "hermes", "state": "running", "status": "Up 30 minutes"},
            {"name": "hermes-coder", "state": "running", "status": "Up 1 hour"},
            {"name": "backup-cron", "state": "running", "status": "Up 3 days"},
        ]
        with patch("scripts.collect_agentops._parse_running_time") as mock_parse:
            # Return durations matching the status strings
            def side_effect(status_str):
                if "30 minutes" in status_str:
                    return timedelta(minutes=30)
                elif "1 hour" in status_str:
                    return timedelta(hours=1)
                elif "3 days" in status_str:
                    return timedelta(days=3)
                return None
            mock_parse.side_effect = side_effect
            result = detect_restarts(containers, threshold_hours=2)
            assert len(result) == 2
            titles = [r["title"] for r in result]
            assert any("hermes" in t for t in titles)
            assert any("hermes-coder" in t for t in titles)
            assert not any("backup-cron" in t for t in titles)


# =============================================================
# Test Suite 3: Backup freshness
# =============================================================


class TestBackupFreshness:
    """Detect stale backups."""

    def test_fresh_backup_no_alert(self):
        """Backup within 24 hours should not generate item."""
        from scripts.collect_agentops import check_backup_freshness

        with patch("scripts.collect_agentops._get_latest_backup_time") as mock_time:
            mock_time.return_value = datetime.now() - timedelta(hours=6)
            result = check_backup_freshness(backup_root="/fake/backup", threshold_hours=24)
            assert len(result) == 0

    def test_stale_backup_alert(self):
        """Backup older than threshold should generate item."""
        from scripts.collect_agentops import check_backup_freshness

        with patch("scripts.collect_agentops._get_latest_backup_time") as mock_time:
            mock_time.return_value = datetime.now() - timedelta(hours=48)
            result = check_backup_freshness(backup_root="/fake/backup", threshold_hours=24)
            assert len(result) == 1
            assert "备份" in result[0]["title"]
            assert result[0]["needs_human_decision"] is True

    def test_no_backup_found(self):
        """No backup dir should generate alert item."""
        from scripts.collect_agentops import check_backup_freshness

        with patch("scripts.collect_agentops._get_latest_backup_time") as mock_time:
            mock_time.return_value = None  # No backup found
            result = check_backup_freshness(backup_root="/nonexistent", threshold_hours=24)
            assert len(result) == 1
            assert result[0]["needs_human_decision"] is True


# =============================================================
# Test Suite 4: Disk usage
# =============================================================


class TestDiskUsage:
    """Detect high disk usage."""

    def test_normal_disk_no_alert(self):
        """Disk below threshold should not generate item."""
        from scripts.collect_agentops import check_disk_usage

        with patch("scripts.collect_agentops._get_disk_usage") as mock_disk:
            mock_disk.return_value = 75.0  # 75% used
            result = check_disk_usage(threshold_percent=85)
            assert len(result) == 0

    def test_high_disk_alert(self):
        """Disk above threshold should generate item."""
        from scripts.collect_agentops import check_disk_usage

        with patch("scripts.collect_agentops._get_disk_usage") as mock_disk:
            mock_disk.return_value = 90.0  # 90% used
            result = check_disk_usage(threshold_percent=85)
            assert len(result) == 1
            assert "磁盘" in result[0]["title"]
            assert result[0]["needs_human_decision"] is True

    def test_disk_at_threshold(self):
        """Disk exactly at threshold should generate alert."""
        from scripts.collect_agentops import check_disk_usage

        with patch("scripts.collect_agentops._get_disk_usage") as mock_disk:
            mock_disk.return_value = 85.0
            result = check_disk_usage(threshold_percent=85)
            assert len(result) == 1


# =============================================================
# Test Suite 5: Gateway error detection
# =============================================================


class TestGatewayErrors:
    """Detect OpenClaw gateway error loops."""

    def test_no_error_loop(self):
        """No error loop should not generate item."""
        from scripts.collect_agentops import check_gateway_errors

        with patch("scripts.collect_agentops._run_check_gateway_errors") as mock_check:
            mock_check.return_value = {"status": "ok"}
            result = check_gateway_errors()
            assert len(result) == 0

    def test_error_loop_detected(self):
        """Error loop should generate item."""
        from scripts.collect_agentops import check_gateway_errors

        with patch("scripts.collect_agentops._run_check_gateway_errors") as mock_check:
            mock_check.return_value = {
                "status": "error_loop_detected",
                "error_message": "ENOENT: no such file or directory",
                "repeat_count_in_sample": 1200,
                "total_occurrences": 500000,
                "first_seen": "2026-07-02T12:00:00",
            }
            result = check_gateway_errors()
            assert len(result) == 1
            assert result[0]["needs_human_decision"] is True
            assert "error loop" in result[0]["title"].lower() or "错误" in result[0]["title"]

    def test_script_failure_handled(self):
        """Script failure should be gracefully handled."""
        from scripts.collect_agentops import check_gateway_errors

        with patch("scripts.collect_agentops._run_check_gateway_errors") as mock_check:
            mock_check.return_value = None  # Script failed
            result = check_gateway_errors()
            assert len(result) == 0  # Should not crash, just skip


# =============================================================
# Test Suite 6: Ledger format
# =============================================================


class TestLedgerFormat:
    """Generate correctly formatted ledger items."""

    def test_format_ledger_item(self):
        """A single item should be formatted as valid markdown."""
        from scripts.collect_agentops import format_ledger_item

        item = {
            "title": "Test Alert",
            "date": "2026-07-02",
            "source": "auto | docker compose ps",
            "status": "new",
            "owner": "owen",
            "evidence": "container uptime: 30 minutes",
            "why_it_matters": "Service may have crashed",
            "suggested_next_action": "Check logs",
            "needs_human_decision": False,
        }
        output = format_ledger_item(item)
        assert output.startswith("## Test Alert\n")
        assert "- date: 2026-07-02" in output
        assert "- source: auto | docker compose ps" in output
        assert "- project: myopenclaw" in output
        assert "- axis: agentops" in output
        assert "- status: new" in output
        assert "- needs_human_decision: no" in output

    def test_format_ledger_item_with_decision(self):
        """needs_human_decision=True should render as 'yes'."""
        from scripts.collect_agentops import format_ledger_item

        item = {
            "title": "Urgent Issue",
            "date": "2026-07-02",
            "source": "auto",
            "status": "new",
            "owner": "owen",
            "evidence": "...",
            "why_it_matters": "...",
            "suggested_next_action": "...",
            "needs_human_decision": True,
        }
        output = format_ledger_item(item)
        assert "- needs_human_decision: yes" in output

    def test_format_multiple_items(self):
        """Multiple items should be separated by blank lines."""
        from scripts.collect_agentops import format_ledger_items

        items = [
            {
                "title": "Item 1",
                "date": "2026-07-02",
                "source": "auto",
                "status": "new",
                "owner": "owen",
                "evidence": "e1",
                "why_it_matters": "w1",
                "suggested_next_action": "a1",
                "needs_human_decision": False,
            },
            {
                "title": "Item 2",
                "date": "2026-07-02",
                "source": "auto",
                "status": "watch",
                "owner": "owen",
                "evidence": "e2",
                "why_it_matters": "w2",
                "suggested_next_action": "a2",
                "needs_human_decision": False,
            },
        ]
        output = format_ledger_items(items)
        assert "## Item 1" in output
        assert "## Item 2" in output
        # Items should be separated
        assert output.count("## ") == 2

    def test_empty_items(self):
        """Empty list should return empty string."""
        from scripts.collect_agentops import format_ledger_items

        output = format_ledger_items([])
        assert output == ""


# =============================================================
# Test Suite 7: Auto/manual merge
# =============================================================


class TestLedgerMerge:
    """Merge auto-generated items with manual items in inbox.md."""

    def test_merge_preserves_manual(self):
        """Manual items (source != auto) should be preserved."""
        from scripts.collect_agentops import merge_ledger

        existing_content = textwrap.dedent("""\
            ## Manual Item
            - date: 2026-07-01
            - source: docker logs hermes
            - project: myopenclaw
            - axis: agentops
            - status: ongoing
            - owner: owen
            - evidence: manual check
            - why_it_matters: important
            - suggested_next_action: monitor
            - needs_human_decision: no
        """)

        auto_items = [
            {
                "title": "Auto Detected Restart",
                "date": "2026-07-02",
                "source": "auto | docker compose ps",
                "status": "watch",
                "owner": "owen",
                "evidence": "uptime: 30 min",
                "why_it_matters": "recent restart",
                "suggested_next_action": "check logs",
                "needs_human_decision": False,
            }
        ]

        merged = merge_ledger(existing_content, auto_items)
        assert "## Manual Item" in merged
        assert "## Auto Detected Restart" in merged
        # Manual item should appear before or after auto items
        assert merged.index("## Manual Item") != merged.index("## Auto Detected Restart")

    def test_merge_replaces_old_auto_items(self):
        """Old auto-generated items should be replaced."""
        from scripts.collect_agentops import merge_ledger

        existing_content = textwrap.dedent("""\
            ## Old Auto Item
            - date: 2026-07-01
            - source: auto | docker compose ps
            - project: myopenclaw
            - axis: agentops
            - status: watch
            - owner: owen
            - evidence: old
            - why_it_matters: old issue
            - suggested_next_action: old
            - needs_human_decision: no

            ## Manual Item
            - date: 2026-07-01
            - source: manual
            - project: myopenclaw
            - axis: agentops
            - status: ongoing
            - owner: owen
            - evidence: important
            - why_it_matters: still relevant
            - suggested_next_action: keep watching
            - needs_human_decision: no
        """)

        auto_items = [
            {
                "title": "New Auto Item",
                "date": "2026-07-02",
                "source": "auto | docker compose ps",
                "status": "new",
                "owner": "owen",
                "evidence": "new",
                "why_it_matters": "new issue",
                "suggested_next_action": "act",
                "needs_human_decision": True,
            }
        ]

        merged = merge_ledger(existing_content, auto_items)
        # Old auto item gone
        assert "## Old Auto Item" not in merged
        # Manual item preserved
        assert "## Manual Item" in merged
        # New auto item present
        assert "## New Auto Item" in merged

    def test_merge_empty_existing(self):
        """Merging into empty file should just write auto items."""
        from scripts.collect_agentops import merge_ledger

        auto_items = [
            {
                "title": "Solo Auto Item",
                "date": "2026-07-02",
                "source": "auto | df -h",
                "status": "new",
                "owner": "owen",
                "evidence": "disk 90%",
                "why_it_matters": "disk full",
                "suggested_next_action": "clean up",
                "needs_human_decision": True,
            }
        ]

        merged = merge_ledger("", auto_items)
        assert "## Solo Auto Item" in merged
        assert merged.strip().endswith("## Solo Auto Item") is False  # content follows


# =============================================================
# Test Suite 8: Integration — full collection pipeline
# =============================================================


class TestCollectPipeline:
    """End-to-end collection pipeline."""

    @patch("scripts.collect_agentops.check_gateway_errors")
    @patch("scripts.collect_agentops.check_disk_usage")
    @patch("scripts.collect_agentops.check_backup_freshness")
    @patch("scripts.collect_agentops.detect_restarts")
    @patch("scripts.collect_agentops.parse_container_status")
    @patch("scripts.collect_agentops.get_container_ps_data")
    def test_collect_all_signals(
        self,
        mock_ps,
        mock_parse,
        mock_restarts,
        mock_backup,
        mock_disk,
        mock_gateway,
    ):
        """Full collection should aggregate all signal types."""
        from scripts.collect_agentops import collect_all_signals

        # Mock container data
        mock_ps.return_value = [{"Name": "hermes", "State": "running", "Status": "Up 3 days"}]
        mock_parse.return_value = [{"name": "hermes", "state": "running", "status": "Up 3 days", "healthy": None}]

        # Mock restarts
        mock_restarts.return_value = []

        # Mock backup stale
        mock_backup.return_value = [
            {
                "title": "备份过期",
                "date": "2026-07-02",
                "source": "auto | backup-cron",
                "status": "watch",
                "owner": "owen",
                "evidence": "last backup: 48h ago",
                "why_it_matters": "backup stale",
                "suggested_next_action": "trigger backup",
                "needs_human_decision": True,
            }
        ]

        # Mock disk ok
        mock_disk.return_value = []

        # Mock gateway ok
        mock_gateway.return_value = []

        result = collect_all_signals()
        assert len(result) > 0
        # Should include backup item
        titles = [r["title"] for r in result]
        assert "备份过期" in titles

    @patch("scripts.collect_agentops.check_gateway_errors")
    @patch("scripts.collect_agentops.check_disk_usage")
    @patch("scripts.collect_agentops.check_backup_freshness")
    @patch("scripts.collect_agentops.detect_restarts")
    @patch("scripts.collect_agentops.parse_container_status")
    @patch("scripts.collect_agentops.get_container_ps_data")
    def test_collect_all_healthy(
        self,
        mock_ps,
        mock_parse,
        mock_restarts,
        mock_backup,
        mock_disk,
        mock_gateway,
    ):
        """When everything is healthy, return empty or status-only items."""
        from scripts.collect_agentops import collect_all_signals

        mock_ps.return_value = [{"Name": "hermes", "State": "running", "Status": "Up 3 days"}]
        mock_parse.return_value = [{"name": "hermes", "state": "running", "status": "Up 3 days", "healthy": None}]
        mock_restarts.return_value = []
        mock_backup.return_value = []
        mock_disk.return_value = []
        mock_gateway.return_value = []

        result = collect_all_signals()
        # Should not crash; may return empty list or a "systems nominal" item
        assert isinstance(result, list)


# =============================================================
# Test Suite 9: Running time parser
# =============================================================


class TestParseRunningTime:
    """Parse Docker status strings into timedeltas."""

    def test_parse_minutes(self):
        """'Up 30 minutes' should parse correctly."""
        from scripts.collect_agentops import _parse_running_time

        result = _parse_running_time("Up 30 minutes")
        assert result == timedelta(minutes=30)

    def test_parse_hours(self):
        """'Up 2 hours' should parse correctly."""
        from scripts.collect_agentops import _parse_running_time

        result = _parse_running_time("Up 2 hours")
        assert result == timedelta(hours=2)

    def test_parse_days(self):
        """'Up 3 days' should parse correctly."""
        from scripts.collect_agentops import _parse_running_time

        result = _parse_running_time("Up 3 days")
        assert result == timedelta(days=3)

    def test_parse_seconds(self):
        """'Up 45 seconds' should parse correctly."""
        from scripts.collect_agentops import _parse_running_time

        result = _parse_running_time("Up 45 seconds")
        assert result == timedelta(seconds=45)

    def test_parse_less_than(self):
        """'Up Less than a second' should return 0."""
        from scripts.collect_agentops import _parse_running_time

        result = _parse_running_time("Up Less than a second")
        assert result == timedelta(seconds=0)

    def test_parse_about(self):
        """'Up About an hour' should parse as ~1 hour."""
        from scripts.collect_agentops import _parse_running_time

        result = _parse_running_time("Up About an hour")
        assert result == timedelta(hours=1)

    def test_parse_unknown_format(self):
        """Unknown format should return None."""
        from scripts.collect_agentops import _parse_running_time

        result = _parse_running_time("Some weird status")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
