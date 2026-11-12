"""守卫：Kanban 的「总管」是 `default`（爱玛士），「派发执行器」只有一台且在 `hermes-coder`。

先分清两个角色，否则很容易读成"worker 当老板"：

- **总管** `default`（爱玛士）：拆活、建卡、指派、验收。这是权威所在。
- **派发执行器**（"工头"）：只做机械动作 —— 60s 扫板、抢占、spawn worker。不做判断。

两者受不同约束（2026-11-11 实测）：四个 Hermes 容器共享同一个
`~/.hermes/kanban.db`，`~/.hermes/kanban/.dispatcher.lock` 是全机唯一的派发锁；
worker 是 dispatcher 的 subprocess（`hermes -p <assignee> … chat -q work kanban
task t_x`），**继承的是 dispatcher 所在容器的挂载与环境变量**，不是 assignee 的。

所以工头必须待在 `hermes-coder`：coder 需要的 `/opt/gitcode-config`、
`/opt/myagentdata`、aisecretary skills 只挂在那里；而真邮箱配置与 OneDrive token
只在 `hermes`（总管）容器，其余容器用空卷遮蔽。工头若跟着总管摆在 `hermes`，
一个 coder worker 就能读到爱玛士的邮箱凭据，破掉 docs/email.md 的隔离。

本测试钉住：**恰好一个**容器开着派发，且它是 `hermes-coder`（而非总管容器）。
只做静态断言：不连网、不读真实 .env、不启动容器。

Run: uv run --with pytest --with pyyaml pytest tests/test_hermes_kanban_wiring.py -q
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
COMPOSE = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
CLAUDE_MD = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
DOCS_INDEX = (REPO_ROOT / "docs" / "index.md").read_text(encoding="utf-8")
KANBAN_DOC_PATH = REPO_ROOT / "docs" / "kanban.md"

GATE = "HERMES_KANBAN_DISPATCH_IN_GATEWAY"

# 四个跑 Hermes gateway 的容器（hermes-dashboard 只跑 `dashboard`，无派发循环）。
GATEWAY_SERVICES = ("hermes", "hermes-coder", "hermes-finance", "hermes-daoyuan")
DISPATCH_HOST = "hermes-coder"  # 工头所在容器；总管是 default（爱玛士）
NON_DISPATCHERS = tuple(s for s in GATEWAY_SERVICES if s != DISPATCH_HOST)


def _env(service: str) -> list[str]:
    return [str(item) for item in COMPOSE["services"][service].get("environment", [])]


def _gate(service: str) -> str | None:
    for item in _env(service):
        key, _, value = item.partition("=")
        if key == GATE:
            return value
    return None


class TestSingleDispatchHost:
    def test_non_dispatchers_explicitly_opt_out(self):
        for service in NON_DISPATCHERS:
            assert _gate(service) == "0", (
                f"{service} 必须设 {GATE}=0 —— 否则它会和 {DISPATCH_HOST} 抢那把全局派发锁，"
                f"worker 就可能落在没有 MCP 凭据的容器里"
            )

    def test_only_one_service_keeps_dispatch_on(self):
        enabled = [s for s in GATEWAY_SERVICES if _gate(s) in (None, "1", "true", "True")]
        assert enabled == [DISPATCH_HOST], (
            f"恰好一个 compose 服务跑派发执行器，实际={enabled}；"
            f"工头必须固定在 {DISPATCH_HOST}（它的挂载/环境才够 worker 用）"
        )

    def test_orchestrator_does_not_run_the_dispatch_loop(self):
        # 总管（default/爱玛士）不跑那个 60s 循环 —— 否则 worker 会继承它容器里的
        # 真邮箱配置与 OneDrive token。
        assert _gate("hermes") == "0"

    def test_dispatch_host_runs_coder_profile(self):
        # 工头要 spawn `hermes -p coder`，所以它必须本身就是 coder profile 的容器。
        command = str(COMPOSE["services"][DISPATCH_HOST].get("command", ""))
        assert "--profile coder" in command, command

    def test_dashboard_is_not_a_gateway(self):
        # 别把 dashboard 也算进来：它只跑 `dashboard --host …`，没有派发循环。
        assert str(COMPOSE["services"]["hermes-dashboard"].get("command", "")).startswith("dashboard")


class TestDocs:
    def test_kanban_doc_exists_and_covers_the_basics(self):
        assert KANBAN_DOC_PATH.is_file(), "docs/kanban.md 缺失"
        doc = KANBAN_DOC_PATH.read_text(encoding="utf-8")
        for needle in ("assignee", "dispatcher", "scratch", "排障", "/kanban create"):
            assert needle in doc, f"docs/kanban.md 缺少关键内容：{needle}"

    def test_dispatcher_decision_is_documented(self):
        # 结论要落在运维手册里，否则下一个人会照抄 compose 再开一个派发者。
        doc = KANBAN_DOC_PATH.read_text(encoding="utf-8")
        assert DISPATCH_HOST in doc and GATE in doc

    def test_claude_md_links_the_doc(self):
        assert "docs/kanban.md" in CLAUDE_MD
        assert GATE in CLAUDE_MD, "CLAUDE.md 必须写明派发开关，避免下次照抄 compose"

    def test_docs_index_links_the_doc(self):
        assert "kanban.md" in DOCS_INDEX

    def test_topology_doc_explains_env_precedence(self):
        # 四份 .env 谁读谁、谁优先 —— 这条是 2026-11-12 花了半天才从代码里确认的，
        # 写进文档才不会再被"改了 compose 怎么不生效"坑一次。
        topology = REPO_ROOT / "docs" / "hermes-topology.md"
        assert topology.is_file(), "docs/hermes-topology.md 缺失"
        doc = topology.read_text(encoding="utf-8")
        for needle in ("override=True", "profiles/<name>/.env", "docker compose"):
            assert needle in doc, f"hermes-topology.md 缺少关键内容：{needle}"
        assert "hermes-topology.md" in DOCS_INDEX
        assert "docs/hermes-topology.md" in CLAUDE_MD
