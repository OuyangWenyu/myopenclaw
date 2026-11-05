"""Feishu card rendering + delivery (app identity or group webhook)."""

from __future__ import annotations

import urllib.error
import urllib.request  # noqa: F401 - preserve the existing transport patch/import surface
from typing import Any

from config import Person
from notifier_delivery import FeishuSender as FeishuSender

AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"

_EVENT_LABELS = {
    "assigned": "新分配",
    "unassigned": "取消分配",
    "opened": "新建",
    "closed": "关闭",
    "reopened": "重新打开",
    "merged": "已合并",
    "review_requested": "请求 review",
    "review_request_removed": "取消 review",
}
_AUTH_BUSINESS_CODES = frozenset({99991661, 99991663, 99991668})
_EVENT_TEMPLATES = {
    "assigned": "blue",
    "unassigned": "orange",
    "opened": "green",
    "closed": "grey",
    "reopened": "orange",
    "merged": "purple",
    "review_requested": "blue",
    "review_request_removed": "orange",
}


def _platform_label(platform: str) -> str:
    return {"github": "GitHub", "gitcode": "GitCode"}.get(platform, platform)


def _names(event: dict[str, Any], people: list[Person]) -> str:
    if people:
        return "、".join(p.name for p in people)
    names = []
    for assignee in event.get("assignees") or []:
        label = assignee.get("name") or assignee.get("login")
        if label:
            names.append(label)
    return "、".join(names) or "—"


def _safe_url(url: str) -> str:
    """Only link to the two code hosts; never render arbitrary external URLs."""
    if url.startswith(("https://github.com/", "https://gitcode.com/")):
        return url
    return ""


def build_card(event: dict[str, Any], people: list[Person]) -> dict[str, Any]:
    platform = event.get("platform") or ""
    repo = f"{event.get('owner')}/{event.get('repo')}"
    number = event.get("number")
    event_type = event.get("event_type") or ""
    label = _EVENT_LABELS.get(event_type, event_type)
    title = event.get("title") or "(无标题)"
    url = _safe_url(event.get("url") or "")
    assignees = _names(event, people)

    # External issue titles are untrusted. Render them as plain_text, not
    # lark_md: lark_md would turn `<at id=all>` / `[x](url)` from a crafted
    # public issue title into a real mention/link inside a trusted bot card.
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "plain_text",
                "content": f"{label} · {title}",
            },
        },
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "plain_text", "content": f"仓库\n{repo}"}},
                {"is_short": True, "text": {"tag": "plain_text", "content": f"人员\n{assignees}"}},
            ],
        },
    ]
    if url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开"},
                        "url": url,
                        "type": "primary",
                    }
                ],
            }
        )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{_platform_label(platform)} {repo} #{number}",
            },
            "template": _EVENT_TEMPLATES.get(event_type, "blue"),
        },
        "elements": elements,
    }
