"""本机语雀 MCP（yuque-mcp）服务守卫：compose / start.sh / 监控 / 备份 / pin 五处联动。

服务体系在本机（替代依赖 UniVPN 的校内服务器，issue #79）：服务端（本仓 compose 的
yuque-mcp 容器）与客户端（Hermes / 天一）在同一份仓库里各改一处 —— 任何一处漏了
都不报错，只会安静地不工作（服务起不来、key 对不上、监控瞎着、活库被裸拷）。
所以逐处钉住。

Run: uv run --with pytest --with pyyaml pytest tests/test_yuque_mcp_local.py -v
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()
START_SH = (REPO_ROOT / "scripts" / "start.sh").read_text()

# 上游 gitcode.com/dlut-water/yuque_mcp_server 没有 tag，只能固定 commit。
# 升级流程：cd ~/code/yuque_mcp_server && git fetch && 审查 diff → 改这里 + 重建镜像。
SIBLING_REPO = REPO_ROOT.parent / "yuque_mcp_server"
PINNED_SHA = "3945bce70ac72002ea7d0026f2c2a7ce16b0d821"


def compose_service_block(name: str) -> str:
    """取 compose 里某个服务的 YAML 块（服务位于 2 空格缩进，键为 4 空格）。"""
    lines = COMPOSE.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"  {name}:"))
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("  ") and not lines[i].startswith("    ")),
               len(lines))
    return "\n".join(lines[start:end])


class TestComposeYuqueService:
    """服务块本身：独立镜像、不发布端口、key 单一来源、两个 volume。"""

    def test_builds_own_image_from_sibling_repo(self):
        """独立镜像从兄弟仓 build context 构建（与 aisecretary/repo-scanner 同模式）。"""
        block = compose_service_block("yuque-mcp")
        assert "context: ../yuque_mcp_server" in block
        assert "image: myopenclaw/yuque-mcp:latest" in block
        assert "container_name: yuque-mcp" in block
        assert "restart: unless-stopped" in block

    def test_sse_cloud_mode_on_18001(self):
        block = compose_service_block("yuque-mcp")
        assert "RUN_MODE=cloud" in block
        assert "PORT=18001" in block

    def test_no_host_port(self):
        """不发布宿主端口：只挂 myopenclaw-net（Bearer key 之外不开面）。"""
        block = compose_service_block("yuque-mcp")
        assert "ports:" not in block
        assert "- myopenclaw-net" in block

    def test_runs_as_root_for_host_volume_writes(self):
        """镜像默认 uid 10001，写不进 macOS 宿主目录 —— root 运行（tianyi 先例）。"""
        block = compose_service_block("yuque-mcp")
        assert "user: root" in block

    def test_mcp_key_single_source(self):
        """服务端 key 与客户端同源：.env 一处定义。

        两处各自生成的表现是"每次请求都被 401"——本仓最恨的跨文件常量不一致。
        """
        block = compose_service_block("yuque-mcp")
        assert "MCP_API_KEY=${MCP_YUQUE_MCP_API_KEY:-}" in block

    def test_persists_change_data_and_backup(self):
        """快照/报告（含团队文档正文）与备份输出都必须落在宿主卷上。"""
        block = compose_service_block("yuque-mcp")
        assert "${HOME}/.myagentdata/yuque-mcp/change_data:/app/yuque/change_data" in block
        assert "${HOME}/.myagentdata/yuque-mcp/backup:/app/yuque/backup" in block


class TestStartShWiring:
    """start.sh 三处：依赖仓库检查、挂载点先于 compose up、token 缺失大声警告。"""

    def test_checks_the_sibling_repo(self):
        """兄弟仓是 build context —— 缺失时 build 直接失败，这里给前置警告。"""
        assert '"${HOME}/code/yuque_mcp_server"' in START_SH

    def test_creates_data_dirs_before_compose_up(self):
        """挂载点必须先于 compose up 建好（paper-queue 同规矩）。"""
        assert "${HOME}/.myagentdata/yuque-mcp/change_data" in START_SH
        assert "${HOME}/.myagentdata/yuque-mcp/backup" in START_SH
        assert START_SH.index("yuque-mcp/change_data") \
            < START_SH.index("docker compose up -d ${BUILD_FLAG}")

    def test_warns_when_yuque_token_missing(self):
        """缺 token 时容器照常 Up，但每夜快照与全部工具静默失败 —— 必须大声警告。"""
        assert "^YUQUE_TOKEN=" in START_SH, "用 grep -q 形态检查 .env（勿用管道，防 set -e 中断）"
        assert "YUQUE_TOKEN 未设置" in START_SH

    def test_warns_when_mcp_key_missing(self):
        """缺 MCP key 时服务端**无认证直通**（上游行为）—— 网内任意容器可免鉴权
        读团队文档。start.sh 必须大声警告（与 token 同一形态）。"""
        assert "^MCP_YUQUE_MCP_API_KEY=" in START_SH
        assert "MCP_YUQUE_MCP_API_KEY 未设置" in START_SH


class TestUptimeKumaMonitors:
    """监控注册脚本的 MONITORS 数组是手工清单（不从 compose 动态读）—— 漏了就瞎着。"""

    @staticmethod
    def _script() -> str:
        return (REPO_ROOT / "scripts" / "setup-uptime-kuma.sh").read_text()

    def test_http_monitor_accepts_only_401(self):
        """不带 token 探 /sse 得到 401 —— 这是"活着 + key 已配置"的双重信号。

        只接受 401：key 意外为空时上游是**无认证直通**（200 长流），接受 2xx 会让
        这个 fail-open 状态永远显示绿 —— 恰好瞎掉最该报警的故障模式。
        """
        script = self._script()
        assert '"Yuque MCP|http|http://yuque-mcp:18001/sse||[\\"401\\"]"' in script

    def test_docker_container_monitor(self):
        assert '"Docker: yuque-mcp|docker||yuque-mcp|"' in self._script()


class TestEnvExampleShape:
    """公开模板（.env.example）是新部署的唯一来源 —— 形状钉住，占位符钉住。"""

    @staticmethod
    def _example() -> str:
        return (REPO_ROOT / ".env.example").read_text()

    def test_points_at_local_service(self):
        example = self._example()
        assert "YUQUE_MCP_URL=http://yuque-mcp:18001/sse" in example
        assert "10.48.0.81" not in example, "模板不得残留校内地址"

    def test_documents_token_and_repos(self):
        example = self._example()
        assert "YUQUE_TOKEN=" in example, "服务端自持 token 是新拓扑的核心变化，模板必须交代"
        # 三库 JSON 必须以**生效行**写进模板 —— 注释行照模板克隆后不生效，新部署会
        # 静默回退到内置两库、丢掉「建设方案」（这正是要防的故障）。
        assert "\nYUQUE_CHANGE_REPOS_JSON=[" in example
        assert "kgo8gd/tnld77" in example and "kgo8gd/xnedwc" in example and "kgo8gd/owoc3p" in example

    def test_tianyi_example_points_at_local_service(self):
        example = (REPO_ROOT / ".env.tianyi-bot.example").read_text()
        assert "TIANYI_BOT_YUQUE_MCP_URL=http://yuque-mcp:18001/sse" in example
        assert "10.48.0.81" not in example


class TestLiveEnvSwitched:
    """部署实况（gitignored .env / .env.tianyi-bot）：切换完成且 key 两端同值。

    与 TestSiblingPin 同风格 —— 读的是本机部署状态，文件缺失时跳过（新克隆仓库）。
    """

    @staticmethod
    def _read_env(path: Path) -> dict[str, str]:
        pairs = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            pairs[key.strip()] = value.strip()
        return pairs

    def test_main_env_switched_to_local_service(self):
        env_path = REPO_ROOT / ".env"
        if not env_path.exists():
            pytest.skip(".env 不存在（未部署）")
        env = self._read_env(env_path)
        assert env.get("YUQUE_MCP_URL") == "http://yuque-mcp:18001/sse", "Hermes 注册源必须已切本机"

    def test_tianyi_env_switched_and_key_matches(self):
        main_path = REPO_ROOT / ".env"
        tianyi_path = REPO_ROOT / ".env.tianyi-bot"
        if not main_path.exists() or not tianyi_path.exists():
            pytest.skip(".env / .env.tianyi-bot 不齐（未部署）")
        main_key = self._read_env(main_path).get("MCP_YUQUE_MCP_API_KEY", "")
        tianyi = self._read_env(tianyi_path)
        assert tianyi.get("TIANYI_BOT_YUQUE_MCP_URL") == "http://yuque-mcp:18001/sse"
        # 比较结果先布尔化 —— 直接 assert 相等会在失败输出里渲染两侧 key 字面值
        keys_match = bool(main_key) and tianyi.get("TIANYI_BOT_MCP_YUQUE_MCP_API_KEY") == main_key
        assert keys_match, (
            "天一是 key 的第二份拷贝（无双源机制）—— 两处必须手工同值，否则天一全程 401"
        )


class TestPortabilityWiring:
    """新机器克隆路径：兄弟仓是 build context 硬依赖，克隆脚本与可移植性文档都要认识它。"""

    def test_clone_deps_includes_sibling_repo(self):
        script = (REPO_ROOT / "scripts" / "clone-deps.sh").read_text()
        assert "gitcode.com/dlut-water/yuque_mcp_server" in script

    def test_portability_doc_lists_sibling_repo(self):
        doc = (REPO_ROOT / "docs" / "portability.md").read_text()
        assert "yuque_mcp_server" in doc


class TestSiblingPin:
    """上游无 tag：本地兄弟仓 checkout 必须与 pin 一致，漂移必须显式 review。"""

    def test_sibling_repo_is_at_pinned_commit(self):
        if not (SIBLING_REPO / ".git").is_dir():
            pytest.skip("../yuque_mcp_server 不存在（未克隆依赖仓库）")
        head = subprocess.run(
            ["git", "-C", str(SIBLING_REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert head == PINNED_SHA, (
            f"兄弟仓 HEAD {head[:7]} 与 pin {PINNED_SHA[:7]} 不一致 —— "
            "先审查上游 diff，再显式更新 pin 与文档后重建镜像"
        )
