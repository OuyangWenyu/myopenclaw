"""守卫：爱码士（Hermes coder profile）的 server-monitoring MCP 注册。

`scripts/ensure_hermes_server_monitoring.py` 用手术式 YAML 编辑把
`mcp_servers.server-monitoring` 写进 coder profile 配置。这里只测纯函数与
CLI 行为，不连网、不读真实 .env、不打印真实 token/域名。

Run: uv run --with pytest --with pyyaml pytest tests/test_ensure_hermes_server_monitoring.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "ensure_hermes_server_monitoring.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "ensure_hermes_server_monitoring_under_test", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ensure = _load_script()

BASE = """\
model:
  default: deepseek-flash
  provider: deepseek

platforms:
  feishu:
    enabled: true
"""


def _server_entry(config_text: str) -> dict:
    data = yaml.safe_load(config_text)
    return data["mcp_servers"]["server-monitoring"]


class TestEnsureServerMonitoring:
    def test_appends_when_missing(self):
        text, status = ensure.ensure_server_monitoring(BASE, enabled=True)
        assert status.startswith("written")
        entry = _server_entry(text)
        assert entry["url"] == "${SERVER_MONITORING_MCP_URL}"
        assert entry["headers"]["Authorization"] == "Bearer ${SERVER_MONITORING_MCP_TOKEN}"
        assert entry["enabled"] is True
        assert entry["managed_by"] == "server-monitoring"
        # 原有内容必须保留
        data = yaml.safe_load(text)
        assert data["model"]["default"] == "deepseek-flash"
        assert data["platforms"]["feishu"]["enabled"] is True

    def test_idempotent(self):
        once, first = ensure.ensure_server_monitoring(BASE, enabled=True)
        twice, second = ensure.ensure_server_monitoring(once, enabled=True)
        assert first.startswith("written")
        assert second.startswith("unchanged")
        assert twice == once

    def test_inserts_into_existing_mcp_servers_block(self):
        text = BASE + "\nmcp_servers:\n  zotero:\n    url: http://zotero-mcp:8002/mcp\n"
        out, status = ensure.ensure_server_monitoring(text, enabled=True)
        assert status.startswith("written")
        data = yaml.safe_load(out)
        assert set(data["mcp_servers"]) == {"zotero", "server-monitoring"}
        assert data["mcp_servers"]["zotero"]["url"] == "http://zotero-mcp:8002/mcp"

    def test_unmanaged_entry_is_a_conflict(self):
        text = BASE + (
            "\nmcp_servers:\n"
            "  server-monitoring:\n"
            "    url: http://custom.example/mcp\n"
        )
        out, status = ensure.ensure_server_monitoring(text, enabled=True)
        assert status.startswith("conflict")
        assert out == text

    def test_disable_removes_managed_entry_and_empty_block(self):
        once, _ = ensure.ensure_server_monitoring(BASE, enabled=True)
        out, status = ensure.ensure_server_monitoring(once, enabled=False)
        assert status.startswith("removed")
        assert "server-monitoring:" not in out
        assert "mcp_servers:" not in out
        data = yaml.safe_load(out)
        assert data["model"]["default"] == "deepseek-flash"

    def test_disable_keeps_sibling_entries(self):
        text = BASE + "\nmcp_servers:\n  zotero:\n    url: http://zotero-mcp:8002/mcp\n"
        once, _ = ensure.ensure_server_monitoring(text, enabled=True)
        out, status = ensure.ensure_server_monitoring(once, enabled=False)
        assert status.startswith("removed")
        data = yaml.safe_load(out)
        assert set(data["mcp_servers"]) == {"zotero"}

    def test_disable_without_entry_is_unchanged(self):
        out, status = ensure.ensure_server_monitoring(BASE, enabled=False)
        assert status.startswith("unchanged")
        assert out == BASE

    def test_replaces_stale_managed_block(self):
        stale = BASE + (
            "\nmcp_servers:\n"
            "  server-monitoring:\n"
            "    url: ${SERVER_MONITORING_MCP_URL}\n"
            "    managed_by: server-monitoring\n"
        )
        out, status = ensure.ensure_server_monitoring(stale, enabled=True)
        assert status.startswith("written")
        entry = _server_entry(out)
        assert entry["headers"]["Authorization"] == "Bearer ${SERVER_MONITORING_MCP_TOKEN}"

    def test_no_real_identifiers_or_tokens(self):
        out, _ = ensure.ensure_server_monitoring(BASE, enabled=True)
        # 配置里只允许 ${VAR} 占位符，不允许出现真实 endpoint/token。
        assert "https://" not in out
        assert "10.48." not in out
        assert "Bearer ey" not in out


class TestCli:
    def test_dry_run_does_not_write(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(BASE, encoding="utf-8")
        rc = ensure.main(
            [
                str(cfg),
                "--url",
                "https://<MCP_DOMAIN>:8765/mcp",
                "--token",
                "dummy-token",
                "--dry-run",
            ]
        )
        assert rc == 0
        assert cfg.read_text(encoding="utf-8") == BASE

    def test_write_then_disable(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(BASE, encoding="utf-8")
        assert (
            ensure.main(
                [
                    str(cfg),
                    "--url",
                    "https://<MCP_DOMAIN>:8765/mcp",
                    "--token",
                    "dummy-token",
                ]
            )
            == 0
        )
        assert "server-monitoring" in cfg.read_text(encoding="utf-8")
        assert ensure.main([str(cfg), "--disable"]) == 0
        assert "server-monitoring" not in cfg.read_text(encoding="utf-8")

    def test_empty_url_disables_existing_entry(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(BASE, encoding="utf-8")
        ensure.main(
            [
                str(cfg),
                "--url",
                "https://<MCP_DOMAIN>:8765/mcp",
                "--token",
                "dummy-token",
            ]
        )
        assert ensure.main([str(cfg), "--url", "", "--token", ""]) == 0
        assert "server-monitoring" not in cfg.read_text(encoding="utf-8")

    def test_unmanaged_entry_exits_nonzero(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            BASE
            + "\nmcp_servers:\n  server-monitoring:\n    url: http://custom.example/mcp\n",
            encoding="utf-8",
        )
        rc = ensure.main(
            [
                str(cfg),
                "--url",
                "https://<MCP_DOMAIN>:8765/mcp",
                "--token",
                "dummy-token",
            ]
        )
        assert rc == 1
        assert "custom.example" in cfg.read_text(encoding="utf-8")
