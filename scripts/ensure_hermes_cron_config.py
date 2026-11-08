#!/usr/bin/env python3
"""Idempotently set a Hermes profile's ``cron.wrap_response`` to ``false``.

Hermes wraps cron deliveries with a ``Cronjob Response: ...`` header/footer
unless the active profile config sets ``cron.wrap_response: false``. The
dailyinfo → 道元 Feishu mirror delivers long briefing bodies verbatim, so the
wrapper is pure noise.

Surgical text edits preserve comments and key order and do **not** require
PyYAML. PyYAML is optional: when present it validates the result before the
file is written.

Usage:
    python3 scripts/ensure_hermes_cron_config.py ~/.hermes/profiles/daoyuan/config.yaml
    python3 scripts/ensure_hermes_cron_config.py <config> --dry-run

Exit codes: 0 = written/unchanged, 1 = conflict/invalid, 2 = usage/missing.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

KEY = "cron"
FIELD = "wrap_response"
VALUE = "false"
FIELD_LINE = f"  {FIELD}: {VALUE}"
BLOCK = f"{KEY}:\n{FIELD_LINE}\n"

_CRON_HEADER = re.compile(r"^cron:\s*$")
_CRON_INLINE_EMPTY = re.compile(r"^cron:\s*(?:\{\}|null|~)\s*$")
_CRON_INLINE_ANY = re.compile(r"^cron:\s*\S")
_FIELD_LINE = re.compile(r"^([ \t]+)wrap_response:\s*(.*?)\s*$")


def _try_load_yaml(text: str):
    """Parse YAML when PyYAML is installed (None = not installed)."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    if data is None:
        return {}
    return data if isinstance(data, dict) else False


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Return (header_index, end_index) for the top-level ``cron:`` block."""
    start = next((i for i, line in enumerate(lines) if _CRON_HEADER.match(line)), None)
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            end = index
            break
    return start, end


def ensure_wrap_response(config_text: str) -> tuple[str, str]:
    """Return ``(new_text, status)``.

    Statuses: ``written``, ``unchanged:false``, ``conflict:<reason>``,
    ``invalid``.
    """
    if _try_load_yaml(config_text) is False:
        return config_text, "invalid"

    lines = config_text.splitlines(keepends=True)
    bounds = _block_bounds(lines)

    if bounds is not None:
        start, end = bounds
        for index in range(start + 1, end):
            match = _FIELD_LINE.match(lines[index].rstrip("\n"))
            if not match:
                continue
            current = match.group(2).strip().strip("'\"")
            if current.lower() in {"false", "no", "off", "0"}:
                return config_text, "unchanged:false"
            if current.startswith(("{", "[")):
                return config_text, "conflict:complex-wrap-response"
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = FIELD_LINE + newline
            return _validated("".join(lines), config_text)
        if not lines[start].endswith("\n"):
            lines[start] += "\n"
        lines.insert(start + 1, FIELD_LINE + "\n")
        return _validated("".join(lines), config_text)

    empty_inline = next(
        (i for i, line in enumerate(lines) if _CRON_INLINE_EMPTY.match(line.rstrip("\n"))),
        None,
    )
    if empty_inline is not None:
        newline = "\n" if lines[empty_inline].endswith("\n") else ""
        lines[empty_inline] = BLOCK.rstrip("\n") + newline
        return _validated("".join(lines), config_text)

    if any(_CRON_INLINE_ANY.match(line) for line in lines):
        return config_text, "conflict:non-block-cron"

    new_text = config_text
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    if new_text and not new_text.endswith("\n\n"):
        new_text += "\n"
    new_text += BLOCK
    return _validated(new_text, config_text)


def _validated(new_text: str, original: str) -> tuple[str, str]:
    parsed = _try_load_yaml(new_text)
    if parsed is False:
        return original, "invalid"
    if isinstance(parsed, dict):
        cron = parsed.get(KEY)
        if not isinstance(cron, dict) or cron.get(FIELD) is not False:
            return original, "conflict:validation-failed"
    return new_text, "written"


def apply_file(path: Path, *, dry_run: bool = False) -> str:
    """Mutate ``path`` in place. Returns the status string."""
    if not path.exists():
        return "missing"
    original = path.read_text()
    new_text, status = ensure_wrap_response(original)
    if status == "written" and new_text != original and not dry_run:
        _atomic_write(path, new_text)
    return status


def _atomic_write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to Hermes profile config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Only report the status")
    args = parser.parse_args(argv)

    status = apply_file(args.config, dry_run=args.dry_run)
    if status == "missing":
        print(f"missing: {args.config}")
        return 2
    if status == "invalid":
        print("invalid: YAML 无法解析，未写入")
        return 1
    if status.startswith("conflict"):
        print(f"{status}：无法安全改写，未写入")
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
