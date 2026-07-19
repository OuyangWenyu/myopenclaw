#!/usr/bin/env python3
"""
Send a Feishu interactive card via Hermes's own bot identity.

Reads Markdown content from stdin, wraps it in a Feishu interactive card,
and sends via lark-cli (Hermes bot) to the configured chat.

Usage (inside hermes container):
  echo "## Test" | python3 send_card.py
  python3 send_card.py < summary.md

Env (optional):
  LARK_CHAT_ID — target chat_id (default: AI秘书项目 group)
"""

import json
import os
import subprocess
import sys
from datetime import date

CHAT_ID = os.environ.get(
    "LARK_CHAT_ID",
    "oc_cee7a420564a62bffabb5503d368663a",  # AI秘书项目
)


def _weekday_cn(d: date) -> str:
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return days[d.weekday()]


def main():
    content = sys.stdin.read().strip()
    if not content:
        print("❌ 无输入内容", file=sys.stderr)
        sys.exit(1)

    today = date.today()

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"Daily Command Center — {today.strftime('%-m月%-d日')} {_weekday_cn(today)}",
            },
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ],
    }

    body = json.dumps({
        "receive_id": CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }, ensure_ascii=False)

    # Bind lark-cli to Hermes context (idempotent)
    subprocess.run(
        ["lark-cli", "config", "bind", "--source", "hermes", "--identity", "bot-only"],
        capture_output=True,
    )

    # Send via lark-cli
    result = subprocess.run(
        [
            "lark-cli", "--as", "bot", "api", "POST",
            f"/open-apis/im/v1/messages?receive_id_type=chat_id",
            "--data", body,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        print(f"❌ lark-cli 发送失败: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    try:
        resp = json.loads(result.stdout)
        code = resp.get("code", -1)
        if code == 0:
            print("✅ 推送成功", file=sys.stderr)
        else:
            print(f"❌ 飞书 API 错误: code={code} msg={resp.get('msg', 'unknown')}", file=sys.stderr)
            sys.exit(1)
    except json.JSONDecodeError:
        print(f"❌ lark-cli 返回非 JSON: {result.stdout[:200]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
