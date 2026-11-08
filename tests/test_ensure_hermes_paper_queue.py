"""守卫：ensure_hermes_paper_queue.py 的幂等 YAML 改写。

Run: uv run --with pytest --with pyyaml pytest tests/test_ensure_hermes_paper_queue.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "ensure_hermes_paper_queue_under_test",
        REPO_ROOT / "scripts" / "ensure_hermes_paper_queue.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ensure = _load()

BASE = """\
mcp_servers:
  zotero:
    url: http://zotero-mcp:8002/mcp
    enabled: true
"""


def test_writes_placeholders_and_preserves_other_servers():
    text, status = ensure.ensure_paper_queue(BASE, enabled=True)
    assert status == "written:paper-queue"
    data = yaml.safe_load(text)
    entry = data["mcp_servers"]["paper-queue"]
    assert entry["url"] == "${PAPER_QUEUE_MCP_URL}"
    assert entry["headers"]["Authorization"] == "Bearer ${PAPER_QUEUE_DAOYUAN_TOKEN}"
    assert entry["managed_by"] == "paper-queue"
    assert data["mcp_servers"]["zotero"]["url"] == "http://zotero-mcp:8002/mcp"


def test_idempotent():
    once, _ = ensure.ensure_paper_queue(BASE, enabled=True)
    twice, status = ensure.ensure_paper_queue(once, enabled=True)
    assert status == "unchanged:paper-queue"
    assert twice == once


def test_disable_removes_only_managed_entry():
    once, _ = ensure.ensure_paper_queue(BASE, enabled=True)
    text, status = ensure.ensure_paper_queue(once, enabled=False)
    assert status == "removed:paper-queue"
    data = yaml.safe_load(text) or {}
    assert "paper-queue" not in (data.get("mcp_servers") or {})
    assert data["mcp_servers"]["zotero"]["url"] == "http://zotero-mcp:8002/mcp"


def test_disable_when_absent_is_noop():
    text, status = ensure.ensure_paper_queue(BASE, enabled=False)
    assert status == "unchanged:absent"
    assert text == BASE


def test_appends_mcp_servers_when_missing():
    text, status = ensure.ensure_paper_queue("model:\n  default: deepseek-flash\n", enabled=True)
    assert status == "written:paper-queue"
    data = yaml.safe_load(text)
    assert data["mcp_servers"]["paper-queue"]["managed_by"] == "paper-queue"
    assert data["model"]["default"] == "deepseek-flash"


def test_expands_inline_empty_mcp_servers():
    text, status = ensure.ensure_paper_queue("mcp_servers: {}\n", enabled=True)
    assert status == "written:paper-queue"
    assert yaml.safe_load(text)["mcp_servers"]["paper-queue"]["enabled"] is True


def test_updates_stale_managed_block():
    stale = """\
mcp_servers:
  paper-queue:
    url: http://old:8003/mcp
    managed_by: paper-queue
"""
    text, status = ensure.ensure_paper_queue(stale, enabled=True)
    assert status == "written:paper-queue"
    entry = yaml.safe_load(text)["mcp_servers"]["paper-queue"]
    assert entry["url"] == "${PAPER_QUEUE_MCP_URL}"


def test_unmanaged_entry_is_a_conflict():
    text = """\
mcp_servers:
  paper-queue:
    url: http://custom:8003/mcp
