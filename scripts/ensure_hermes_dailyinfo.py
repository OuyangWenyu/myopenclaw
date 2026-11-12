#!/usr/bin/env python3
"""Register the read-only dailyinfo MCP on a Hermes profile.

Thin entry point over ``ensure_hermes_mcp``. An empty URL or token removes
the managed entry.

Usage:
    python3 scripts/ensure_hermes_dailyinfo.py ~/.hermes/profiles/daoyuan/config.yaml
    python3 scripts/ensure_hermes_dailyinfo.py <config> --dry-run
    python3 scripts/ensure_hermes_dailyinfo.py <config> --disable

Exit codes: 0 = written/unchanged/removed, 1 = conflict/invalid, 2 = usage.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_core():
    """Load the shared implementation by path so callers need no sys.path edit."""
    name = "ensure_hermes_mcp"
    module = sys.modules.get(name)
    if module is not None and hasattr(module, "McpTarget"):
        return module
    path = Path(__file__).resolve().parent / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_core = _load_core()

TARGET = _core.McpTarget(
    server_name="dailyinfo",
    managed_by="dailyinfo",
    url_env="DAILYINFO_MCP_URL",
    token_env="DAILYINFO_MCP_KEY",
    description="幂等注册 Hermes profile 的 dailyinfo MCP（不打印 token/真实域名）",
)


def ensure_dailyinfo(text: str, *, enabled: bool) -> tuple[str, str]:
    """Return ``(new_text, status)`` for the dailyinfo entry."""
    return _core.ensure(text, TARGET, enabled=enabled)


def main(argv: list[str] | None = None) -> int:
    """CLI entry. See the module docstring for exit codes."""
    return _core.run(TARGET, argv)


if __name__ == "__main__":
    sys.exit(main())
