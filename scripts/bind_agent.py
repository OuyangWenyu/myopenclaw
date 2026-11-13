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
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = REPO_ROOT / "agents" / "hermes-bind" / "defaults.yaml"

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
        BindError: ``open_id`` is empty, or the primary model key is missing.
            Nothing is created in either case.
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
    source = os.environ if model_env is None else model_env
    if not str(source.get("DEEPSEEK_API_KEY", "")).strip():
        raise BindError(
            "DEEPSEEK_API_KEY is required to create an agent; set it in the repo .env"
        )
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
        _write_json_atomic(staging / "bind.json", record)
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


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write JSON through a temporary sibling, then replace atomically."""
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def mark_failed(root: Path, agent_id: str, reason: str) -> dict:
    """Record why a bound agent never reached a usable state.

    Args:
        root: Agents root that holds every scanned agent.
        agent_id: Directory name under ``root``.
        reason: Short explanation shown to the operator.

    Returns:
        The updated ``bind.json`` mapping.

    Raises:
        BindError: The id is not a plain directory name, or no ``bind.json``
            exists for it.
    """
    if not agent_id or agent_id in {".", ".."} or "/" in agent_id or "\\" in agent_id:
        raise BindError("agent_id must be a plain directory name")
    bind_path = root / agent_id / "bind.json"
    if not bind_path.is_file():
        raise BindError(f"bind.json not found for agent {agent_id}")
    record = json.loads(bind_path.read_text(encoding="utf-8"))
    record["status"] = "failed"
    record["failure_reason"] = reason.strip()[:500]
    record["failed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_json_atomic(bind_path, record)
    return record


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


MODEL_ENV_KEYS = ("DEEPSEEK_API_KEY", "GLM_API_KEY")


def load_model_env(path: Path) -> dict[str, str]:
    """Read only the shared model keys from a dotenv file.

    Args:
        path: Repository ``.env`` or a test fixture. Other keys are ignored.

    Returns:
        ``DEEPSEEK_API_KEY`` and ``GLM_API_KEY``. Missing keys are empty strings.
    """
    found = {key: "" for key in MODEL_ENV_KEYS}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name in found:
            found[name] = value.strip().strip('"').strip("'")
    return found


def launch_record(root: Path, result: BindResult) -> dict[str, object]:
    """Describe how to start the container. The app secret is not included.

    Args:
        root: Agents root that holds ``compose.yml``.
        result: Outcome of :func:`materialize`.

    Returns:
        Container name, compose path, and the exact ``docker compose up`` argv.
    """
    container = f"hermes-bind-{result.agent_id}"
    compose_file = str(root / "compose.yml")
    return {
        "agent_id": result.agent_id,
        "container_name": container,
        "compose_file": compose_file,
        "created": result.created,
        "up_command": [
            "docker",
            "compose",
            "-f",
            compose_file,
            "-p",
            "hermes-bind",
            "up",
            "-d",
            container,
        ],
    }


def materialize_stdin(root: Path, payload: dict, env_file: Path) -> dict[str, object]:
    """Turn one credential object into a home and a launch record.

    Args:
        root: Agents root, normally ``~/.hermes-agents``.
        payload: ``client_id``, ``client_secret``, ``open_id``, ``tenant_brand``.
        env_file: Dotenv file that supplies the two model keys.

    Returns:
        The public launch record.

    Raises:
        BindError: ``open_id`` is empty, or the record would contain the secret.
    """
    credential = Credential(
        client_id=str(payload.get("client_id", "")),
        client_secret=str(payload.get("client_secret", "")),
        open_id=str(payload.get("open_id", "")),
        tenant_brand=str(payload.get("tenant_brand") or "feishu"),
    )
    result = materialize(root, credential, model_env=load_model_env(env_file))
    record = launch_record(root, result)
    encoded = json.dumps(record)
    if credential.client_secret and credential.client_secret in encoded:
        raise BindError("launch record must not contain the app secret")
    return record


def main(argv: list[str] | None = None) -> int:
    """Command line entry. ``--print-register-options`` prints JSON and exits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print-register-options",
        action="store_true",
        help="Print the registerApp options as JSON",
    )
    parser.add_argument(
        "--materialize-stdin",
        action="store_true",
        help="Read one credential JSON object from stdin and write its home",
    )
    parser.add_argument(
        "--mark-failed",
        action="store_true",
        help="Mark an existing agent's bind.json as failed",
    )
    parser.add_argument("--root", help="Agents root")
    parser.add_argument("--agent-id", help="Agent directory name for --mark-failed")
    parser.add_argument("--reason", default="", help="Failure reason for --mark-failed")
    parser.add_argument("--env-file", help="Dotenv file with the two model keys")
    args = parser.parse_args(argv)
    try:
        if args.print_register_options:
            print(json.dumps(register_options(load_defaults())))
            return 0
        if args.mark_failed:
            if not args.root or not args.agent_id:
                parser.error("--mark-failed requires --root and --agent-id")
            record = mark_failed(Path(args.root), args.agent_id, args.reason)
            print(json.dumps({"agent_id": record["agent_id"], "status": record["status"]}))
            return 0
        if args.materialize_stdin:
            if not args.root or not args.env_file:
                parser.error("--materialize-stdin requires --root and --env-file")
            payload = json.load(sys.stdin)
            record = materialize_stdin(Path(args.root), payload, Path(args.env_file))
            json.dump(record, sys.stdout)
            sys.stdout.write("\n")
            return 0
    except BindError as error:
        print(f"bind_agent.py: {error}", file=sys.stderr)
        return 1
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
