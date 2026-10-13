"""server-monitoring MCP 接入守卫：CA / 模板 / 渲染器 / compose / entrypoint / env 模板 / node 语义七处联动。

拓扑（详见 docs/server-monitoring-mcp.md）：天一容器 → TLS（内网自签 CA）+ Bearer →
`https://10.48.0.81:8765/mcp`。链路里**每一处漏了都不报错，只会安静地不工作**：

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

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

TEMPLATE_PATH = REPO_ROOT / "docker" / "tianyi-bot" / "openclaw.json.template"
RENDERER = REPO_ROOT / "docker" / "tianyi-bot" / "render-config.mjs"
ENTRYPOINT = (REPO_ROOT / "docker" / "tianyi-bot" / "entrypoint.sh").read_text()
COMPOSE = (REPO_ROOT / "docker-compose.tianyi-bot.yml").read_text()
ENV_EXAMPLE = (REPO_ROOT / ".env.tianyi-bot.example").read_text()
START_SH = (REPO_ROOT / "scripts" / "start-tianyi-bot.sh").read_text()
AGENTS_MD = (REPO_ROOT / "openclaw-tianyi" / "workspace" / "AGENTS.md").read_text()

CA_HOST_PATH = REPO_ROOT / "docker" / "tianyi-bot" / "ca" / "server-monitoring-ca.pem"
CA_CONTAINER_PATH = "/opt/tianyi-bot/ca/server-monitoring-ca.pem"

URL_VAR = "TIANYI_BOT_SERVER_MONITORING_MCP_URL"
TOKEN_VAR = "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN"
NODE = shutil.which("node")

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
    """CA 是公钥材料，随仓库提交（私有团队仓）；私钥当然绝不进仓库。"""

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
            "TIANYI_BOT_SERVER_MONITORING_MCP_URL": "https://10.48.0.81:8765/mcp",
            "TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN": token,
        })
        assert proc.returncode == 0, proc.stderr
        raw = out.read_text()
        entry = json.loads(raw)["mcp"]["servers"]["server-monitoring"]
        assert entry["url"] == "https://10.48.0.81:8765/mcp"
        # 字节级不变 = 渲染器没有展开 ${VAR}（展开发生在 OpenClaw 运行时）
        assert entry["headers"]["authorization"] == "Bearer ${MCP_SERVER_MONITORING_TOKEN}"
        assert token not in raw, "token 绝不能出现在渲染产物里（配置文件会被备份、进快照）"
        assert "__SERVER_MONITORING_MCP_URL__" not in raw

    def test_url_without_token_fails_loud(self, tmp_path):
        """URL 非空而 token 空 → 非零退出。静默降级成"每次 401"才是最难查的。"""
        proc, out = render(tmp_path, {
            "TIANYI_BOT_SERVER_MONITORING_MCP_URL": "https://10.48.0.81:8765/mcp",
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

    def test_documents_real_url_and_rules(self):
        assert any("10.48.0.81:8765/mcp" in line for line in self.lines)
        assert "docs/server-monitoring-mcp.md" in ENV_EXAMPLE


class TestAgentPolicy:
    def test_agents_md_documents_discipline(self):
        assert "## Server Monitoring (server-monitoring MCP)" in AGENTS_MD
        assert "read-only" in AGENTS_MD
        assert "gfs-visualization" in AGENTS_MD, "GFS 主 worker 与可视化要分开 —— 别让模型合并报"
        assert "UNKNOWN" in AGENTS_MD


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
