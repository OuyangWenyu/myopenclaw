"""Health notification settings; real credentials stay in deployment settings."""

from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    mcp_url: str = "https://jupyterhub.waterism.com:8765/mcp"
    mcp_token: str = ""
    poll_seconds: int = 60
    max_pages: int = 5
    entity_patterns: tuple[str, ...] = ("server:*", "service:*", "gfs:*")
    include_auxiliary: bool = True
    feishu_mode: str = "app"
    chat_id: str = ""
    app_id: str = ""
    app_secret: str = ""
    webhook_url: str = ""

    @property
    def app_mode(self) -> bool:
        return self.feishu_mode == "app"


def load_config(path: Path, *, require_delivery: bool = True) -> Config:
    def env(name, default=""):
        return os.environ.get("HEALTH_NOTIFIER_" + name, default).strip()

    url = env("MCP_URL", Config.mcp_url)
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/mcp"
    ):
        raise ValueError("Invalid HEALTH_NOTIFIER_MCP_URL; do not embed credentials")
    token = env("MCP_TOKEN")
    if (
        not token
        or not token.isascii()
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
    ):
        raise ValueError("HEALTH_NOTIFIER_MCP_TOKEN is required")
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    patterns = data.get("entity_patterns", list(Config.entity_patterns))
    auxiliary = data.get("include_auxiliary", True)
    if (
        not isinstance(patterns, list)
        or not 1 <= len(patterns) <= 50
        or any(not isinstance(p, str) or not 1 <= len(p) <= 200 for p in patterns)
        or type(auxiliary) is not bool
    ):
        raise ValueError("Invalid entity filter configuration")
    try:
        interval, pages = int(env("POLL_SECONDS", "60")), int(env("MAX_PAGES", "5"))
    except ValueError:
        raise ValueError("Invalid health notifier polling setting") from None
    if not 15 <= interval <= 3600 or not 1 <= pages <= 20:
        raise ValueError("Invalid health notifier polling setting")
    mode = env("FEISHU_MODE", "app")
    if mode not in ("app", "webhook"):
        raise ValueError("HEALTH_NOTIFIER_FEISHU_MODE must be app or webhook")
    chat, app, secret, hook = (
        env("FEISHU_CHAT_ID"),
        env("FEISHU_APP_ID"),
        env("FEISHU_APP_SECRET"),
        env("FEISHU_WEBHOOK"),
    )
    if require_delivery and mode == "app" and not (chat and app and secret):
        raise ValueError(
            "Health notifier Feishu app credentials and chat id are required"
        )
    if require_delivery and mode == "webhook":
        target = urlsplit(hook)
        if (
            target.scheme != "https"
            or target.hostname != "open.feishu.cn"
            or not target.path.startswith("/open-apis/bot/v2/hook/")
            or target.query
            or target.fragment
            or target.username
        ):
            raise ValueError("Invalid unsigned Feishu webhook URL")
    return Config(
        url,
        token,
        interval,
        pages,
        tuple(patterns),
        auxiliary,
        mode,
        chat,
        app,
        secret,
        hook,
    )
