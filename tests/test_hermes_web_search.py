"""Tests for Hermes ddgs web_search backend wiring.

Run: uv run --with pytest --with pyyaml pytest tests/test_hermes_web_search.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ensure_hermes_web_search import (  # noqa: E402
    apply_file,
    ensure_ddgs_backend,
)

DOCKERFILE = REPO_ROOT / "docker" / "hermes" / "Dockerfile"
START_SH = REPO_ROOT / "scripts" / "start.sh"


class TestDockerfile:
    """The Hermes image must install ddgs into the agent venv."""

    def test_dockerfile_installs_ddgs_with_uv(self):
        text = DOCKERFILE.read_text()
        assert "uv pip install --python /opt/hermes/.venv/bin/python3 ddgs" in text


class TestStartShWiring:
    """start.sh must call the helper against the host Hermes config."""

    def test_start_sh_invokes_ensure_script(self):
        text = START_SH.read_text()
        assert "ensure_hermes_web_search.py" in text
        assert "HERMES_DEFAULT_CONFIG" in text


class TestEnsureDdgsBackend:
    """Surgical YAML edits for web.search_backend."""

    def test_appends_web_block_when_missing(self):
        original = "model:\n  default: mimo-v2.5\ncron_mode: allow\n"
        new_text, status = ensure_ddgs_backend(original)
        assert status == "written"
        assert 'search_backend: "ddgs"' in new_text
        assert "cron_mode: allow" in new_text
        assert "model:" in new_text

    def test_inserts_under_existing_empty_web_block(self):
        original = (
            "model:\n  default: mimo-v2.5\nweb:\nmemory:\n  memory_enabled: true\n"
        )
        new_text, status = ensure_ddgs_backend(original)
        assert status == "written"
        assert 'web:\n  search_backend: "ddgs"\nmemory:' in new_text

    def test_inserts_under_web_with_other_keys(self):
        original = (
            "web:\n" "  extract_backend: none\n" "model:\n" "  default: mimo-v2.5\n"
        )
        new_text, status = ensure_ddgs_backend(original)
        assert status == "written"
        assert 'search_backend: "ddgs"' in new_text
        assert "extract_backend: none" in new_text

    def test_replaces_empty_inline_web(self):
        original = "model:\n  default: mimo-v2.5\nweb: {}\n"
        new_text, status = ensure_ddgs_backend(original)
        assert status == "written"
        assert "web: {}" not in new_text
        assert 'search_backend: "ddgs"' in new_text

    def test_idempotent_when_already_ddgs(self):
        original = 'web:\n  search_backend: "ddgs"\nmodel:\n  default: x\n'
        new_text, status = ensure_ddgs_backend(original)
        assert status == "unchanged:ddgs"
        assert new_text == original

    def test_does_not_overwrite_brave_free(self):
        original = "web:\n  search_backend: brave_free\n"
        new_text, status = ensure_ddgs_backend(original)
        assert status == "unchanged:brave_free"
        assert new_text == original

    def test_preserves_comments(self):
        original = "# keep this comment\n" "model:\n" "  default: mimo-v2.5  # inline\n"
        new_text, status = ensure_ddgs_backend(original)
        assert status == "written"
        assert "# keep this comment" in new_text
        assert "# inline" in new_text

    def test_invalid_yaml_is_a_no_op(self):
        original = "model: [\n  broken"
        new_text, status = ensure_ddgs_backend(original)
        assert status == "invalid"
        assert new_text == original

    def test_empty_file_writes_web_block(self):
        new_text, status = ensure_ddgs_backend("")
        assert status == "written"
        assert 'search_backend: "ddgs"' in new_text


class TestApplyFile:
    """Filesystem wrapper used by start.sh."""

    def test_missing_file(self, tmp_path: Path):
        assert apply_file(tmp_path / "nope.yaml") == "missing"

    def test_writes_then_is_idempotent(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        path.write_text("cron_mode: allow\n")
        assert apply_file(path) == "written"
        text = path.read_text()
        assert 'search_backend: "ddgs"' in text
        assert apply_file(path) == "unchanged:ddgs"
        assert path.read_text() == text
