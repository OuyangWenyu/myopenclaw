#!/usr/bin/env python3
"""Create an isolated Hermes home from a Feishu app credential.

The app secret is written only to the agent ``.env`` (mode 600). ``bind.json``
and the generated compose file record the app id and the scanning user's
open_id, never the secret.

Run with ``uv run --with pyyaml python3 scripts/bind_agent.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = REPO_ROOT / "agents" / "hermes" / "bind-defaults.yaml"

PROFILE_CONFIG = """\
model:
  default: deepseek-flash
  provider: deepseek
  base_url: https://api.deepseek.com
fallback_providers:
  - zai
fallback_model:
  provider: zai
  model: glm-5.3-flash
agent:
  reasoning_overrides:
    glm-5.3-flash: low
memory:
  memory_enabled: true
  provider: local
"""


class BindError(Exception):
    """The credential cannot be turned into an agent home."""


@dataclass(frozen=True)
class Credential:
    """Feishu app credential returned by ``registerApp``.

    Attributes:
        client_id: Feishu App ID (``cli_...``).
        client_secret: Feishu App Secret. Stored only in the agent ``.env``.
        open_id: Scanning user's open_id. The only allowed DM sender.
        tenant_brand: ``feishu`` or ``lark``.
    """

    client_id: str
    client_secret: str
    open_id: str
    tenant_brand: str = "feishu"


@dataclass(frozen=True)
class BindResult:
    """Outcome of :func:`materialize`.

    Attributes:
        agent_id: Directory name under the agents root.
        home: Absolute path of that directory.
        compose_text: Compose document covering every materialized agent.
        created: False when ``client_id`` already had a home.
    """

    agent_id: str
    home: Path
    compose_text: str
    created: bool


def load_defaults(path: Path | None = None) -> dict:
    """Load ``bind-defaults.yaml``.

    Args:
        path: Override for tests. Defaults to the committed file.

    Returns:
        The parsed mapping.
    """
    defaults_path = path or DEFAULTS_PATH
    loaded = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise BindError(f"{defaults_path} must be a mapping")
    return loaded


def materialize(
    root: Path,
    credential: Credential,
    model_env: Mapping[str, str] | None = None,
) -> BindResult:
    """Write one agent home, or return the home that already uses this app id.

    Args:
        root: Directory that holds every scanned agent (``~/.hermes-agents``).
        credential: App id, secret, and the scanning user's open_id.
        model_env: Source for ``DEEPSEEK_API_KEY`` and ``GLM_API_KEY``.
            Defaults to the process environment.

    Returns:
        The home and the compose document for every agent under ``root``.

    Raises:
        BindError: ``open_id`` is empty. Nothing is created in that case.
    """
    open_id = credential.open_id.strip()
    if not open_id:
        raise BindError("open_id is required; refusing to start a pairing-mode gateway")

    existing = _find_client(root, credential.client_id)
    if existing is not None:
        home, record = existing
        return BindResult(
            agent_id=record["agent_id"],
            home=home,
            compose_text=_write_compose(root),
            created=False,
        )

    defaults = load_defaults()
    agent_id = _new_agent_id(root)
    staging = root / f".{agent_id}.tmp"
    home = root / agent_id
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _write_profile(staging)
        _write_env(staging, credential, open_id, defaults, model_env)
        record = {
            "agent_id": agent_id,
            "client_id": credential.client_id,
            "open_id": open_id,
            "tenant_brand": credential.tenant_brand,
            "container_name": f"hermes-bind-{agent_id}",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "ready",
        }
        (staging / "bind.json").write_text(
            json.dumps(record, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.rename(home)
    except Exception:
        if staging.exists():
            _remove_tree(staging)
        raise

    return BindResult(
        agent_id=agent_id,
        home=home,
        compose_text=_write_compose(root),
        created=True,
    )


def _new_agent_id(root: Path) -> str:
    """Return an 8-hex-char id that does not already exist under ``root``."""
    import secrets

    while True:
        agent_id = secrets.token_hex(4)
        if not (root / agent_id).exists() and not (root / f".{agent_id}.tmp").exists():
            return agent_id


def _write_profile(home: Path) -> None:
    config = home / "profiles" / "owner" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(PROFILE_CONFIG, encoding="utf-8")


def _write_env(
    home: Path,
    credential: Credential,
    open_id: str,
    defaults: dict,
    model_env: Mapping[str, str] | None,
) -> None:
    source = os.environ if model_env is None else model_env
    fixed = defaults["container"]["fixed_env"]
    lines: list[str] = []
    for key in defaults["container"]["env_keys"]:
        if key in fixed:
            value = str(fixed[key])
        elif key == "FEISHU_APP_ID":
            value = credential.client_id
        elif key == "FEISHU_APP_SECRET":
            value = credential.client_secret
        elif key == "FEISHU_ALLOWED_USERS":
            value = open_id
        else:
            value = str(source.get(key, ""))
        if "\n" in value or "\r" in value:
            raise BindError(f"{key} contains a newline")
        lines.append(f"{key}={value}")
    env_path = home / ".env"
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _find_client(root: Path, client_id: str) -> tuple[Path, dict] | None:
    if not root.is_dir():
        return None
    for home, record in _ready_agents(root):
        if record.get("client_id") == client_id:
            return home, record
    return None


def _ready_agents(root: Path) -> list[tuple[Path, dict]]:
    found: list[tuple[Path, dict]] = []
    if not root.is_dir():
        return found
    for child in sorted(path for path in root.iterdir() if path.is_dir()):
        if child.name.startswith("."):
            continue
        bind_path = child / "bind.json"
        if not bind_path.is_file():
            continue
        record = json.loads(bind_path.read_text(encoding="utf-8"))
        found.append((child, record))
    return found


def _write_compose(root: Path) -> str:
    """Rewrite ``compose.yml`` from the homes currently on disk.

    Args:
        root: Agents root.

    Returns:
        The compose document text.
    """
    defaults = load_defaults()
    services: dict = {}
    network = defaults["container"]["network"]
    for home, record in _ready_agents(root):
        service = record["container_name"]
        services[service] = {
            "image": defaults["container"]["image"],
            "container_name": service,
            "restart": "unless-stopped",
            "command": defaults["container"]["command"],
            "env_file": [str(home / ".env")],
            "volumes": [
                f"{home}:/opt/data",
                "email-config-none:/opt/data/.config/himalaya",
                "email-tokens-none:/opt/data/.config/ortie",
            ],
            "deploy": {
                "resources": {
                    "limits": {
                        "memory": defaults["container"]["memory"],
                        "cpus": str(defaults["container"]["cpus"]),
                    }
                }
            },
            "networks": [network],
        }
    document = {
        "name": "hermes-bind",
        "services": services,
        "networks": {
            network: {
                "external": True,
                "name": defaults["container"]["network_name"],
            }
        },
        "volumes": {"email-config-none": None, "email-tokens-none": None},
    }
    text = yaml.safe_dump(document, sort_keys=False)
    root.mkdir(parents=True, exist_ok=True)
    (root / "compose.yml").write_text(text, encoding="utf-8")
    return text


def _remove_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            child.rmdir()
    path.rmdir()


LOOPBACK_HOST = "127.0.0.1"

PAGE_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>绑定飞书助手</title></head>
<body>
<p>在这台电脑上打开本页，用飞书扫码新建一个只负责聊天的助手。</p>
<p>应用密钥不会显示在这个页面上。</p>
</body>
</html>
"""


