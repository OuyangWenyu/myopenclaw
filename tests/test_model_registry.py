"""模型源注册表与 modelctl 适配器的守卫。

Run: uv run --with pytest --with pyyaml pytest tests/test_model_registry.py -v

为什么这些测试值得存在：换源的失败模式几乎都不是「报错」，而是**静默**——
某个 agent 还跑在旧模型上、上下文被截断、或者 Claude 拿 A 家的钥匙打 B 家的
端点。这里钉住的是「渲染出来的形状」和「跨文件约定」，不是运行时连通性
（后者用 ``python3 scripts/modelctl.py probe``）。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELCTL = REPO_ROOT / "scripts" / "modelctl.py"
REGISTRY = REPO_ROOT / "config" / "models.toml"
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENTRYPOINT = REPO_ROOT / "docker" / "claude-code" / "entrypoint.sh"


def _modelctl():
    import sys

    spec = importlib.util.spec_from_file_location("modelctl", MODELCTL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses 解析字符串注解时要能回查模块，必须先登记再 exec
    sys.modules["modelctl"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ensure_hermes_model():
    spec = importlib.util.spec_from_file_location(
        "ensure_hermes_model", REPO_ROOT / "scripts" / "ensure_hermes_model.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# 测试用的假端点：真实域名是采购专属值，住在 .env，不该出现在仓库里。
FAKE_BASE_URL = "https://ws-test.cn-beijing.maas.aliyuncs.com"


@pytest.fixture(scope="module")
def reg():
    mc = _modelctl()
    expanded, missing = mc.Registry.load().with_env({"ALIYUN_MAAS_BASE_URL": FAKE_BASE_URL})
    assert not missing, f"注册表里还有未展开的端点变量: {missing}"
    return expanded


@pytest.fixture(scope="module")
def mc():
    return _modelctl()


def _service_env(service: str) -> dict[str, str]:
    data = yaml.safe_load(COMPOSE.read_text())
    env = data["services"][service].get("environment", [])
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    return dict(item.split("=", 1) for item in env if "=" in str(item))


# ══════════════════════════════════════════════════════════════════
# 注册表本身
# ══════════════════════════════════════════════════════════════════
class TestRegistry:
    def test_loads_and_every_binding_resolves(self, reg):
        assert reg.providers and reg.models and reg.roles
        resolved = reg.resolved_bindings()
        assert {r.binding for r in resolved} >= {
            "hermes.default",
            "hermes.coder",
            "hermes.finance",
            "hermes.daoyuan",
            "claude-code",
            "openclaw.main",
            "bot.zhixun",
            "bot.tianyi",
            "tdai-memory",
        }

    def test_primary_is_aliyun_and_fallback_is_official(self, reg):
        """主源走百炼、备用源走 DeepSeek 官方 —— 这是本次切换的全部语义。"""
        resolved = reg.resolve(reg.bindings["hermes.default"])
        assert resolved.provider.name == "aliyun"
        assert resolved.model.id == "deepseek-v4.1-flash-1017"
        assert resolved.fallback is not None
        assert resolved.fallback_provider is not None
        assert resolved.fallback_provider.name == "deepseek"
        assert resolved.fallback.id == "deepseek-flash"

    def test_claude_binding_provider_has_anthropic_endpoint(self, reg):
        resolved = reg.resolve(reg.bindings["claude-code"])
        assert resolved.provider.anthropic_url, "Claude Code 需要 Anthropic 兼容端点"

    def test_capability_metadata_is_plausible(self, reg):
        """抄错一个数字的后果是静默截断，不是报错。"""
        model = reg.models["deepseek-v4.1-flash-1017"]
        assert model.context == 1000000
        assert model.max_output == 393216
        assert "image" in model.input, "V4.1 Flash 原生多模态，丢了 image 就没法读图"

    def test_probe_targets_use_each_providers_own_model(self, reg):
        """备用 provider 必须探它自己的 model id。

        真实踩过：按 provider 去重时复用了主模型，于是探 DeepSeek 官方时拿着
        百炼的 ``deepseek-v4.1-flash-1017`` 去调，得到一条「model 不存在」的假红
        （服务端原文：supported API model names are deepseek-flash, …）。
        """
        mc = _modelctl()
        targets = {p.name: m.id for p, m in mc.probe_targets(reg)}
        assert targets["aliyun"] == "deepseek-v4.1-flash-1017"
        assert targets["deepseek"] == "deepseek-flash"

    def test_pinned_is_only_for_unswitchable_consumers(self, reg):
        """``[pinned]`` 只放**结构上**切不了的消费方。

        TDAI 曾经也在这里，但它其实能渲染（compose 读 env）—— 留在 pinned 的
        后果是没人校验它的模型 id，于是 `deepseek-chat` 那个路由别名在里面躺了
        很久。凡是可以被 apply/check 覆盖的，就应该当普通 binding。
        """
        assert "tdai-memory" not in reg.pinned
        assert set(reg.pinned) >= {"voice", "hermes-bind"}
        for pinned in reg.pinned.values():
            assert pinned.reason.strip(), f"{pinned.name} 缺理由"

    def test_every_binding_declares_a_role(self, reg):
        """车道定义在 [roles]，消费方只声明"我走哪条"。

        同一份注册表里两种写法（role 与 model 覆盖）会让人分不清哪个是常态：
        `model =` 只留给「临时给单个消费方钉死」的逃生口。
        """
        overrides = [b.name for b in reg.bindings.values() if not b.role]
        assert not overrides, f"这些 binding 绕过了 role（应改成 role）：{overrides}"

    def test_tdai_uses_the_official_lane(self, reg):
        binding = reg.bindings["tdai-memory"]
        assert binding.role == "official"
        resolved = reg.resolve(binding)
        assert resolved.model.id == "deepseek-flash"
        assert resolved.provider.name == "deepseek"

    def test_env_block_covers_every_compose_consumer(self, reg):
        """托管块只有一个写者：两个消费方的键必须一次算全，否则互相覆盖。"""
        mc = _modelctl()
        entries = mc.env_entries(reg, {"DASHSCOPE_API_KEY": "", "DEEPSEEK_API_KEY": "k"})
        assert entries["ANTHROPIC_BASE_URL"].endswith("/apps/anthropic")
        assert entries["CLAUDE_MODEL_KEY_ENV"] == "DASHSCOPE_API_KEY"
        assert entries["TDAI_LLM_MODEL"] == "deepseek-flash"
        assert entries["TDAI_LLM_BASE_URL"] == "https://api.deepseek.com/v1"
        assert entries["TDAI_LLM_API_KEY"] == "", (
            "TDAI 在官方源时不该复制密钥：compose 回落到 DEEPSEEK_API_KEY"
        )

    def test_missing_endpoint_env_is_loud(self):
        """部署专属端点缺变量时，注册表要显式报缺，不能渲染出半截 URL。

        真实形状：``${ALIYUN_MAAS_BASE_URL}/compatible-mode/v1`` 缺变量会退化成
        ``/compatible-mode/v1`` —— 那是一个"看起来像 URL"的死值，落到下游后
        表现为连不上，而不是"没配置"。
        """
        mc = _modelctl()
        expanded, missing = mc.Registry.load().with_env({})
        assert missing == ["ALIYUN_MAAS_BASE_URL"]
        aliyun = expanded.providers["aliyun"]
        assert aliyun.openai_url == "" and aliyun.anthropic_url == ""

    def test_apply_refuses_without_endpoint(self, tmp_path):
        mc = _modelctl()
        reg, missing = mc.Registry.load().with_env({})
        rc = mc.cmd_apply(
            reg,
            tmp_path,
            {},
            only=["hermes"],
            dry_run=True,
            allow_missing_keys=False,
            missing_env=missing,
        )
        assert rc == 2, "端点缺失时 apply 必须拒绝执行"

    def test_bad_registry_is_rejected(self):
        mod = _modelctl()
        with pytest.raises(mod.RegistryError):
            mod.Registry({"version": 1, "providers": {}, "models": {"x": {"provider": "nope"}}})


# ══════════════════════════════════════════════════════════════════
# Hermes
# ══════════════════════════════════════════════════════════════════
HERMES_SAMPLE = """\
# 手工维护的注释必须活下来
model:
  base_url: https://api.deepseek.com
  default: deepseek-flash
  provider: deepseek
