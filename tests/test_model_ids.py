"""Static guards for DeepSeek model id hygiene across the repo.

Run: uv run --with pytest --with pyyaml pytest tests/test_model_ids.py -v

DeepSeek 于 2026-09-10 发布 V4.1 Flash，canonical id 为 ``deepseek-flash``。
下列 id 已下线（前两者由服务端暂路由到 V4.1 Flash、按 Flash 计费，但不应对其产生新依赖）：

    deepseek-v4-flash                已下线，暂路由
    deepseek-v4-flash-vision-exp     已下线，暂路由
    deepseek-v4-pro                  2026-09-14 12:00(北京) 起路由到 V4.1 Flash

``deepseek-chat`` **不在**下线名单 —— 它是另一个模型（V3），TDAI 记忆层与 OpenClaw
的 provider 列表仍在有意使用，批量替换时不要误伤（见 TestDeepseekChatUntouched）。

另外守两条改造中容易误删的东西：虾酱的 TTS 仍走 xiaomi，而它的端点定义藏在
``models.providers.xiaomi`` 里（``tts.providers.xiaomi`` 块内没有 baseUrl；该块在
2.0 里从 ``messages.tts`` 迁到了顶层 ``tts``）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

OPENCLAW_EXAMPLE = REPO_ROOT / "openclaw" / "config" / "openclaw.json.example"
CC_ENTRYPOINT = REPO_ROOT / "docker" / "claude-code" / "entrypoint.sh"
CC_SETTINGS_EXAMPLE = REPO_ROOT / "claude" / "config" / "settings.json.example"
COMPOSE = REPO_ROOT / "docker-compose.yml"
START_SH = REPO_ROOT / "scripts" / "start.sh"


def _service_env(service: str) -> dict[str, str]:
    """解析 compose 中某服务的 environment，列表与映射两种写法都支持。"""
    data = yaml.safe_load(COMPOSE.read_text())
    env = data["services"][service].get("environment", [])
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    return dict(item.split("=", 1) for item in env if "=" in str(item))


def _start_sh_heredoc_yaml(var: str) -> dict:
    """取出 ``scripts/start.sh`` 中写入 ``${<var>}`` 的那段 heredoc YAML。

    profile 配置的**首次生成**模板在这里；若它回退到旧模型，新建/恢复出来的
    主机就会拿到旧模型，而这条路径不经过任何其它文件。
    """
    m = re.search(rf'cat > "\$\{{{var}\}}" << \'YAML\'\n(.*?)\nYAML', START_SH.read_text(), re.S)
    assert m, f"未能在 scripts/start.sh 中定位 ${{{var}}} 的 heredoc"
    return yaml.safe_load(m.group(1))

# 已下线的 id 片段。``deepseek-v4-flash`` 同时覆盖 ``deepseek-v4-flash-vision-exp``，
# 后者另用 ``vision-exp`` 兜一遍。
RETIRED_IDS = ("deepseek-v4-flash", "deepseek-v4-pro", "vision-exp")

# 生成物 / 缓存 / 第三方目录不参与文本扫描
SKIP_DIRS = {
    ".git",
    "site",  # MkDocs 构建产物（gitignored），由 docs/ 重新生成
    "tests",  # 测试套件正是「引用下线 id 作为断言数据与证据」的地方，不随镜像发布
    "__pycache__",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".codegraph",
    "logs",
    "node_modules",
}


def _iter_repo_files():
    """仓库内所有参与扫描的文本文件（跳过生成物与本测试自身）。"""
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.resolve() == SELF:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        yield path


def _scan_retired_ids() -> list[str]:
    """扫描结果只报 ``文件:行号 [命中的 id]``，**不回显该行内容**。

    扫描范围包含 gitignored 的 ``.env*`` / ``.cloud.conf``（它们同样需要守卫），
    而这些文件含真实凭据 —— 一旦某行同时含密钥与下线 id，回显整行会把密钥打进
    测试日志/CI 输出。定位到行号即可，内容由人去文件里看。
    """
    hits: list[str] = []
    for path in _iter_repo_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # 二进制文件（sqlite / tgz）不参与
        for lineno, line in enumerate(text.splitlines(), 1):
            matched = next((rid for rid in RETIRED_IDS if rid in line), None)
            if matched:
                rel = path.relative_to(REPO_ROOT)
                hits.append(f"{rel}:{lineno} [{matched}]")
    return hits


class TestNoRetiredModelIds:
    """仓库里不应再出现已下线的 model id（issue #71 验收项之一）。

    ⚠️ 扫描范围**包含** gitignored 的 ``.env*`` / ``.cloud.conf``（它们同样需要守卫，
    例如 ``.env.zhixun-bot`` 里的 ``*_MODEL_ID``），因此红绿取决于本地未跟踪文件的状态
    —— 这**不能**当作「仓库纯净性」的 CI 检查用，它是本机部署的一致性守卫。
    """

    def test_no_retired_model_ids_anywhere(self):
        hits = _scan_retired_ids()
        assert not hits, (
            f"发现 {len(hits)} 处已下线的 model id，应统一为 deepseek-flash：\n"
            + "\n".join(hits)
        )


class TestAgentsDeclareCanonicalModel:
    """三个 agent 的主模型必须落在 canonical id 上。"""

    def test_openclaw_primary_is_canonical(self):
        cfg = json.loads(OPENCLAW_EXAMPLE.read_text())
        primary = cfg["agents"]["defaults"]["model"]["primary"]
        assert primary == "deepseek/deepseek-flash", (
            f"虾酱主模型应为 deepseek/deepseek-flash，实际 {primary}"
        )

    def test_openclaw_deepseek_provider_metadata_is_correct(self):
        """provider 里的模型元数据必须反映 V4.1 Flash 的真实能力。

        改造前这两个字段描述的是旧认知（纯文本 / 128K），照抄会让虾酱丢掉
        图片输入并把上下文从 1M 缩到 128K。
        """
        cfg = json.loads(OPENCLAW_EXAMPLE.read_text())
        models = {m["id"]: m for m in cfg["models"]["providers"]["deepseek"]["models"]}
        assert "deepseek-flash" in models, "deepseek provider 未声明 deepseek-flash"
        entry = models["deepseek-flash"]
        assert "image" in entry["input"], "V4.1 Flash 原生多模态，input 必须含 image"
        assert entry["contextWindow"] == 1000000, "V4.1 Flash 上下文为 1M"

    def test_claude_code_tiers_map_to_canonical(self):
        text = CC_ENTRYPOINT.read_text()
        keys = (
            "ANTHROPIC_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_FABLE_MODEL",
        )
        block = re.search(r"const TIER_MODELS = \{(.*?)\n\};", text, re.S)
        assert block, "entrypoint 缺少 TIER_MODELS 表（本测试的解析锚点）"
        tiers = dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))
        for key in keys:
            assert tiers.get(key) in ("deepseek-flash", "deepseek-flash[1M]"), (
                f"{key} 应为 deepseek-flash 系，实际 {tiers.get(key)!r}"
            )

        settings = json.loads(CC_SETTINGS_EXAMPLE.read_text())
        env = settings.get("env", {})
        for key in keys:
            assert env.get(key) in ("deepseek-flash", "deepseek-flash[1M]"), (
                f"settings.json.example 的 {key} 未同步，实际 {env.get(key)!r}"
            )

    def test_bot_model_defaults_are_canonical(self):
        # 用锚定的精确匹配：宽松的 `.*?deepseek-flash` 会被同行注释满足
        # （`VAR=deepseek-v4-pro  # TODO 改成 deepseek-flash` 也算通过）。
        for path, var in (
            (REPO_ROOT / "docker-compose.zhixun-bot.yml", "ZHIXUN_BOT_MODEL_ID"),
            (REPO_ROOT / "docker-compose.tianyi-bot.yml", "TIANYI_BOT_MODEL_ID"),
        ):
            assert re.search(rf"{var}:\s*\$\{{{var}:-deepseek-flash\}}", path.read_text()), (
                f"{path.name} 的 {var} 默认值不是 deepseek-flash"
            )
        for path, var in (
            (REPO_ROOT / ".env.zhixun-bot.example", "ZHIXUN_BOT_MODEL_ID"),
            (REPO_ROOT / ".env.tianyi-bot.example", "TIANYI_BOT_MODEL_ID"),
        ):
            assert re.search(rf"^{var}=deepseek-flash$", path.read_text(), re.M), (
                f"{path.name} 的 {var} 未精确指向 deepseek-flash"
            )

    def test_start_sh_profile_templates_use_canonical_model(self):
        """爱玛士/爱码士 的模型定义也在这里。

        它们的 profile 配置是宿主机的**手工文件**，仓库里唯一描述它们默认值的
        地方就是 `scripts/start.sh` 的两个 heredoc —— 而这段代码只在文件不存在时
        生效，回退成旧模型不会有任何运行时报错，只会让新建 or 从快照恢复的主机
        悄悄跑在旧模型上。本测试守住这条不经过 openclaw / claude-code 的路径。
        """
        for var, label in (("CODER_CONFIG", "爱码士 coder"), ("DAOYUAN_CONFIG", "道元 daoyuan")):
            cfg = _start_sh_heredoc_yaml(var)
            assert cfg["model"]["default"] == "deepseek-flash", (
                f"start.sh 的 {label} 模板写的是 {cfg['model']['default']}"
            )
            assert cfg["model"]["provider"] == "deepseek"


class TestXiaomiTtsRetained:
    """虾酱的语音回复仍走 xiaomi —— 切主模型不得顺手删掉小米。"""

    def test_tts_still_uses_xiaomi(self):
        # 2.0 把 `messages.tts` 重命名为**顶层** `tts`（旧写法在新版判为 invalid）。
        cfg = json.loads(OPENCLAW_EXAMPLE.read_text())
        assert "messages" not in cfg, "messages 段已被 2.0 废弃"
        tts = cfg["tts"]
        assert tts["provider"] == "xiaomi"
        assert tts["providers"]["xiaomi"]["model"] == "mimo-v2.5-tts"

    def test_xiaomi_provider_block_retained(self):
        # TTS 块内没有 baseUrl，端点定义只存在于这里；删掉会连带破坏 TTS
        cfg = json.loads(OPENCLAW_EXAMPLE.read_text())
        xiaomi = cfg["models"]["providers"]["xiaomi"]
        assert xiaomi["baseUrl"] == "https://api.xiaomimimo.com/v1"
        assert "mimo-v2.5" in [m["id"] for m in xiaomi["models"]]

    def test_xiaomi_key_still_injected_into_its_only_consumer(self):
        """注入必须落在**真正消费它的服务**上。

        只断言"compose 里出现过 XIAOMI_API_KEY"会被假绿骗过：删掉 openclaw-gateway
        （虾酱 TTS 的唯一消费方）那一行后，字符串仍留在 hermes / hermes-coder
        两处「当前无消费方」的注入上，测试照样通过。
        """
        assert "XIAOMI_API_KEY" in _service_env("openclaw-gateway")


class TestLegacyModelMigration:
    """``scripts/ensure_hermes_model.py`` —— 既有机器的主模型迁移。

    heredoc 只在文件不存在时生效，所以「已在运行的机器」与「从快照恢复的机器」
    得靠这个幂等迁移，否则会静默留在旧模型上。
    """

    @staticmethod
    def _mod():
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ensure_hermes_model", REPO_ROOT / "scripts" / "ensure_hermes_model.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_known_legacy_values_are_migrated(self):
        mod = self._mod()
        for legacy in ("mimo-v2.5", "mimo-v2.5-pro", "deepseek-v4-flash", "deepseek-v4-pro"):
            text = f"model:\n  default: {legacy}\n  provider: xiaomi\nterminal:\n  backend: local\n"
            new, status = mod.ensure_model(text)
            assert status == "written", f"{legacy} 未被识别为历史取值"
            assert "default: deepseek-flash" in new
            assert "provider: deepseek" in new
            assert "base_url: https://api.deepseek.com" in new

    def test_operator_choice_is_not_overwritten(self):
        mod = self._mod()
        text = "model:\n  default: claude-opus-4.6\n  provider: anthropic\n"
        _, status = mod.ensure_model(text)
        assert status == "unchanged:claude-opus-4.6"

    def test_migration_is_idempotent(self):
        mod = self._mod()
        once, status1 = mod.ensure_model("model:\n  default: mimo-v2.5\n")
        twice, status2 = mod.ensure_model(once)
        assert status1 == "written"
        assert status2 == "unchanged:deepseek-flash"
        assert twice == once

    def test_comment_and_key_order_preserved(self):
        mod = self._mod()
        text = (
            "model:\n"
            "  # 主模型（手工维护）\n"
            "  default: mimo-v2.5\n"
            "  provider: xiaomi\n"
            "  base_url: https://api.xiaomimimo.com/v1\n"
            "fallback_providers:\n"
            "  - zai\n"
        )
        new, _ = mod.ensure_model(text)
        assert "  # 主模型（手工维护）" in new, "注释被破坏"
        assert "fallback_providers:" in new, "块外的键被吞掉"
        assert "https://api.xiaomimimo.com" not in new, "旧 base_url 未迁移"
        # 键序不变：default 仍在 provider 之前
        assert new.index("default:") < new.index("provider:")


class TestDeepseekChatUntouched:
    """``deepseek-chat`` 不在下线名单，批量替换不应波及它。"""

    def test_tdai_default_still_deepseek_chat(self):
        assert "TDAI_LLM_MODEL:-deepseek-chat" in COMPOSE.read_text()

    def test_openclaw_keeps_deepseek_chat_option(self):
        cfg = json.loads(OPENCLAW_EXAMPLE.read_text())
        ids = [m["id"] for m in cfg["models"]["providers"]["deepseek"]["models"]]
        assert "deepseek-chat" in ids
