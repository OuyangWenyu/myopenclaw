"""Guards for scripts/ensure_openclaw_paper_queue.py — the idempotent 虾酱 config surgery.

Run: uv run --with pytest pytest tests/test_ensure_openclaw_paper_queue.py -v

The scary case is `plugins.allow`: it is an allowlist by *presence*, so creating it where
it did not exist would silently stop every other plugin from loading. That is exactly the
kind of one-way door a static guard cannot catch — hence real configs, real runs.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENSURE = REPO_ROOT / "scripts" / "ensure_openclaw_paper_queue.py"


@pytest.fixture(scope="module")
def ensure():
    spec = importlib.util.spec_from_file_location("ensure_pq", ENSURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_config(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, indent=2))
    return path


def read_config(path: Path) -> dict:
    return json.loads(path.read_text())


class TestFirstRun:
    def test_registers_mcp_server_and_enables_plugin(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {"tools": {}})
        assert ensure.apply(cfg) is True

        data = read_config(cfg)
        assert data["mcp"]["servers"]["paper-queue"]["command"] == "python3"
        assert "/home/node/.openclaw/skills/paper-queue/mcp_server.py" in \
            data["mcp"]["servers"]["paper-queue"]["args"]
        assert "/home/node/.openclaw/extensions/paper-queue-actor" in data["plugins"]["load"]["paths"]
        assert data["plugins"]["entries"]["paper-queue-actor"]["enabled"] is True

    def test_disables_paper_fetch(self, ensure, tmp_path):
        """「只记不下」必须是机器强制 —— 两个 skill 的触发语高度重叠。"""
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg)
        assert read_config(cfg)["skills"]["entries"]["paper-fetch"] == {"enabled": False}

    def test_does_not_create_an_allowlist_from_nothing(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg)
        assert "allow" not in read_config(cfg).get("plugins", {}), (
            "配置里原本没有 plugins.allow（= 全部允许）；凭空创建一个只含自己的白名单，"
            "会把 deepseek/discord/feishu 等所有插件一起挡掉"
        )

    def test_appends_to_an_existing_allowlist(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {"plugins": {"allow": ["discord"]}})
        ensure.apply(cfg)
        allow = read_config(cfg)["plugins"]["allow"]
        assert allow == ["discord", "paper-queue-actor"], "必须追加而不是覆盖"


class TestActorSecret:
    """插件签、服务端验，两边密钥必须**逐字相同** —— 不一致会让每次写入都被拒。"""

    def test_secret_is_shared_between_plugin_and_server(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg)
        data = read_config(cfg)
        from_plugin = data["plugins"]["entries"]["paper-queue-actor"]["config"]["secret"]
        from_server = data["mcp"]["servers"]["paper-queue"]["env"]["PAPER_QUEUE_ACTOR_SECRET"]
        assert from_plugin == from_server
        assert len(from_plugin) >= 32, "密钥太短"

    def test_secret_is_stable_across_runs(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg)
        first = read_config(cfg)["plugins"]["entries"]["paper-queue-actor"]["config"]["secret"]
        ensure.apply(cfg)
        again = read_config(cfg)["plugins"]["entries"]["paper-queue-actor"]["config"]["secret"]
        assert first == again, "重跑脚本不能换密钥 —— 那会让所有写入被拒"

    def test_rotating_by_deleting_the_secret(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg)
        data = read_config(cfg)
        data["plugins"]["entries"]["paper-queue-actor"]["config"].pop("secret")
        cfg.write_text(json.dumps(data))
        assert ensure.apply(cfg) is True
        rotated = read_config(cfg)["plugins"]["entries"]["paper-queue-actor"]["config"]["secret"]
        assert rotated and rotated != data["mcp"]["servers"]["paper-queue"]["env"]["PAPER_QUEUE_ACTOR_SECRET"]

    def test_preserves_operator_added_server_keys(self, ensure, tmp_path):
        """运维手工往 server 条目里加的键（如改 DB 路径）不能被覆盖掉。"""
        cfg = write_config(tmp_path / "o.json", {"mcp": {"servers": {"paper-queue": {
            "command": "python3", "args": ["/x.py"], "cwd": "/custom"}}}})
        ensure.apply(cfg)
        server = read_config(cfg)["mcp"]["servers"]["paper-queue"]
        assert server["cwd"] == "/custom"
        assert server["args"] == ["/home/node/.openclaw/skills/paper-queue/mcp_server.py"]


class TestIdempotency:
    def test_second_run_makes_no_change(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {"plugins": {"allow": ["discord"]}})
        assert ensure.apply(cfg) is True
        first = cfg.read_text()
        assert ensure.apply(cfg) is False
        assert cfg.read_text() == first

    def test_preserves_unrelated_keys(self, ensure, tmp_path):
        original = {
            "channels": {"discord": {"token": "x", "allowFrom": ["1"]}},
            "tools": {"profile": "coding", "exec": {"mode": "ask"}},
            "plugins": {"allow": ["discord"], "entries": {"discord": {"enabled": True}}},
        }
        cfg = write_config(tmp_path / "o.json", original)
        ensure.apply(cfg)
        data = read_config(cfg)
        assert data["channels"] == original["channels"]
        assert data["tools"] == original["tools"]
        assert data["plugins"]["entries"]["discord"] == {"enabled": True}

    def test_does_not_tighten_exec_policy(self, ensure, tmp_path):
        """本功能与 host exec 姿态无关，绝不能顺手改动它。"""
        cfg = write_config(tmp_path / "o.json", {"tools": {"exec": {"mode": "ask"}}})
        ensure.apply(cfg)
        assert read_config(cfg)["tools"]["exec"]["mode"] == "ask"


class TestCli:
    def test_prints_updated_then_unchanged(self, tmp_path):
        import subprocess
        import sys
        cfg = write_config(tmp_path / "o.json", {})
        first = subprocess.run([sys.executable, str(ENSURE), str(cfg)],
                               capture_output=True, text=True)
        second = subprocess.run([sys.executable, str(ENSURE), str(cfg)],
                                capture_output=True, text=True)
        assert (first.returncode, first.stdout.strip()) == (0, "updated")
        assert (second.returncode, second.stdout.strip()) == (0, "unchanged")

    def test_missing_config_is_a_script_error(self, tmp_path):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, str(ENSURE), str(tmp_path / "nope.json")],
                           capture_output=True, text=True)
        assert r.returncode == 2

    def test_malformed_config_is_a_script_error(self, tmp_path):
        import subprocess
        import sys
        cfg = tmp_path / "bad.json"
        cfg.write_text("{not json")
        r = subprocess.run([sys.executable, str(ENSURE), str(cfg)],
                           capture_output=True, text=True)
        assert r.returncode == 2
