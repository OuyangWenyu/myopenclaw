"""Hermes 兜底模型与 zai 端点守卫。

2026-10-15 定案（Owen）：三个有兜底的 profile 统一 ``zai / glm-5.3-flash``（GLM 性价比），
弃用爱玛士的 kimi-k2.6（Moonshot kimi-k2.5 静默下线同款风险面，见
memory: shrimp-kimi-fallback-fix-2026-10-15）。

三条断言各对应一个实测踩过的坑：

1. **模型 id 必须是 glm-5.3-flash**（start.sh 首建模板 + live 三份 config）——
   新部署拿到旧 glm-5.1 不会报错，只会静默用旧档。
2. **必须配 agent.reasoning_overrides: glm-5.3-flash: low**——glm-5.3-flash 强制思考，
   hermes 全局 reasoningDefault=off 会发 disable 被智谱 400
   （"This model always engages in thinking and cannot be disabled"）；
   low 是兜底场景的性价比档，且国内端点词表只有 low/high/max（没有 medium）。
3. **zai 端点必须是国内 open.bigmodel.cn**——2026-10-15 实测：auth add 后
   detect_zai_endpoint 的探测缓存会落 api.z.ai（国外 Global 端点），
   同一把 key 在 coder 池却缓存成 cn；hosts 黑洞 api.z.ai 后调用仍成功才算钉住。

Run: uv run --with pytest --with pyyaml pytest tests/test_hermes_fallback_glm.py -v
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
START_SH = REPO_ROOT / "scripts" / "start.sh"

HOME = Path.home()
LIVE_CONFIGS = {
    "default": HOME / ".hermes" / "config.yaml",
    "coder": HOME / ".hermes" / "profiles" / "coder" / "config.yaml",
    "daoyuan": HOME / ".hermes" / "profiles" / "daoyuan" / "config.yaml",
}
LIVE_AUTH_STORES = {
    "default": HOME / ".hermes" / "auth.json",
    "daoyuan": HOME / ".hermes" / "profiles" / "daoyuan" / "auth.json",
    "coder": HOME / ".hermes" / "profiles" / "coder" / "auth.json",
}

EXPECTED_MODEL = "glm-5.3-flash"
EXPECTED_PROVIDER = "zai"
EXPECTED_EFFORT = "low"
CN_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


def _heredoc_yaml(var: str) -> dict:
    """取出 start.sh 中写入 ``${<var>}`` 的 heredoc YAML（首次创建模板）。"""
    m = re.search(
        rf'cat > "\$\{{{var}\}}" << \'YAML\'\n(.*?)\nYAML', START_SH.read_text(), re.S
    )
    assert m, f"start.sh 中未能定位 ${{{var}}} 的 heredoc"
    return yaml.safe_load(m.group(1))


def _assert_fallback_block(cfg: dict, where: str) -> None:
    fb = cfg.get("fallback_model")
    assert fb == {"provider": EXPECTED_PROVIDER, "model": EXPECTED_MODEL}, (
        f"{where}: fallback_model 应为 {EXPECTED_PROVIDER}/{EXPECTED_MODEL}，实为 {fb}"
    )
    # 独立断言：错误信息区分「模型错」和「provider 错」
    assert fb.get("model") == EXPECTED_MODEL, f"{where}: 兜底模型 id 不对: {fb}"
    assert fb.get("provider") == EXPECTED_PROVIDER, f"{where}: 兜底 provider 不对: {fb}"


def _assert_reasoning_override(cfg: dict, where: str) -> None:
    ro = ((cfg.get("agent") or {}).get("reasoning_overrides")) or {}
    assert ro.get(EXPECTED_MODEL) == EXPECTED_EFFORT, (
        f"{where}: agent.reasoning_overrides 需要 {{{EXPECTED_MODEL}: {EXPECTED_EFFORT}}}，"
        f"实为 {ro}（缺了它 glm-5.3-flash 会因 thinking=off 被智谱 400）"
    )


class TestStartShTemplates:
    """start.sh 首建模板 —— 新机唯一来源，漏改则新部署拿到旧档。"""

    def test_coder_heredoc_fallback(self):
        cfg = _heredoc_yaml("CODER_CONFIG")
        _assert_fallback_block(cfg, "start.sh CODER_CONFIG")
        _assert_reasoning_override(cfg, "start.sh CODER_CONFIG")

    def test_daoyuan_heredoc_fallback(self):
        cfg = _heredoc_yaml("DAOYUAN_CONFIG")
        _assert_fallback_block(cfg, "start.sh DAOYUAN_CONFIG")
        _assert_reasoning_override(cfg, "start.sh DAOYUAN_CONFIG")


class TestLiveConfigs:
    """live 三份 config —— 只断言存在该文件的机器（换机/未部署时跳过）。"""

    @pytest.mark.parametrize("profile", sorted(LIVE_CONFIGS))
    def test_fallback_and_override(self, profile: str):
        path = LIVE_CONFIGS[profile]
        if not path.exists():
            pytest.skip(f"{path} 不存在（未部署 Hermes）")
        cfg = yaml.safe_load(path.read_text())
        _assert_fallback_block(cfg, str(path))
        _assert_reasoning_override(cfg, str(path))


class TestLiveZaiEndpointCn:
    """zai 凭据端点钉国内 —— 探测缓存曾漂 api.z.ai（2026-10-15 实测）。"""

    @pytest.mark.parametrize("store", sorted(LIVE_AUTH_STORES))
    def test_endpoints_are_cn(self, store: str):
        path = LIVE_AUTH_STORES[store]
        if not path.exists():
            pytest.skip(f"{path} 不存在（未部署 Hermes）")
        data = json.loads(path.read_text())
        for entry in (data.get("credential_pool") or {}).get("zai") or []:
            assert entry.get("base_url") == CN_BASE_URL, (
                f"{path}: zai 池条目 {entry.get('id')} 端点应为国内，实为 {entry.get('base_url')}"
            )
        state = ((data.get("providers") or {}).get("zai") or {}).get("detected_endpoint")
        if state is not None:
            assert state.get("base_url") == CN_BASE_URL, (
                f"{path}: zai 探测缓存应为国内，实为 {state.get('base_url')}"
            )