def page_html() -> str:
    """Return the bind page. It has no credential fields.

    Returns:
        A static HTML document.
    """
    return PAGE_HTML


def serve(host: str = LOOPBACK_HOST, port: int = 8766) -> None:
    """Serve the bind page on loopback until the process is stopped.

    Args:
        host: Must be ``127.0.0.1``. Any other address is refused.
        port: TCP port on that address.

    Raises:
        BindError: ``host`` is not the loopback address. The socket is not opened.
    """
    if host != LOOPBACK_HOST:
        raise BindError("the bind page listens on 127.0.0.1 only")

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
            body = page_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Drop access logs so a request cannot echo a credential."""
            return

    server = ThreadingHTTPServer((host, port), _Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    """Command line entry. ``--print-register-options`` prints JSON and exits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-register-options",
        action="store_true",
        help="Print the registerApp options as JSON",
    )
    args = parser.parse_args(argv)
    if args.print_register_options:
        print(json.dumps(register_options(load_defaults())))
        return 0
    parser.error("nothing to do")
    return 2


def register_options(defaults: dict) -> dict:
    """Build the ``registerApp`` options for a new chat-only bot.

    Args:
        defaults: Parsed ``bind-defaults.yaml``.

    Returns:
        A JSON-ready dict. ``createOnly`` is true and the preset is off.
    """
    feishu = defaults["feishu"]
    return {
        "createOnly": bool(feishu["create_only"]),
        "appPreset": {"name": feishu["name"]},
        "addons": {
            "preset": bool(feishu["preset"]),
            "scopes": {
                "tenant": list(feishu["scopes"]),
                "user": list(feishu["user_scopes"]),
            },
            "events": {
                "items": {
                    "tenant": list(feishu["events"]),
                    "user": [],
                }
            },
            "callbacks": {"items": list(feishu["callbacks"])},
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
