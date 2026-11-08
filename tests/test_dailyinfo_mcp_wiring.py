"""守卫：dailyinfo 边界（独立 compose 栈 + 道元/虾酱 MCP 客户端）。

dailyinfo 的调度与 FreshRSS 已迁到它自己的 compose 栈（2026-11-08）。
本仓只保留两件事：MCP 客户端注册（道元 + 虾酱）与备份覆盖。这里钉住：

* compose 里不再有 freshrss 服务，也不再出现 dailyinfo launchd 文件
* 两个 ensure 脚本写的是 `${VAR}` 占位符，真实 URL/key 只在容器环境展开
* start.sh / .env.example / 文档 / Uptime Kuma 的接线不漂移

Run: uv run --with pytest --with pyyaml pytest tests/test_dailyinfo_mcp_wiring.py -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()
START_SH = (REPO_ROOT / "scripts" / "start.sh").read_text()
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text()
KUMA = (REPO_ROOT / "scripts" / "setup-uptime-kuma.sh").read_text()
DOC = (REPO_ROOT / "docs" / "dailyinfo.md").read_text()


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hermes_ensure = _load("ensure_hermes_dailyinfo_under_test", "ensure_hermes_dailyinfo.py")
openclaw_ensure = _load("ensure_openclaw_dailyinfo_under_test", "ensure_openclaw_dailyinfo.py")


def _service_block(name: str) -> str:
    lines = COMPOSE.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"  {name}:"))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("  ") and not lines[i].startswith("    ")), len(lines))
    return "\n".join(lines[start:end])


class TestHermesRegistration:
    BASE = """\
mcp_servers:
  zotero:
    url: http://zotero-mcp:8002/mcp
    enabled: true
