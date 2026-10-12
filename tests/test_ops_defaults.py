"""Static guards for start.sh init dirs, backup-cron defaults, and OpenClaw exec policy.

Run: uv run --with pytest --with pyyaml pytest tests/test_ops_defaults.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

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

    def test_backup_cron_mounts_the_wal_dbs_rw(self):
        """备份容器必须能**写** WAL 库所在目录 —— 只读挂载上连 SELECT 都 CANTOPEN。

        2026-09-18~21 每天 02:00 的 data 备份连续失败就是这么来的（读者要在
        wal-index 里加锁，`:ro` 建不了/写不了 `-shm`；`-shm` 已存在也救不了）。
        2026-09-21 审计后又补两条同类窄挂载：vectors.db 与 repos.sqlite 也是 WAL。
        同 agentops 心跳的套路：只放开这些子目录，父目录保持只读。
        """
        block = compose_service_block("backup-cron")
        for sub in ("paper-queue", "tdai-memory", "repo-scanner"):
            assert f"- ${{HOME}}/.myagentdata/{sub}:/.myagentdata/{sub}:rw" in block, (
                f"{sub} 的窄 rw 挂载缺失 —— 它的 WAL 库在只读挂载上打不开"
            )
        assert "- ${HOME}/.myagentdata:/.myagentdata:ro" in block, (
            "父目录必须保持只读 —— 只放开热备需要的子目录"
        )
        # hermes 的 cron 活库（executions.db，WAL）同样要一条窄 rw 嵌套挂载
        assert "- ${HOME}/.hermes/cron:/root/.hermes/cron:rw" in block
        assert "- ${HOME}/.hermes:/root/.hermes:ro" in block, (
            "~/.hermes 父目录必须保持只读 —— 只放开 cron 这一个子目录"
        )
        # 挂载点必须落在 backup-data.sh 实际读的 DATA_ROOT 之下 —— 两边各自改动会
        # **静默**错位（挂载看着还在，热备照样 CANTOPEN）。backup-all-docker.sh 传的
        # 是 DATA_ROOT=/.myagentdata，脚本读 ${DATA_ROOT}/<库路径>。
        assert "DATA_ROOT=/.myagentdata" in BACKUP_ALL

    def test_backup_cron_mounts_the_queue_rw(self):
        """备份容器必须能**写**队列目录 —— WAL 库在只读挂载上连打开都会失败。

        2026-09-18~21 每天 02:00 的 data 备份连续失败：MCP server 是「每次调用现开
        现关」，凌晨无人调用时 `-shm` 必然不存在，而 `:ro` 挂载建不了它 → CANTOPEN，
        连普通 SELECT 都打不开（不只是 `.backup`）。同 agentops 心跳的套路：
        只放开这一个子目录，父目录保持只读。
        """
        block = compose_service_block("backup-cron")
        assert "- ${HOME}/.myagentdata/paper-queue:/.myagentdata/paper-queue:rw" in block
        assert "- ${HOME}/.myagentdata:/.myagentdata:ro" in block, (
            "父目录必须保持只读 —— 只放开 paper-queue 这一个子目录"
        )
        # 挂载点必须落在 backup-data.sh 实际读的 DATA_ROOT 之下 —— 两边各自改动会
        # **静默**错位（挂载看着还在，热备照样 CANTOPEN）。backup-all-docker.sh 传的
        # 是 DATA_ROOT=/.myagentdata，脚本读 ${DATA_ROOT}/paper-queue/queue.sqlite。
        assert "DATA_ROOT=/.myagentdata" in BACKUP_ALL


@pytest.mark.skipif(shutil.which("sqlite3") is None,
                    reason="宿主没有 sqlite3 CLI —— 本测试真跑 backup-data.sh 的热备路径")
class TestBackupHotDbs:
    """活着的 SQLite 一律热备、不裸 rsync —— 覆盖面和机制都钉住。

    2026-09-21 审计发现 ~/.myagentdata 下还有 4 个活库被裸 rsync：WAL 的
    vectors.db（4.1MB 数据只在 `-wal` 里）与 repos.sqlite；回滚模式的 freshrss
    （每 15 分钟刷新）与 transactions.sqlite（撞上写事务会拷到不可回滚的半写态）。
    修法与 paper-queue 统一：rsync 排除裸库及 sidecar → sqlite3 .backup 到 .tmp
    → 成功才 mv。WAL 库的读者要写 `-shm` ⇒ 需要窄 rw 挂载；回滚模式的库
    `-readonly` 打开即可（实测：挂 ro 上也能 .backup），不需要挂载。

    2026-10-09 语雀 MCP 本机化（issue #79）后同样收编 change_summary.db。
    """

    WAL_DBS = ("paper-queue/queue.sqlite", "tdai-memory/vectors.db",
               "repo-scanner/repos.sqlite", "tdai-memory/memories.sqlite")
    ROLLBACK_DBS = ("aisecretary/transactions.sqlite",
                    "dailyinfo/freshrss/data/users/*/db.sqlite",
                    "yuque-mcp/change_data/change_summary.db")

    @staticmethod
    def _script() -> str:
        return (REPO_ROOT / "scripts" / "backup-data.sh").read_text()

    def test_every_live_db_is_in_the_hot_list(self):
        script = self._script()
        for rel in self.WAL_DBS + self.ROLLBACK_DBS:
            assert f'"{rel}"' in script, f"热备清单缺 {rel} —— 它会退化成裸 rsync"

    def test_rsync_excludes_derive_from_the_hot_list(self):
        """排除式必须由清单推导 —— 手写第二份清单就会漂移。"""
        script = self._script()
        assert 'RSYNC_EXCLUDES+=(--exclude="${_db}*")' in script
        assert '"${RSYNC_EXCLUDES[@]}" "${DATA_ROOT}/" "${DEST}/"' in script

    def test_wal_dbs_writable_open_rollback_dbs_readonly(self):
        script = self._script()
        wal = script[script.index("HOT_DBS_RW=("):script.index("HOT_DBS_RO=(")]
        for rel in self.WAL_DBS:
            assert f'"{rel}"' in wal, f"{rel} 是 WAL 库，必须在 HOT_DBS_RW（普通打开）"
        rollback = script[script.index("HOT_DBS_RO=("):script.index("RSYNC_EXCLUDES=")]
        for rel in self.ROLLBACK_DBS:
            assert f'"{rel}"' in rollback, f"{rel} 是回滚模式，应在 HOT_DBS_RO"
        assert 'hot_copy readonly "${_rel}"' in script, (
            "回滚模式的库要用 -readonly 热备 —— 它们挂在 ro 上"
        )
        assert "sqlite3 -readonly" in script
        assert '-cmd ".timeout' in script, (
            "拿读锁要带 busy timeout —— 写事务提交的瞬间硬失败会让整晚备份挂掉"
        )

    def test_hot_copy_writes_then_moves(self):
        script = self._script()
        assert ".tmp" in script and "mv -f" in script, (
            "热备必须先写 .tmp、成功才 mv（失败不留 0 字节假库）"
        )

    def test_missing_sqlite3_fails_loud(self):
        script = self._script()
        assert "❌ sqlite3 未安装" in script and "exit 1" in script, (
            "缺 sqlite3 时必须失败退出（不 cp 兜底）—— 否则备份静默不可用"
        )


class TestOpenclawAgentDbHotCopy:
    """openclaw 的 146MB 会话库（回滚模式、活库）必须热备 —— 裸 rsync 会拷到半写态。

    2026-09-21 审计：它每晚上云 146MB（30 天 ≈ 4.4GB 稳态），且撞上写事务时得到的
    副本没有 journal 可回滚。回滚模式的库挂在 ro 上也能 `-readonly` 热备（实测），
    不需要新增挂载。
    """

    SCRIPT = REPO_ROOT / "openclaw" / "scripts" / "backup.sh"
    TIMESTAMP = "2026-09-21_120000"

    def test_excluded_from_raw_rsync(self):
        script = self.SCRIPT.read_text()
        assert '--exclude="*/agent/openclaw-agent.sqlite"' in script, (
            "146MB 的会话库不能再被裸 rsync —— 要么热备、要么明确不备"
        )

    def test_hot_copied_readonly_write_then_move(self):
        script = self.SCRIPT.read_text()
        assert "sqlite3 -readonly" in script
        assert 'tmp="${DEST}/${rel_actual}.tmp"' in script, "先写 .tmp，成功才 mv"
        assert "mv -f" in script

    def test_memory_dbs_use_the_same_hot_copy(self):
        """memory/main.sqlite 与 虾酱 memory-tdai 走同一套热备（不能退回裸拷）。"""
        script = self.SCRIPT.read_text()
        assert 'hot_copy readonly "memory/main.sqlite"' in script
        assert 'hot_copy readonly "memory-tdai/memories.sqlite"' in script

    def test_no_cp_fallback(self):
        """CLAUDE.md 明文：热备不得有 cp 兜底 —— 拷到半写状态且静默降级。"""
        script = self.SCRIPT.read_text()
        assert 'cp "' not in script, (
            "活库备份不得回退到 cp —— 拷到的是半写副本，缺 sqlite3 时应当直接失败"
        )

    def test_behavioral_copy_is_valid_and_journal_free(self, tmp_path):
        agents = tmp_path / ".openclaw" / "agents" / "main" / "agent"
        agents.mkdir(parents=True)
        db = agents / "openclaw-agent.sqlite"
        subprocess.run(["sqlite3", str(db), "CREATE TABLE t (x); INSERT INTO t VALUES (1);"],
                       check=True, capture_output=True)
        (agents / "models.json").write_text("{}")     # 证明 rsync 部分照常工作
        env = {**os.environ, "HOME": str(tmp_path), "BACKUP_ROOT": str(tmp_path / "bk"),
               "BACKUP_SKIP_PRUNE": "1"}
        proc = subprocess.run(["bash", str(self.SCRIPT), self.TIMESTAMP],
                              env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        snap = tmp_path / "bk" / "openclaw" / self.TIMESTAMP
        copy = snap / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
        rows = subprocess.run(["sqlite3", str(copy), "SELECT count(*) FROM t;"],
                              capture_output=True, text=True, check=True)
        assert rows.stdout.strip() == "1", "热备副本读不出数据"
        assert (snap / "agents" / "main" / "agent" / "models.json").exists()
        assert not list(snap.rglob("*-journal")), "热备的 -journal 残影不该进快照"


@pytest.mark.skipif(shutil.which("sqlite3") is None,
                    reason="宿主没有 sqlite3 CLI —— 本测试真跑 backup-data.sh 的热备路径")
class TestHermesCronDbHotCopy:
    """hermes 的 cron 活库同样热备：WAL 的 executions.db + 回滚的 notepad.db。

    2026-09-21 审计收尾：executions.db 是 WAL —— 读者要写 `-shm`，靠 compose 给
    `~/.hermes/cron` 的窄 rw 嵌套挂载；notepad.db 回滚模式，`-readonly` 即可。
    """

    SCRIPT = REPO_ROOT / "hermes" / "scripts" / "backup.sh"
    TIMESTAMP = "2026-09-21_120000"

    def test_excluded_from_raw_rsync(self):
        script = self.SCRIPT.read_text()
        assert '--exclude="executions.db*"' in script
        assert '--exclude="notepad.db*"' in script

    def test_open_modes_and_write_then_move(self):
        script = self.SCRIPT.read_text()
        assert 'hot_copy rw "cron/executions.db"' in script, "WAL 库要普通打开（rw 挂载）"
        assert 'hot_copy readonly "cron/notepad.db"' in script, "回滚模式用 -readonly"
        assert '-cmd ".timeout' in script
        assert 'tmp="${DEST}/${rel}.tmp"' in script and "mv -f" in script

    def test_behavioral_copy_is_valid(self, tmp_path):
        cron = tmp_path / ".hermes" / "cron"
        cron.mkdir(parents=True)
        wal = tmp_path / "_seed_wal.sqlite"
        subprocess.run(["sqlite3", str(wal),
                        "PRAGMA journal_mode=WAL; CREATE TABLE t (x); INSERT INTO t VALUES (1);"],
                       check=True, capture_output=True)
        (cron / "executions.db").write_bytes(wal.read_bytes())
        plain = tmp_path / "_seed_plain.sqlite"
        subprocess.run(["sqlite3", str(plain), "CREATE TABLE t (x); INSERT INTO t VALUES (1);"],
                       check=True, capture_output=True)
        (cron / "notepad.db").write_bytes(plain.read_bytes())
        env = {**os.environ, "HOME": str(tmp_path),
               "BACKUP_ROOT": str(tmp_path / "bk"), "BACKUP_SKIP_PRUNE": "1"}
        proc = subprocess.run(["bash", str(self.SCRIPT), self.TIMESTAMP],
                              env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        snap = tmp_path / "bk" / "hermes" / self.TIMESTAMP
        for rel in ("cron/executions.db", "cron/notepad.db"):
            copy = snap / rel
            assert copy.stat().st_size > 0, f"{rel} 热备产物是 0 字节"
            rows = subprocess.run(["sqlite3", str(copy), "SELECT count(*) FROM t;"],
                                  capture_output=True, text=True, check=True)
            assert rows.stdout.strip() == "1", f"{rel} 的热备副本读不出数据"
        assert not (snap / "cron" / "executions.db-wal").exists(), "sidecar 不该进快照"


@pytest.mark.skipif(shutil.which("sqlite3") is None,
                    reason="宿主没有 sqlite3 CLI —— 本测试真跑 backup-data.sh 的热备路径")
class TestBackupDataHotCopy:
    """热备必须「先写 .tmp、成功才 mv」—— 失败时不能在快照里留下 0 字节的假库。

    真跑一次 scripts/backup-data.sh，不做字符串匹配 —— 要验的是行为。
    2026-09-18~21 的四个云盘快照里 `paper-queue/queue.sqlite` 都是 0 字节：`.backup`
    会**先把目标文件建出来**再去读源，源读不了时假文件已经落下了（线上触发条件是
    只读挂载上的 WAL 库 CANTOPEN，宿主复现不了，这里用「源不是数据库」触发同一
    性质 —— 实测旧脚本在此触发下确实留下 0 字节文件）。0 字节比缺文件更阴险：
    恢复方会把它读成「清单是空的」。
    """

    TIMESTAMP = "2026-09-21_000000"

    def _run(self, tmp_path, dbs: dict):
        for rel, data in dbs.items():
            src = tmp_path / "src" / rel
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(data)
        env = {**os.environ,
               "DATA_ROOT": str(tmp_path / "src"),
               "BACKUP_ROOT": str(tmp_path / "bk"),
               "BACKUP_SKIP_PRUNE": "1"}
        proc = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "backup-data.sh"), self.TIMESTAMP],
            env=env, capture_output=True, text=True)
        return proc, tmp_path / "bk" / "data" / self.TIMESTAMP

    @staticmethod
    def _wal_db_bytes(tmp_path) -> bytes:
        """生产同款：建好、写入、关掉 —— 落盘就是 WAL 模式且 -wal/-shm 已清。"""
        seed = tmp_path / "_seed.sqlite"
        subprocess.run(
            ["sqlite3", str(seed),
             "PRAGMA journal_mode=WAL; CREATE TABLE t (x); INSERT INTO t VALUES (1);"],
            check=True, capture_output=True)
        data = seed.read_bytes()
        seed.unlink()
        return data

    @staticmethod
    def _plain_db_bytes(tmp_path, name="_plain.sqlite") -> bytes:
        """回滚模式（默认 journal）的小库 —— 走 -readonly 那条路。"""
        seed = tmp_path / name
        subprocess.run(["sqlite3", str(seed),
                        "CREATE TABLE t (x); INSERT INTO t VALUES (1);"],
                       check=True, capture_output=True)
        data = seed.read_bytes()
        seed.unlink()
        return data

    def test_success_path_writes_a_valid_copy_and_leaves_no_tmp(self, tmp_path):
        proc, snap = self._run(tmp_path,
                               {"paper-queue/queue.sqlite": self._wal_db_bytes(tmp_path)})
        assert proc.returncode == 0, proc.stderr
        copy = snap / "paper-queue" / "queue.sqlite"
        assert copy.stat().st_size > 0, "热备产物是 0 字节"
        rows = subprocess.run(["sqlite3", str(copy), "SELECT count(*) FROM t;"],
                              capture_output=True, text=True, check=True)
        assert rows.stdout.strip() == "1", "热备副本读不出原始数据"
        assert not (snap / "paper-queue" / "queue.sqlite.tmp").exists(), "热备的 .tmp 没被清掉"

    def test_failure_path_leaves_no_lying_empty_db(self, tmp_path):
        proc, snap = self._run(tmp_path, {"paper-queue/queue.sqlite": b"not a database"})
        assert proc.returncode != 0, "源打不开时脚本竟然成功返回"
        assert not (snap / "paper-queue" / "queue.sqlite").exists(), (
            "热备失败却在快照里留下了 queue.sqlite —— 0 字节是假库，恢复方会读成"
            "「清单是空的」，比缺文件更阴险"
        )
        assert not (snap / "paper-queue" / "queue.sqlite.tmp").exists()

    def test_every_configured_db_is_hot_copied_and_sidecars_stay_out(self, tmp_path):
        """WAL 与回滚两种打开方式都要过；sidecar 残影不得进快照。

        ⚠️ 垃圾 sidecar 要按库的模式配对放：WAL 库不读 `-journal`、回滚库不读
        `-wal`/`-shm`，放反了会让 SQLite 在打开源库时试图处理它 —— 回滚库 + 真
        `-journal` 会以「attempt to write a readonly database」响亮失败（实测踩过）。
        """
        proc, snap = self._run(tmp_path, {
            "paper-queue/queue.sqlite": self._wal_db_bytes(tmp_path),   # WAL → 普通打开
            "paper-queue/queue.sqlite-journal": b"sidecar junk",        # 不得进快照
            "aisecretary/transactions.sqlite": self._plain_db_bytes(tmp_path),  # 回滚 → -readonly
            "aisecretary/transactions.sqlite-wal": b"sidecar junk",     # 不得进快照
            "aisecretary/transactions.sqlite-shm": b"sidecar junk",
        })
        assert proc.returncode == 0, proc.stderr
        # ⚠️ 文件清单必须先于「打开副本」取好：SQLite 连接会在打开时顺手 unlink
        # 旁边的陈旧 `-journal`（非 hot journal 的正常清理），先读库再查清单会让
        # 这条断言恒真 —— 实测踩过。
        snap_files = {str(p.relative_to(snap)) for p in snap.rglob("*") if p.is_file()}
        for rel in ("paper-queue/queue.sqlite", "aisecretary/transactions.sqlite"):
            copy = snap / rel
            assert copy.stat().st_size > 0, f"{rel} 热备产物是 0 字节"
            rows = subprocess.run(["sqlite3", str(copy), "SELECT count(*) FROM t;"],
                                  capture_output=True, text=True, check=True)
            assert rows.stdout.strip() == "1", f"{rel} 的热备副本读不出数据"
        for leftover in ("paper-queue/queue.sqlite-journal",
                         "aisecretary/transactions.sqlite-wal",
                         "aisecretary/transactions.sqlite-shm"):
            assert leftover not in snap_files, (
                f"sidecar 残影 {leftover} 进了快照 —— 它是某一瞬间的中间态"
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
        """rw 只以「窄子目录挂载」的形式存在（agentops / paper-queue 各一条）；
        整个 ~/.myagentdata 必须仍是只读。

        心跳不能放云盘目录：实测云盘对宿主进程的可见性按进程上下文分裂 ——
        launchd 能 readdir 云盘根目录却读不了里面的文件（EPERM），交互式 shell
        恰好反过来。放云盘里的信号总有一类宿主进程读不到。
        """
        block = compose_service_block("backup-cron")
        assert "- ${HOME}/.myagentdata/agentops:/.agentops:rw" in block
        assert "- ${HOME}/.myagentdata:/.myagentdata:ro" in block, (
            "整个 ~/.myagentdata 必须保持只读 —— 只允许子目录级的窄 rw 例外"
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
