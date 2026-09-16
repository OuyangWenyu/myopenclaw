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
BACKUP_DOCKERFILE = (REPO_ROOT / "docker" / "backup-cron" / "Dockerfile").read_text()
BACKUP_ALL = (REPO_ROOT / "scripts" / "backup-all-docker.sh").read_text()
OPENCLAW_EXAMPLE = REPO_ROOT / "openclaw" / "config" / "openclaw.json.example"
TIANYI_TEMPLATE = REPO_ROOT / "docker" / "tianyi-bot" / "openclaw.json.template"


def compose_service_block(name: str) -> str:
    """取 compose 里某个服务的 YAML 块（服务位于 2 空格缩进，键为 4 空格）。"""
    lines = COMPOSE.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"  {name}:"))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("  ") and not lines[i].startswith("    ")),
               len(lines))
    return "\n".join(lines[start:end])

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


class TestPaperQueueWiring:
    """论文清单的装机链路：模板 / start.sh / compose / 备份 四处必须同时到位。

    这四处里任何一处漏了都不会报错 —— 只会安静地不工作（新部署没有清单，
    或者队列根本没进备份）。所以逐处钉住。
    """

    def test_start_sh_creates_the_mount_point_before_compose_up(self):
        """挂载点必须先于 compose up 存在，否则 Docker 以 root 建目录，容器写不进去。"""
        assert 'mkdir -p "${HOME}/.myagentdata/paper-queue"' in START_SH
        assert START_SH.index('mkdir -p "${HOME}/.myagentdata/paper-queue"') \
            < START_SH.index("docker compose up -d ${BUILD_FLAG}")

    def test_start_sh_installs_skill_and_plugin(self):
        assert "install_paper_queue()" in START_SH
        assert "openclaw/skills/paper-queue" in START_SH
        assert "openclaw/plugins/paper-queue-actor" in START_SH

    def test_start_sh_calls_the_idempotent_ensure_script(self):
        assert "ensure_openclaw_paper_queue.py" in START_SH

    def test_start_sh_restarts_gateway_when_config_or_code_changed(self):
        """mcp.servers / plugins.load 不热加载，**装进去的代码换了也一样** ——
        MCP server 是网关按需拉起的子进程，网关不重启跑的还是老代码。"""
        assert "OPENCLAW_RESTART_NEEDED" in START_SH
        assert "docker compose restart openclaw-gateway" in START_SH
        # 装文件时要比对内容，不能盲拷 —— 否则"改了仓库源码再跑 start.sh"是静默 no-op
        assert "diff -rq" in START_SH

    def test_start_sh_skips_the_redundant_restart(self):
        """重建过的容器启动时已读到新配置，不需要再重启。

        这不是洁癖：2026-09-16 实测，OpenClaw 2026.9.1 会在 state 目录上跑启动迁移，
        多出来的那次重启会把迁移打断、下次从头再来，表现是网关**看起来卡死** ——
        不绑端口、不报错、docker logs 只有几行，静置几分钟才恢复。
        """
        assert "_openclaw_started_before" in START_SH
        assert "State.StartedAt" in START_SH

    @staticmethod
    def _service_block(name: str) -> str:
        return compose_service_block(name)

    def test_compose_keeps_the_queue_out_of_the_gateway(self):
        """队列目录**不能**挂进 openclaw-gateway。

        2026-09-16 实测：虾酱的 `read`/`write` 工具够得着那个目录（只有 `exec` 被
        allowlist 挡住）。挂在一起时，被提示注入的模型可以绕开一切校验直接改库。
        现在只有 paper-queue-mcp 挂它，虾酱只能走 MCP 接口。
        """
        gateway = self._service_block("openclaw-gateway")
        # 只看挂载行 —— 注释里解释"为什么不挂"会自然地提到它，别被自己的说明绊倒
        gateway_mounts = [line.strip() for line in gateway.splitlines()
                          if line.strip().startswith("- ")]
        assert not any("myagentdata/paper-queue" in line for line in gateway_mounts), (
            f"openclaw-gateway 又挂上了队列目录（{gateway_mounts}）—— "
            "虾酱能直接改库，签名形同虚设"
        )
        sidecar = self._service_block("paper-queue-mcp")
        assert "${HOME}/.myagentdata/paper-queue:/data" in sidecar
        assert "PAPER_QUEUE_ACTOR_SECRET" in sidecar, (
            "签名密钥必须注入 sidecar，否则它无法验签 —— 每次写入都会被拒"
        )

    def test_template_registers_mcp_server(self):
        mcp = json.loads(OPENCLAW_EXAMPLE.read_text()).get("mcp", {}).get("servers", {})
        assert "paper-queue" in mcp, "模板是新部署的唯一来源 —— 漏了它新机器就没有清单"
        assert mcp["paper-queue"]["url"].startswith("http://paper-queue-mcp:"), (
            "服务在独立容器里，模板必须用 URL 形态（stdio 形态意味着又挂回了网关）"
        )

    def test_template_disables_paper_fetch(self):
        """「只记不下」由机器强制：两个 skill 的触发语（都是"下载论文"）会互相抢。"""
        skills = json.loads(OPENCLAW_EXAMPLE.read_text()).get("skills", {}).get("entries", {})
        assert skills.get("paper-fetch") == {"enabled": False}

    def test_backup_hot_copies_the_queue_db(self):
        """WAL 下直接 rsync 会拷到撕裂副本 —— 必须 .backup，且缺 sqlite3 要失败退出。"""
        backup = (REPO_ROOT / "scripts" / "backup-data.sh").read_text()
        assert "--exclude='paper-queue/queue.sqlite*'" in backup
        assert 'sqlite3 "${PQ_SQLITE_SRC}" ".backup' in backup
        block = backup[backup.index("PQ_SQLITE_SRC"):]
        assert "exit 1" in block, (
            "缺 sqlite3 时必须失败退出（不 cp 兜底）—— 否则备份静默不可用"
        )


