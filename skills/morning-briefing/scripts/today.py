#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""权威日期来源（北京时间 CST/UTC+8）。

用途：晨间简报等 cron 任务必须先跑这个脚本确定"今天"，
禁止凭模型直觉推算星期。容器时区是 UTC，而用户按北京时间作息，
23:30 UTC 触发时北京时间已经是次日上午——自己推算必然出错。

用法:
    python3 today.py            # 人类可读一行
    python3 today.py --json     # 机器可读
    python3 today.py --date     # 只要 YYYY-MM-DD
    python3 today.py --check-briefing-day   # 休闲日（周日）exit 1，简报日（周一至周六）exit 0

推送口径（2026-10-17 起）：周一至周六发晨间简报（周六汇总周五信息），
仅周日休息。`is_weekend` 只作事实字段（周六/周日均 true），
是否发简报以 `is_briefing_day` 为准。
"""
import json
import sys
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
WEEK = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def now_cst() -> datetime:
    return datetime.now(CST)


def build_payload(n: datetime) -> dict:
    """把北京时间 datetime 格式化成 cron/agent 消费的日期载荷。"""
    dow = n.isoweekday()          # 1=周一 ... 7=周日
    return {
        "date": n.strftime("%Y-%m-%d"),
        "date_cn": f"{n.year}年{n.month}月{n.day}日",
        "weekday": WEEK[dow - 1],
        "isoweekday": dow,
        "is_weekend": dow >= 6,
        # 简报日 = 周一至周六；仅周日休息（2026-10-17 产品口径）
        "is_briefing_day": dow <= 6,
        "time_cst": n.strftime("%Y-%m-%d %H:%M:%S%z"),
        "time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z"),
    }


def main(argv=None) -> int:
    n = now_cst()
    payload = build_payload(n)
    args = sys.argv[1:] if argv is None else argv
    if "--date" in args:
        print(payload["date"])
        return 0
    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if "--check-briefing-day" in args:
        return 0 if payload["is_briefing_day"] else 1
    print(f"{payload['date_cn']} {payload['weekday']}（北京时间 {payload['time_cst']}｜"
          f"容器 UTC {payload['time_utc']}）"
          + ("  ← 简报日" if payload["is_briefing_day"] else "  ← 休息日（周日不发简报）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