"""

    def test_writes_placeholders_and_preserves_other_servers(self):
        text, status = hermes_ensure.ensure_dailyinfo(self.BASE, enabled=True)
        assert status == "written:dailyinfo"
        data = yaml.safe_load(text)
        entry = data["mcp_servers"]["dailyinfo"]
        assert entry["url"] == "${DAILYINFO_MCP_URL}"
        assert entry["headers"]["Authorization"] == "Bearer ${DAILYINFO_MCP_KEY}"
        assert entry["managed_by"] == "dailyinfo"
        assert data["mcp_servers"]["zotero"]["url"] == "http://zotero-mcp:8002/mcp"

    def test_idempotent(self):
        once, _ = hermes_ensure.ensure_dailyinfo(self.BASE, enabled=True)
        twice, status = hermes_ensure.ensure_dailyinfo(once, enabled=True)
        assert status == "unchanged:dailyinfo"
        assert twice == once

    def test_disable_removes_only_managed_entry(self):
        once, _ = hermes_ensure.ensure_dailyinfo(self.BASE, enabled=True)
        text, status = hermes_ensure.ensure_dailyinfo(once, enabled=False)
        assert status == "removed:dailyinfo"
        data = yaml.safe_load(text) or {}
        assert "dailyinfo" not in (data.get("mcp_servers") or {})
        assert data["mcp_servers"]["zotero"]["url"] == "http://zotero-mcp:8002/mcp"

    def test_disable_when_absent_is_noop(self):
        text, status = hermes_ensure.ensure_dailyinfo(self.BASE, enabled=False)
        assert status == "unchanged:absent"
        assert text == self.BASE


class TestOpenClawRegistration:
    BASE = {
        "mcp": {"servers": {"paper-queue": {"url": "http://paper-queue-mcp:8003/mcp"}}},
        "channels": {"discord": {"enabled": True}},
    }

    def _write(self, tmp_path: Path) -> Path:
        path = tmp_path / "openclaw.json"
        path.write_text(json.dumps(self.BASE), encoding="utf-8")
        return path

    def test_writes_placeholders_and_preserves_other_servers(self, tmp_path):
        path = self._write(tmp_path)
        status = openclaw_ensure.apply(path, enabled=True, dry_run=False)
        assert status == "written:dailyinfo"
        data = json.loads(path.read_text())
        entry = data["mcp"]["servers"]["dailyinfo"]
        assert entry["url"] == "${DAILYINFO_MCP_URL}"
        assert entry["headers"]["authorization"] == "Bearer ${DAILYINFO_MCP_KEY}"
        assert entry["transport"] == "streamable-http"
        assert data["mcp"]["servers"]["paper-queue"]["url"] == "http://paper-queue-mcp:8003/mcp"
        assert data["channels"]["discord"]["enabled"] is True

    def test_idempotent(self, tmp_path):
        path = self._write(tmp_path)
        assert openclaw_ensure.apply(path, enabled=True, dry_run=False) == "written:dailyinfo"
        assert openclaw_ensure.apply(path, enabled=True, dry_run=False) == "unchanged:dailyinfo"

    def test_disable_removes_entry(self, tmp_path):
        path = self._write(tmp_path)
        openclaw_ensure.apply(path, enabled=True, dry_run=False)
        assert openclaw_ensure.apply(path, enabled=False, dry_run=False) == "removed:dailyinfo"
        data = json.loads(path.read_text())
        assert "dailyinfo" not in data["mcp"]["servers"]

    def test_dry_run_does_not_write(self, tmp_path):
        path = self._write(tmp_path)
        before = path.read_text()
        openclaw_ensure.apply(path, enabled=True, dry_run=True)
        assert path.read_text() == before


class TestBoundaryWiring:
    def test_compose_has_no_freshrss_service(self):
        assert "\n  freshrss:" not in COMPOSE

    def test_dailyinfo_launchd_files_are_gone(self):
        assert not list((REPO_ROOT / "scripts" / "launchd").glob("ai.dailyinfo.*"))
        assert not (REPO_ROOT / "scripts" / "launchd" / "install-dailyinfo.sh").exists()
        assert not (REPO_ROOT / "scripts" / "launchd" / "uninstall-dailyinfo.sh").exists()
        assert not (REPO_ROOT / "scripts" / "migrate-dailyinfo-cron.sh").exists()

    def test_installer_no_longer_installs_dailyinfo(self):
        installer = (REPO_ROOT / "scripts" / "launchd" / "install-all-schedulers.sh").read_text()
        assert "install-dailyinfo.sh" not in installer
        assert '  "dailyinfo" \\' not in installer

    def test_compose_passes_env_to_both_consumers(self):
        for service in ("hermes-daoyuan", "openclaw-gateway"):
            block = _service_block(service)
            assert "DAILYINFO_MCP_URL=${DAILYINFO_MCP_URL:-}" in block
            assert "DAILYINFO_MCP_KEY=${DAILYINFO_MCP_KEY:-}" in block

    def test_start_sh_calls_both_ensure_scripts(self):
        assert "ensure_hermes_dailyinfo.py" in START_SH
        assert "ensure_openclaw_dailyinfo.py" in START_SH

    def test_start_sh_soft_starts_the_dailyinfo_stack(self):
        assert "DAILYINFO_LOCAL_STACK" in START_SH
        assert 'cd "${DAILYINFO_DIR}" && docker compose up -d' in START_SH
        assert "DAILYINFO_LOCAL_STACK" in ENV_EXAMPLE

    def test_env_example_documents_placeholders(self):
        assert "# DAILYINFO_MCP_URL=" in ENV_EXAMPLE
        assert "# DAILYINFO_MCP_KEY=" in ENV_EXAMPLE
        assert not __import__("re").search(r"\b10\.\d+\.\d+\.\d+\b", ENV_EXAMPLE)

    def test_uptime_kuma_monitors_follow_the_new_containers(self):
        assert "FreshRSS|http|http://host.docker.internal:8081" in KUMA
        assert "Docker: dailyinfo-freshrss|docker||dailyinfo-freshrss|" in KUMA
        assert "Docker: dailyinfo-scheduler|docker||dailyinfo-scheduler|" in KUMA

    def test_doc_points_at_the_independent_stack(self):
        assert "dailyinfo-mcp" in DOC
        assert "ensure_hermes_dailyinfo.py" in DOC
        assert "install-dailyinfo.sh" not in DOC

    def test_daoyuan_digest_script_and_helper_exist(self):
        assert (REPO_ROOT / "scripts" / "dailyinfo_digest.py").is_file()
        assert (REPO_ROOT / "scripts" / "ensure_hermes_cron_config.py").is_file()

    def test_start_sh_installs_daoyuan_digest_before_compose_up(self):
        up = START_SH.index("docker compose up -d ${BUILD_FLAG}")
        assert START_SH.index("scripts/dailyinfo_digest.py") < up
        assert "DAOYUAN_SCRIPTS_DIR" in START_SH
        assert "ensure_hermes_cron_config.py" in START_SH

    def test_start_sh_registers_daoyuan_digest_job(self):
        assert "DAOYUAN_FEISHU_DM_CHAT_ID" in START_SH
        assert '--name "dailyinfo-digest"' in START_SH
        assert '"30 23 * * *"' in START_SH
        assert "--no-agent" in START_SH
        assert "--script dailyinfo_digest.py" in START_SH
        assert '--deliver "feishu:${DAOYUAN_DM_CHAT_ID}"' in START_SH

    def test_env_example_documents_daoyuan_dm_chat_id(self):
        assert "# DAOYUAN_FEISHU_DM_CHAT_ID=oc_xxx" in ENV_EXAMPLE
        assert not __import__("re").search(r"oc_[0-9a-f]{32}", ENV_EXAMPLE)

    def test_doc_describes_the_daoyuan_digest(self):
        assert "dailyinfo-digest" in DOC
        assert "DAOYUAN_FEISHU_DM_CHAT_ID" in DOC
        assert "dailyinfo_digest.py" in DOC
