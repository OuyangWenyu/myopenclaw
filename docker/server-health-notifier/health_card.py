"""Deterministic health cards: bounded plain text, no arbitrary links or LLM."""

from datetime import datetime, timedelta, timezone
import math
import re


def _local_time(value):
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(timezone(timedelta(hours=8)))
        .strftime("%Y-%m-%d %H:%M:%S +08:00")
    )


def build_card(event):
    entity = event["entity"]
    recovered = event["transition"] == "recovered"
    title = "服务健康恢复" if recovered else "服务健康异常"
    color = (
        "green"
        if recovered
        else "red"
        if event["to_state"] == "UNHEALTHY"
        else "orange"
    )
    details = event.get("details", {})
    lines = [
        f"{entity['label'][:160]}（{entity['key'][:240]}）",
        f"状态：{event['from_state']} → {event['to_state']}",
        "组件：" + ("核心" if entity["required"] else "辅助"),
        "首次观测：" + _local_time(event["observed_at"]),
        "确认时间：" + _local_time(event["detected_at"]),
    ]
    for field, label in (
        ("server", "服务器"),
        ("project", "项目"),
        ("environment", "环境"),
        ("component", "组件名"),
    ):
        if isinstance(entity.get(field), str):
            lines.append(label + "：" + entity[field][:160])
    reasons = details.get("reason_codes", [])
    if isinstance(reasons, list):
        codes = [
            v
            for v in reasons
            if isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", v)
        ][:10]
        if codes:
            lines.append("原因码：" + "、".join(codes))
    for key, label in (
        ("max_disk_used_percent", "最高磁盘使用率(%)"),
        ("cpu_used_percent", "CPU使用率(%)"),
        ("restart_count", "重启次数"),
        ("cycle_age_seconds", "最近成功cycle年龄(秒)"),
    ):
        value = details.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            lines.append(f"{label}：{value:g}")
    if event["to_state"] == "UNKNOWN":
        lines.append("UNKNOWN 表示证据不足或陈旧，不等同于已确认服务故障。")
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
        "elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": "\n".join(lines)}}
        ],
    }
