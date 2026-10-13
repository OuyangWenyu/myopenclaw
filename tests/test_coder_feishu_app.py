"""compose 守卫：hermes-coder 的飞书映射必须指向它自己的应用（爱码士），绝不许换成默认（爱玛士）app。

历史（2026-10-12 事故）：hermes-coder 服务的 environment 里带着
`FEISHU_APP_ID=${FEISHU_APP_ID:-}`（默认 app = 爱玛士）——叠加 coder profile
配置 `platforms.feishu.enabled` 漂移为 true，coder 容器长期挂着**第二个
爱玛士连接**。飞书把 DM 分给多条连接时，落到 coder 的轮次读不了/发不出
邮箱（用户症状："时好时坏，之前还能转发"）。当日处置 = 摘除 coder 的
全部飞书注入（止血）；副作用是 coder 从飞书彻底消失（2026-10-13 用户报
「飞书上跟他聊没反应」才浮现）。

2026-10-13 正式接线：coder 接入自己的独立飞书应用（爱码士 bot）——
compose 把 `CODER_FEISHU_APP_ID/SECRET` 映射为容器内 `FEISHU_APP_ID/SECRET`。
本测试钉住三条不变量：
① coder 必须映射 CODER_FEISHU_*（防止未来"清理过度"再次摘除）；
② coder 绝不许引用默认 app 的 `${FEISHU_APP_ID…}`（事故不复发）；
③ 正例对照 —— hermes 映射默认 app、daoyuan/finance 映射各自的 app。

Run: uv run --with pytest --with pyyaml pytest tests/test_coder_feishu_app.py -v
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


class TestCoderMapsItsOwnFeishuApp:
    def test_maps_coder_app_id(self, services: dict):
        env = _env(services, "hermes-coder")
        assert "FEISHU_APP_ID=${CODER_FEISHU_APP_ID:-}" in env, (
            "coder 必须映射自己的 CODER_FEISHU_APP_ID（2026-10-13 接线；"
            "摘除会让爱码士从飞书消失）"
        )

    def test_maps_coder_app_secret(self, services: dict):
        env = _env(services, "hermes-coder")
        assert "FEISHU_APP_SECRET=${CODER_FEISHU_APP_SECRET:-}" in env, (
            "coder 必须映射自己的 CODER_FEISHU_APP_SECRET"
        )

    def test_websocket_mode(self, services: dict):
        env = _env(services, "hermes-coder")
        assert "FEISHU_CONNECTION_MODE=websocket" in env
        assert "FEISHU_DOMAIN=feishu" in env

    def test_never_maps_default_app(self, services: dict):
        bad = [
            e for e in _env(services, "hermes-coder")
            if "${FEISHU_APP_ID" in e or "${FEISHU_APP_SECRET" in e
        ]
        assert not bad, (
            f"hermes-coder 引用了默认 app（爱玛士）凭据 {bad} —— 会让他成为"
            "「第二爱玛士」抢 DM（2026-10-12 事故），不得恢复"
        )


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
