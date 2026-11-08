"""守卫：remove_openclaw_paper_queue.py 的幂等清理。

Run: uv run --with pytest pytest tests/test_remove_openclaw_paper_queue.py -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "remove_openclaw_paper_queue_under_test",
        REPO_ROOT / "scripts" / "remove_openclaw_paper_queue.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cleanup = _load()

BASE = {
    "mcp": {
        "servers": {
            "dailyinfo": {"url": "http://dailyinfo-mcp:8768/mcp"},
            "paper-queue": {"url": "http://paper-queue-mcp:8003/mcp"},
            "github": {"command": "npx"},
        }
    },
    "plugins": {
        "allow": ["discord", "paper-queue-actor", "feishu"],
        "entries": {
            "discord": {"enabled": True},
            "paper-queue-actor": {"config": {"secret": "x"}},
        },
        "load": {
            "paths": [
                "/home/node/.openclaw/extensions/paper-queue-actor",
                "/opt/other-plugin",
            ]
        },
    },
    "skills": {
        "entries": {
            "paper-fetch": {"enabled": False},
            "paper-queue": {"enabled": True},
        }
    },
}


def test_removes_every_paper_queue_artifact():
    data, status = cleanup.remove_artifacts(BASE)
    assert status == "removed"
    assert "paper-queue" not in data["mcp"]["servers"]
    assert "paper-queue-actor" not in data["plugins"]["entries"]
    assert "paper-queue-actor" not in data["plugins"]["allow"]
    assert data["plugins"]["load"]["paths"] == ["/opt/other-plugin"]
    assert "paper-queue" not in data["skills"]["entries"]
    # 兄弟项与 paper-fetch 的 disabled 状态保持不变
    assert data["mcp"]["servers"]["dailyinfo"]["url"] == "http://dailyinfo-mcp:8768/mcp"
    assert data["plugins"]["entries"]["discord"]["enabled"] is True
    assert data["skills"]["entries"]["paper-fetch"] == {"enabled": False}


def test_is_idempotent():
    once, _ = cleanup.remove_artifacts(BASE)
    twice, status = cleanup.remove_artifacts(once)
    assert status == "unchanged"
    assert twice == once


def test_input_is_not_mutated():
    snapshot = json.loads(json.dumps(BASE))
    cleanup.remove_artifacts(BASE)
    assert BASE == snapshot


def test_drops_empty_load_paths_key():
    data = {"plugins": {"load": {"paths": ["/home/node/.openclaw/extensions/paper-queue-actor"]}}}
    out, status = cleanup.remove_artifacts(data)
    assert status == "removed"
    assert "paths" not in out["plugins"]["load"]


def test_missing_keys_are_noops():
    data, status = cleanup.remove_artifacts({"mcp": {"servers": {}}})
    assert status == "unchanged"
    assert data == {"mcp": {"servers": {}}}


def test_apply_file_dry_run_does_not_write(tmp_path):
    config = tmp_path / "openclaw.json"
    config.write_text(json.dumps(BASE))
    assert cleanup.apply_file(config, dry_run=True) == "removed"
    assert json.loads(config.read_text()) == BASE


def test_apply_file_writes_atomically_and_preserves_mode(tmp_path):
    config = tmp_path / "openclaw.json"
    config.write_text(json.dumps(BASE))
    config.chmod(0o640)
    assert cleanup.apply_file(config) == "removed"
    data = json.loads(config.read_text())
    assert "paper-queue" not in data["mcp"]["servers"]
    assert oct(config.stat().st_mode & 0o777) == "0o640"
    assert cleanup.apply_file(config) == "unchanged"


def test_missing_file(tmp_path):
    assert cleanup.apply_file(tmp_path / "nope.json") == "missing"
