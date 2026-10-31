#!/usr/bin/env python3
"""Idempotently add the per-user Bearer header to Hermes's repo-scanner MCP.

Hermes's config supports per-server headers (same shape as yuque-mcp), but the
repo-scanner entry predates MCP auth. This helper inserts:

    headers:
      Authorization: Bearer ${HERMES_REPO_SCANNER_KEY}

under `mcp_servers.repo-scanner` without touching the rest of the YAML
(comments and ordering are preserved). Safe to run on every start.

Usage:
  python3 scripts/ensure_hermes_repo_scanner_auth.py
  python3 scripts/ensure_hermes_repo_scanner_auth.py --dry-run
  python3 scripts/ensure_hermes_repo_scanner_auth.py --config path
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".hermes" / "config.yaml"
ENTRY = "repo-scanner"
HEADER_LINE = "      Authorization: Bearer ${HERMES_REPO_SCANNER_KEY}"


class ScriptError(RuntimeError):
    pass


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def add_header(lines: list[str]) -> list[str]:
    """Return lines with the Authorization header under repo-scanner."""
    entry_idx = None
    entry_indent = 0
    for i, line in enumerate(lines):
        if re.match(rf"^\s+{re.escape(ENTRY)}:\s*$", line):
            entry_idx, entry_indent = i, _indent(line)
            break
    if entry_idx is None:
        raise ScriptError(f"mcp server entry '{ENTRY}' not found")

    end = len(lines)
    for j in range(entry_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped and _indent(lines[j]) <= entry_indent:
            end = j
            break

    block = lines[entry_idx + 1 : end]
    if any("Bearer ${HERMES_REPO_SCANNER_KEY}" in line for line in block):
        return lines
    if any(line.strip() == "headers:" for line in block):
        h = next(i for i in range(entry_idx + 1, end) if lines[i].strip() == "headers:")
        child_indent = " " * (_indent(lines[h]) + 2)
        return lines[: h + 1] + [child_indent + HEADER_LINE.strip()] + lines[h + 1 :]

    # insert after the block's last non-blank line
    insert_at = end
    while insert_at > entry_idx + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    headers_indent = " " * (entry_indent + 2)
    auth_indent = " " * (entry_indent + 4)
    addition = [
        headers_indent + "headers:",
        auth_indent + HEADER_LINE.strip(),
    ]
    return lines[:insert_at] + addition + lines[insert_at:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ensure Hermes repo-scanner Bearer header")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.config.is_file():
        print(f"⚠️  Hermes config not found: {args.config} (skipped)", file=sys.stderr)
        return 0

    lines = args.config.read_text(encoding="utf-8").splitlines()
    try:
        updated = add_header(lines)
    except ScriptError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    if updated == lines:
        print("✅ Hermes repo-scanner auth already configured")
        return 0
    if args.dry_run:
        print("\n".join(updated))
        print("(dry-run: no changes written)")
        return 0

    tmp = Path(str(args.config) + ".tmp")
    tmp.write_text("\n".join(updated) + "\n", encoding="utf-8")
    # os.replace swaps the inode; copy the original mode so a hardened 0640
    # config does not silently become the temp file's umask mode.
    os.chmod(tmp, stat.S_IMODE(args.config.stat().st_mode))
    tmp.replace(args.config)
    print(f"✅ added repo-scanner Bearer header to {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
