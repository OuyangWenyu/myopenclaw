#!/usr/bin/env python3
"""
Tests for morning_triage_summary.py — pure functions only.
External API calls (TDAI Gateway, DeepSeek, Feishu) are mocked.

Usage:
  python3 scripts/test-morning-triage-summary.py
"""

import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

# Import the module under test
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import morning_triage_summary as mts


class TestMemoryQueries(unittest.TestCase):
    """Test TDAI Gateway search query construction."""

    def test_search_keywords_are_non_empty(self):
        self.assertGreater(len(mts.MEMORY_SEARCH_KEYWORDS), 0)
        for kw in mts.MEMORY_SEARCH_KEYWORDS:
            self.assertIsInstance(kw, str)
            self.assertGreater(len(kw), 0)

    def test_build_search_requests(self):
        """build_search_requests() should return list of endpoint+body dicts."""
        requests = mts.build_search_requests()
        self.assertIsInstance(requests, list)
        self.assertGreater(len(requests), 0)
        for req in requests:
            self.assertIn("endpoint", req)
            self.assertIn("body", req)
            self.assertIsInstance(req["body"], dict)
            self.assertIn("query", req["body"])
            has_limit = "limit" in req["body"]
            has_session_key = "session_key" in req["body"]
            self.assertTrue(
                has_limit or has_session_key,
                f"{req['endpoint']} body missing limit or session_key",
            )


class TestAgentOpsIntegration(unittest.TestCase):
    """Test AgentOps signal collection (imports collect_agentops.py)."""

    def test_collect_agentops_safe_returns_list(self):
        """collect_agentops_signals_safe() should return list even on failure."""
        signals = mts.collect_agentops_signals_safe()
        self.assertIsInstance(signals, list)

    def test_collect_agentops_safe_never_raises(self):
        """Safe wrapper must never raise."""
        with patch(
            "morning_triage_summary.collect_agentops_signals",
            side_effect=Exception("Boom"),
        ):
            try:
                signals = mts.collect_agentops_signals_safe()
                self.assertIsInstance(signals, list)
            except Exception as e:
                self.fail(f"collect_agentops_signals_safe raised: {e}")


class TestReportGeneration(unittest.TestCase):
    """Test Markdown report generation."""

    def test_empty_report_structure(self):
        """Report with no data still has all three sections."""
        report = mts.generate_report([], [], [], "")
        self.assertIn("系统健康", report)
        self.assertIn("昨日记忆", report)
        self.assertIn("活跃场景", report)
        self.assertIn("Daily Command Center", report)

    def test_report_with_agentops_only(self):
        signals = [{
            "title": "测试容器近期重启",
            "status": "watch",
            "evidence": "测试容器运行时间: Up 30 minutes",
            "why_it_matters": "可能发生崩溃",
            "suggested_next_action": "检查日志",
        }]
        report = mts.generate_report(signals, [], [], "")
        self.assertIn("测试容器近期重启", report)
        self.assertIn("可能发生崩溃", report)

    def test_report_with_memories(self):
        memories = ["用户昨天决定使用 PostgreSQL 替代 SQLite"]
        report = mts.generate_report([], memories, [], "")
        self.assertIn("用户昨天决定使用 PostgreSQL 替代 SQLite", report)

    def test_report_with_scenarios(self):
        scenarios = ["Morning Triage v2 开发"]
        report = mts.generate_report([], [], scenarios, "")
        self.assertIn("Morning Triage v2 开发", report)

    def test_report_with_manual_override(self):
        override = "今天要看一下备份是否正常"
        report = mts.generate_report([], [], [], override)
        self.assertIn("手动备注", report)
        self.assertIn("今天要看一下备份是否正常", report)

    def test_empty_memory_shows_placeholder(self):
        """Empty memories should show a placeholder, not silent omission."""
        report = mts.generate_report([], [], [], "")
        self.assertIn("记忆数据积累中", report)

    def test_healthy_agentops_shows_green(self):
        """No signals should show 'all systems nominal'."""
        report = mts.generate_report([], [], [], "")
        self.assertIn("所有服务正常运行", report)


class TestWeekdayCN(unittest.TestCase):
    def test_all_days(self):
        """All 7 weekdays map to Chinese."""
        expected = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        for i, exp in enumerate(expected):
            d = date(2026, 7, 20 + i)  # 2026-07-20 is Monday
            self.assertEqual(mts._weekday_cn(d), exp)


