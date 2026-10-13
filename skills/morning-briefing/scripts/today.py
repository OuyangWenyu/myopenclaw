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
    python3 today.py --check-weekend   # 周末 exit 1，工作日 exit 0（用于网关判断）
"""
import json
import sys
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
WEEK = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def now_cst() -> datetime:
    return datetime.now(CST)


def main() -> int:
    n = now_cst()
    dow = n.isoweekday()          # 1=周一 ... 7=周日
    payload = {
        "date": n.strftime("%Y-%m-%d"),
        "date_cn": f"{n.year}年{n.month}月{n.day}日",
        "weekday": WEEK[dow - 1],
        "isoweekday": dow,
        "is_weekend": dow >= 6,
        "time_cst": n.strftime("%Y-%m-%d %H:%M:%S%z"),
        "time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z"),
    }
    args = sys.argv[1:]
    if "--date" in args:
        print(payload["date"])
        return 0
    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if "--check-weekend" in args:
        return 1 if payload["is_weekend"] else 0
    print(f"{payload['date_cn']} {payload['weekday']}（北京时间 {payload['time_cst']}｜"
          f"容器 UTC {payload['time_utc']}）"
          + ("  ← 周末" if payload["is_weekend"] else "  ← 工作日"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
