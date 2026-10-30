"""server-monitoring MCP 接入守卫：CA / 模板 / 渲染器 / compose / entrypoint / env 模板 / node 语义七处联动。

拓扑（详见 docs/server-monitoring-mcp.md）：天一容器 → HTTPS + Bearer → 校园域名入口
（公网证书）或内网直连（自签 CA）。链路里**每一处漏了都不报错，只会安静地不工作**：

- CA 文件缺失：node 实测**静默忽略** ``NODE_EXTRA_CA_CERTS``（空值、缺失、坏文件都不告警）
  —— 症状是启用态下所有监控工具 TLS 报错，而启动日志干干净净。所以容器里有 entrypoint 显式守卫。
- 模板 header 变量名与 compose 注入名不一致：``${…}`` 运行时展开成空串 → 全程 401。
- URL 占位符在渲染器里漏替换：渲染出的是字面量 ``__SERVER_MONITORING_MCP_URL__``。
- URL 非空而 token 空：服务端 401 + 渲染器抛错 + start 脚本预检，三道都要在。

Run: uv run --with pytest --with pyyaml pytest tests/test_server_monitoring_mcp.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

TEMPLATE_PATH = REPO_ROOT / "docker" / "tianyi-bot" / "openclaw.json.template"
RENDERER = REPO_ROOT / "docker" / "tianyi-bot" / "render-config.mjs"
ENTRYPOINT = (REPO_ROOT / "docker" / "tianyi-bot" / "entrypoint.sh").read_text()
COMPOSE = (REPO_ROOT / "docker-compose.tianyi-bot.yml").read_text()
ENV_EXAMPLE = (REPO_ROOT / ".env.tianyi-bot.example").read_text()
START_SH = (REPO_ROOT / "scripts" / "start-tianyi-bot.sh").read_text()
AGENTS_MD = (REPO_ROOT / "openclaw-tianyi" / "workspace" / "AGENTS.md").read_text()
MONITORING_DOC = (REPO_ROOT / "docs" / "server-monitoring-mcp.md").read_text()
README_MD = (REPO_ROOT / "README.md").read_text()
CLAUDE_MD = (REPO_ROOT / "CLAUDE.md").read_text()

CA_HOST_PATH = REPO_ROOT / "docker" / "tianyi-bot" / "ca" / "server-monitoring-ca.pem"
CA_CONTAINER_PATH = "/opt/tianyi-bot/ca/server-monitoring-ca.pem"

URL_VAR = "TIANYI_BOT_SERVER_MONITORING_MCP_URL"
TOKEN_VAR = "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN"
NODE = shutil.which("node")


def _live_monitoring_host() -> str | None:
    """从 gitignored 的 `.env.tianyi-bot` 读取真实入口主机名（用于隐私守卫）。

    公开仓库里不写真实域名/IP；测试在本地读 live 配置，断言这些值没有
    被误写进公开文档。CI/新机器没有 `.env.tianyi-bot` 时跳过该守卫。
    """
    env_file = REPO_ROOT / ".env.tianyi-bot"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("TIANYI_BOT_SERVER_MONITORING_MCP_URL="):
            value = line.split("=", 1)[1].strip()
            return urlparse(value).hostname or None
    return None

# 渲染器的必需环境变量（与 tests/test-bot-rendered-config.sh 的 tianyi spec 同源）
BASE_ENV = {
    "TIANYI_BOT_FEISHU_APP_ID": "dummy_id",
    "TIANYI_BOT_FEISHU_APP_SECRET": "dummy_secret",
    "TIANYI_BOT_MODEL_API_KEY": "dummy_key",
    "TIANYI_BOT_MODEL_ID": "deepseek-flash",
    "TIANYI_BOT_MODEL_BASE_URL": "https://api.deepseek.com",
}


def render(tmp_path: Path, extra_env: dict[str, str]) -> tuple[subprocess.CompletedProcess, Path]:
    """跑一次渲染器。环境从零构造（不带宿主机上可能残留的 TIANYI_BOT_*）。"""
    out = tmp_path / "openclaw.json"
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")}
    env.update(BASE_ENV)
    env.update(extra_env)
    proc = subprocess.run(
        [NODE, str(RENDERER), str(TEMPLATE_PATH), str(out)],
        capture_output=True, text=True, env=env,
    )
    return proc, out


class TestCaFile:
    """CA 是公钥材料，随仓库提交（公开仓亦然：只能验证签名、不能制造签名）；私钥当然绝不进仓库。"""

    def test_ca_file_exists_and_parses(self):
        assert CA_HOST_PATH.is_file(), (
            "缺少 docker/tianyi-bot/ca/server-monitoring-ca.pem —— "
            "启用监控时 entrypoint 会直接拒绝启动（这是刻意的：比静默 TLS 失败好）"
        )
        pem = CA_HOST_PATH.read_text()
        assert "PRIVATE KEY" not in pem, "CA 私钥绝不能进仓库 —— 只提交证书（公钥材料）"
        blocks = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
        assert blocks, "不是 PEM 证书"
        assert ssl.PEM_cert_to_DER_cert(blocks[0]), "PEM 解析失败"

    def test_compose_ca_path_maps_to_repo_file(self):
        """compose 里 NODE_EXTRA_CA_CERTS 指的容器内路径，必须能经挂载对回仓库文件。

        对不上的表现：启用态下 node 静默忽略缺失的 CA（实测不告警）→ 一切照跑、
        除监控外全正常，只有监控 TLS 报错。
        """
        assert f"${{{URL_VAR}:+{CA_CONTAINER_PATH}}}" in COMPOSE, (
            "NODE_EXTRA_CA_CERTS 必须用 ${URL:+路径} 形式：URL 留空时不信任内网 CA"
        )
        assert "./docker/tianyi-bot:/opt/tianyi-bot:ro" in COMPOSE, (
            "CA 依赖 ./docker/tianyi-bot 挂载 —— 挂载没了上面那个路径悬空"
        )
        # 容器内路径 → 仓库路径
        assert CA_CONTAINER_PATH.startswith("/opt/tianyi-bot/")
        mapped = REPO_ROOT / "docker" / "tianyi-bot" / CA_CONTAINER_PATH[len("/opt/tianyi-bot/"):]
        assert mapped == CA_HOST_PATH


class TestTemplateEntry:
    def _entry(self) -> dict:
        template = json.loads(TEMPLATE_PATH.read_text())
        return template["mcp"]["servers"]["server-monitoring"]

    def test_entry_shape(self):
        entry = self._entry()
        assert entry["url"] == "__SERVER_MONITORING_MCP_URL__"
        assert entry["transport"] == "streamable-http"  # 服务端是 streamable HTTP（stateless）
        assert entry["headers"]["authorization"] == "Bearer ${MCP_SERVER_MONITORING_TOKEN}", (
            "header 必须是运行时可展开的字面量 —— 渲染器只替换 __LIKE_THIS__，"
            "${VAR} 由 OpenClaw 从容器进程环境展开（与 yuque-mcp 同款）"
        )
        assert entry["supportsParallelToolCalls"] is True

    def test_timeouts_match_repo_scanner(self):
        """超时与 repo-scanner 对齐 —— 两个 MCP 走同一个网关进程，别各写一套。"""
        template = json.loads(TEMPLATE_PATH.read_text())
        servers = template["mcp"]["servers"]
        for key in ("connectionTimeoutMs", "requestTimeoutMs"):
            assert servers["server-monitoring"][key] == servers["repo-scanner"][key]


@pytest.mark.skipif(NODE is None, reason="宿主机无 node，跳过渲染器行为测试")
class TestRendererStates:
    """三种状态：URL 空 → 摘条目；URL+token → 条目在且 header 不展开；URL 无 token → 抛错。"""

    def test_url_empty_drops_entry(self, tmp_path):
        proc, out = render(tmp_path, {
            "TIANYI_BOT_SERVER_MONITORING_MCP_URL": "",
            "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN": "",
        })
        assert proc.returncode == 0, proc.stderr
        raw = out.read_text()
        config = json.loads(raw)
        assert "server-monitoring" not in config["mcp"]["servers"]
        assert "${MCP_SERVER_MONITORING_TOKEN}" not in raw, "关闭态不得残留任何痕迹"

    def test_url_set_keeps_entry_and_never_renders_token(self, tmp_path):
        token = "tok-" + "a" * 40
        proc, out = render(tmp_path, {
            "TIANYI_BOT_SERVER_MONITORING_MCP_URL": "https://mcp.example.com:8765/mcp",
            "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN": token,
        })
        assert proc.returncode == 0, proc.stderr
        raw = out.read_text()
        entry = json.loads(raw)["mcp"]["servers"]["server-monitoring"]
        assert entry["url"] == "https://mcp.example.com:8765/mcp"
        # 字节级不变 = 渲染器没有展开 ${VAR}（展开发生在 OpenClaw 运行时）
        assert entry["headers"]["authorization"] == "Bearer ${MCP_SERVER_MONITORING_TOKEN}"
        assert token not in raw, "token 绝不能出现在渲染产物里（配置文件会被备份、进快照）"
        assert "__SERVER_MONITORING_MCP_URL__" not in raw

    def test_url_without_token_fails_loud(self, tmp_path):
        """URL 非空而 token 空 → 非零退出。静默降级成"每次 401"才是最难查的。"""
        proc, out = render(tmp_path, {
            "TIANYI_BOT_SERVER_MONITORING_MCP_URL": "https://mcp.example.com:8765/mcp",
            "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN": "",
        })
        assert proc.returncode != 0
        assert "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN" in proc.stderr


class TestComposeWiring:
    def test_passes_the_three_env_vars(self):
        assert f"{URL_VAR}: ${{{URL_VAR}:-}}" in COMPOSE
        assert f"{TOKEN_VAR}: ${{{TOKEN_VAR}:-}}" in COMPOSE
        # 模板 header 引用的是无前缀名 —— compose 必须把它注入成容器内变量
        assert "MCP_SERVER_MONITORING_TOKEN: ${" + TOKEN_VAR + ":-}" in COMPOSE


class TestCrossFileConstants:
    """跨文件常量是这类接入最阴的失败源：单看每个文件都对，合起来全程 401 或静默关闭。"""

    def test_header_var_name_wired_in_compose(self):
        entry = json.loads(TEMPLATE_PATH.read_text())["mcp"]["servers"]["server-monitoring"]
        match = re.fullmatch(r"Bearer \$\{([A-Z0-9_]+)\}", entry["headers"]["authorization"])
        assert match, "header 形状变了 —— 运行时不认识的写法会安静地不发 header"
        var = match.group(1)
        assert f"{var}: " in COMPOSE, f"模板引用的 ${{{var}}} 未在 compose 注入（运行时展开为空串 → 401）"

    def test_url_placeholder_known_to_renderer_and_template(self):
        renderer = RENDERER.read_text()
        assert "__SERVER_MONITORING_MCP_URL__" in renderer
        assert "__SERVER_MONITORING_MCP_URL__" in TEMPLATE_PATH.read_text()
        assert URL_VAR in renderer  # 渲染器从哪个环境变量读它

    def test_url_var_known_to_every_consumer(self):
        assert URL_VAR in COMPOSE          # 注入进容器
        assert URL_VAR in ENTRYPOINT       # entrypoint 用它判断是否启用
        assert URL_VAR in START_SH         # start 脚本用它做预检


class TestEntrypointGuard:
    def test_guard_exists_and_runs_before_render(self):
        assert CA_CONTAINER_PATH in ENTRYPOINT
        assert "server-monitoring-ca.pem" in ENTRYPOINT
        guard_at = ENTRYPOINT.index("server-monitoring-ca.pem")
        render_at = ENTRYPOINT.index("render-config.mjs")
        assert guard_at < render_at, "CA 检查必须在渲染/启动之前 —— 否则容器带着坏配置跑起来"

    def test_guard_fails_loud(self):
        guard_at = ENTRYPOINT.index("server-monitoring-ca.pem")
        window = ENTRYPOINT[guard_at:guard_at + 600]
        assert "exit 1" in window
        assert "TIANYI_BOT_SERVER_MONITORING_MCP_URL" in ENTRYPOINT[guard_at - 300:guard_at]

    def test_precreates_node_owned_cache_dir(self):
        """2026-10-14 事故守卫：root 阶段必须预建 node 属主的 /home/node/.cache。

        竞态：某个 root 上下文进程抢建了 root:700 的 /home/node/.cache + openclaw-0，
        随后真正的 gateway（node, uid 1000）连遍历都 EACCES → "SQLite read-only worker
        Unsafe fallback ... openclaw-1000" → crash-loop（重启复用同一容器可写层，
        永久无法自愈；全新容器则正常）。预建 + chown -R 让竞态无论谁赢都安全。
        """
        assert "chown -R node:node /home/node/.cache" in ENTRYPOINT
        cache_at = ENTRYPOINT.index("/home/node/.cache")
        switch_at = ENTRYPOINT.index("Switching to node user")
        assert cache_at < switch_at, "必须在切 node 用户之前预建（切完之后是 node，chown 失败）"


class TestDigestAutomation:
    """声明式预置每日监控摘要 automation（2026-10-14）：拉起即有、幂等更新、
    URL 清空即无、绝不阻塞网关启动。幂等语义实测确认：同 declaration-key 重加
    = created:false/updated:true（原地更新、ID 不变、不重复）。
    """

    _MARKER = "# ── Declare daily monitoring digest automation"

    def test_entrypoint_provisions_automation(self):
        assert self._MARKER in ENTRYPOINT
        block = ENTRYPOINT[ENTRYPOINT.index(self._MARKER):]
        assert "cron add" in block
        assert "--declaration-key tianyi-monitoring-daily" in block
        assert "--channel feishu" in block
        assert "--announce" in block
        assert '--to "${TIANYI_BOT_OWNER_OPEN_ID}"' in block
        assert "--tz Asia/Shanghai" in block
        assert "TIANYI_BOT_MONITORING_DIGEST_CRON" in block
        assert "30 8 * * *" in block, "默认排期 08:30（北京）"

    def test_provisioning_gated_backgrounded_before_exec(self):
        marker_at = ENTRYPOINT.index(self._MARKER)
        exec_at = ENTRYPOINT.index("exec node /app/openclaw.mjs gateway")
        assert marker_at < exec_at
        block = ENTRYPOINT[marker_at:exec_at]
        assert "TIANYI_BOT_SERVER_MONITORING_MCP_URL" in block, "门控：URL 非空才声明"
        assert ") &" in block, "必须后台运行（等待网关健康），不得阻塞 exec gateway"
        assert "healthz" in block, "创建前要等网关健康（cron 经 Gateway 管理）"

    def test_gate_directions_pinned(self):
        """方向必须钉死：变异 `-n`→`-z` / `= off`→`!= off` 均会反转语义而静默通过
        （2026-10-14 独立复核实测的假守卫）。这里把比较形式逐字钉住。"""
        block = ENTRYPOINT[ENTRYPOINT.index(self._MARKER):]
        assert '[ -n "${TIANYI_BOT_SERVER_MONITORING_MCP_URL:-}" ]' in block, "门控必须是 URL 非空（-n）"
        assert '[ "${digest_cron}" = "off" ]' in block, "关闭判断必须是 = off（不是 !=）"
        assert '!= "off"' not in block

    def test_off_path_reclaims_existing_automation(self):
        """OFF 态（URL 空 / DIGEST_CRON=off）必须**主动回收**既有 automation。

        框架不会自动回收 service 声明的 job（只有 system-owned 类型启动时收敛，
        2026-10-14 复核镜像内证据 cron-jobs.md:234-236）——不做回收的话，关掉开关
        后旧 job 仍每日投递（届时降级成无监控工具的日报），"清空即消失"就是假话。
        回收经 cron-job-id.mjs 按 declaration-key 找 id（cron rm 只吃 id）。
        """
        block = ENTRYPOINT[ENTRYPOINT.index(self._MARKER):]
        assert "cron-job-id.mjs tianyi-monitoring-daily" in block, "回收需经提取器按 declaration-key 找 id"
        assert "cron rm" in block
        assert "未启用" in block, "URL 空也要走回收分支（「监控未启用」口径）"

    def test_failure_branch_is_reachable(self):
        """cron add 失败时 set -e 会杀子壳，`case` 形态的 ❌ 分支是死代码（复核实测）。
        必须用 if cmd; then ✅ else ❌ fi 才能开出失败提示。"""
        block = ENTRYPOINT[ENTRYPOINT.index(self._MARKER):]
        assert "if out=$(node /app/openclaw.mjs cron add" in block
        assert "❌ 每日监控摘要 automation 创建失败" in block

    def test_missing_owner_warns_not_blocks(self):
        block = ENTRYPOINT[ENTRYPOINT.index(self._MARKER):]
        owner_guard_at = block.index("TIANYI_BOT_OWNER_OPEN_ID")
        window = block[:owner_guard_at + 200]
        assert "⚠️" in window, "缺 OWNER id 只警告，不拦启动"
        assert "exit 1" not in block.split("cron add")[0], "provisioning 段不得有拦启动的 exit 1"

    def test_compose_passes_digest_envs(self):
        assert "TIANYI_BOT_OWNER_OPEN_ID: ${TIANYI_BOT_OWNER_OPEN_ID:-}" in COMPOSE
        assert "TIANYI_BOT_MONITORING_DIGEST_CRON: ${TIANYI_BOT_MONITORING_DIGEST_CRON:-}" in COMPOSE

    def test_env_example_documents_digest(self):
        assert "TIANYI_BOT_OWNER_OPEN_ID" in ENV_EXAMPLE
        assert "TIANYI_BOT_MONITORING_DIGEST_CRON" in ENV_EXAMPLE


class TestExecApprovalsApprover:
    """审批体验修复（2026-10-14 最终版）：飞书在 2026.9.1 **不是**原生态审批渠道。

    实证链（全部有出处）：
    - 真校验器拒绝 `channels.feishu.execApprovals`（must not have additional properties）；
      上游六次飞书审批卡 PR 全部关闭未合并，canonical 跟踪 = openclaw/openclaw#104521；
    - 顶层 `approvals.exec` 转发是**错误尝试**并已回退：它制造一条"审批路由"，绕过上游
      7 月修复 #108935 的"no-route → askFallback 秒级内联生效"快速路径 → 审批转为
      "等人类" → 阻塞每会话队列 → 撞 300s 上限被驱逐（实测 229-312s 卡死、消息被吞）；
    - 正解 = `tools.exec.mode: "auto"`：白名单直通；未命中先过自动审查员（低风险放行/
      有风险拒绝），回合秒级完成——不再需要任何人工审批界面。
    """

    def test_feishu_channel_must_not_carry_exec_approvals_key(self):
        template = json.loads(TEMPLATE_PATH.read_text())
        assert "execApprovals" not in template["channels"]["feishu"], (
            "2026.9.1 飞书 schema 拒绝该键（真校验器实证）——飞书原生审批是上游未发布功能"
        )

    def test_template_must_not_carry_approvals_forwarding(self):
        """负向守卫：`approvals.exec` 转发制造路由 → 等待人类 → 300s 队列驱逐（实测事故）。"""
        template = json.loads(TEMPLATE_PATH.read_text())
        assert "approvals" not in template, (
            "不要再加 approvals.exec 转发——它会绕过 #108935 的 askFallback 内联快速路径，"
            "在无审批 UI 的飞书渠道造成队列级卡死（2026-10-14 实测）"
        )

    def test_template_exec_mode_is_auto(self):
        """2026-10-14 决策：tools.exec.mode ask→auto。
        无 UI 渠道下'等人工'必然死；auto = 白名单直通 + 审查员对低风险未命中放行。
        """
        template = json.loads(TEMPLATE_PATH.read_text())
        assert template["tools"]["exec"]["mode"] == "auto"

    @pytest.mark.skipif(NODE is None, reason="宿主机无 node，跳过渲染器行为测试")
    def test_rendered_config_shape(self, tmp_path):
        proc, out = render(tmp_path, {})
        assert proc.returncode == 0, proc.stderr
        config = json.loads(out.read_text())
        assert "execApprovals" not in config["channels"]["feishu"]
        assert "approvals" not in config
        assert config["tools"]["exec"]["mode"] == "auto"


class TestCronJobIdExtractor:
    """cron-job-id.mjs（OFF 态回收用的 id 提取器）行为单测。"""

    @pytest.mark.skipif(NODE is None, reason="宿主机无 node")
    def test_extracts_id_by_declaration_key(self):
        payload = '{"jobs": [{"id": "aaa", "declarationKey": "other"}, {"id": "bbb", "declarationKey": "tianyi-monitoring-daily"}]}'
        proc = subprocess.run(
            [NODE, str(REPO_ROOT / "docker" / "tianyi-bot" / "cron-job-id.mjs"), "tianyi-monitoring-daily"],
            input=payload, capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert proc.stdout == "bbb"

    @pytest.mark.skipif(NODE is None, reason="宿主机无 node")
    def test_missing_key_or_broken_json_yields_empty(self):
        for payload in ('{"jobs": []}', "not json at all"):
            proc = subprocess.run(
                [NODE, str(REPO_ROOT / "docker" / "tianyi-bot" / "cron-job-id.mjs"), "tianyi-monitoring-daily"],
                input=payload, capture_output=True, text=True,
            )
            assert proc.returncode == 0, "解析失败也要退出码 0（按无需回收处理）"
            assert proc.stdout == ""


class TestStartScriptPreflight:
    """compose up 对 crash-loop 返回 0 —— 预检是"启动命令看起来成功"时唯一的破功点。"""

    def test_errors_when_url_set_but_token_missing(self):
        assert f"^{URL_VAR}=" in START_SH  # grep -E 形态读 .env（既有惯例）
        assert f"^{TOKEN_VAR}=" in START_SH
        assert "未正确设置" in START_SH

    def test_help_block_mentions_probe(self):
        assert "mcp probe server-monitoring --json" in START_SH


class TestEnvExample:
    lines = ENV_EXAMPLE.splitlines()

    def test_monitoring_disabled_by_default(self):
        """新部署照模板克隆必须默认关闭：URL/token 两行都注释着。"""
        assert not any(line.strip().startswith(f"{URL_VAR}=") for line in self.lines)
        assert not any(line.strip().startswith(f"{TOKEN_VAR}=") for line in self.lines)

    def test_documents_placeholder_and_rules(self):
        """公开模板不得含真实内网地址（全仓 .example 惯例）——真实地址以 docs 为准。

        2026-10-13 前此处曾用真实 IP + 把「示例禁现校内地址」守卫收窄到 YUQUE 行；
        改为 <MCP_HOST> 占位符后守卫恢复全量语义（见 test_yuque_mcp_local.py）。
        """
        assert any("https://<MCP_HOST>:8765/mcp" in line for line in self.lines)
        assert "docs/server-monitoring-mcp.md" in ENV_EXAMPLE
        assert "10.48.0.81" not in ENV_EXAMPLE


class TestAgentPolicy:
    def test_agents_md_documents_discipline(self):
        assert "## Server Monitoring (server-monitoring MCP)" in AGENTS_MD
        assert "read-only" in AGENTS_MD
        assert "gfs-visualization" in AGENTS_MD, "GFS 主 worker 与可视化要分开 —— 别让模型合并报"
        assert "UNKNOWN" in AGENTS_MD

    def test_agents_md_exec_guidance_matches_mode_auto(self):
        """2026-10-14 复核实锤：AGENTS.md 的 exec 指引必须与 `tools.exec.mode=auto` 同口径。

        旧文案（"approvals are effectively broken… 非白名单命令说'不能跑'"）写于
        mode=auto 之前的凌晨；放行路径已改由自动审查员承担，旧文案会让模型拒绝
        本可成功的命令（实测 uname / python3 -c 均在审查员放行之列），恰好抵消修复。
        """
        assert "auto reviewer" in AGENTS_MD, "指引应写明：非白名单命令走自动审查员"
        assert "effectively broken" not in AGENTS_MD, (
            "旧口径（'approvals are effectively broken…'）已废弃"
            "（mode=auto 下低风险命令可跑）——注意别用 'cannot run here' 做断言："
            "新文案在『不要这样说』的否定句里合法地引用了它"
        )
        assert "attempt" in AGENTS_MD.lower(), "指引应为『直接尝试，被拒再解释』"


@pytest.mark.skipif(NODE is None, reason="宿主机无 node")
class TestNodeExtraCaCertsSemantics:
    """把「为什么 NODE_EXTRA_CA_CERTS 要用 URL 门控」钉在实测行为上（node v22 实测）。

    空值与**缺失文件**都完全静默（stderr 0 字节）—— 所以：
    ① 关闭态（空串）不会有任何日志噪音，开关干净；
    ② 启用态 CA 缺失也不会有人替你报警，entrypoint 守卫是唯一防线（TestEntrypointGuard）。
    """

    @staticmethod
    def _node(extra: str, code: str = "0") -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "NODE_EXTRA_CA_CERTS"}
        env["NODE_EXTRA_CA_CERTS"] = extra
        return subprocess.run([NODE, "-e", code], capture_output=True, text=True, env=env)

    def test_empty_value_is_silently_ignored(self):
        proc = self._node("")
        assert proc.returncode == 0
        assert "Ignoring extra certs" not in proc.stderr

    def test_missing_file_is_also_silent(self, tmp_path):
        proc = self._node(str(tmp_path / "not-there.pem"))
        assert proc.returncode == 0
        assert "Ignoring extra certs" not in proc.stderr, (
            "（若哪天 node 开始告警，此断言失败是提醒：entrypoint 守卫仍然要留着）"
        )

    def test_valid_file_is_actually_loaded(self):
        """正控：compose 的表达式在启用态确实把 CA 加进信任库（extra store 里能看到）。"""
        if not CA_HOST_PATH.is_file():
            pytest.skip("CA 文件尚未提供")
        proc = self._node(
            str(CA_HOST_PATH),
            "const t=require('tls');const f=t.getCACertificates;"
            "if(!f){console.log('SKIP');process.exit(0)};"
            "console.log(f('extra').length)",
        )
        assert proc.returncode == 0, proc.stderr
        if proc.stdout.strip() == "SKIP":
            pytest.skip("node 无 tls.getCACertificates API")
        assert int(proc.stdout.strip()) >= 1


class TestToolSurfaceAlignment:
    """2026-10-13：服务端正式发布交付层，工具面 9 → 12（+list_projects /
    get_project_overview / get_project_build，Jenkins CI 证据链）。

    客户端策略（AGENTS.md 纪律 + docs + README/CLAUDE 表述）必须与能力面同步——
    "服务端加了工具而客户端纪律没跟上"是最阴的漂移：不影响连通，只是模型永远
    不知道能问、报告也永远少一层事实。检查器集合本身由
    tests/test_check_server_monitoring_mcp.py 钉住，这里钉跨文件表述。
    """

    def test_agents_md_covers_delivery_tools(self):
        for name in ("list_projects", "get_project_overview", "get_project_build"):
            assert name in AGENTS_MD, f"AGENTS.md 纪律缺少交付工具 {name}"
        low = AGENTS_MD.lower()
        assert "staging" in low, "交付纪律必须写明 staging ≠ 生产验收"
        assert "unknown" in low, "交付纪律必须写明 UNKNOWN ≠ 成功"

    def test_agents_md_notes_weather_docker_gap(self):
        """weather 没有 Docker 详情后端（实测 DOCKER_UNAVAILABLE）——纪律要如实
        告知，否则模型会把"查不到容器"报成"服务挂了"。"""
        assert "weather" in AGENTS_MD
        assert "DOCKER_UNAVAILABLE" in AGENTS_MD

    def test_docs_cover_registry_and_tools(self):
        for token in ("12", "list_projects", "get_project_overview", "get_project_build",
                      "rainfall-oa-data", "zhixun-core", "realtime-forecast"):
            assert token in MONITORING_DOC, f"docs/server-monitoring-mcp.md 缺少 {token}"
        assert "qupengfei" not in MONITORING_DOC, "公开面文档不得出现内网部署用户名"
        assert "私有团队仓" not in MONITORING_DOC, "本仓是公开仓——假前提文案已废弃"

    def test_readme_and_claude_md_say_twelve(self):
        assert "12 个只读工具" in README_MD
        assert "12 个只读工具" in CLAUDE_MD
        assert "9 工具" not in CLAUDE_MD, "旧「9 工具」表述必须清除（含命令注释里的简写）"

    def test_docs_describe_the_campus_domain_entry(self):
        """2026-10-30：客户端切到校园域名入口。文档必须同时写清两个入口、
        公网证书与内网 CA 的区别、以及服务端 Host 白名单依赖（issue #5）——
        只写「URL 改了」会让排障的人继续按自签 CA 的方向查。"""
        assert "<MCP_DOMAIN>" in MONITORING_DOC
        assert "公网证书" in MONITORING_DOC
        assert "10.48.0.81" in MONITORING_DOC, "内网直连回退入口仍要保留说明"
        assert "MCP_ALLOWED_HOSTS" in MONITORING_DOC

    def test_env_example_offers_both_entrypoints_without_real_ip(self):
        assert "<MCP_DOMAIN>" in ENV_EXAMPLE
        assert "<MCP_HOST>" in ENV_EXAMPLE
        assert "10.48.0.81" not in ENV_EXAMPLE, "公开模板不得含真实内网地址"

    def test_public_surfaces_do_not_hardcode_live_entry(self):
        """2026-10-30 隐私收口：真实校园域名只留在 gitignored `.env.tianyi-bot`；
        公开文档与模板一律用占位符。真实值不在测试源码里重复一遍，而是从
        live env 读取后再断言它没有出现在公开面（无 live env 时跳过）。"""
        live_host = _live_monitoring_host()
        if not live_host:
            pytest.skip("无 .env.tianyi-bot（CI/新机器）——跳过 live 入口隐私守卫")
        for label, text in (
            ("docs/server-monitoring-mcp.md", MONITORING_DOC),
            ("CLAUDE.md", CLAUDE_MD),
            ("README.md", README_MD),
            (".env.tianyi-bot.example", ENV_EXAMPLE),
        ):
            assert live_host not in text, f"{label} 不得硬编码真实校园域名（用 <MCP_DOMAIN>）"
