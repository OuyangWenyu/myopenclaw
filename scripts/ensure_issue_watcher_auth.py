#!/usr/bin/env python3
"""Ensure per-user MCP keys for the issue watcher/notifier consumers.

The repo-scanner MCP keeps one key per consumer (the team server already works
this way). This helper is idempotent:

  * ensures HERMES_REPO_SCANNER_KEY / TIANYI_BOT_REPO_SCANNER_KEY /
    ISSUE_NOTIFIER_MCP_TOKEN exist and are >=32 printable chars;
  * merges them into GIT_STATS_API_KEYS ({key: user}) without dropping any
    keys an operator added for other consumers;
  * rewrites only those keys in .env, preserving comments/order;
  * never prints a full key.

Usage:
  python3 scripts/ensure_issue_watcher_auth.py             # update .env
  python3 scripts/ensure_issue_watcher_auth.py --dry-run
  python3 scripts/ensure_issue_watcher_auth.py --env-file path
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO_ROOT / ".env"
DEFAULT_TIANYI_ENV = REPO_ROOT / ".env.tianyi-bot"

CONSUMERS = {
    "HERMES_REPO_SCANNER_KEY": "hermes",
    "TIANYI_BOT_REPO_SCANNER_KEY": "tianyi",
    "ISSUE_NOTIFIER_MCP_TOKEN": "notifier",
}
CONSUMER_USERS = frozenset(CONSUMERS.values())
MIN_KEY_LEN = 32
MARKER = "# issue-watcher MCP keys (per consumer; generated)"


class ScriptError(RuntimeError):
    pass


def read_env(path: Path) -> list[str]:
    if not path.is_file():
        raise ScriptError(f"env file not found: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def parse_env(lines: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs


def _valid_key(value: str) -> bool:
    return len(value) >= MIN_KEY_LEN and all(0x21 <= ord(ch) <= 0x7E for ch in value)


def ensure_keys(pairs: dict[str, str]) -> dict[str, str]:
    """Return a copy with each consumer key present and valid."""
    out = dict(pairs)
    for var in CONSUMERS:
        if not _valid_key(out.get(var, "")):
            out[var] = secrets.token_urlsafe(32)
    return out


def build_key_map(pairs: dict[str, str]) -> str:
    raw = (pairs.get("GIT_STATS_API_KEYS") or "").strip()
    try:
        key_map = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ScriptError("GIT_STATS_API_KEYS is not valid JSON; fix it manually") from exc
    if not isinstance(key_map, dict):
        raise ScriptError("GIT_STATS_API_KEYS must be a JSON object")
    # Drop stale consumer entries before adding the current keys, otherwise a
    # rotated key stays valid forever ("one key per consumer", but the old key
    # is still in the map). Preserve operator entries for other consumers.
    key_map = {
        key: user for key, user in key_map.items() if user not in CONSUMER_USERS
    }
    for var, user in CONSUMERS.items():
        key_map[pairs[var]] = user
    return json.dumps(key_map, separators=(",", ":"), ensure_ascii=True)


def update_lines(lines: list[str], pairs: dict[str, str]) -> list[str]:
    """Rewrite known keys in place, append missing ones with a marker."""
    wanted = dict(CONSUMERS)  # keys to ensure
    wanted["GIT_STATS_API_KEYS"] = "map"
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in wanted:
                value = pairs[key] if key != "GIT_STATS_API_KEYS" else build_key_map(pairs)
                out.append(f"{key}={value}")
                seen.add(key)
                continue
        out.append(line)

    missing = [key for key in list(CONSUMERS) + ["GIT_STATS_API_KEYS"] if key not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append(MARKER)
        for key in missing:
            value = pairs[key] if key != "GIT_STATS_API_KEYS" else build_key_map(pairs)
            out.append(f"{key}={value}")
    return out


def sync_tianyi(lines: list[str], key: str) -> list[str]:
    """Return .env.tianyi-bot lines with the tianyi key set to ``key``.

    The tianyi stack reads its own env file, but the server's key map is built
    from the root .env. They must be the same value or tianyi gets a 401 after
    a rotation, so the helper keeps the mirror in sync when the file exists.
    """
    out: list[str] = []
    seen = False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if name == "TIANYI_BOT_REPO_SCANNER_KEY":
                out.append(f"TIANYI_BOT_REPO_SCANNER_KEY={key}")
                seen = True
                continue
        out.append(line)
    if not seen:
        if out and out[-1].strip():
            out.append("")
        out.append("# issue-watcher MCP key (synced from root .env)")
        out.append(f"TIANYI_BOT_REPO_SCANNER_KEY={key}")
    return out


def write_env(path: Path, lines: list[str]) -> None:
    """Write atomically while preserving the existing file mode (or 0600)."""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    tmp = Path(str(path) + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    tmp.replace(path)


def mask(value: str) -> str:
    return f"…(len {len(value)})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ensure per-user repo-scanner MCP keys")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument(
        "--tianyi-env",
        type=Path,
        default=DEFAULT_TIANYI_ENV,
        help="mirror the tianyi key into this file when it exists",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        lines = read_env(args.env_file)
        pairs = ensure_keys(parse_env(lines))
        updated = update_lines(lines, pairs)
        build_key_map(pairs)  # validate the map before writing
        tianyi_updated = None
        if args.tianyi_env.is_file():
            tianyi_lines = read_env(args.tianyi_env)
            tianyi_updated = sync_tianyi(
                tianyi_lines, pairs["TIANYI_BOT_REPO_SCANNER_KEY"]
            )
    except ScriptError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    print("issue-watcher MCP keys:")
    for var, user in CONSUMERS.items():
        print(f"  {var} -> {user}: {mask(pairs[var])}")
    print("  GIT_STATS_API_KEYS: merged (values hidden)")

    if args.dry_run:
        print("(dry-run: no changes written)")
        return 0

    changed = False
    if updated != lines:
        write_env(args.env_file, updated)
        print(f"✅ updated {args.env_file}")
        changed = True
    if tianyi_updated is not None and tianyi_updated != tianyi_lines:
        write_env(args.tianyi_env, tianyi_updated)
        print(f"✅ synced tianyi key to {args.tianyi_env}")
        changed = True
    if not changed:
        print("✅ already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
