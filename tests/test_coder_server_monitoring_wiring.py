"""守卫：爱码士 server-monitoring MCP 的 compose / start.sh / env 接线。

只做静态断言：不连网、不读真实 .env、不打印 token/域名。

Run: uv run --with pytest --with pyyaml pytest tests/test_coder_server_monitoring_wiring.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
COMPOSE_TEXT = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
START_SH = (REPO_ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
MONITORING_DOC = (REPO_ROOT / "docs" / "server-monitoring-mcp.md").read_text(encoding="utf-8")


def _env(service: str) -> list[str]:
    return [str(item) for item in COMPOSE["services"][service].get("environment", [])]


class TestCompose:
    def test_coder_receives_server_monitoring_env(self):
        env = _env("hermes-coder")
        assert any(item.startswith("SERVER_MONITORING_MCP_URL=") for item in env)
        assert any(item.startswith("SERVER_MONITORING_MCP_TOKEN=") for item in env)

    def test_other_hermes_profiles_do_not_receive_it(self):
        # 只给爱码士；默认/道元/finance 不注册该 MCP。
        for service in ("hermes", "hermes-daoyuan", "hermes-finance"):
            env = _env(service)
            assert not any(
                item.startswith("SERVER_MONITORING_MCP_") for item in env
            ), service

    def test_coder_env_uses_placeholders_only(self):
        env = _env("hermes-coder")
        url = next(item for item in env if item.startswith("SERVER_MONITORING_MCP_URL="))
        token = next(item for item in env if item.startswith("SERVER_MONITORING_MCP_TOKEN="))
        # 只允许 compose 变量展开，不允许把真实地址/令牌写进公开仓库。
        assert url == "SERVER_MONITORING_MCP_URL=${SERVER_MONITORING_MCP_URL:-}"
        assert token == "SERVER_MONITORING_MCP_TOKEN=${SERVER_MONITORING_MCP_TOKEN:-}"
        assert "https://" not in url
        assert not re.search(r"\b10\.\d+\.\d+\.\d+\b", url)


class TestStartSh:
    def test_calls_ensure_helper_before_up(self):
        assert "ensure_hermes_server_monitoring.py" in START_SH
        assert "CODER_CONFIG" in START_SH
        # 配置必须在容器启动前写好；否则新容器启动时读不到新 MCP。
        up_marker = "docker compose up -d ${BUILD_FLAG}"
        assert START_SH.index("ensure_hermes_server_monitoring.py") < START_SH.index(up_marker)

    def test_restarts_coder_after_config_change(self):
        assert "CODER_MCP_STATUS" in START_SH
        assert "docker compose restart hermes-coder" in START_SH
        assert "_coder_started_before" in START_SH
        assert "_coder_started_after" in START_SH
        # 重启判断在 up -d 之后（容器已重建时跳过，避免无谓重启）。
        assert START_SH.index("docker compose restart hermes-coder") > START_SH.index(
            "docker compose up -d ${BUILD_FLAG}"
        )


class TestEnvExample:
    def test_placeholders_only(self):
        assert "SERVER_MONITORING_MCP_URL=https://<MCP_DOMAIN>:8765/mcp" in ENV_EXAMPLE
        assert "SERVER_MONITORING_MCP_TOKEN=" in ENV_EXAMPLE
        assert not re.search(r"\b10\.\d+\.\d+\.\d+\b", ENV_EXAMPLE)


class TestDocs:
    def test_docs_describe_coder_consumer(self):
        assert "爱码士" in MONITORING_DOC
        assert "server-monitoring" in MONITORING_DOC
        assert "coder" in MONITORING_DOC or "爱码士" in MONITORING_DOC
