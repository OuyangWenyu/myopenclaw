"""compose 守卫：hermes-coder 不得注入默认（爱玛士）app 的飞书凭据。

事故背景（2026-10-12）：hermes-coder 服务的 environment 里带着
`FEISHU_APP_ID=${FEISHU_APP_ID:-}`（默认 app = 爱玛士）——叠加 coder profile
配置 `platforms.feishu.enabled` 漂移为 true，coder 容器长期挂着**第二个
爱玛士连接**。而受限容器的 himalaya/ortie 是拒绝桩、邮箱目录是空卷 ——
飞书把爱玛士 DM 随机分给多条连接时，落到 coder 的轮次读不了/发不出邮箱
（用户症状："时好时坏，之前还能转发"）。

本测试钉住接线：coder 服务不得出现任何 FEISHU_* 注入；
同时正例对照 —— hermes（默认）映射 FEISHU_* 、daoyuan/finance 映射各自的
DAOYUAN_/FINANCE_ app（防止未来"清理过度"误删正确映射）。

Run: uv run --with pytest --with pyyaml pytest tests/test_coder_no_feishu.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def _env(services: dict, name: str) -> list[str]:
    return [e for e in (services[name].get("environment") or []) if isinstance(e, str)]


class TestCoderHasNoDefaultFeishu:
    def test_no_feishu_app_id(self, services: dict):
        bad = [e for e in _env(services, "hermes-coder") if "FEISHU_APP_ID" in e]
        assert not bad, (
            f"hermes-coder 注入了飞书 app 凭据 {bad} —— 默认 app 会让她成为"
            "「第二爱玛士」抢 DM（2026-10-12 事故），不得恢复"
        )

    def test_no_feishu_env_at_all(self, services: dict):
        bad = [e for e in _env(services, "hermes-coder") if e.startswith("FEISHU_")]
        assert bad == [], f"coder 服务残留 FEISHU_* 注入: {bad}"


class TestOtherServiceMappingsStayCorrect:
    def test_default_service_maps_default_app(self, services: dict):
        env = _env(services, "hermes")
        assert "FEISHU_APP_ID=${FEISHU_APP_ID:-}" in env, "默认服务的爱玛士映射被误删"

    @pytest.mark.parametrize(
        ("svc", "var"),
        [
            ("hermes-daoyuan", "DAOYUAN_FEISHU_APP_ID"),
            ("hermes-finance", "FINANCE_FEISHU_APP_ID"),
        ],
    )
    def test_restricted_profiles_map_their_own_app(self, services: dict, svc: str, var: str):
        env = _env(services, svc)
        assert any(var in e for e in env), f"{svc} 应映射自己的 app ({var})"
