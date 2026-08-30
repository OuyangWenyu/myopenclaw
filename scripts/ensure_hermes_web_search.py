#!/usr/bin/env python3
"""Idempotently set Hermes ``web.search_backend`` to ``ddgs``.

Hermes ships several web-search providers. ``ddgs`` (DuckDuckGo scrape)
needs no API key; the container only needs the ``ddgs`` Python package
(installed in ``docker/hermes/Dockerfile``).

This script is called from ``scripts/start.sh``. It never overwrites an
existing ``search_backend`` value so an operator can switch to
``brave_free`` without being reverted on the next start.

Surgical text edits preserve comments and key order and do **not**
require PyYAML. PyYAML is optional: when present it is used to detect
invalid YAML (leave the file untouched) and as a last-resort dump for
exotic ``web:`` shapes.

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
# Top-level flow mapping: web: {extract_backend: none}
_WEB_FLOW_MAPPING = re.compile(r"(?m)^web:\s*\{([^}]*)\}\s*$")
# web.search_backend in a block mapping.
_BLOCK_BACKEND = re.compile(
    r"(?m)^web:\s*\n(?:[ \t]+.*\n)*?[ \t]+search_backend:\s*[\"']?([^\s\"'#]+)"
)
# web.search_backend in a flow mapping.
_FLOW_BACKEND = re.compile(
    r"(?m)^web:\s*\{[^}]*\bsearch_backend:\s*[\"']?([^\s\"',}]+)"
)
_TOP_LEVEL_WEB = re.compile(r"(?m)^web:")


def _try_load_yaml(text: str):
    """Parse YAML when PyYAML is installed.

    Returns:
        dict: parsed mapping (empty dict for an empty document)
        False: PyYAML present but the text is not a mapping / is invalid
        None: PyYAML is not installed
    """
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
    if not isinstance(data, dict):
        return False
    return data


def current_backend(cfg: dict) -> str | None:
    """Return the configured search backend from a parsed mapping."""
    web = cfg.get("web")
    if not isinstance(web, dict):
        return None
    value = web.get("search_backend")
    if value is None or value == "":
        return None
    return str(value)


def _backend_from_text(text: str) -> str | None:
    """Detect ``web.search_backend`` without PyYAML."""
    match = _BLOCK_BACKEND.search(text)
    if match:
        return match.group(1).rstrip(",")
    match = _FLOW_BACKEND.search(text)
    if match:
        return match.group(1)
    return None


def ensure_ddgs_backend(config_text: str) -> tuple[str, str]:
    """Insert ``web.search_backend: ddgs`` if missing.

    Args:
        config_text: Current Hermes ``config.yaml`` contents.

    Returns:
        A ``(new_text, status)`` pair. ``status`` is one of:
        ``written``, ``unchanged:<value>``, ``invalid``, ``unsupported``.
    """
    cfg = _try_load_yaml(config_text)
    if cfg is False:
        return config_text, "invalid"

    if isinstance(cfg, dict):
        existing = current_backend(cfg)
    else:
        existing = _backend_from_text(config_text)
    if existing is not None:
        return config_text, f"unchanged:{existing}"

    parsed = cfg if isinstance(cfg, dict) else None
    new_text = _insert_search_backend(config_text, parsed)
    if new_text == config_text:
        return config_text, "unsupported"
    return new_text, "written"


def _insert_search_backend(text: str, cfg: dict | None) -> str:
    """Insert the ddgs backend with the least invasive edit possible."""
    if _WEB_EMPTY_INLINE.search(text):
        return _WEB_EMPTY_INLINE.sub(WEB_BLOCK.rstrip(), text, count=1)
    if _WEB_BLOCK_HEADER.search(text):
        return _WEB_BLOCK_HEADER.sub(f"web:\n{SEARCH_LINE}", text, count=1)

    flow = _WEB_FLOW_MAPPING.search(text)
    if flow:
        inner = flow.group(1).strip().rstrip(",")
        addition = f'search_backend: "{BACKEND}"'
        new_inner = f"{inner}, {addition}" if inner else addition
        return _WEB_FLOW_MAPPING.sub(f"web: {{{new_inner}}}", text, count=1)

    if not _TOP_LEVEL_WEB.search(text):
        if text and not text.endswith("\n"):
            text += "\n"
        return text + "\n" + WEB_BLOCK

    if cfg is not None:
        return _dump_with_backend(cfg)
    return text


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
    """CLI entry point used by start.sh.

    Returns 0 when the desired backend is in place (written or already
    set) or the config file is absent. Returns 1 when injection could
    not be performed so start.sh prints a visible warning.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to Hermes config.yaml")
    args = parser.parse_args(argv)

    status = apply_file(args.config)
    if status == "missing":
        print(f"   ⚠️  {args.config} 不存在，跳过 web.search_backend 注入")
        return 0
    if status == "invalid":
        print("   ⚠️  Hermes config.yaml 无法解析，跳过 web.search_backend 注入")
        return 1
    if status == "unsupported":
        print("   ⚠️  Hermes config.yaml 的 web: 段无法安全改写，跳过注入")
        return 1
    if status == "written":
        print(f'   🔍 Hermes web.search_backend: "{BACKEND}"（已写入）')
        return 0
    if status.startswith("unchanged:"):
        value = status.split(":", 1)[1]
        print(f'   ✅ Hermes web.search_backend: "{value}"')
        return 0
    print(f"   ⚠️  Hermes web.search_backend 未知状态: {status}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