class TestBackupCronTimezone:
    """compose 声明了 TZ，而 Alpine 不带 zoneinfo —— 缺 tzdata 时 musl 会**静默**
    退回 UTC，声明形同虚设。

    实测（2026-09-16）：claude-code / uptime-kuma 都吃到了 Asia/Shanghai，只有
    backup-cron 跑在 UTC。后果两层，都不报错：
      ① crond 的 `0 2 * * *` 在 02:00 UTC（= 10:00 北京）触发，不是 02:00 北京；
      ② 快照目录名成了 UTC 时间戳。备份探测按**目录名**解析时间戳，宿主按本地时区
         读就差 8 小时 —— 足以把「21h 前」读成「29h 前」，越过 24h 阈值误报过期。
    """

    def test_compose_declares_tz_for_backup_cron(self):
        """这条是上面那条的前提：没有 TZ 声明就不需要 tzdata。"""
        assert "TZ=" in compose_service_block("backup-cron")

    def test_image_installs_tzdata(self):
        assert "tzdata" in BACKUP_DOCKERFILE, (
            "backup-cron 声明了 TZ 但 Alpine 镜像没装 tzdata —— musl 静默退回 UTC"
        )


class TestBackupHeartbeatWiring:
    """心跳是备份新鲜度的首选来源，链路横跨三个文件。

    容器写 /.agentops/backup-heartbeat.json，宿主读
    ~/.myagentdata/agentops/backup-heartbeat.json，两者靠 compose 挂载对齐。
    三处任意一处改了名字，链路就**静默**断掉 —— 探测会退回快照目录名（照样能出结果），
    于是没人会发现心跳早就没了。所以逐处钉住。
    """

    def test_compose_exposes_a_narrow_rw_heartbeat_dir(self):
        """只放开 agentops 这一个子目录；整个 ~/.myagentdata 必须仍是只读。

        心跳不能放云盘目录：实测云盘对宿主进程的可见性按进程上下文分裂 ——
        launchd 能 readdir 云盘根目录却读不了里面的文件（EPERM），交互式 shell
        恰好反过来。放云盘里的信号总有一类宿主进程读不到。
        """
        block = compose_service_block("backup-cron")
        assert "- ${HOME}/.myagentdata/agentops:/.agentops:rw" in block
        assert "- ${HOME}/.myagentdata:/.myagentdata:ro" in block, (
            "整个 ~/.myagentdata 必须保持只读 —— 心跳只需要 agentops 这一个子目录可写"
        )

    def test_mount_point_exists_before_compose_up(self):
        """挂载点必须先于 compose up 存在，否则 Docker 以 root 建目录，容器写不进去
        —— 心跳会静默写失败（且按设计不报错），新鲜度探测就退回快照目录名。"""
        mount_point = 'mkdir -p "${HOME}/.myagentdata/agentops"'
        assert mount_point in START_SH
        assert START_SH.index(mount_point) < START_SH.index("docker compose up -d ${BUILD_FLAG}")

    def test_container_side_path(self):
        assert "${AGENTOPS_HEARTBEAT_DIR:-/.agentops}" in BACKUP_ALL
        assert "backup-heartbeat.json" in BACKUP_ALL

    def test_host_side_path_matches_the_mount(self):
        collector = (REPO_ROOT / "scripts" / "collect_agentops.py").read_text()
        assert "~/.myagentdata/agentops/backup-heartbeat.json" in collector, (
            "宿主侧默认心跳路径必须与 compose 挂载（host ~/.myagentdata/agentops "
            "→ 容器 /.agentops）对齐"
        )

    def test_heartbeat_never_fails_the_backup(self):
        """心跳是尽力而为：写不了一律只告警，绝不影响备份本身。"""
        writer = BACKUP_ALL[BACKUP_ALL.index("write_heartbeat()"):]
        writer = writer[:writer.index("\nmkdir -p ")]
        assert writer.count("return 0") >= 3, (
            "心跳的三条失败路径（目录不存在 / 写入失败 / 落盘失败）都必须 return 0 "
            "—— set -e 下任何一条漏了都会让整次备份非零退出"
        )
        assert "exit 1" not in writer
