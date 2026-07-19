#!/usr/bin/env python3
"""
Tests for ai_news_weekly_push.py.

Usage:
  python3 scripts/test-ai-news-weekly.py
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import ai_news_weekly_push as anw


class TestFindLatestRecap(unittest.TestCase):
    """Test finding the latest weekly recap file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.weekly_dir = Path(self.tmpdir) / "weekly"
        self.weekly_dir.mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_files_returns_none(self):
        result = anw.find_latest_recap(str(self.weekly_dir))
        self.assertIsNone(result)

    def test_finds_weekly_recap(self):
        path = self.weekly_dir / "weekly_recap_2026-07-19.md"
        path.write_text("## AI News Weekly Recap")
        result = anw.find_latest_recap(str(self.weekly_dir))
        self.assertEqual(result, str(path))

    def test_prefers_newest(self):
        older = self.weekly_dir / "weekly_recap_2026-07-12.md"
        newer = self.weekly_dir / "weekly_recap_2026-07-19.md"
        older.write_text("old")
        newer.write_text("new")
        result = anw.find_latest_recap(str(self.weekly_dir))
        self.assertEqual(result, str(newer))

    def test_ignores_non_recap_files(self):
        self.weekly_dir.joinpath("notes.md").write_text("misc")
        self.weekly_dir.joinpath("draft.md").write_text("draft")
        result = anw.find_latest_recap(str(self.weekly_dir))
        self.assertIsNone(result)


class TestPolishPrompt(unittest.TestCase):
    """Test that the polishing prompt is well-formed."""

    def test_prompt_contains_keywords(self):
        prompt = anw.POLISH_PROMPT
        self.assertIn("周报", prompt)
        self.assertIn("润色", prompt)
        self.assertIn("飞书", prompt)
        self.assertIn("AI News", prompt)


class TestReportFormat(unittest.TestCase):
    """Test the combined report format."""

    def test_format_output_with_polish(self):
        """When LLM polish is available, it's used directly."""
        polished = "🤖 AI News 周报 — 7月19日\n\n## 精选\n\n内容..."
        output = anw.format_output("raw", polished, date(2026, 7, 19))
        self.assertEqual(output, polished)

    def test_format_output_fallback(self):
        """Without polish, raw content is wrapped with header."""
        raw = "# AI News Weekly\n\nContent here"
        output = anw.format_output(raw, None, date(2026, 7, 19))
        self.assertIn("AI News 周报", output)
        self.assertIn("Content here", output)


class TestCredentialResolution(unittest.TestCase):
    """Test Feishu credential resolution (Hermes identity)."""

    def test_feishu_app_id_fallback_to_lark(self):
        with patch.dict("os.environ", {
            "LARK_CLI_APP_ID": "lark-app",
            "LARK_CLI_APP_SECRET": "lark-secret",
        }, clear=True):
            import importlib
            import ai_news_weekly_push as anw2
            importlib.reload(anw2)
            self.assertEqual(anw2.FEISHU_APP_ID, "lark-app")

    def test_feishu_app_id_primary(self):
        with patch.dict("os.environ", {
            "FEISHU_APP_ID": "hermes-app",
            "FEISHU_APP_SECRET": "hermes-secret",
            "LARK_CLI_APP_ID": "lark-app",
        }, clear=True):
            import importlib
            import ai_news_weekly_push as anw2
            importlib.reload(anw2)
            self.assertEqual(anw2.FEISHU_APP_ID, "hermes-app")


if __name__ == "__main__":
    unittest.main(verbosity=2)
