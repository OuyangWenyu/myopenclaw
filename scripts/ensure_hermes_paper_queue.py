#!/usr/bin/env python3
"""Register the paper-queue MCP on a Hermes profile.

Thin entry point over ``ensure_hermes_mcp``. A non-empty URL with an empty
token removes the entry and reports ``removed:paper-queue:no-token``.

Usage:
    python3 scripts/ensure_hermes_paper_queue.py ~/.hermes/profiles/daoyuan/config.yaml
    python3 scripts/ensure_hermes_paper_queue.py <config> --dry-run
    python3 scripts/ensure_hermes_paper_queue.py <config> --disable

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
    server_name="paper-queue",
    managed_by="paper-queue",
    url_env="PAPER_QUEUE_MCP_URL",
    token_env="PAPER_QUEUE_DAOYUAN_TOKEN",
    description="幂等注册 Hermes profile 的 paper-queue MCP（不打印 token/真实地址）",
    report_missing_token=True,
)


def ensure_paper_queue(text: str, *, enabled: bool) -> tuple[str, str]:
    """Return ``(new_text, status)`` for the paper-queue entry."""
    return _core.ensure(text, TARGET, enabled=enabled)


def main(argv: list[str] | None = None) -> int:
    """CLI entry. See the module docstring for exit codes."""
    return _core.run(TARGET, argv)


if __name__ == "__main__":
    sys.exit(main())
