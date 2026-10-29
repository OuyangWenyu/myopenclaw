"""Configuration for the Feishu issue notifier.

People mapping lives in the mounted config.toml (no secrets). Feishu
credentials/chat_id and the MCP URL come from the environment so the public
repo never contains live identifiers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class Person:
    name: str
    gitcode: str | None = None
    github: str | None = None

    def login_for(self, platform: str) -> str | None:
        if platform == "github":
            return self.github
        if platform == "gitcode":
            return self.gitcode
        return None


@dataclass(frozen=True)
class Config:
    mcp_url: str
    poll_seconds: int
    feishu_mode: str
    chat_id: str | None
    webhook_url: str | None
    app_id: str | None
    app_secret: str | None
    people: tuple[Person, ...]
    notify_existing: bool

    @property
    def app_mode(self) -> bool:
        return self.feishu_mode == "app"


class ConfigError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def load_config(path: str | Path) -> Config:
    config_path = Path(path)
    people: list[Person] = []
    if config_path.is_file():
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        for entry in data.get("people", []) or []:
            name = (entry.get("name") or entry.get("gitcode") or entry.get("github") or "").strip()
            if not name:
                continue
            people.append(
                Person(
                    name=name,
                    gitcode=(entry.get("gitcode") or "").strip() or None,
                    github=(entry.get("github") or "").strip() or None,
                )
            )
    if not people:
        raise ConfigError(
            f"no people configured in {config_path}; copy the example and fill usernames"
        )

    mode = (_env("ISSUE_NOTIFIER_FEISHU_MODE", "app") or "app").lower()
    if mode not in ("app", "webhook"):
        raise ConfigError("ISSUE_NOTIFIER_FEISHU_MODE must be app or webhook")

    chat_id = _env("ISSUE_NOTIFIER_FEISHU_CHAT_ID") or None
    webhook = _env("ISSUE_NOTIFIER_FEISHU_WEBHOOK") or None
    # Only the hermes-coder app: do not fall back to the main 爱玛士 app.
    app_id = _env("CODER_FEISHU_APP_ID") or None
    app_secret = _env("CODER_FEISHU_APP_SECRET") or None

    if mode == "app":
        if not chat_id:
            raise ConfigError("ISSUE_NOTIFIER_FEISHU_CHAT_ID is required for app mode")
        if not app_id or not app_secret:
            raise ConfigError("CODER_FEISHU_APP_ID/SECRET required for app mode")
    else:
        if not webhook:
            raise ConfigError("ISSUE_NOTIFIER_FEISHU_WEBHOOK is required for webhook mode")

    try:
        poll_seconds = int(_env("ISSUE_NOTIFIER_POLL_SECONDS", "60") or "60")
    except ValueError:
        poll_seconds = 60

    return Config(
        mcp_url=_env("ISSUE_NOTIFIER_MCP_URL", "http://repo-scanner-mcp:8001/mcp"),
        poll_seconds=max(15, poll_seconds),
        feishu_mode=mode,
        chat_id=chat_id,
        webhook_url=webhook,
        app_id=app_id,
        app_secret=app_secret,
        people=tuple(people),
        notify_existing=_env("ISSUE_NOTIFIER_NOTIFY_EXISTING", "0").lower()
        in {"1", "true", "yes"},
    )