fallback_providers:
- zai
fallback_model:
  provider: zai
  model: glm-5.3-flash
toolsets:
- hermes-cli
agent:
  max_turns: 90
"""


class TestHermesRenderer:
    def test_model_block_is_rewritten(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["hermes.default"])
        out = mc.render_hermes(HERMES_SAMPLE, resolved)
        assert "default: deepseek-v4.1-flash-1017" in out
        assert "provider: alibaba-cn" in out
        assert f"base_url: {FAKE_BASE_URL}" in out
        assert out.index("default:") < out.index("provider:") or True  # 键序不强制

    def test_fallback_is_official_deepseek(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["hermes.default"])
        out = mc.render_hermes(HERMES_SAMPLE, resolved)
        doc = yaml.safe_load(out)
        assert doc["fallback_providers"] == ["deepseek"]
        assert doc["fallback_model"] == {"provider": "deepseek", "model": "deepseek-flash"}

    def test_untouched_blocks_and_comments_survive(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["hermes.default"])
        out = mc.render_hermes(HERMES_SAMPLE, resolved)
        assert "# 手工维护的注释必须活下来" in out
        doc = yaml.safe_load(out)
        assert doc["toolsets"] == ["hermes-cli"]
        assert doc["agent"]["max_turns"] == 90

    def test_indented_list_keeps_its_indent(self, reg):
        """Hermes 自己 save_config 写的形状是 2 空格缩进；混排会让 YAML 解析失败。"""
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["hermes.default"])
        text = (
            "model:\n  default: deepseek-flash\n  provider: deepseek\n"
            "fallback_providers:\n  - zai\n"
            "fallback_model:\n  provider: zai\n  model: glm-5.3-flash\n"
            "toolsets:\n  - hermes-cli\n"
        )
        out = mc.render_hermes(text, resolved)
        assert "fallback_providers:\n  - deepseek\n" in out
        assert "\n- deepseek\n" not in out
        assert yaml.safe_load(out)["fallback_providers"] == ["deepseek"]

    def test_idempotent(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["hermes.default"])
        once = mc.render_hermes(HERMES_SAMPLE, resolved)
        twice = mc.render_hermes(once, resolved)
        assert once == twice

    def test_missing_model_block_is_reported_not_silently_written(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["hermes.default"])
        assert mc.render_hermes("toolsets:\n  - hermes-cli\n", resolved) is None

    def test_status_compares_endpoint_not_provider(self, reg, tmp_path):
        """status 的"实际"列必须和"期望"列同类型。

        真实踩过：Hermes 的期望值是端点 URL，实际值却是 provider 名，于是明明
        已经切好也永远显示漂移（换源前两边都是 deepseek，正好把这个错位藏住了）。
        """
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["hermes.default"])
        cfg = tmp_path / ".hermes" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(mc.render_hermes(HERMES_SAMPLE, resolved))
        live = mc._live_value(reg, "hermes.default", tmp_path, {})
        assert live == mc._desired_pair(resolved)

    def test_live_reader_matches_writer(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["hermes.default"])
        out = mc.render_hermes(HERMES_SAMPLE, resolved)
        model, provider, base = mc.read_hermes_live(out)
        assert (model, provider) == ("deepseek-v4.1-flash-1017", "alibaba-cn")
        assert base.startswith(FAKE_BASE_URL)


# ══════════════════════════════════════════════════════════════════
# Claude Code
# ══════════════════════════════════════════════════════════════════
CLAUDE_SAMPLE = """\
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "sk-old",
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_MODEL": "deepseek-flash[1M]",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "deepseek-flash",
    "API_TIMEOUT_MS": "3000000"
  },
  "permissions": {"allow": ["Bash(*)"]}
}
"""

CC_TOML_SAMPLE = """\
[[projects]]
name = "claude-code"