class TestFeishuCardFormat(unittest.TestCase):
    """Test Feishu interactive card format."""

    def test_build_card(self):
        content = "测试内容"
        today = date(2026, 7, 20)
        card = mts.build_feishu_card(content, today)
        self.assertIsInstance(card, dict)
        self.assertIn("config", card)
        self.assertIn("header", card)
        self.assertIn("elements", card)
        self.assertIn("title", card["header"])
        self.assertEqual(card["header"]["title"]["tag"], "plain_text")
        elements = card["elements"]
        self.assertEqual(len(elements), 1)
        self.assertEqual(elements[0]["tag"], "markdown")
        self.assertEqual(elements[0]["content"], content)


class TestErrorHandling(unittest.TestCase):
    """Test graceful degradation on failures."""

    @patch("morning_triage_summary._gateway_post")
    def test_search_memories_handles_connection_error(self, mock_post):
        mock_post.side_effect = RuntimeError("Gateway unreachable")
        try:
            result = mts.search_memories_batch()
            self.assertIsInstance(result, list)
        except Exception as e:
            self.fail(f"search_memories_batch raised unexpectedly: {e}")

    @patch("morning_triage_summary._gateway_post")
    def test_search_memories_handles_empty_response(self, mock_post):
        mock_post.return_value = {"results": "No matching memories found.", "total": 0}
        result = mts.search_memories_batch()
        self.assertIsInstance(result, list)

    def test_extract_text_handles_non_dict(self):
        self.assertEqual(mts._extract_text(None), "")  # type: ignore
        self.assertEqual(mts._extract_text("string"), "")  # type: ignore
        self.assertEqual(mts._extract_text([]), "")  # type: ignore

    def test_extract_text_recall_response(self):
        result = mts._extract_text({"context": "用户正在开发 Morning Triage v2", "memory_count": 3})
        self.assertIn("Morning Triage", result)

    def test_extract_text_search_response(self):
        result = mts._extract_text({"results": "Found 5 matches", "total": 5})
        self.assertEqual(result, "Found 5 matches")


class TestFeishuCredentialResolution(unittest.TestCase):
    """Test Feishu credential fallback chain uses Hermes identity."""

    def test_feishu_app_id_preferred(self):
        """FEISHU_APP_ID should be primary — no fallback needed."""
        with patch.dict("os.environ", {
            "FEISHU_APP_ID": "hermes-app",
            "FEISHU_APP_SECRET": "hermes-secret",
            "LARK_CLI_APP_ID": "lark-app",
            "LARK_CLI_APP_SECRET": "lark-secret",
            "CC_CONNECT_FEISHU_APP_ID": "cc-app",
            "CC_CONNECT_FEISHU_APP_SECRET": "cc-secret",
        }, clear=True):
            # Reload module-level configs
            import importlib
            import morning_triage_summary as mts2
            importlib.reload(mts2)
            self.assertEqual(mts2.FEISHU_APP_ID, "hermes-app")
            self.assertEqual(mts2.FEISHU_APP_SECRET, "hermes-secret")

    def test_fallback_to_lark_cli(self):
        """Without FEISHU_APP_ID, fall back to LARK_CLI (Hermes bot)."""
        with patch.dict("os.environ", {
            "LARK_CLI_APP_ID": "lark-app",
            "LARK_CLI_APP_SECRET": "lark-secret",
            "CC_CONNECT_FEISHU_APP_ID": "cc-app",
            "CC_CONNECT_FEISHU_APP_SECRET": "cc-secret",
        }, clear=True):
            import importlib
            import morning_triage_summary as mts2
            importlib.reload(mts2)
            self.assertEqual(mts2.FEISHU_APP_ID, "lark-app")
            self.assertEqual(mts2.FEISHU_APP_SECRET, "lark-secret")

    def test_ultimate_fallback_to_cc_connect(self):
        """Without FEISHU or LARK_CLI, fall back to CC_CONNECT."""
        with patch.dict("os.environ", {
            "CC_CONNECT_FEISHU_APP_ID": "cc-app",
            "CC_CONNECT_FEISHU_APP_SECRET": "cc-secret",
        }, clear=True):
            import importlib
            import morning_triage_summary as mts2
            importlib.reload(mts2)
            self.assertEqual(mts2.FEISHU_APP_ID, "cc-app")
            self.assertEqual(mts2.FEISHU_APP_SECRET, "cc-secret")

    def test_no_credentials_is_empty(self):
        """CC_CONNECT_FEISHU should NOT be in the fallback chain."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib
            import morning_triage_summary as mts2
            importlib.reload(mts2)
            # Should be empty string, not cc-connect
            self.assertEqual(mts2.FEISHU_APP_ID, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
