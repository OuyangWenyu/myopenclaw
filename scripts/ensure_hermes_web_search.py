#!/usr/bin/env python3
"""Idempotently set Hermes ``web.search_backend`` to ``ddgs``.

Hermes ships several web-search providers. ``ddgs`` (DuckDuckGo scrape)
needs no API key; the container only needs the ``ddgs`` Python package
(installed in ``docker/hermes/Dockerfile``).

This script is called from ``scripts/start.sh``. It never overwrites an
existing ``search_backend`` value so an operator can switch to
``brave_free`` without being reverted on the next start.

Surgical text edits preserve comments and key order. Full ``yaml.dump``
is used only as a last resort when the ``web:`` section cannot be
located as a block mapping.

Usage:
    python3 scripts/ensure_hermes_web_search.py ~/.hermes/config.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND = "ddgs"
SEARCH_LINE = f'  search_backend: "{BACKEND}"\n'
WEB_BLOCK = f"web:\n{SEARCH_LINE}"

# Top-level `web:` followed by a newline (block mapping).
_WEB_BLOCK_HEADER = re.compile(r"(?m)^web:\s*\n")
# Top-level `web: {}` / `web: null` / `web: ~` on a single line.
_WEB_EMPTY_INLINE = re.compile(r"(?m)^web:\s*(?:\{\}|null|~)\s*$")


def _load_yaml(text: str):
    """Parse YAML; return ``None`` if PyYAML is missing or the text is invalid."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if data is None:
        return {}
    if not isinstance(data, dict):
        return None
    return data


def current_backend(cfg: dict) -> str | None:
    """Return the configured search backend, or None if unset."""
    web = cfg.get("web")
    if not isinstance(web, dict):
        return None
    value = web.get("search_backend")
    if value is None or value == "":
        return None
    return str(value)


def ensure_ddgs_backend(config_text: str) -> tuple[str, str]:
    """Insert ``web.search_backend: ddgs`` if missing.

    Args:
        config_text: Current Hermes ``config.yaml`` contents.

    Returns:
        A ``(new_text, status)`` pair. ``status`` is one of:
        ``written``, ``unchanged:<value>``, ``invalid``.
    """
    cfg = _load_yaml(config_text)
    if cfg is None:
        return config_text, "invalid"

    existing = current_backend(cfg)
    if existing is not None:
        return config_text, f"unchanged:{existing}"

    new_text = _insert_search_backend(config_text, cfg)
    return new_text, "written"


def _insert_search_backend(text: str, cfg: dict) -> str:
    """Insert the ddgs backend with the least invasive edit possible."""
    web = cfg.get("web")
    if web is None or web == {}:
        empty_inline = _WEB_EMPTY_INLINE.search(text)
        if empty_inline:
            return _WEB_EMPTY_INLINE.sub(WEB_BLOCK.rstrip(), text, count=1)
        if _WEB_BLOCK_HEADER.search(text):
            return _WEB_BLOCK_HEADER.sub(f"web:\n{SEARCH_LINE}", text, count=1)
        if text and not text.endswith("\n"):
            text += "\n"
        return text + "\n" + WEB_BLOCK

    if isinstance(web, dict) and _WEB_BLOCK_HEADER.search(text):
        return _WEB_BLOCK_HEADER.sub(f"web:\n{SEARCH_LINE}", text, count=1)

    return _dump_with_backend(cfg)


def _dump_with_backend(cfg: dict) -> str:
    """Last-resort rewrite via PyYAML when surgical insert is impossible."""
    import yaml

    web = cfg.get("web")
    if not isinstance(web, dict):
        web = {}
    web["search_backend"] = BACKEND
    cfg["web"] = web
    return yaml.dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False)


def apply_file(path: Path) -> str:
    """Mutate ``path`` in place. Returns the status string."""
    if not path.exists():
        return "missing"
    original = path.read_text()
    new_text, status = ensure_ddgs_backend(original)
    if status == "written" and new_text != original:
        path.write_text(new_text)
    return status


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by start.sh."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to Hermes config.yaml")
    args = parser.parse_args(argv)

    status = apply_file(args.config)
    if status == "missing":
        print(f"   ⚠️  {args.config} 不存在，跳过 web.search_backend 注入")
        return 0
    if status == "invalid":
        print("   ⚠️  Hermes config.yaml 无法解析，跳过 web.search_backend 注入")
        return 0
    if status == "written":
        print(f'   🔍 Hermes web.search_backend: "{BACKEND}"（已写入）')
        return 0
    if status.startswith("unchanged:"):
        value = status.split(":", 1)[1]
        print(f'   ✅ Hermes web.search_backend: "{value}"')
        return 0
    print(f"   ⚠️  Hermes web.search_backend 未知状态: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
