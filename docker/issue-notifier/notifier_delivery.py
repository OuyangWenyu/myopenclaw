"""Shared Feishu transport; callers own filtering, cards and delivery state."""

from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
_AUTH_BUSINESS_CODES = frozenset({99991661, 99991663, 99991668})


class FeishuSender:
    def __init__(self, config: Any):
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
            AUTH_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
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

    def send(self, card: dict[str, Any], *, message_key: str | None = None) -> None:
        if self.config.app_mode:
            token = self._tenant_token()
            payload = json.dumps(
                {
                    "receive_id": self.config.chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False),
                    **(
                        {
                            "uuid": str(
                                uuid.uuid5(
                                    uuid.NAMESPACE_URL,
                                    str(self.config.app_id)
                                    + "|"
                                    + str(self.config.chat_id)
                                    + "|"
                                    + message_key,
                                )
                            )
                        }
                        if message_key
                        else {}
                    ),
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
        ok = data.get("code") == 0 or (
            not self.config.app_mode and data.get("StatusCode") == 0
        )
        if not ok:
            if data.get("code") in _AUTH_BUSINESS_CODES:
                # HTTP 200 but a business auth error: drop the cached token.
                self._token = None
                self._expires_at = 0
            raise RuntimeError(
                f"feishu send failed: code={data.get('code')} status={data.get('StatusCode')}"
            )