"""
    new_text, status = ensure.ensure_paper_queue(text, enabled=True)
    assert status == "conflict:unmanaged"
    assert new_text == text


def test_invalid_yaml_is_not_written():
    text = "mcp_servers: [unclosed\n"
    new_text, status = ensure.ensure_paper_queue(text, enabled=True)
    assert status == "invalid:yaml"
    assert new_text == text


def test_non_mapping_mcp_servers_is_unsupported():
    text = "mcp_servers: [a, b]\n"
    new_text, status = ensure.ensure_paper_queue(text, enabled=True)
    assert status == "unsupported:yaml"
    assert new_text == text


def test_four_space_indent_is_rejected_not_silently_mangled():
    text = "mcp_servers:\n    zotero:\n        url: http://zotero-mcp:8002/mcp\n"
    new_text, status = ensure.ensure_paper_queue(text, enabled=True)
    assert status == "unsupported:indent"
    assert new_text == text


def test_inline_non_empty_mapping_is_rejected():
    text = "mcp_servers: {zotero: {url: http://zotero-mcp:8002/mcp}}\n"
    new_text, status = ensure.ensure_paper_queue(text, enabled=True)
    assert status == "unsupported:inline-mcp-servers"
    assert new_text == text


def test_comment_after_header_is_rejected():
    text = "mcp_servers:  # keep this\n  zotero:\n    url: x\n"
    new_text, status = ensure.ensure_paper_queue(text, enabled=True)
    assert status == "unsupported:inline-mcp-servers"
    assert new_text == text


def test_duplicate_top_level_sections_are_rejected():
    text = "mcp_servers:\n  zotero:\n    url: x\nmcp_servers:\n  other:\n    url: y\n"
    new_text, status = ensure.ensure_paper_queue(text, enabled=True)
    assert status == "unsupported:duplicate-mcp-servers"
    assert new_text == text


def test_config_contains_no_real_token():
    text, _ = ensure.ensure_paper_queue(BASE, enabled=True)
    assert "PAPER_QUEUE_DAOYUAN_TOKEN" in text
    assert not any(
        len(chunk) == 64 and all(c in "0123456789abcdef" for c in chunk)
        for chunk in text.replace("$", " ").split()
    )


class TestCli:
    def test_writes_and_is_idempotent(self, tmp_path, capsys):
        config = tmp_path / "config.yaml"
        config.write_text(BASE)
        env = tmp_path / ".env"
        env.write_text(
            "PAPER_QUEUE_MCP_URL=http://paper-queue-mcp:8003/mcp\n"
            f"PAPER_QUEUE_DAOYUAN_TOKEN={'k' * 64}\n"
        )
        assert ensure.main([str(config), "--env-file", str(env)]) == 0
        assert "written:paper-queue" in capsys.readouterr().out
        assert yaml.safe_load(config.read_text())["mcp_servers"]["paper-queue"]
        assert ensure.main([str(config), "--env-file", str(env)]) == 0
        assert "unchanged:paper-queue" in capsys.readouterr().out

    def test_no_token_removes_entry(self, tmp_path, capsys):
        config = tmp_path / "config.yaml"
        config.write_text(ensure.ensure_paper_queue(BASE, enabled=True)[0])
        env = tmp_path / ".env"
        env.write_text("PAPER_QUEUE_MCP_URL=http://paper-queue-mcp:8003/mcp\n")
        assert ensure.main([str(config), "--env-file", str(env)]) == 0
        assert "removed:paper-queue:no-token" in capsys.readouterr().out
        data = yaml.safe_load(config.read_text()) or {}
        assert "paper-queue" not in (data.get("mcp_servers") or {})

    def test_disable(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(BASE)
        env = tmp_path / ".env"
        env.write_text(
            "PAPER_QUEUE_MCP_URL=http://paper-queue-mcp:8003/mcp\n"
            f"PAPER_QUEUE_DAOYUAN_TOKEN={'k' * 64}\n"
        )
        assert ensure.main([str(config), "--env-file", str(env)]) == 0
        assert ensure.main([str(config), "--env-file", str(env), "--disable"]) == 0
        data = yaml.safe_load(config.read_text()) or {}
        assert "paper-queue" not in (data.get("mcp_servers") or {})

    def test_dry_run_does_not_write(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text(BASE)
        env = tmp_path / ".env"
        env.write_text(
            "PAPER_QUEUE_MCP_URL=http://paper-queue-mcp:8003/mcp\n"
            f"PAPER_QUEUE_DAOYUAN_TOKEN={'k' * 64}\n"
        )
        assert ensure.main([str(config), "--env-file", str(env), "--dry-run"]) == 0
        assert config.read_text() == BASE

    def test_missing_config(self, tmp_path):
        assert ensure.main([str(tmp_path / "nope.yaml")]) == 2
