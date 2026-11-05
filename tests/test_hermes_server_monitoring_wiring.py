"""守卫：Hermes（爱玛士 default + 爱码士 coder）接入 server-monitoring MCP 与日报 cron。

只做静态断言：不连网、不读真实 .env、不打印 token/域名/群 id。

Run: uv run --with pytest --with pyyaml pytest tests/test_hermes_server_monitoring_wiring.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
START_SH = (REPO_ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
SKILL = (REPO_ROOT / "skills" / "server-monitoring-digest" / "SKILL.md").read_text(encoding="utf-8")


def _env(service: str) -> list[str]:
    return [str(item) for item in COMPOSE["services"][service].get("environment", [])]


class TestCompose:
    def test_both_profiles_receive_server_monitoring_env(self):
        for service in ("hermes", "hermes-coder"):
            env = _env(service)
            assert "SERVER_MONITORING_MCP_URL=${SERVER_MONITORING_MCP_URL:-}" in env, service
            assert "SERVER_MONITORING_MCP_TOKEN=${SERVER_MONITORING_MCP_TOKEN:-}" in env, service

    def test_other_hermes_profiles_do_not_receive_it(self):
        for service in ("hermes-daoyuan", "hermes-finance"):
            assert not any(
                item.startswith("SERVER_MONITORING_MCP_") for item in _env(service)
            ), service

    def test_skill_mounted_in_both_profiles(self):
        for service in ("hermes", "hermes-coder"):
            volumes = [str(v) for v in COMPOSE["services"][service].get("volumes", [])]
            assert any(
                "server-monitoring-digest:/opt/hermes-skills/server-monitoring-digest:ro" in v
                for v in volumes
            ), service


class TestStartSh:
    def test_ensure_helper_called_for_both_profiles_before_up(self):
        assert "ensure_hermes_server_monitoring.py" in START_SH
        assert '"${HERMES_DEFAULT_CONFIG}" --env-file' in START_SH
        assert '"${CODER_CONFIG}" --env-file' in START_SH
        up_marker = "docker compose up -d ${BUILD_FLAG}"
        assert START_SH.index("ensure_hermes_server_monitoring.py") < START_SH.index(up_marker)

    def test_restart_logic_for_both_profiles(self):
        assert "DEFAULT_MCP_STATUS" in START_SH
        assert "CODER_MCP_STATUS" in START_SH
        assert "restart_hermes_for_mcp hermes " in START_SH
        assert "restart_hermes_for_mcp hermes-coder " in START_SH
        assert START_SH.index("restart_hermes_for_mcp hermes ") > START_SH.index(
            "docker compose up -d ${BUILD_FLAG}"
        )

    def test_cron_jobs_registered_for_both_profiles(self):
        assert 'docker compose exec -T hermes "${HERMES_BIN}" cron create' in START_SH
        assert 'docker compose exec -T hermes-coder "${HERMES_BIN}" -p coder cron create' in START_SH
        assert '"30 0 * * *"' in START_SH, "08:30 北京 = 00:30 UTC"
        assert '--deliver "${DELIVER}"' in START_SH, "爱玛士私聊"
        assert '--deliver "feishu:${MONITORING_GROUP_CHAT_ID}"' in START_SH, "爱码士→预报系统群"
        assert "MONITORING_DIGEST_GROUP_CHAT_ID" in START_SH
        assert '--name "server-monitoring-digest"' in START_SH
        assert '"${DEFAULT_MCP_STATUS}" != removed*' in START_SH, "URL 清空后不得再注册日报"
        assert '"${CODER_MCP_STATUS}" != removed*' in START_SH, "URL 清空后不得再注册群日报"


class TestEnvExample:
    def test_placeholders_only(self):
        assert "SERVER_MONITORING_MCP_URL=https://<MCP_DOMAIN>:8765/mcp" in ENV_EXAMPLE
        assert "SERVER_MONITORING_MCP_TOKEN=" in ENV_EXAMPLE
        assert "MONITORING_DIGEST_GROUP_CHAT_ID=oc_xxxxxxxx" in ENV_EXAMPLE
        assert not re.search(r"\b10\.\d+\.\d+\.\d+\b", ENV_EXAMPLE)


class TestSkill:
    def test_skill_documents_summary_view_and_readonly(self):
        assert "get_project_runtime_status" in SKILL
        assert 'view="summary"' in SKILL
        assert "不要写文件" in SKILL
        assert "UNKNOWN" in SKILL
        assert "get_project_overview" in SKILL

    def test_skill_is_discoverable_path(self):
        assert (REPO_ROOT / "skills" / "server-monitoring-digest" / "SKILL.md").is_file()