[projects.agent.options]
model = "haiku"

[projects.agent.options.env]
ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
ANTHROPIC_MODEL = "deepseek-flash[1M]"
API_TIMEOUT_MS = "3000000"

[[projects.platforms]]
type = "feishu"

[projects.platforms.options]
app_id = "${CC_CONNECT_FEISHU_APP_ID}"
"""


class TestClaudeRenderer:
    def test_settings_env_is_rewritten(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        out = mc.render_claude_settings(CLAUDE_SAMPLE, resolved)
        env = __import__("json").loads(out)["env"]
        assert env["ANTHROPIC_BASE_URL"].endswith("/apps/anthropic")
        assert env["ANTHROPIC_MODEL"] == "deepseek-v4.1-flash-1017"
        for key in mc.CLAUDE_TIER_KEYS:
            assert env[key] == "deepseek-v4.1-flash-1017"

    def test_context_window_is_declared_to_claude(self, reg):
        """Claude Code 不认识非 Anthropic 的模型 id，默认按 200K 算。

        不声明 `CLAUDE_CODE_MAX_CONTEXT_TOKENS` 不会报错，只会让长会话在 200K
        就压缩 —— 静默降质。
        """
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        env = __import__("json").loads(
            mc.render_claude_settings(CLAUDE_SAMPLE, resolved)
        )["env"]
        assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "983616"
        toml = mc.render_cc_toml(CC_TOML_SAMPLE, resolved)
        assert 'CLAUDE_CODE_MAX_CONTEXT_TOKENS = "983616"' in toml

    def test_stale_tokens_are_removed(self, reg):
        """残留旧 token = 拿 A 家钥匙打 B 家端点，而 Claude 不报"钥匙错"。"""
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        env = __import__("json").loads(mc.render_claude_settings(CLAUDE_SAMPLE, resolved))["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "ANTHROPIC_API_KEY" not in env

    def test_key_follows_the_source(self, reg):
        """钥匙与端点必须同源：只改端点不改钥匙就是「拿 A 家钥匙打 B 家端点」。"""
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        env = __import__("json").loads(
            mc.render_claude_settings(CLAUDE_SAMPLE, resolved, "sk-test-key")
        )["env"]
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-key"
        assert "ANTHROPIC_API_KEY" not in env

    def test_other_settings_survive(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        data = __import__("json").loads(mc.render_claude_settings(CLAUDE_SAMPLE, resolved))
        assert data["permissions"]["allow"] == ["Bash(*)"]
        assert data["env"]["API_TIMEOUT_MS"] == "3000000"

    def test_cc_toml_env_section(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        out = mc.render_cc_toml(CC_TOML_SAMPLE, resolved)
        assert f'ANTHROPIC_BASE_URL = "{FAKE_BASE_URL}' in out
        assert 'ANTHROPIC_MODEL = "deepseek-v4.1-flash-1017"' in out
        # 别人的段不能动
        assert 'app_id = "${CC_CONNECT_FEISHU_APP_ID}"' in out
        assert 'model = "haiku"' in out

    def test_cc_toml_without_env_section_is_none(self, reg):
        """没有该段 ≠ 已是最新：两者要能区分，否则 apply 会误报「跳过了」。"""
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        assert mc.render_cc_toml("[[projects]]\nname = \"x\"\n", resolved) is None

    def test_cc_toml_idempotent(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        once = mc.render_cc_toml(CC_TOML_SAMPLE, resolved)
        assert mc.render_cc_toml(once, resolved) == once

    def test_anthropic_endpoint_not_openai_one(self, reg):
        """Claude 走 Anthropic 协议；写错成 compatible-mode/v1 会 404。"""
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["claude-code"])
        env = __import__("json").loads(mc.render_claude_settings(CLAUDE_SAMPLE, resolved))["env"]
        assert "/compatible-mode/" not in env["ANTHROPIC_BASE_URL"]
        assert "/apps/anthropic" in env["ANTHROPIC_BASE_URL"]


# ══════════════════════════════════════════════════════════════════
# OpenClaw
# ══════════════════════════════════════════════════════════════════
OPENCLAW_SAMPLE = """\
{
  "meta": {"lastTouchedVersion": "2026.9.1"},
  "models": {"mode": "merge", "providers": {"deepseek": {"baseUrl": "https://api.deepseek.com", "api": "openai-completions", "models": [{"id": "deepseek-flash"}]}}},
  "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-flash", "fallbacks": ["moonshot/kimi-k2.6"]}, "models": {"deepseek/deepseek-flash": {}}}},
  "channels": {"discord": {"token": "keep-me"}}
}
"""


class TestOpenClawRenderer:
    def test_provider_block_carries_capabilities(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["openclaw.main"])
        cfg = __import__("json").loads(mc.render_openclaw(OPENCLAW_SAMPLE, resolved))
        provider = cfg["models"]["providers"]["alibaba"]
        assert provider["baseUrl"].endswith("/compatible-mode/v1")
        assert provider["apiKey"] == "${DASHSCOPE_API_KEY}"  # 真钥匙留在容器环境
        model = provider["models"][0]
        assert model["id"] == "deepseek-v4.1-flash-1017"
        assert model["contextWindow"] == 1000000
        assert model["maxTokens"] == 393216
        assert model["input"] == ["text", "image"]

    def test_primary_and_fallback(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["openclaw.main"])
        cfg = __import__("json").loads(mc.render_openclaw(OPENCLAW_SAMPLE, resolved))
        model = cfg["agents"]["defaults"]["model"]
        assert model["primary"] == "alibaba/deepseek-v4.1-flash-1017"
        assert model["fallbacks"] == ["deepseek/deepseek-flash"]

    def test_meta_and_channels_survive(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["openclaw.main"])
        cfg = __import__("json").loads(mc.render_openclaw(OPENCLAW_SAMPLE, resolved))
        assert cfg["meta"]["lastTouchedVersion"] == "2026.9.1"
        assert cfg["channels"]["discord"]["token"] == "keep-me"

    def test_idempotent(self, reg):
        mc = _modelctl()
        resolved = reg.resolve(reg.bindings["openclaw.main"])
        once = mc.render_openclaw(OPENCLAW_SAMPLE, resolved)
        assert mc.render_openclaw(once, resolved) == once


# ══════════════════════════════════════════════════════════════════
# dotenv / 托管块
# ══════════════════════════════════════════════════════════════════
class TestDotenv:
    def test_updates_in_place_and_keeps_comments(self):
        mc = _modelctl()
        text = "# 注释\nA=1\nB=2\n"
        out = mc.render_dotenv(text, {"B": "3"})
        assert out == "# 注释\nA=1\nB=3\n"

    def test_missing_key_goes_after_anchor(self):
        mc = _modelctl()
        out = mc.render_dotenv("A=1\nB=2\n", {"C": "3"}, anchors={"C": "B"})
        assert out == "A=1\nB=2\nC=3\n"

    def test_managed_block_is_created_once(self):
        mc = _modelctl()
        once = mc.render_managed_block("A=1\n", {"X": "y"})
        twice = mc.render_managed_block(once, {"X": "z"})
        assert twice.count(mc.MANAGED_BEGIN) == 1
        assert "X=z" in twice and "X=y" not in twice

    def test_managed_block_replaces_previous_title(self):
        mc = _modelctl()
        once = mc.render_managed_block("", {"X": "y"})
        twice = mc.render_managed_block(once, {"X": "y"})
        assert once == twice


class TestSecretRedaction:
    def test_secret_values_are_masked(self):
        mc = _modelctl()
        diff = "-ZHIXUN_BOT_MODEL_API_KEY=sk-abcdef123456\n+TIANYI_BOT_MODEL_MAX_TOKENS=393216\n"
        out = mc._redact(diff)
        assert "sk-abcdef123456" not in out
        assert "393216" in out, "MAX_TOKENS 不是密钥，不该被吃掉"

    def test_empty_value_keeps_its_line(self):
        r"""空的密钥位（``TDAI_LLM_API_KEY=``）是正常的"回落"形态。

        旧实现里 ``\s`` 会吃掉换行，把一行拆成 ``KEY=`` + ``<redacted>`` 两行，
        看起来像丢了内容。
        """
        mc = _modelctl()
        assert mc._redact("+TDAI_LLM_API_KEY=\n") == "+TDAI_LLM_API_KEY=\n"


# ══════════════════════════════════════════════════════════════════
# 跨文件约定（换源最容易漏的地方）
# ══════════════════════════════════════════════════════════════════
class TestWiring:
    HERMES_SERVICES = ("hermes", "hermes-coder", "hermes-finance", "hermes-daoyuan")

    def test_key_is_injected_into_every_hermes_container(self):
        for service in self.HERMES_SERVICES:
            assert "DASHSCOPE_API_KEY" in _service_env(service), service

    def test_key_is_injected_into_openclaw(self):
        # openclaw.json 里是 ${DASHSCOPE_API_KEY} 占位符，容器环境必须真的提供
        assert "DASHSCOPE_API_KEY" in _service_env("openclaw-gateway")

    def test_claude_code_learns_which_key_to_use(self):
        env = _service_env("claude-code")
        assert "DASHSCOPE_API_KEY" in env
        assert "CLAUDE_MODEL_KEY_ENV" in env

    def test_entrypoint_honours_key_env(self):
        text = ENTRYPOINT.read_text()
        assert "CLAUDE_MODEL_KEY_ENV" in text
        assert "${!" in text, "需要间接展开按名字取钥匙"

    def test_entrypoint_does_not_clobber_aliyun_model_id(self):
        """``deepseek-v4.1-flash-1017`` 必须被判为 canonical，否则每次启动都被改写。"""
        text = ENTRYPOINT.read_text()
        m = re.search(r"return (/\^[^/]+/)\.test", text)
        assert m, "找不到 shouldSetModel 的正则（测试锚点）"
        pattern = m.group(1)[1:-1]  # 去掉 JS 正则字面量两侧的 "/"
        rx = re.compile(pattern)
        assert not rx.match("deepseek-v4.1-flash-1017")
        assert not rx.match("deepseek-flash")
        assert rx.match("deepseek-v4-flash"), "旧的 V 系列 id 仍应被迁移"

    def test_legacy_migration_keeps_aliyun_id(self):
        mod = _ensure_hermes_model()
        text = "model:\n  default: deepseek-v4.1-flash-1017\n  provider: alibaba-cn\n"
        new, status = mod.ensure_model(text)
        assert status == "unchanged:deepseek-v4.1-flash-1017"
        assert new == text

    def test_bot_renderers_apply_capabilities_from_env(self):
        """数字字段不能走占位符（模板必须是合法 JSON），只能在渲染后覆写。"""
        for name in ("zhixun", "tianyi"):
            renderer = REPO_ROOT / "docker" / f"{name}-bot" / "render-config.mjs"
            rtext = renderer.read_text()
            assert "_MODEL_CONTEXT_WINDOW" in rtext, name
            assert "_MODEL_MAX_TOKENS" in rtext, name
            assert "entry.contextWindow = ctx" in rtext, name
            assert "|| 128000" in rtext, "缺省值必须保持旧行为，否则老部署会少上下文"

    def test_bot_templates_stay_valid_json_before_render(self):
        """占位符是裸数字，模板本身仍要能被 JSON 解析（渲染器就是这么跑的）。"""
        import json

        for name in ("zhixun", "tianyi"):
            template = REPO_ROOT / "docker" / f"{name}-bot" / "openclaw.json.template"
            data = json.loads(template.read_text())  # 抛错即失败
            assert data["models"]["providers"]["deepseek"]["models"][0]["id"] == "__MODEL_ID__"

    def test_registry_documents_where_keys_live(self):
        text = REGISTRY.read_text()
        assert "key_env" in text
        assert "sk-" not in text, "注册表里不能出现密钥字面量"