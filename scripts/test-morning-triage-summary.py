#!/usr/bin/env python3
"""
Tests for morning_triage_summary.py — pure functions only.

Usage:
  python3 scripts/test-morning-triage-summary.py
"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

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
        requests = mts.build_search_requests()
        self.assertIsInstance(requests, list)
        self.assertGreater(len(requests), 0)
        for req in requests:
            self.assertIn("endpoint", req)
            self.assertIn("body", req)
            self.assertIsInstance(req["body"], dict)
            self.assertIn("query", req["body"])


class TestAgentOpsIntegration(unittest.TestCase):
    """Test AgentOps signal collection."""

    def test_collect_agentops_safe_returns_list(self):
        signals = mts.collect_agentops_signals_safe()
        self.assertIsInstance(signals, list)

    def test_collect_agentops_safe_never_raises(self):
        with patch(
            "morning_triage_summary.collect_agentops_signals",
            side_effect=Exception("Boom"),
        ):
            try:
                signals = mts.collect_agentops_signals_safe()
                self.assertIsInstance(signals, list)
            except Exception as e:
                self.fail(f"raised: {e}")


class TestReportGeneration(unittest.TestCase):
    """Test Markdown report generation."""

    def test_empty_report_has_all_sections(self):
        report = mts.generate_report([], [], [], "")
        self.assertIn("系统健康", report)
        self.assertIn("昨日记忆", report)
        self.assertIn("活跃场景", report)
        self.assertIn("Daily Command Center", report)

    def test_report_with_agentops(self):
        signals = [{"title": "测试", "status": "watch",
                     "evidence": "e", "why_it_matters": "w",
                     "suggested_next_action": "a"}]
        report = mts.generate_report(signals, [], [], "")
        self.assertIn("测试", report)

    def test_empty_memory_shows_placeholder(self):
        report = mts.generate_report([], [], [], "")
        self.assertIn("记忆数据积累中", report)

    def test_healthy_agentops_shows_green(self):
        report = mts.generate_report([], [], [], "")
        self.assertIn("所有服务正常运行", report)

    def test_manual_override_appears(self):
        report = mts.generate_report([], [], [], "手动备注内容")
        self.assertIn("手动备注", report)
        self.assertIn("手动备注内容", report)


class TestWeekdayCN(unittest.TestCase):
    def test_all_days(self):
        expected = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        for i, exp in enumerate(expected):
            d = date(2026, 7, 20 + i)
            self.assertEqual(mts._weekday_cn(d), exp)


class TestErrorHandling(unittest.TestCase):
    """Test graceful degradation."""

    @patch("morning_triage_summary._gateway_post")
    def test_search_batch_handles_error(self, mock_post):
        mock_post.side_effect = RuntimeError("unreachable")
        result = mts.search_memories_batch()
        self.assertIsInstance(result, list)

    @patch("morning_triage_summary._gateway_post")
    def test_search_batch_handles_empty(self, mock_post):
        mock_post.return_value = {"results": "No matching memories found.", "total": 0}
        result = mts.search_memories_batch()
        self.assertIsInstance(result, list)

    def test_extract_text_edge_cases(self):
        self.assertEqual(mts._extract_text(None), "")
        self.assertEqual(mts._extract_text("string"), "")
        self.assertEqual(mts._extract_text([]), "")

    def test_extract_text_recall(self):
        result = mts._extract_text({"context": "test context", "memory_count": 1})
        self.assertIn("test context", result)

    def test_extract_text_search(self):
        result = mts._extract_text({"results": "Found 5 matches", "total": 5})
        self.assertEqual(result, "Found 5 matches")


if __name__ == "__main__":
    unittest.main(verbosity=2)
