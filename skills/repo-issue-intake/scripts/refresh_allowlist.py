#!/usr/bin/env python3
"""Regenerate ``repos.json`` from the repo registry (host-side maintenance only).

The allowlist mirrors the daily-scan registry in the
``git-contribution-stats`` checkout. Refresh it when repos are added there, then
update ``references/repos.md`` as well — the test suite fails if an allowlisted
repo has no row in that table.

Usage (from the myopenclaw checkout, on the host):

    python3 skills/repo-issue-intake/scripts/refresh_allowlist.py
    python3 skills/repo-issue-intake/scripts/refresh_allowlist.py \
        --toml ~/code/git-contribution-stats/configs/repos.toml
    python3 skills/repo-issue-intake/scripts/refresh_allowlist.py \
        --sqlite ~/.myagentdata/repo-scanner/repos.sqlite

Exit codes: 0 written/unchanged, 1 no repos found, 2 source unreadable.

The script never runs inside the agent container: it needs the host paths.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tomllib
from pathlib import Path

DEFAULT_TOML = Path.home() / "code" / "git-contribution-stats" / "configs" / "repos.toml"
DEFAULT_SQLITE = Path.home() / ".myagentdata" / "repo-scanner" / "repos.sqlite"
OUT = Path(__file__).resolve().parent / "repos.json"


def from_toml(path: Path) -> list[dict]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = []
    for platform in ("github", "gitcode"):
        for entry in data.get(platform, []):
            owner, name = entry["owner"], entry["repo"]
            rows.append(
                {
                    "platform": platform,
                    "owner": owner,
                    "name": name,
                    "repo": f"{owner}/{name}",
                }
            )
    return rows


def from_sqlite(path: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select platform, owner, name from repositories"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "platform": platform,
            "owner": owner,
            "name": name,
            "repo": f"{owner}/{name}",
        }
        for platform, owner, name in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--toml", type=Path, default=None)
    group.add_argument("--sqlite", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        if args.sqlite:
            rows = from_sqlite(args.sqlite)
            source = str(args.sqlite)
        else:
            path = args.toml or DEFAULT_TOML
            rows = from_toml(path)
            source = str(path)
    except (OSError, KeyError, tomllib.TOMLDecodeError, sqlite3.Error) as exc:
        print(f"ERROR=cannot read registry: {exc}", file=sys.stderr)
        return 2

    rows.sort(key=lambda r: (r["platform"], r["owner"].lower(), r["name"].lower()))
    rows = [r for i, r in enumerate(rows) if r not in rows[:i]]
    if not rows:
        print("ERROR=registry contained no repos", file=sys.stderr)
        return 1

    payload = {
        "generated_from": source,
        "count": len(rows),
        "repositories": rows,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if OUT.is_file() and OUT.read_text(encoding="utf-8") == text:
        print(f"UNCHANGED {OUT} ({len(rows)} repos)")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"WROTE {OUT} ({len(rows)} repos) from {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())