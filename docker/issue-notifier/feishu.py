"""Feishu card rendering + delivery (app identity or group webhook)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from config import Config, Person

AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"

_EVENT_LABELS = {
    "assigned": "新分配",
    "unassigned": "取消分配",
    "opened": "新建",
    "closed": "关闭",
    "reopened": "重新打开",
    "merged": "已合并",
}
_AUTH_BUSINESS_CODES = frozenset({99991661, 99991663, 99991668})
_EVENT_TEMPLATES = {
    "assigned": "blue",
    "unassigned": "orange",
    "opened": "green",
    "closed": "grey",
    "reopened": "orange",
    "merged": "purple",
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


class FeishuSender:
    def __init__(self, config: Config):
        self.config = config
        self._token: str | None = None
        self._expires_at = 0.0

    def _tenant_token(self) -> str:
        now = time.time()
        if self._token and now < self._expires_at - 60:
            return self._token
        body = json.dumps(
            {"app_id": self.config.app_id, "app_secret": self.config.app_secret}
        ).encode()
        req = urllib.request.Request(
            AUTH_URL, data=body, method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                self._token = None
                self._expires_at = 0
            raise RuntimeError(f"feishu auth HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001 - never echo the request URL
            raise RuntimeError(f"feishu auth failed: {type(exc).__name__}") from None
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            self._token = None
            self._expires_at = 0
            # Do not include data['msg']: Feishu error text can echo ids.
            raise RuntimeError(f"feishu auth failed: code={data.get('code')}")
        self._token = data["tenant_access_token"]
        self._expires_at = now + int(data.get("expire") or 7200)
        return self._token

    def send(self, card: dict[str, Any]) -> None:
        if self.config.app_mode:
            token = self._tenant_token()
            payload = json.dumps(
                {
                    "receive_id": self.config.chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False),
                },
                ensure_ascii=False,
            ).encode()
            req = urllib.request.Request(
                MSG_URL,
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        else:
            payload = json.dumps(
                {"msg_type": "interactive", "card": card}, ensure_ascii=False
            ).encode()
            req = urllib.request.Request(
                self.config.webhook_url or "",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # Force a fresh tenant token on the next attempt.
                self._token = None
                self._expires_at = 0
            # Do not echo the response body: it can contain the chat_id /
            # log_id; webhook URL errors can contain the hook secret.
            raise RuntimeError(f"feishu HTTP {exc.code}") from None
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"feishu send failed: {type(exc).__name__}") from None

        # Custom-bot webhooks have returned both {"code":0} and
        # {"StatusCode":0}; accept either shape.
        ok = data.get("code") == 0 or (not self.config.app_mode and data.get("StatusCode") == 0)
        if not ok:
            if data.get("code") in _AUTH_BUSINESS_CODES:
                # HTTP 200 but a business auth error: drop the cached token.
                self._token = None
                self._expires_at = 0
            raise RuntimeError(
                f"feishu send failed: code={data.get('code')} status={data.get('StatusCode')}"
            )
