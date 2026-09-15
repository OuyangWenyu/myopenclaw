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
TIANYI_TEMPLATE = REPO_ROOT / "docker" / "tianyi-bot" / "openclaw.json.template"

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
    """exec 策略不得比线上更松 —— 模板是新建部署的唯一来源。

    线上 `~/.openclaw/openclaw.json` 早已被手工加固，2.0 的 `doctor --fix` 又把它归一成
    canonical 形式 `{"mode": "ask"}`（等价于旧键 `security=allowlist` + `ask=on-miss`）。
    而主模板长期停留在 `full` / `off`；tianyi 模板则**连 exec 段都没有** —— 而 OpenClaw 对
    coding profile 的内置默认恰恰是最松的一档（镜像内 `DEFAULT_SECURITY = "full"`、
    `DEFAULT_ASK = "off"`，实测于 `dist/exec-approvals-*.js`）。tianyi 又同时是
    coding profile + 无 agent 级 tools.allow 收窄 + `sandbox.mode: "off"` +
    飞书 `allowFrom: ["*"]` / `dmPolicy: "open"` —— 等于对新部署默认放开无确认的全量 shell，
    而那个容器 env 里有 GH_TOKEN 与 provider key。

    模板与新建机器之间没有任何其它校验环节（`start.sh` 只做 `cp`，`config validate`
    也不检查 exec 策略），所以只能靠这条守卫钉住。

    **zhixun 不需要 exec 段**（故不在此测试内）：它是 `messaging` profile，`exec` 工具
    不属于该 profile；且 agent 级 `tools.allow` 只有 `["bundle-mcp"]` —— exec 根本不可达。
    """

    @staticmethod
    def _exec_of(path):
        return json.loads(path.read_text())["tools"].get("exec")

    def _assert_hardened(self, exec_cfg, label):
        assert exec_cfg is not None, (
            f"{label} 没有 exec 段 —— OpenClaw 对 coding profile 的内置默认是 "
            "最松的一档，等于默认放开无确认的全量 shell"
        )
        assert exec_cfg.get("mode") == "ask", (
            f"{label} exec.mode 是 {exec_cfg.get('mode')!r}，应为 'ask'。"
            "该档位等价于旧键 security=allowlist + ask=on-miss（2.0 起 doctor 会"
            "把旧键归一成 mode）；更松的取值意味着新部署默认放开无确认的全量 shell"
        )

    def test_main_template_exec_matches_hardened_value(self):
        self._assert_hardened(self._exec_of(OPENCLAW_EXAMPLE), "主模板 openclaw.json.example")

    def test_tianyi_template_exec_matches_hardened_value(self):
        self._assert_hardened(self._exec_of(TIANYI_TEMPLATE), "tianyi 模板")
