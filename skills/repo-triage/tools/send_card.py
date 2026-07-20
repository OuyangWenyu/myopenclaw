#!/usr/bin/env python3
"""
Send repo-triage summary as a Feishu interactive card to the user's private chat.

Reads Markdown content from stdin, wraps it in a Feishu interactive card,
and sends via Hermes's Feishu bot identity (LARK_CLI_APP_ID/SECRET).

Usage (inside hermes container):
  echo "## 仓库动态" | python3 /opt/hermes-skills/repo-triage/tools/send_card.py

Env:
  LARK_CLI_APP_ID/SECRET — Hermes Feishu bot credentials
  LARK_USER_OPEN_ID — target user's open_id (default: 庄赖宏)
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

# ── Config ──────────────────────────────────────────────────────────────────

APP_ID = os.environ.get("LARK_CLI_APP_ID", "")
APP_SECRET = os.environ.get("LARK_CLI_APP_SECRET", "")

# Hermes app 下用户私聊 open_id（从 .env 读取）
TARGET_OPEN_ID = os.environ["LARK_USER_OPEN_ID"]

AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"


def _weekday_cn(d: date) -> str:
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return days[d.weekday()]


def get_tenant_token() -> str:
    """Get Feishu tenant_access_token using Hermes app credentials."""
    body = json.dumps({
        "app_id": APP_ID,
        "app_secret": APP_SECRET,
    }).encode()
    req = urllib.request.Request(AUTH_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["tenant_access_token"]


def send_card(token: str, open_id: str, card_json: str) -> dict:
    """Send an interactive card message."""
    payload = json.dumps({
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": card_json,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(MSG_URL, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main() -> None:
    content = sys.stdin.read().strip()
    if not content:
        print("No content to send", file=sys.stderr)
        sys.exit(1)

    if not APP_ID or not APP_SECRET:
        print(
            "Missing LARK_CLI_APP_ID/SECRET — "
            "set FEISHU_APP_ID/SECRET or LARK_CLI_APP_ID/SECRET",
            file=sys.stderr,
        )
        sys.exit(1)

    today = date.today()
    header = f"仓库动态 — {today.month}月{today.day}日 {_weekday_cn(today)}"

    # Build Feishu card
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": header},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": content,
            }
        ],
    }
    card_json = json.dumps(card, ensure_ascii=False)

    try:
        token = get_tenant_token()
        resp = send_card(token, TARGET_OPEN_ID, card_json)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:500]
        except Exception:
            pass
        print(
            f"[repo-triage] Feishu HTTP {e.code}: {body}",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"[repo-triage] Send failed: {e}", file=sys.stderr)
        sys.exit(1)

    if resp.get("code") == 0:
        print(f"[repo-triage] 私聊推送成功 → {header}", file=sys.stderr)
    else:
        print(
            f"[repo-triage] 推送失败: code={resp.get('code')} "
            f"msg={resp.get('msg')}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
