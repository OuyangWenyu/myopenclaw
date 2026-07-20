#!/usr/bin/env python3
"""
Send a Feishu interactive card via Hermes's bot identity.

Reads Markdown content from stdin, wraps it in a Feishu interactive card,
and sends to the configured chat via Feishu Bot API.

Credentials: FEISHU_APP_ID/SECRET or LARK_CLI_APP_ID/SECRET (env vars).

Usage (inside hermes container):
  echo "## Daily Command Center" | python3 send_card.py

Env (optional):
  LARK_CHAT_ID — target chat_id (default: AI秘书项目)
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

CHAT_ID = os.environ["LARK_CHAT_ID"]

APP_ID = os.environ.get("FEISHU_APP_ID") or os.environ.get("LARK_CLI_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET") or os.environ.get("LARK_CLI_APP_SECRET", "")


def _weekday_cn(d: date) -> str:
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return days[d.weekday()]


def get_tenant_token() -> str:
    """Get Feishu tenant_access_token."""
    body = json.dumps({
        "app_id": APP_ID,
        "app_secret": APP_SECRET,
    }).encode()
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=body, method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["tenant_access_token"]


def send_card(token: str, chat_id: str, card_json: str) -> dict:
    """Send an interactive card to a chat."""
    payload = json.dumps({
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": card_json,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=payload, method="POST",
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")

    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main():
    content = sys.stdin.read().strip()
    if not content:
        print("No input content", file=sys.stderr)
        sys.exit(1)

    if not APP_ID or not APP_SECRET:
        print("Missing FEISHU_APP_ID/SECRET or LARK_CLI_APP_ID/SECRET", file=sys.stderr)
        sys.exit(1)

    today = date.today()

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": (
                    f"Daily Command Center — "
                    f"{today.strftime('%-m月%-d日')} {_weekday_cn(today)}"
                ),
            },
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ],
    }
    card_json = json.dumps(card, ensure_ascii=False)

    try:
        token = get_tenant_token()
        result = send_card(token, CHAT_ID, card_json)
        code = result.get("code", -1)
        if code == 0:
            print("OK", file=sys.stderr)
        else:
            print(
                f"Feishu API error: code={code} msg={result.get('msg', 'unknown')}",
                file=sys.stderr,
            )
            sys.exit(1)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Send failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
