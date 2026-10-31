#!/usr/bin/env python3
"""Idempotently register the team server-monitoring MCP for 爱码士 (Hermes coder).

The coder profile is isolated: it does not inherit the default profile's
``mcp_servers``. This helper writes a single managed entry into the coder
profile's ``config.yaml`` using surgical text edits (comments and key order
are preserved; PyYAML is optional and only used to validate the result).

The entry stores **placeholders only** — the real endpoint and token come from
the container environment at runtime:

    mcp_servers:
      server-monitoring:
        url: ${SERVER_MONITORING_MCP_URL}
        headers:
          Authorization: Bearer ${SERVER_MONITORING_MCP_TOKEN}

URL/token are read from the repo-root ``.env`` (or ``--url`` / ``--token``).
An empty URL/token disables the entry (URL 非空 = 开关), mirroring the tianyi
bot's server-monitoring switch.

Usage:
    python3 scripts/ensure_hermes_server_monitoring.py ~/.hermes/profiles/coder/config.yaml
    python3 scripts/ensure_hermes_server_monitoring.py <config> --dry-run
    python3 scripts/ensure_hermes_server_monitoring.py <config> --disable

Exit codes: 0 = written/unchanged/removed, 1 = conflict/invalid, 2 = usage.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path.home() / ".hermes" / "profiles" / "coder" / "config.yaml"

SERVER_NAME = "server-monitoring"
MANAGED_BY = "server-monitoring"
URL_ENV = "SERVER_MONITORING_MCP_URL"
TOKEN_ENV = "SERVER_MONITORING_MCP_TOKEN"

BLOCK_LINES = [
    f"  {SERVER_NAME}:",
    f"    url: ${{{URL_ENV}}}",
    "    headers:",
    f"      Authorization: Bearer ${{{TOKEN_ENV}}}",
    "    connect_timeout: 60",
    "    timeout: 120",
    "    enabled: true",
    f"    managed_by: {MANAGED_BY}",
]

_MCP_HEADER = re.compile(r"^mcp_servers:\s*$")
_MCP_INLINE_EMPTY = re.compile(r"^mcp_servers:\s*(?:\{\}|null|~)\s*$")
_ENTRY_HEADER = f"  {SERVER_NAME}:"


def _load_yaml():
    try:
        import yaml
    except ImportError:
        return None
    return yaml


def _yaml_state(text: str) -> str:
    """Return 'ok' | 'invalid' | 'not-mapping' | 'no-yaml' | 'mcp-kind'."""
    yaml = _load_yaml()
    if yaml is None:
        return "no-yaml"
    try:
        data = yaml.safe_load(text)
    except Exception:
        return "invalid"
    if data is None:
        return "not-mapping"
    if not isinstance(data, dict):
        return "not-mapping"
    servers = data.get("mcp_servers")
    if servers is not None and not isinstance(servers, dict):
        return "mcp-kind"
    return "ok"


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


def _is_top_level(line: str) -> bool:
    return bool(line) and not line.startswith((" ", "\t", "#")) and ":" in line


def _find_section(lines: list[str]) -> tuple[int, int] | None:
    """Return (header_index, section_end) for the top-level mcp_servers block."""
    start = None
    for index, line in enumerate(lines):
        if _MCP_HEADER.match(line.rstrip("\n")):
            start = index
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _is_top_level(lines[index]):
            end = index
            break
    return start, end


def _entry_span(lines: list[str], header: int, end: int) -> tuple[int, int] | None:
    start = None
    for index in range(header + 1, end):
        if lines[index].rstrip("\n") == _ENTRY_HEADER:
            start = index
            break
    if start is None:
        return None
    stop = start + 1
    while stop < end:
        line = lines[stop]
        if not line.strip():
            break
        if line.startswith("    "):
            stop += 1
            continue
        break
    return start, stop


def _block_is_managed(lines: list[str], start: int, end: int) -> bool:
    return any(
        line.strip() == f"managed_by: {MANAGED_BY}" for line in lines[start:end]
    )


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    blanks = 0
    for line in lines:
        if not line.strip():
            blanks += 1
            if blanks > 2:
                continue
        else:
            blanks = 0
        out.append(line)
    return out


def _remove_entry(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    section = _find_section(lines)
    if section is None:
        return text, "unchanged:absent"
    header, end = section
    span = _entry_span(lines, header, end)
    if span is None:
        return text, "unchanged:absent"
    start, stop = span
    if not _block_is_managed(lines, start, stop):
        return text, "conflict:unmanaged"

    del lines[start:stop]

    # Drop an empty mcp_servers block entirely.
    section = _find_section(lines)
    if section is not None:
        header, end = section
        has_children = any(
            line.strip() and not line.lstrip().startswith("#")
            for line in lines[header + 1 : end]
        )
        if not has_children:
            del lines[header]
            if header < len(lines) and not lines[header].strip():
                del lines[header]

    new_text = "".join(_collapse_blank_lines(lines))
    return new_text, "removed:server-monitoring"


def _insert_or_update(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    section = _find_section(lines)

    if section is None:
        # Handle `mcp_servers: {}` / null / ~ inline forms before appending.
        for index, line in enumerate(lines):
            if _MCP_INLINE_EMPTY.match(line.rstrip("\n")):
                lines[index] = "mcp_servers:\n"
                lines[index + 1 : index + 1] = [f"{entry}\n" for entry in BLOCK_LINES]
                return "".join(lines), "written:server-monitoring"

        if text and not text.endswith("\n"):
            text += "\n"
        if text and not text.endswith("\n\n"):
            text += "\n"
        block = "mcp_servers:\n" + "\n".join(BLOCK_LINES) + "\n"
        return text + block, "written:server-monitoring"

    header, end = section
    span = _entry_span(lines, header, end)
    if span is not None:
        start, stop = span
        if not _block_is_managed(lines, start, stop):
            return text, "conflict:unmanaged"
        replacement = [f"{entry}\n" for entry in BLOCK_LINES]
        if lines[start:stop] == replacement:
            return text, "unchanged:server-monitoring"
        lines[start:stop] = replacement
        return "".join(lines), "written:server-monitoring"

    insertion = [f"{entry}\n" for entry in BLOCK_LINES]
    lines[header + 1 : header + 1] = insertion
    return "".join(lines), "written:server-monitoring"


def ensure_server_monitoring(text: str, *, enabled: bool) -> tuple[str, str]:
    """Return ``(new_text, status)``.

    ``status`` starts with one of: written / unchanged / removed / conflict /
    invalid / unsupported.
    """
    state = _yaml_state(text)
    if state in {"invalid", "not-mapping", "mcp-kind"}:
        return text, f"{'unsupported' if state == 'mcp-kind' else 'invalid'}:yaml"

    if enabled:
        new_text, status = _insert_or_update(text)
    else:
        new_text, status = _remove_entry(text)

    if new_text != text:
        after = _yaml_state(new_text)
        if after in {"invalid", "not-mapping", "mcp-kind"}:
            return text, "invalid:yaml"
    return new_text, status


def _resolve(value: str | None, env_name: str, env_file: dict[str, str]) -> str:
    if value is not None:
        return value.strip()
    return (os.environ.get(env_name) or env_file.get(env_name, "")).strip()


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.chmod(stat.S_IMODE(path.stat().st_mode))
    except OSError:
        pass
    os.replace(tmp, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="幂等注册爱码士的 server-monitoring MCP（不打印 token/真实域名）"
    )
    parser.add_argument("config", nargs="?", default=str(DEFAULT_CONFIG),
                        help="coder profile 的 config.yaml")
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env"),
                        help="读取 URL/token 的 env 文件（默认仓库根 .env）")
    parser.add_argument("--url", default=None,
                        help=f"覆盖 {URL_ENV}；显式空串 = 停用")
    parser.add_argument("--token", default=None,
                        help=f"覆盖 {TOKEN_ENV}；只用于判断是否启用，不写入配置")
    parser.add_argument("--disable", action="store_true", help="移除受管条目")
    parser.add_argument("--dry-run", action="store_true", help="只打印状态，不写文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config).expanduser()
    if not config_path.is_file():
        print(f"❌ 配置不存在: {config_path}", file=sys.stderr)
        return 2

    env_file = _parse_env_file(Path(args.env_file).expanduser())
    url = _resolve(args.url, URL_ENV, env_file)
    token = _resolve(args.token, TOKEN_ENV, env_file)
    enabled = not args.disable and bool(url) and bool(token)

    original = config_path.read_text(encoding="utf-8")
    new_text, status = ensure_server_monitoring(original, enabled=enabled)

    if status.startswith(("conflict", "invalid", "unsupported")):
        print(f"❌ {status}", file=sys.stderr)
        return 1

    if new_text != original and not args.dry_run:
        _write_atomic(config_path, new_text)

    print(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
