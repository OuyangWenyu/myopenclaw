"""Static guards for start.sh init dirs and backup-cron defaults.

Run: uv run --with pytest pytest tests/test_ops_defaults.py -v
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
START_SH = (REPO_ROOT / "scripts" / "start.sh").read_text()
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text()
BACKUP_ENTRYPOINT = (REPO_ROOT / "docker" / "backup-cron" / "entrypoint.sh").read_text()

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
