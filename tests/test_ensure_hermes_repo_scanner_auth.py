"""Unit tests for scripts/ensure_hermes_repo_scanner_auth.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ensure_hermes_repo_scanner_auth.py"
spec = importlib.util.spec_from_file_location("ensure_hermes_repo_scanner_auth", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

BASE = """mcp_servers:
  repo-scanner:
    connect_timeout: 60
    enabled: true
    timeout: 120
    url: http://repo-scanner-mcp:8001/mcp
  zotero:
    url: http://zotero-mcp:8002/mcp
"""


class TestAddHeader:
    def test_inserts_headers_under_repo_scanner(self):
        out = mod.add_header(BASE.splitlines())
        text = "\n".join(out)
        assert "    headers:\n      Authorization: Bearer ${HERMES_REPO_SCANNER_KEY}" in text
        assert "  zotero:\n    url: http://zotero-mcp:8002/mcp" in text

    def test_idempotent(self):
        once = mod.add_header(BASE.splitlines())
        twice = mod.add_header(once)
        assert once == twice

    def test_existing_headers_gets_child(self):
        src = """mcp_servers:
  repo-scanner:
    url: http://x/mcp
    headers:
      X-Test: 1
  zotero:
    url: http://z/mcp
"""
        out = "\n".join(mod.add_header(src.splitlines()))
        assert "      Authorization: Bearer ${HERMES_REPO_SCANNER_KEY}" in out
        assert "      X-Test: 1" in out

    def test_missing_entry_fails_loud(self):
        with pytest.raises(mod.ScriptError):
            mod.add_header(["mcp_servers:", "  other:", "    url: x"])

    def test_preserves_comments(self):
        src = "# top\nmcp_servers:\n  repo-scanner:\n    url: x\n# tail\n"
        out = "\n".join(mod.add_header(src.splitlines()))
        assert "# top" in out and "# tail" in out
