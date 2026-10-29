"""Unit tests for scripts/ensure_issue_watcher_auth.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ensure_issue_watcher_auth.py"
spec = importlib.util.spec_from_file_location("ensure_issue_watcher_auth", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

VALID = "k" * 40


class TestKeys:
    def test_generates_missing_and_preserves_existing(self):
        pairs = {"HERMES_REPO_SCANNER_KEY": VALID}
        out = mod.ensure_keys(pairs)
        assert out["HERMES_REPO_SCANNER_KEY"] == VALID
        for var in ("TIANYI_BOT_REPO_SCANNER_KEY", "ISSUE_NOTIFIER_MCP_TOKEN"):
            assert len(out[var]) >= 32

    def test_short_key_is_replaced(self):
        out = mod.ensure_keys({"HERMES_REPO_SCANNER_KEY": "short"})
        assert out["HERMES_REPO_SCANNER_KEY"] != "short"
        assert len(out["HERMES_REPO_SCANNER_KEY"]) >= 32

    def test_key_map_merges_existing_entries(self):
        existing = {"x" * 40: "other"}
        pairs = {
            "HERMES_REPO_SCANNER_KEY": "h" * 40,
            "TIANYI_BOT_REPO_SCANNER_KEY": "t" * 40,
            "ISSUE_NOTIFIER_MCP_TOKEN": "n" * 40,
            "GIT_STATS_API_KEYS": json.dumps(existing),
        }
        merged = json.loads(mod.build_key_map(pairs))
        assert merged["x" * 40] == "other"
        assert merged["h" * 40] == "hermes"
        assert merged["t" * 40] == "tianyi"
        assert merged["n" * 40] == "notifier"

    def test_invalid_key_map_fails_loud(self):
        pairs = {
            "HERMES_REPO_SCANNER_KEY": VALID,
            "TIANYI_BOT_REPO_SCANNER_KEY": VALID,
            "ISSUE_NOTIFIER_MCP_TOKEN": VALID,
            "GIT_STATS_API_KEYS": "not-json",
        }
        with pytest.raises(mod.ScriptError):
            mod.build_key_map(pairs)


class TestUpdateLines:
    def test_rewrites_existing_and_appends_missing(self):
        lines = [
            "# comment",
            "OTHER=1",
            "HERMES_REPO_SCANNER_KEY=old",
        ]
        pairs = mod.ensure_keys(mod.parse_env(lines))
        updated = mod.update_lines(lines, pairs)
        text = "\n".join(updated)
        assert "# comment" in text
        assert "OTHER=1" in text
        assert f"HERMES_REPO_SCANNER_KEY={pairs['HERMES_REPO_SCANNER_KEY']}" in text
        assert "TIANYI_BOT_REPO_SCANNER_KEY=" in text
        assert "ISSUE_NOTIFIER_MCP_TOKEN=" in text
        assert "GIT_STATS_API_KEYS={" in text

    def test_idempotent(self):
        lines = ["A=1"]
        pairs = mod.ensure_keys(mod.parse_env(lines))
        once = mod.update_lines(lines, pairs)
        twice = mod.update_lines(once, mod.ensure_keys(mod.parse_env(once)))
        assert once == twice

    def test_mask_does_not_expose_value(self):
        masked = mod.mask(VALID)
        assert VALID not in masked
        assert "len 40" in masked


class TestCli:
    def test_dry_run_does_not_write(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OTHER=1\n", encoding="utf-8")
        before = env.read_text(encoding="utf-8")
        assert mod.main(["--env-file", str(env), "--dry-run"]) == 0
        assert env.read_text(encoding="utf-8") == before

    def test_writes_valid_keys(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OTHER=1\n", encoding="utf-8")
        assert mod.main(["--env-file", str(env)]) == 0
        pairs = mod.parse_env(mod.read_env(env))
        assert pairs["OTHER"] == "1"
        assert mod._valid_key(pairs["HERMES_REPO_SCANNER_KEY"])
        key_map = json.loads(pairs["GIT_STATS_API_KEYS"])
        assert key_map[pairs["ISSUE_NOTIFIER_MCP_TOKEN"]] == "notifier"
