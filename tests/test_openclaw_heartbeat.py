"""虾酱 heartbeat 周期推送守卫。

2026-10-19 定案（Owen）：heartbeat 每 30 分钟往 Discord 私聊推一次检查结果，
过于频繁；系统本身无异常，不需要持续推送。定案的关法是
``agents.defaults.heartbeat.every: "0m"`` —— 只关闭周期心跳，事件驱动的
heartbeat（如后台 exec 完成唤醒）仍可用。

两条断言各对应一个失效面：

1. **模板**（``openclaw.json.example``，新部署的唯一来源）必须是 ``"0m"`` ——
   漏改则新机器重新开始 30 分钟一次推送。
2. **live 配置**（本机 ``~/.openclaw/openclaw.json``，存在才断言）同样必须是
   ``"0m"`` —— 防止手工编辑或配置回滚把它改回默认值。

Run: uv run --with pytest pytest tests/test_openclaw_heartbeat.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "openclaw" / "config" / "openclaw.json.example"
LIVE_CONFIG = Path.home() / ".openclaw" / "openclaw.json"

DISABLED_EVERY = "0m"


def _heartbeat_every(path: Path) -> str | None:
    cfg = json.loads(path.read_text())
    defaults = ((cfg.get("agents") or {}).get("defaults") or {})
    return (defaults.get("heartbeat") or {}).get("every")


class TestTemplate:
    """新部署模板 —— 漏改则新机默认恢复 30 分钟一次 Discord 推送。"""

    def test_heartbeat_cadence_disabled(self):
        assert _heartbeat_every(TEMPLATE) == DISABLED_EVERY, (
            f"{TEMPLATE}: agents.defaults.heartbeat.every 应为 {DISABLED_EVERY!r}"
            "（30 分钟一次的周期心跳会持续往 Discord 推消息）"
        )


class TestLiveConfig:
    """本机 live 配置 —— 存在才断言（换机/未部署时跳过）。"""

    def test_heartbeat_cadence_disabled(self):
        if not LIVE_CONFIG.exists():
            pytest.skip(f"{LIVE_CONFIG} 不存在（未部署 OpenClaw）")
        assert _heartbeat_every(LIVE_CONFIG) == DISABLED_EVERY, (
            f"{LIVE_CONFIG}: agents.defaults.heartbeat.every 应为 {DISABLED_EVERY!r}"
            "（容器内 config patch 修改，勿在宿主机直接编辑）"
        )
