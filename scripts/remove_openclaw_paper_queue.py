#!/usr/bin/env python3
"""Idempotently remove 虾酱's paper-queue artifacts from ``openclaw.json``.

The paper-queue capability now belongs to 道元. 虾酱 no longer installs or
registers it, but an existing deployment still carries:

- ``mcp.servers.paper-queue``
- ``plugins.entries.paper-queue-actor``
- the ``paper-queue-actor`` entry in ``plugins.allow``
- ``skills.entries.paper-queue`` (if present)

``paper-fetch`` is deliberately left untouched (its disabled state is separate).
The plugin/ensure source files stay in the repo as a reference implementation
for the future multi-user Hermes identity plugin.

Usage:
    python3 scripts/remove_openclaw_paper_queue.py ~/.openclaw/openclaw.json
    python3 scripts/remove_openclaw_paper_queue.py <config> --dry-run

Exit codes: 0 = removed/unchanged, 1 = invalid JSON, 2 = missing file.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

SERVER_KEY = "paper-queue"
PLUGIN_KEY = "paper-queue-actor"
SKILL_KEY = "paper-queue"
PLUGIN_DIR = "/home/node/.openclaw/extensions/paper-queue-actor"


def remove_artifacts(config: dict) -> tuple[dict, str]:
    """Return ``(new_config, status)``; the input mapping is not mutated."""
    data = copy.deepcopy(config)
    changed = False

    mcp = data.get("mcp")
    if isinstance(mcp, dict):
        servers = mcp.get("servers")
        if isinstance(servers, dict) and SERVER_KEY in servers:
            del servers[SERVER_KEY]
            changed = True

    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        entries = plugins.get("entries")
        if isinstance(entries, dict) and PLUGIN_KEY in entries:
            del entries[PLUGIN_KEY]
            changed = True
        allow = plugins.get("allow")
        if isinstance(allow, list) and PLUGIN_KEY in allow:
            plugins["allow"] = [item for item in allow if item != PLUGIN_KEY]
            changed = True
        load = plugins.get("load")
        if isinstance(load, dict):
            paths = load.get("paths")
            if isinstance(paths, list):
                kept = [p for p in paths if str(p).rstrip("/") != PLUGIN_DIR.rstrip("/")]
                if kept != paths:
                    changed = True
                    if kept:
                        load["paths"] = kept
                    else:
                        del load["paths"]

    skills = data.get("skills")
    if isinstance(skills, dict):
        entries = skills.get("entries")
        if isinstance(entries, dict) and SKILL_KEY in entries:
            del entries[SKILL_KEY]
            changed = True

    return data, "removed" if changed else "unchanged"


def apply_file(path: Path, *, dry_run: bool = False) -> str:
    if not path.is_file():
        return "missing"
    try:
        original = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "invalid"
    if not isinstance(original, dict):
        return "invalid"

    data, status = remove_artifacts(original)
    if status == "removed" and not dry_run:
        _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return status


def _atomic_write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="移除 openclaw.json 中的 paper-queue 残留")
    parser.add_argument("config", type=Path, help="Path to openclaw.json")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = parser.parse_args(argv)

    status = apply_file(args.config, dry_run=args.dry_run)
    if status == "missing":
        print(f"missing: {args.config}", file=sys.stderr)
        return 2
    if status == "invalid":
        print(f"invalid JSON: {args.config}", file=sys.stderr)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
