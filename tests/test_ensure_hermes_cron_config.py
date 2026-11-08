"""守卫：ensure_hermes_cron_config.py 的幂等改写。

Run: uv run --with pytest --with pyyaml pytest tests/test_ensure_hermes_cron_config.py -q
"""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "ensure_hermes_cron_config_under_test",
        REPO_ROOT / "scripts" / "ensure_hermes_cron_config.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cron_cfg = _load()


def test_appends_block_when_missing():
    text = "model:\n  default: deepseek-flash\n"
    new_text, status = cron_cfg.ensure_wrap_response(text)
    assert status == "written"
    assert yaml.safe_load(new_text)["cron"]["wrap_response"] is False
    assert "model:\n  default: deepseek-flash" in new_text


def test_inserts_into_existing_block():
    text = "cron:\n  mirror_delivery: false\n"
    new_text, status = cron_cfg.ensure_wrap_response(text)
    assert status == "written"
    assert yaml.safe_load(new_text)["cron"] == {"wrap_response": False, "mirror_delivery": False}


def test_replaces_true():
    text = "cron:\n  wrap_response: true\n"
    new_text, status = cron_cfg.ensure_wrap_response(text)
    assert status == "written"
    assert yaml.safe_load(new_text)["cron"]["wrap_response"] is False


def test_unchanged_when_already_false():
    text = "cron:\n  wrap_response: false\n"
    new_text, status = cron_cfg.ensure_wrap_response(text)
    assert status == "unchanged:false"
    assert new_text == text


def test_expands_empty_inline_mapping():
    new_text, status = cron_cfg.ensure_wrap_response("cron: {}\n")
    assert status == "written"
    assert yaml.safe_load(new_text)["cron"]["wrap_response"] is False


def test_expands_null():
    new_text, status = cron_cfg.ensure_wrap_response("cron: null\n")
    assert status == "written"
    assert yaml.safe_load(new_text)["cron"]["wrap_response"] is False


def test_conflicts_on_scalar_cron():
    text = "cron: true\n"
    new_text, status = cron_cfg.ensure_wrap_response(text)
    assert status == "conflict:non-block-cron"
    assert new_text == text


def test_conflicts_on_complex_value():
    text = "cron:\n  wrap_response: {a: b}\n"
    new_text, status = cron_cfg.ensure_wrap_response(text)
    assert status == "conflict:complex-wrap-response"
    assert new_text == text


def test_invalid_yaml_is_not_written():
    text = "cron: [unclosed\n"
    new_text, status = cron_cfg.ensure_wrap_response(text)
    assert status == "invalid"
    assert new_text == text


def test_preserves_comments_and_order():
    text = "# top\ncron:\n  # keep me\n  mirror_delivery: false\nagent:\n  x: 1\n"
    new_text, status = cron_cfg.ensure_wrap_response(text)
    assert status == "written"
    assert "# top" in new_text
    assert "# keep me" in new_text
    assert new_text.index("cron:") < new_text.index("agent:")
    assert yaml.safe_load(new_text)["agent"] == {"x": 1}


def test_apply_file_dry_run_does_not_write(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  default: deepseek-flash\n")
    status = cron_cfg.apply_file(config, dry_run=True)
    assert status == "written"
    assert config.read_text() == "model:\n  default: deepseek-flash\n"


def test_apply_file_writes_atomically_and_keeps_mode(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  default: deepseek-flash\n")
    config.chmod(0o640)
    assert cron_cfg.apply_file(config) == "written"
    assert yaml.safe_load(config.read_text())["cron"]["wrap_response"] is False
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    assert cron_cfg.apply_file(config) == "unchanged:false"


def test_missing_file(tmp_path):
    assert cron_cfg.apply_file(tmp_path / "nope.yaml") == "missing"
