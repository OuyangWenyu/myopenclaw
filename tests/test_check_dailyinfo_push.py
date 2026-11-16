"""dailyinfo 三路推送自检脚本的单测（纯函数）。

Run: uv run --with pytest pytest tests/test_check_dailyinfo_push.py -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_dailyinfo_push_under_test",
        REPO_ROOT / "scripts" / "check_dailyinfo_push.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load()


def _write_delivery(root: Path, sink: str, day: str, category: str, payload: dict):
    path = root / "deliveries" / sink / f"{category}-{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_briefing(root: Path, day: str, category: str):
    year, month, dom = day.split("-")
    path = root / "publications" / "briefings" / year / month / dom / category / "briefing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


class TestBeijingDate:
    def test_utc_after_midnight_is_previous_cst_day(self):
        assert checker.beijing_date("2026-11-14T23:30:17.335460+00:00") == "2026-11-15"

    def test_utc_noon_is_same_day(self):
        assert checker.beijing_date("2026-11-15T04:00:00Z") == "2026-11-15"

    def test_invalid_returns_none(self):
        assert checker.beijing_date("not-a-date") is None
        assert checker.beijing_date(None) is None


class TestExpectedCategories:
    def test_only_categories_with_briefing(self, tmp_path):
        _write_briefing(tmp_path, "2026-11-15", "papers")
        _write_briefing(tmp_path, "2026-11-15", "ai_news")
        assert checker.expected_categories(tmp_path, "2026-11-15") == ["papers", "ai_news"]

    def test_unknown_day_is_empty(self, tmp_path):
        assert checker.expected_categories(tmp_path, "2026-11-15") == []

    def test_bad_date_is_empty(self, tmp_path):
        assert checker.expected_categories(tmp_path, "2026-11") == []


class TestDeliveryEntries:
    def test_success_failed_missing_and_corrupt(self, tmp_path):
        day = "2026-11-15"
        _write_delivery(tmp_path, "discord", day, "papers", {"status": "success", "attempt_count": 1})
        _write_delivery(
            tmp_path, "discord", day, "code",
            {"status": "failed", "attempt_count": 3, "last_error": "timeout"},
        )
        (tmp_path / "deliveries" / "discord" / f"resource-{day}.json").write_text(
            "{not json", encoding="utf-8"
        )
        entries = checker.delivery_entries(
            tmp_path, "discord", day, ["papers", "code", "resource", "ai_news"]
        )
        assert entries["papers"]["status"] == "success"
        assert entries["code"]["status"] == "failed"
        assert entries["code"]["error"] == "timeout"
        assert entries["resource"]["status"] == "missing"
        assert entries["ai_news"]["status"] == "missing"

    def test_pending_is_not_success(self, tmp_path):
        _write_delivery(tmp_path, "web", "2026-11-15", "papers", {"status": "pending"})
        entries = checker.delivery_entries(tmp_path, "web", "2026-11-15", ["papers"])
        assert entries["papers"]["status"] == "pending"
        assert checker.channel_overall(entries) == "missing"


class TestChannelOverall:
    def test_all_success(self):
        assert checker.channel_overall({"a": {"status": "success"}}) == "success"

    def test_any_failed_wins_over_missing(self):
        entries = {"a": {"status": "failed"}, "b": {"status": "missing"}}
        assert checker.channel_overall(entries) == "failed"

    def test_missing_only(self):
        assert checker.channel_overall({"a": {"status": "missing"}}) == "missing"

    def test_empty(self):
        assert checker.channel_overall({}) == "empty"


class TestExecutions:
    def test_pick_latest_by_beijing_day(self):
        rows = [
            {"id": "a", "started_at": "2026-11-14T23:30:00+00:00"},
            {"id": "b", "started_at": "2026-11-13T23:30:00+00:00"},
            {"id": "c", "started_at": "2026-11-15T01:00:00+00:00"},
        ]
        assert checker.pick_latest_execution(rows, "2026-11-15")["id"] == "c"

    def test_pick_none_for_other_day(self):
        rows = [{"id": "a", "started_at": "2026-11-13T23:30:00+00:00"}]
        assert checker.pick_latest_execution(rows, "2026-11-15") is None

    def test_execution_status(self):
        assert checker.execution_status(None) == "missing"
        assert checker.execution_status({"status": "completed"}) == "success"
        assert checker.execution_status(
            {"status": "completed", "delivery_outcome": "failed"}
        ) == "failed"
        assert checker.execution_status({"status": "failed"}) == "failed"
        assert checker.execution_status({"status": "running"}) == "running"
        assert checker.execution_status({"status": "unknown"}) == "failed"


class TestExitCode:
    def _report(self, statuses):
        return {"channels": {name: {"status": status} for name, status in statuses.items()}}

    def test_all_success(self):
        report = self._report({"discord": "success", "web": "success", "daoyuan": "success"})
        assert checker.overall_exit_code(report) == 0

    def test_any_failure(self):
        report = self._report({"discord": "success", "web": "failed", "daoyuan": "success"})
        assert checker.overall_exit_code(report) == 1

    def test_config_error(self):
        assert checker.overall_exit_code({"config_error": "boom", "channels": {}}) == 2

    def test_missing_channels(self):
        assert checker.overall_exit_code({}) == 2


class TestRenderHuman:
    def test_renders_failures_and_hint(self):
        report = {
            "date": "2026-11-15",
            "checked_at": "2026-11-15T07:45:00+08:00",
            "ok": False,
            "diagnostics": {"scheduler": "healthy"},
            "actions": [{"channel": "discord", "action": "run-job push_early", "ok": True}],
            "channels": {
                "discord": {
                    "status": "failed",
                    "categories": {"papers": {"status": "failed", "error": "timeout"}},
                },
                "web": {"status": "success", "categories": {}},
                "daoyuan": {
                    "status": "failed",
                    "error": "CONTENT_CHANGED",
                    "hint": "需要修 mcp",
                },
            },
        }
        text = checker.render_human(report)
        assert "Discord" in text and "papers: failed timeout" in text
        assert "需要修 mcp" in text
        assert "run-job push_early" in text
        assert "仍有失败" in text

    def test_renders_ok(self):
        report = {
            "date": "2026-11-15",
            "checked_at": "2026-11-15T07:45:00+08:00",
            "ok": True,
            "channels": {
                "discord": {"status": "success", "categories": {}},
                "web": {"status": "success", "categories": {}},
                "daoyuan": {"status": "success"},
            },
        }
        assert "三路都正常" in checker.render_human(report)


class TestKnownIssuesWiring:
    def test_content_changed_is_non_retryable_and_has_hint(self):
        assert "CONTENT_CHANGED" in checker._NON_RETRYABLE
        assert "CONTENT_CHANGED" in checker.KNOWN_ISSUES
        assert "st_ino" in checker.KNOWN_ISSUES["CONTENT_CHANGED"]

    def test_category_job_mapping_covers_all_categories(self):
        assert set(checker.CATEGORY_JOB) == set(checker.CANONICAL_CATEGORIES)
