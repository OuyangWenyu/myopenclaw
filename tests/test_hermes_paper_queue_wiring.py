"""守卫：道元 paper-queue 接线、道元权限收紧、虾酱接线移除。

Run: uv run --with pytest --with pyyaml pytest tests/test_hermes_paper_queue_wiring.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()
START_SH = (REPO_ROOT / "scripts" / "start.sh").read_text()
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text()
DOC = (REPO_ROOT / "docs" / "paper-queue.md").read_text()
SKILL = (REPO_ROOT / "skills" / "paper-queue" / "SKILL.md").read_text()
DOCKERFILE = (REPO_ROOT / "docker" / "paper-queue-mcp" / "Dockerfile").read_text()


def _service_block(name: str) -> str:
    lines = COMPOSE.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"  {name}:"))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("  ") and not lines[i].startswith("    ")), len(lines))
    return "\n".join(lines[start:end])


class TestComposeWiring:
    def test_server_gets_the_token_binding(self):
        block = _service_block("paper-queue-mcp")
        assert "PAPER_QUEUE_DAOYUAN_TOKEN=${PAPER_QUEUE_DAOYUAN_TOKEN:-}" in block
        # 真实 Discord ID 只放 .env，不落公开仓库
        assert "PAPER_QUEUE_DAOYUAN_ACTOR_ID=${PAPER_QUEUE_DAOYUAN_ACTOR_ID:-}" in block
        assert "PAPER_QUEUE_DAOYUAN_ACTOR_NAME=${PAPER_QUEUE_DAOYUAN_ACTOR_NAME:-Owen}" in block
        # 旧 HMAC 密钥保留在 .env，但默认不注入（避免重新打开任意 actor 写入旁路）
        assert "\n      - PAPER_QUEUE_ACTOR_SECRET=" not in block
        assert "# - PAPER_QUEUE_ACTOR_SECRET=${PAPER_QUEUE_ACTOR_SECRET:-}" in block

    def test_daoyuan_gets_the_mcp_entry_and_token(self):
        block = _service_block("hermes-daoyuan")
        assert "PAPER_QUEUE_MCP_URL=${PAPER_QUEUE_MCP_URL:-http://paper-queue-mcp:8003/mcp}" in block
        assert "PAPER_QUEUE_DAOYUAN_TOKEN=${PAPER_QUEUE_DAOYUAN_TOKEN:-}" in block

    def test_daoyuan_mounts_the_shared_skill(self):
        block = _service_block("hermes-daoyuan")
        assert "./skills/paper-queue:/opt/hermes-skills/paper-queue:ro" in block

    def test_daoyuan_cannot_see_the_queue_db(self):
        block = _service_block("hermes-daoyuan")
        assert "myagentdata/paper-queue" not in block

    def test_daoyuan_is_owner_only(self):
        block = _service_block("hermes-daoyuan")
        # 群聊 allowlist = 仅 FEISHU_ALLOWED_USERS（名单内用户）@ 时响应；require_mention 默认 true
        assert "FEISHU_GROUP_POLICY=${DAOYUAN_FEISHU_GROUP_POLICY:-allowlist}" in block
        assert "GATEWAY_ALLOW_ALL_USERS=true" not in block
        assert "FEISHU_ALLOWED_USERS=${DAOYUAN_FEISHU_ALLOWED_USERS:-}" in block

    def test_start_sh_warns_when_the_allowlist_is_empty(self):
        # 留空 = pairing 模式（不是 deny-all）；部署脚本必须先喊一声
        assert "DAOYUAN_FEISHU_ALLOWED_USERS 为空" in START_SH

    def test_server_port_is_loopback_only(self):
        block = _service_block("paper-queue-mcp")
        assert '127.0.0.1:${PAPER_QUEUE_MCP_PORT:-8003}:8003' in block

    def test_mcp_sdk_is_pinned(self):
        # token 通道依赖 1.30.0 的 Context 注入；漂移会让请求静默走回 HMAC
        assert "mcp==1.30.0" in DOCKERFILE


class TestStartShWiring:
    def test_registers_the_hermes_mcp_before_compose_up(self):
        up = START_SH.index("docker compose up -d ${BUILD_FLAG}")
        assert START_SH.index("ensure_hermes_paper_queue.py") < up

    def test_generates_the_token_and_url(self):
        assert "PAPER_QUEUE_DAOYUAN_TOKEN" in START_SH
        assert "PAPER_QUEUE_MCP_URL" in START_SH
        assert "secrets.token_hex(32)" in START_SH

    def test_links_the_skill_into_daoyuan_profile(self):
        assert "profiles/daoyuan/skills/paper-queue" in START_SH
        assert "/opt/hermes-skills/paper-queue" in START_SH

    def test_openclaw_paper_queue_install_is_gone(self):
        assert "install_paper_queue" not in START_SH
        assert "ensure_openclaw_paper_queue.py" not in START_SH

    def test_live_openclaw_cleanup_is_wired(self):
        assert "remove_openclaw_paper_queue.py" in START_SH
        assert "paper-queue-actor" in START_SH

    def test_daoyuan_restart_after_mcp_change(self):
        assert "restart_hermes_for_mcp hermes-daoyuan" in START_SH


class TestOpenClawReferenceCodeIsKept:
    """虾酱不再安装，但代码保留给未来道元多用户身份插件做参考。"""

    def test_plugin_and_ensure_script_exist(self):
        assert (REPO_ROOT / "openclaw" / "plugins" / "paper-queue-actor" / "index.ts").is_file()
        assert (REPO_ROOT / "scripts" / "ensure_openclaw_paper_queue.py").is_file()

    def test_openclaw_example_config_no_longer_registers_it(self):
        example = (REPO_ROOT / "openclaw" / "config" / "openclaw.json.example").read_text()
        data = yaml.safe_load(example)
        servers = ((data.get("mcp") or {}).get("servers") or {})
        assert "paper-queue" not in servers


class TestEnvExample:
    def test_documents_placeholders_only(self):
        assert "# PAPER_QUEUE_MCP_URL=" in ENV_EXAMPLE
        assert "# PAPER_QUEUE_DAOYUAN_TOKEN=" in ENV_EXAMPLE
        assert "# DAOYUAN_FEISHU_ALLOWED_USERS=" in ENV_EXAMPLE
        assert not re.search(r"^PAPER_QUEUE_DAOYUAN_TOKEN=[0-9a-f]{64}$", ENV_EXAMPLE, re.M)
        assert "PAPER_QUEUE_OPENCLAW_ENABLED" not in ENV_EXAMPLE


class TestSkill:
    def test_reuses_one_runtime_neutral_skill(self):
        assert "paper_queue_add" in SKILL
        assert "mcp__paper_queue__paper_queue_add" in SKILL
        assert "mcp__paper_queue__paper_queue_list" in SKILL
        assert "mcp__paper_queue__paper_queue_cancel" in SKILL
        assert "只记不下" in SKILL
        assert "paper-fetch" in SKILL
        assert "不要填" in SKILL
        # 旧的 OpenClaw 副本不应再存在
        assert not (REPO_ROOT / "openclaw" / "skills" / "paper-queue").exists()


class TestDailySync:
    """每日对账：已入库 Zotero 的条目自动撤销（no-agent cron）。"""

    def test_script_exists(self):
        assert (REPO_ROOT / "scripts" / "paper_queue_sync.py").is_file()

    def test_start_sh_installs_the_script_before_compose_up(self):
        up = START_SH.index("docker compose up -d ${BUILD_FLAG}")
        assert START_SH.index("scripts/paper_queue_sync.py") < up
        assert "install_hermes_profile_script" in START_SH

    def test_start_sh_registers_the_daily_job(self):
        assert (
            'ensure_daoyuan_script_cron "paper-queue-sync" "20 23 * * *"' in START_SH
        )
        assert 'paper_queue_sync.py "${DAOYUAN_DM_CHAT_ID}"' in START_SH
        assert "--no-agent" in START_SH
        # 共用函数必须真的把参数传给 cron create/edit，而不是只收了参数
        assert '--schedule "${schedule}"' in START_SH
        assert '--script "${script}"' in START_SH
        assert '--deliver "feishu:${chat_id}"' in START_SH

    def test_registration_is_gated_on_the_queue_url(self):
        # .env 显式留空 PAPER_QUEUE_MCP_URL = 停用（非空才算配置）
        assert "PAPER_QUEUE_URL_SET" in START_SH
        assert "grep -c '^PAPER_QUEUE_MCP_URL=.'" in START_SH
        assert "跳过 paper-queue-sync 注册" in START_SH

    def test_disabled_queue_removes_the_existing_job(self):
        # 文档承诺的「持久关闭」必须真的停掉已注册的 job，而不是只跳过注册
        assert "remove_daoyuan_script_cron" in START_SH
        assert 'cron remove "${name}"' in START_SH

    def test_doc_describes_the_daily_sync(self):
        assert "paper-queue-sync" in DOC
        assert "paper_queue_sync.py" in DOC
        assert "已入库" in DOC

    def test_skill_warns_that_entries_may_disappear(self):
        assert "自动清理" in SKILL


class TestDocs:
    def test_mentions_the_daoyuan_token_path(self):
        assert "PAPER_QUEUE_DAOYUAN_TOKEN" in DOC
        assert "道元" in DOC
        assert "attribution_source" in DOC
        assert "虾酱" in DOC
