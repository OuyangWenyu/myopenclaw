"""The three Hermes Bearer-MCP registrations share one implementation.

Run: uv run --with pytest --with pyyaml pytest tests/test_ensure_hermes_mcp.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / filename
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load("ensure_hermes_mcp_under_test", "ensure_hermes_mcp.py")
McpTarget = core.McpTarget

TARGETS = [
    McpTarget(
        server_name="server-monitoring",
        managed_by="server-monitoring",
        url_env="SERVER_MONITORING_MCP_URL",
        token_env="SERVER_MONITORING_MCP_TOKEN",
        description="server-monitoring",
    ),
    McpTarget(
        server_name="dailyinfo",
        managed_by="dailyinfo",
        url_env="DAILYINFO_MCP_URL",
        token_env="DAILYINFO_MCP_KEY",
        description="dailyinfo",
    ),
    McpTarget(
        server_name="paper-queue",
        managed_by="paper-queue",
        url_env="PAPER_QUEUE_MCP_URL",
        token_env="PAPER_QUEUE_DAOYUAN_TOKEN",
        description="paper-queue",
        report_missing_token=True,
    ),
]


def _ids(target: McpTarget) -> str:
    return target.server_name


BASE = """\
mcp_servers:
  zotero:
    url: http://zotero-mcp:8002/mcp
    enabled: true
"""


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_write_preserves_sibling_and_stores_placeholders(target: McpTarget) -> None:
    text, status = core.ensure(BASE, target, enabled=True)
    assert status == f"written:{target.server_name}"
    servers = yaml.safe_load(text)["mcp_servers"]
    assert servers["zotero"]["url"] == "http://zotero-mcp:8002/mcp"
    entry = servers[target.server_name]
    assert entry["url"] == f"${{{target.url_env}}}"
    assert entry["headers"]["Authorization"] == f"Bearer ${{{target.token_env}}}"
    assert entry["managed_by"] == target.managed_by


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_second_write_is_unchanged(target: McpTarget) -> None:
    once, _ = core.ensure(BASE, target, enabled=True)
    twice, status = core.ensure(once, target, enabled=True)
    assert status == f"unchanged:{target.server_name}"
    assert twice == once


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_stale_managed_block_is_rewritten(target: McpTarget) -> None:
    stale = (
        "mcp_servers:\n"
        f"  {target.server_name}:\n"
        "    url: http://old.example/mcp\n"
        f"    managed_by: {target.managed_by}\n"
    )
    text, status = core.ensure(stale, target, enabled=True)
    assert status == f"written:{target.server_name}"
    entry = yaml.safe_load(text)["mcp_servers"][target.server_name]
    assert entry["url"] == f"${{{target.url_env}}}"


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_disable_removes_only_the_managed_entry(target: McpTarget) -> None:
    written, _ = core.ensure(BASE, target, enabled=True)
    text, status = core.ensure(written, target, enabled=False)
    assert status == f"removed:{target.server_name}"
    servers = yaml.safe_load(text)["mcp_servers"]
    assert target.server_name not in servers
    assert "zotero" in servers


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_disable_when_absent_is_a_noop(target: McpTarget) -> None:
    text, status = core.ensure(BASE, target, enabled=False)
    assert status == "unchanged:absent"
    assert text == BASE


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_unmanaged_entry_is_a_conflict(target: McpTarget) -> None:
    text = (
        "mcp_servers:\n"
        f"  {target.server_name}:\n"
        "    url: http://custom.example/mcp\n"
    )
    new_text, status = core.ensure(text, target, enabled=True)
    assert status == "conflict:unmanaged"
    assert new_text == text


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_bad_indent_is_rejected(target: McpTarget) -> None:
    text = "mcp_servers:\n    zotero:\n        url: http://zotero-mcp:8002/mcp\n"
    new_text, status = core.ensure(text, target, enabled=True)
    assert status == "unsupported:indent"
    assert new_text == text


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_inline_mapping_is_rejected(target: McpTarget) -> None:
    text = "mcp_servers: {zotero: {url: http://zotero-mcp:8002/mcp}}\n"
    new_text, status = core.ensure(text, target, enabled=True)
    assert status == "unsupported:inline-mcp-servers"
    assert new_text == text


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_duplicate_mcp_servers_keys_are_rejected(target: McpTarget) -> None:
    text = "mcp_servers:\n  zotero:\n    url: x\nmcp_servers:\n  other:\n    url: y\n"
    new_text, status = core.ensure(text, target, enabled=True)
    assert status == "unsupported:duplicate-mcp-servers"
    assert new_text == text


@pytest.mark.parametrize("target", TARGETS, ids=_ids)
def test_missing_token_suffix_follows_the_target_flag(
    target: McpTarget, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.yaml"
    written, _ = core.ensure(BASE, target, enabled=True)
    config.write_text(written, encoding="utf-8")
    rc = core.run(
        target,
        [str(config), "--url", "http://mcp.example/mcp", "--token", ""],
    )
    assert rc == 0
    expected = f"removed:{target.server_name}"
    if target.report_missing_token:
        expected += ":no-token"
    assert capsys.readouterr().out.strip() == expected
    servers = (yaml.safe_load(config.read_text(encoding="utf-8")) or {}).get(
        "mcp_servers"
    ) or {}
    assert target.server_name not in servers


def test_entry_points_delegate_to_the_shared_implementation() -> None:
    cases = [
        (
            "ensure_hermes_server_monitoring.py",
            "ensure_server_monitoring",
            "server-monitoring",
        ),
        ("ensure_hermes_dailyinfo.py", "ensure_dailyinfo", "dailyinfo"),
        ("ensure_hermes_paper_queue.py", "ensure_paper_queue", "paper-queue"),
    ]
    for filename, func_name, server_name in cases:
        module = _load(f"{filename}_under_test", filename)
        text, status = getattr(module, func_name)(BASE, enabled=True)
        assert status == f"written:{server_name}"
        assert server_name in yaml.safe_load(text)["mcp_servers"]
