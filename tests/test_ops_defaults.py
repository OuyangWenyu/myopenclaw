"""Static guards for start.sh init dirs, backup-cron defaults, and OpenClaw exec policy.

Run: uv run --with pytest --with pyyaml pytest tests/test_ops_defaults.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
START_SH = (REPO_ROOT / "scripts" / "start.sh").read_text()
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text()
BACKUP_ENTRYPOINT = (REPO_ROOT / "docker" / "backup-cron" / "entrypoint.sh").read_text()
OPENCLAW_EXAMPLE = REPO_ROOT / "openclaw" / "config" / "openclaw.json.example"

# Daily 02:00 (not weekly Sunday). Aligns with AgentOps 24h stale threshold.
DAILY_CRON = "0 2 * * *"
WEEKLY_CRON = "0 2 * * 0"


class TestAgentopsDirInit:
    """start.sh must create ~/.myagentdata/agentops so the volume has the subdir."""

    def test_mkdir_agentops(self):
        assert 'mkdir -p "${HOME}/.myagentdata/agentops"' in START_SH

    def test_warns_when_launchd_missing_on_darwin(self):
        assert "install-collect-agentops.sh" in START_SH
        assert "ai.myopenclaw.collect-agentops.plist" in START_SH


class TestDailyBackupDefault:
    """Backup cron default is daily so AgentOps 24h freshness is not a false alarm."""

    def test_env_example_daily(self):
        assert f"BACKUP_CRON={DAILY_CRON}" in ENV_EXAMPLE
        assert f"BACKUP_CRON={WEEKLY_CRON}" not in ENV_EXAMPLE

    def test_compose_default_daily(self):
        assert f"BACKUP_CRON=${{BACKUP_CRON:-{DAILY_CRON}}}" in COMPOSE

    def test_entrypoint_default_daily(self):
        assert f"BACKUP_CRON:-{DAILY_CRON}" in BACKUP_ENTRYPOINT
        assert WEEKLY_CRON not in BACKUP_ENTRYPOINT


class TestOpenclawExecDefault:
    """OpenClaw 模板的 exec 策略不得比线上更松。

    线上 `~/.openclaw/openclaw.json` 早已被手工加固为 `allowlist` / `on-miss`，而模板
    长期停留在 `full` / `off` —— 于是**新部署照模板落地就会默认拿到无确认的全量 shell
    执行**，而那个容器 env 里有 3 个 provider key + GH token。模板与新建机器之间没有
    任何其它校验环节（`start.sh` 只做 `cp`），所以这条只能靠守卫钉住。
    """

    def test_exec_security_matches_hardened_value(self):
        exec_cfg = json.loads(OPENCLAW_EXAMPLE.read_text())["tools"]["exec"]
        assert exec_cfg["security"] == "allowlist", (
            f"模板 exec.security 是 {exec_cfg['security']!r}，"
            "而线上加固值是 'allowlist' —— full 意味着新部署默认放开全量 shell"
        )
        assert exec_cfg["ask"] == "on-miss", (
            f"模板 exec.ask 是 {exec_cfg['ask']!r}，不得默认跳过确认"
        )
