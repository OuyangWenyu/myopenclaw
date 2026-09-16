"""Static guards for openclaw/plugins/paper-queue-actor/index.ts.

Run: uv run --with pytest pytest tests/test_paper_queue_plugin.py -v

The plugin cannot be unit-tested on the host — it imports OpenClaw's plugin SDK and only
runs inside the gateway process, and Node cannot resolve `openclaw/plugin-sdk/*` here.
So these are *static* guards pinning the load-bearing invariants, and real behaviour is
verified live (see the plan's Task 6: send a Discord message, then read the DB).

Two of them are genuine cross-file checks rather than string matching:
  * every actor_* param the plugin injects must be one the MCP server actually reads
  * every tool the MCP server exposes must have a matching suffix in the plugin

Static guards are weak on their own; they are here to stop a future edit from quietly
removing the fail-closed path or renaming a param that nothing else would notice.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "openclaw" / "plugins" / "paper-queue-actor"
INDEX_TS = (PLUGIN_DIR / "index.ts").read_text()
SERVER_PY = REPO_ROOT / "openclaw" / "skills" / "paper-queue" / "mcp_server.py"


@pytest.fixture(scope="module")
def server():
    spec = importlib.util.spec_from_file_location("pq_mcp_server_for_plugin_test", SERVER_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestManifest:
    """A malformed manifest or a wrong entry path means the plugin silently never loads."""

    def test_package_json_points_at_index(self):
        pkg = json.loads((PLUGIN_DIR / "package.json").read_text())
        assert pkg["openclaw"]["extensions"] == ["./index.ts"]

    def test_plugin_manifest_id_matches_directory(self):
        manifest = json.loads((PLUGIN_DIR / "openclaw.plugin.json").read_text())
        assert manifest["id"] == "paper-queue-actor"

    def test_activates_on_startup(self):
        manifest = json.loads((PLUGIN_DIR / "openclaw.plugin.json").read_text())
        assert manifest["activation"]["onStartup"] is True


class TestHooks:
    """The three-hook split is the whole mechanism — losing one breaks attribution."""

    @pytest.mark.parametrize("hook", [
        "message_received", "before_agent_run", "before_tool_call", "agent_end",
    ])
    def test_registers_hook(self, hook):
        assert f'api.on("{hook}"' in INDEX_TS

    def test_binds_on_agent_run_not_prompt_build(self):
        """before_prompt_build 的 messages 里还没有本轮入站块，绑在那儿会抓空。"""
        assert 'api.on("before_agent_run"' in INDEX_TS
        assert 'api.on("before_prompt_build"' not in INDEX_TS, (
            "曾经踩过：before_prompt_build 时 messages 里还没有本轮入站块（实测 22 vs 23 条），"
            "在那里抓 message_id 必然为空，只能退化成会话兜底"
        )

    def test_does_not_scrape_sender_out_of_free_text(self):
        """**安全回归守卫**：早先的版本用正则去提示词里抓 message_id 来「精确绑定」。

        那条路走过用户可写的文本 —— 群友只要在消息里贴一个别人的 message_id，
        就能把这个回合的归属绑到别人头上。现在身份只能来自宿主给的结构化字段，
        任何扫描自由文本的写法都不要再进来。
        """
        body = "".join(line for line in INDEX_TS.splitlines()
                       if not line.strip().startswith("//"))   # 注释里的说明不算
        assert "matchAll" not in body, "插件里不应再出现对提示词的正则扫描"
        assert "JSON.stringify(messages" not in body
        # 身份只能来自宿主给的结构化字段（ctx / event / metadata）
        assert "senderId: str(c.senderId ?? e.senderId ?? meta.senderId)" in body


class TestFailClosed:
    """before_tool_call 抛异常会阻塞工具调用 —— 静默烧掉虾酱的工具面。"""

    def test_tool_hook_swallows_errors(self):
        body = INDEX_TS.split('api.on("before_tool_call"', 1)[1]
        assert "catch" in body
        # 早返回（非本插件的工具）+ catch 兜底，至少两处
        assert body.count("return undefined") >= 2

    def test_injects_empty_actor_when_unbound(self):
        """取不到身份时注入空串让 server 拒绝，绝不用模型填的 requester 兜底。"""
        body = INDEX_TS.split('api.on("before_tool_call"', 1)[1]
        assert 'const actorId = sender?.senderId ?? ""' in body, (
            "actor_id 只能来自宿主绑定的 sender；任何 '模型传了什么就用什么' 的写法都会毁掉归属"
        )
        assert "actor_id: actorId," in body

    def test_caches_are_bounded(self):
        """网关是长期进程：无上限的 Map 是慢泄漏。"""
        assert "MAX_CACHE" in INDEX_TS
        assert "while (map.size > MAX_CACHE)" in INDEX_TS

    def test_cleans_up_on_agent_end(self):
        body = INDEX_TS.split('api.on("agent_end"', 1)[1]
        assert "byRun.delete" in body
        assert "ambiguousRuns.delete" in body


class TestAttributionAmbiguity:
    """同一回合内多人发言会串台 —— 必须可检出，而不是假装不会发生。"""

    def test_detects_second_speaker_in_a_run(self):
        assert "ambiguousRuns.add" in INDEX_TS
        assert "ambiguousRuns.has" in INDEX_TS

    def test_injects_the_ambiguity_flag(self):
        assert "actor_ambiguous" in INDEX_TS


class TestAttributionSignature:
    """签名把「插件没加载」从静默伪造变成响亮的拒绝写入。"""

    def test_signs_the_injected_identity(self):
        assert 'import { createHmac } from "node:crypto"' in INDEX_TS
        assert "actor_sig: sign(" in INDEX_TS

    def test_reads_the_secret_from_plugin_config(self):
        assert "pluginConfig" in INDEX_TS and "secret" in INDEX_TS

    def test_signs_exactly_what_the_server_verifies(self, server):
        """签名覆盖的字段必须与服务端逐字一致 —— 不一致 = 每次写入都被拒。"""
        assert server.ACTOR_SIGNED_FIELDS == ("actor_id", "session_ref", "actor_message")
        for field in server.ACTOR_SIGNED_FIELDS:
            assert field in INDEX_TS


class TestCrossFileConsistency:
    """Renaming a param on either side would silently disable attribution."""

    def test_plugin_injects_only_params_the_server_reads(self, server):
        for param in server._ACTOR_PROPS:
            assert f"{param}:" in INDEX_TS, (
                f"server 读取 {param}，但插件没有注入它 —— 归属会静默失效"
            )

    def test_every_server_tool_has_a_plugin_suffix(self, server):
        suffixes = INDEX_TS.split("const TOOL_SUFFIXES", 1)[1].split("]", 1)[0]
        for tool in server.TOOLS:
            assert f'"__{tool["name"]}"' in suffixes, (
                f"server 暴露 {tool['name']}，插件的后缀列表里没有它 —— 该工具的调用不会被注入身份"
            )

    def test_suffix_matching_is_namespaced(self, server):
        """MCP 工具名是 `<server>__<tool>`，裸名匹配会匹配不到。"""
        for tool in server.TOOLS:
            assert f'"__{tool['name']}"' in INDEX_TS
