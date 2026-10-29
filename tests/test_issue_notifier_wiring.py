"""Static guards for the issue-notifier wiring (compose/env/start/backup/docs).

Run: uv run --with pytest --with pyyaml pytest tests/test_issue_notifier_wiring.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
COMPOSE_TEXT = (REPO_ROOT / "docker-compose.yml").read_text()
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text()
START_SH = (REPO_ROOT / "scripts" / "start.sh").read_text()
BACKUP = (REPO_ROOT / "scripts" / "backup-data.sh").read_text()
EXAMPLE_CONFIG = (REPO_ROOT / "config" / "issue-notifier.example.toml").read_text()

# Feishu chat/user ids are 32 lowercase hex chars. Never embed a live value.
_FEISHU_ID = re.compile(r"\b(?:oc|ou)_[0-9a-f]{32}\b")


def test_repo_scanner_uses_combined_watcher_image():
    svc = COMPOSE["services"]["repo-scanner-mcp"]
    assert svc["build"]["dockerfile"] == "docker/issue-watcher/Dockerfile"
    assert any("/data:rw" in v for v in svc["volumes"])
    assert "127.0.0.1:8001:8001" in svc["ports"]
    assert svc["user"] == "${HOST_UID:-0}:${HOST_GID:-0}"
    assert "no-new-privileges:true" in svc["security_opt"]
    env = svc["environment"]
    assert "REPO_SCANNER_WATCH_OWNS_ISSUES=1" in env or any(
        e.startswith("REPO_SCANNER_WATCH_OWNS_ISSUES=") for e in env
    )
    assert any(e.startswith("ISSUE_WATCHER_POLL_SECONDS=") for e in env)
    assert any(e.startswith("KUMA_PUSH_URL=") for e in env)
    assert any(e.startswith("GIT_STATS_API_KEYS=") for e in env)
    # Least privilege: no main gh token, no main Feishu app secret.
    assert not any(e.startswith("GH_TOKEN=") for e in env)


def test_issue_notifier_service_wiring():
    svc = COMPOSE["services"]["issue-notifier"]
    assert svc["build"]["context"] == "./docker/issue-notifier"
    assert any("/data" in v for v in svc["volumes"])
    assert "repo-scanner-mcp" in svc["depends_on"]
    env = svc["environment"]
    assert any(e.startswith("ISSUE_NOTIFIER_MCP_URL=http://repo-scanner-mcp:8001/mcp") for e in env)
    assert any(e.startswith("ISSUE_NOTIFIER_MCP_TOKEN=") for e in env)
    assert any(e.startswith("CODER_FEISHU_APP_ID=") for e in env)
    assert not any(e.startswith("FEISHU_APP_SECRET=") for e in env)
    assert svc["user"] == "${HOST_UID:-0}:${HOST_GID:-0}"
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["deploy"]["resources"]["limits"]["memory"] == "128M"


def test_env_example_has_only_placeholders():
    assert "ISSUE_NOTIFIER_FEISHU_CHAT_ID=oc_xxxx" in ENV_EXAMPLE
    assert "ISSUE_NOTIFIER_FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/" in ENV_EXAMPLE
    assert "REPO_SCANNER_WATCH_OWNS_ISSUES=1" in ENV_EXAMPLE
    assert "KUMA_PUSH_URL_DOCKER" in ENV_EXAMPLE
    assert "KUMA_PUSH_URL_DOCKER" in START_SH
    assert not _FEISHU_ID.search(ENV_EXAMPLE)
    assert not _FEISHU_ID.search(COMPOSE_TEXT)


def test_start_sh_seeds_config_and_warns():
    assert "mkdir -p \"${HOME}/.myagentdata/issue-notifier\"" in START_SH
    assert "issue-notifier.example.toml" in START_SH
    assert "ISSUE_NOTIFIER_FEISHU_CHAT_ID" in START_SH
    assert "export HOST_UID" in START_SH
    assert "export HOST_GID" in START_SH


def test_backup_includes_notifier_state():
    assert "issue-notifier/state.sqlite" in BACKUP


def test_example_config_has_no_real_usernames_or_chat():
    assert "example-gitcode" in EXAMPLE_CONFIG
    assert not _FEISHU_ID.search(EXAMPLE_CONFIG)


def test_per_user_repo_scanner_keys_wired():
    # Four Hermes profiles consume the repo-scanner MCP.
    for name in ("hermes", "hermes-coder", "hermes-finance", "hermes-daoyuan"):
        env = COMPOSE["services"][name]["environment"]
        assert any(e.startswith("HERMES_REPO_SCANNER_KEY=") for e in env), name

    tianyi_compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.tianyi-bot.yml").read_text()
    )
    tianyi_env = tianyi_compose["services"]["openclaw-tianyi"]["environment"]
    assert any(str(v).startswith("TIANYI_BOT_REPO_SCANNER_KEY=") for v in tianyi_env.values()) or any(
        str(e).startswith("TIANYI_BOT_REPO_SCANNER_KEY=") for e in tianyi_env
    ) or "TIANYI_BOT_REPO_SCANNER_KEY" in tianyi_env

    template = (REPO_ROOT / "docker" / "tianyi-bot" / "openclaw.json.template").read_text()
    assert "Bearer ${TIANYI_BOT_REPO_SCANNER_KEY}" in template

    assert "ensure_issue_watcher_auth.py" in START_SH
    assert "ensure_hermes_repo_scanner_auth.py" in START_SH
    for var in (
        "HERMES_REPO_SCANNER_KEY",
        "TIANYI_BOT_REPO_SCANNER_KEY",
        "ISSUE_NOTIFIER_MCP_TOKEN",
        "GIT_STATS_API_KEYS",
    ):
        assert var in ENV_EXAMPLE


def test_notifier_healthcheck_uses_status_env_path():
    text = (REPO_ROOT / "docker" / "issue-notifier" / "Dockerfile").read_text()
    assert "ISSUE_NOTIFIER_STATUS" in text


def test_uptime_kuma_repo_scanner_probe_requires_401():
    text = (REPO_ROOT / "scripts" / "setup-uptime-kuma.sh").read_text()
    assert r'Repo Scanner MCP|http|http://repo-scanner-mcp:8001/mcp||[\"401\"]' in text
    # Existing monitors must converge, not stay skipped forever.
    assert "UPDATED" in text and "UPDATE monitor" in text


def test_start_sh_fails_closed_when_api_keys_missing():
    # The old guard matched the commented template line, so a failed helper
    # left the MCP unauthenticated with no warning. Anchor the check and make
    # helper failures fatal.
    assert "grep -qE '^GIT_STATS_API_KEYS=.'" in START_SH
    assert 'if ! _auth_out="$(python3 "${_auth_helper}" 2>&1)"; then' in START_SH
    assert "MCP 认证配置失败" in START_SH
    # A rotated tianyi key must recreate its container (env is frozen).
    assert "synced tianyi key" in START_SH
    assert "start-tianyi-bot.sh" in START_SH
    assert "缺少 ${_hermes_helper}" in START_SH
