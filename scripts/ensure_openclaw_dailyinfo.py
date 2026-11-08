#!/usr/bin/env python3
"""Idempotently register the read-only dailyinfo MCP for 虾酱 (openclaw.json).

The entry stores **placeholders only**; OpenClaw expands ``${VAR}`` at gateway
activation, so the real endpoint and Bearer key stay in the container env:

    "mcp": {
      "servers": {
        "dailyinfo": {
          "url": "${DAILYINFO_MCP_URL}",
          "transport": "streamable-http",
          "headers": {"authorization": "Bearer ${DAILYINFO_MCP_KEY}"},
          "connectionTimeoutMs": 10000,
          "requestTimeoutMs": 30000
        }
      }
    }

URL/key are read from the repo-root ``.env`` (or ``--url`` / ``--key``). An
empty URL or key removes the managed entry (URL 非空 = 开关), matching the
Hermes-side registration helper.

Usage:
    python3 scripts/ensure_openclaw_dailyinfo.py ~/.openclaw/openclaw.json
    python3 scripts/ensure_openclaw_dailyinfo.py <config> --dry-run
    python3 scripts/ensure_openclaw_dailyinfo.py <config> --disable

Exit codes: 0 = written/unchanged/removed, 1 = conflict/invalid, 2 = usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

SERVER_NAME = "dailyinfo"
URL_ENV = "DAILYINFO_MCP_URL"
KEY_ENV = "DAILYINFO_MCP_KEY"

WANTED = {
    "url": f"${{{URL_ENV}}}",
    "transport": "streamable-http",
    "headers": {"authorization": f"Bearer ${{{KEY_ENV}}}"},
    "connectionTimeoutMs": 10000,
    "requestTimeoutMs": 30000,
}


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _resolve(cli_value: str | None, env_name: str, env_file: dict[str, str]) -> str:
    return (cli_value if cli_value is not None else env_file.get(env_name, "")).strip()


def _servers(data: dict) -> dict | None:
    mcp = data.get("mcp")
    if mcp is None:
        return None
    if not isinstance(mcp, dict):
        raise ValueError("openclaw.json: mcp must be an object")
    servers = mcp.get("servers")
    if servers is None:
        return None
    if not isinstance(servers, dict):
        raise ValueError("openclaw.json: mcp.servers must be an object")
    return servers


def apply(config_path: Path, *, enabled: bool, dry_run: bool) -> str:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("openclaw.json: top-level value must be an object")

    servers = _servers(data)
    existing = servers.get(SERVER_NAME) if servers else None

    if not enabled:
        if not servers or SERVER_NAME not in servers:
            return "unchanged:dailyinfo (absent)"
        del servers[SERVER_NAME]
        status = "removed:dailyinfo"
    else:
        if servers is None:
            servers = data.setdefault("mcp", {}).setdefault("servers", {})
        if not isinstance(existing, dict):
            existing = {}
        merged = dict(existing)
        for key, value in WANTED.items():
            if merged.get(key) != value:
                merged[key] = value
        if existing == merged:
            return "unchanged:dailyinfo"
        servers[SERVER_NAME] = merged
        status = "written:dailyinfo"

    if not dry_run:
        tmp = config_path.with_name(config_path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, config_path)
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="幂等注册虾酱（openclaw.json）的 dailyinfo MCP（不打印 token/真实域名）"
    )
    parser.add_argument("config", nargs="?", default=str(DEFAULT_CONFIG),
                        help=f"openclaw.json 路径（默认 {DEFAULT_CONFIG}）")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                        help="读取 DAILYINFO_MCP_URL / DAILYINFO_MCP_KEY 的 .env")
    parser.add_argument("--url", default=None, help="覆盖 DAILYINFO_MCP_URL")
    parser.add_argument("--key", default=None, help="覆盖 DAILYINFO_MCP_KEY")
    parser.add_argument("--disable", action="store_true", help="移除受管条目")
    parser.add_argument("--dry-run", action="store_true", help="只打印状态，不写文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_file = _parse_env_file(Path(args.env_file))
    url = _resolve(args.url, URL_ENV, env_file)
    key = _resolve(args.key, KEY_ENV, env_file)
    enabled = not args.disable and bool(url) and bool(key)

    try:
        status = apply(Path(args.config), enabled=enabled, dry_run=args.dry_run)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"dailyinfo MCP: {exc}", file=sys.stderr)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
