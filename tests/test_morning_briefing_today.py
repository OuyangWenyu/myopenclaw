"""晨间简报日期闸门单测（2026-10-17 产品口径）。

推送日 = 北京时间**周一至周六**（周六汇总周五信息），仅**周日**休息。
背景：10-13 事故后 cron 表达式从 `0-5` 缩到 `0-4`（周六不跑）；
2026-10-17 Owen 确认产品行为应为周六也推送 → 表达式恢复 `0-5`，
且 skill 的日期闸门必须同步（否则周六会被脚本自己判成周末拒发）。

本测试钉住 `skills/morning-briefing/scripts/today.py` 的纯函数语义：
- is_briefing_day: 周一至周六 true，周日 false
- is_weekend 保留为事实字段（周六/周日均 true），不承载闸门语义
- --check-briefing-day: 简报日 exit 0，休息日 exit 1

Run: uv run --with pytest pytest tests/test_morning_briefing_today.py -v
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TODAY_PY = REPO_ROOT / "skills" / "morning-briefing" / "scripts" / "today.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("morning_briefing_today", TODAY_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload_for(y: int, m: int, d: int) -> dict:
    module = _load_module()
    dt = datetime(y, m, d, 7, 30, tzinfo=timezone(timedelta(hours=8)))
    return module.build_payload(dt)


class TestBriefingDayGate:
    def test_saturday_is_a_briefing_day(self):
        # 2026-10-17 是周六：仍是简报日（周五信息）
        payload = _payload_for(2026, 10, 17)
        assert payload["isoweekday"] == 6
        assert payload["is_weekend"] is True, "is_weekend 仍应如实反映周六=周末"
        assert payload["is_briefing_day"] is True, (
            "周六必须是简报日 —— 否则 cron 触发了也会被脚本拒发"
        )

    def test_sunday_is_the_only_rest_day(self):
        payload = _payload_for(2026, 10, 18)
        assert payload["isoweekday"] == 7
        assert payload["is_briefing_day"] is False, "周日是唯一不推送的日子"

    def test_weekdays_are_briefing_days(self):
        for d in range(12, 17):  # 2026-10-12(周一) .. 10-16(周五)
            payload = _payload_for(2026, 10, d)
            assert payload["is_briefing_day"] is True, f"10-{d} 是工作日，必须是简报日"


class TestCheckFlagExitCodes:
    def test_saturday_exits_zero(self, monkeypatch):
        module = _load_module()
        monkeypatch.setattr(
            module, "now_cst",
            lambda: datetime(2026, 10, 17, 7, 30, tzinfo=timezone(timedelta(hours=8))),
        )
        assert module.main(["--check-briefing-day"]) == 0

    def test_sunday_exits_one(self, monkeypatch):
        module = _load_module()
        monkeypatch.setattr(
            module, "now_cst",
            lambda: datetime(2026, 10, 18, 7, 30, tzinfo=timezone(timedelta(hours=8))),
        )
        assert module.main(["--check-briefing-day"]) == 1
