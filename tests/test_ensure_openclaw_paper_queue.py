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
SECRET = "test-secret-0123456789abcdef"


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
        assert ensure.apply(cfg, SECRET) is True

        data = read_config(cfg)
        server = data["mcp"]["servers"]["paper-queue"]
        assert server["url"].startswith("http://paper-queue-mcp:"), (
            "服务在独立容器里 —— 配成 stdio 形态意味着队列目录又挂回了虾酱所在的网关"
        )
        assert server["transport"] == "streamable-http"
        assert "/home/node/.openclaw/extensions/paper-queue-actor" in data["plugins"]["load"]["paths"]
        assert data["plugins"]["entries"]["paper-queue-actor"]["enabled"] is True

    def test_disables_paper_fetch(self, ensure, tmp_path):
        """「只记不下」必须是机器强制 —— 两个 skill 的触发语高度重叠。"""
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg, SECRET)
        assert read_config(cfg)["skills"]["entries"]["paper-fetch"] == {"enabled": False}

    def test_does_not_create_an_allowlist_from_nothing(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg, SECRET)
        assert "allow" not in read_config(cfg).get("plugins", {}), (
            "配置里原本没有 plugins.allow（= 全部允许）；凭空创建一个只含自己的白名单，"
            "会把 deepseek/discord/feishu 等所有插件一起挡掉"
        )

    def test_appends_to_an_existing_allowlist(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {"plugins": {"allow": ["discord"]}})
        ensure.apply(cfg, SECRET)
        allow = read_config(cfg)["plugins"]["allow"]
        assert allow == ["discord", "paper-queue-actor"], "必须追加而不是覆盖"


class TestActorSecret:
    """密钥由 .env 提供（start.sh 生成并传入），本脚本把它写进**插件**配置。

    唯一来源是刻意的：两处各自生成一旦不一致，表现是"每次写入都被拒"，而根因很难找。
    服务端那份由 compose 注入 sidecar（同一个 .env 变量），不在这里出现。
    """

    def test_secret_lands_in_the_plugin_config(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg, SECRET)
        entry = read_config(cfg)["plugins"]["entries"]["paper-queue-actor"]
        assert entry["config"]["secret"] == SECRET

    def test_the_server_entry_carries_no_secret(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {})
        ensure.apply(cfg, SECRET)
        server = read_config(cfg)["mcp"]["servers"]["paper-queue"]
        assert not ({"command", "args", "env"} & set(server))

    def test_stale_stdio_keys_are_removed(self, ensure, tmp_path):
        """早先是 stdio 形态；残留的 command/args/env 会让配置自相矛盾。"""
        cfg = write_config(tmp_path / "o.json", {"mcp": {"servers": {"paper-queue": {
            "command": "python3", "args": ["/old.py"], "env": {"X": "1"}}}}})
        ensure.apply(cfg, SECRET)
        server = read_config(cfg)["mcp"]["servers"]["paper-queue"]
        assert not ({"command", "args", "env"} & set(server))

    def test_preserves_operator_added_server_keys(self, ensure, tmp_path):
        """运维手工加的键（如 cwd）不能被抹掉 —— 那种丢失是静默的。"""
        cfg = write_config(tmp_path / "o.json", {"mcp": {"servers": {"paper-queue": {
            "url": "http://old", "cwd": "/custom"}}}})
        ensure.apply(cfg, SECRET)
        assert read_config(cfg)["mcp"]["servers"]["paper-queue"]["cwd"] == "/custom"

    def test_rotating_the_secret_counts_as_a_change(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {})
        assert ensure.apply(cfg, SECRET) is True
        assert ensure.apply(cfg, SECRET) is False
        assert ensure.apply(cfg, "another-secret") is True
        assert read_config(cfg)["plugins"]["entries"]["paper-queue-actor"]["config"]["secret"] \
            == "another-secret"

    def test_cli_without_secret_fails_loudly(self, tmp_path):
        """没走 start.sh 就该响亮失败，而不是自己造一个、然后与服务端那份对不上。"""
        import subprocess
        import sys
        cfg = write_config(tmp_path / "o.json", {})
        r = subprocess.run([sys.executable, str(ENSURE), str(cfg)],
                           capture_output=True, text=True)
        assert r.returncode == 2
        assert "密钥" in r.stderr


class TestIdempotency:
    def test_second_run_makes_no_change(self, ensure, tmp_path):
        cfg = write_config(tmp_path / "o.json", {"plugins": {"allow": ["discord"]}})
        assert ensure.apply(cfg, SECRET) is True
        first = cfg.read_text()
        assert ensure.apply(cfg, SECRET) is False
        assert cfg.read_text() == first

    def test_preserves_unrelated_keys(self, ensure, tmp_path):
        original = {
            "channels": {"discord": {"token": "x", "allowFrom": ["1"]}},
            "tools": {"profile": "coding", "exec": {"mode": "ask"}},
            "plugins": {"allow": ["discord"], "entries": {"discord": {"enabled": True}}},
        }
        cfg = write_config(tmp_path / "o.json", original)
        ensure.apply(cfg, SECRET)
        data = read_config(cfg)
        assert data["channels"] == original["channels"]
        assert data["tools"] == original["tools"]
        assert data["plugins"]["entries"]["discord"] == {"enabled": True}

    def test_does_not_tighten_exec_policy(self, ensure, tmp_path):
        """本功能与 host exec 姿态无关，绝不能顺手改动它。"""
        cfg = write_config(tmp_path / "o.json", {"tools": {"exec": {"mode": "ask"}}})
        ensure.apply(cfg, SECRET)
        assert read_config(cfg)["tools"]["exec"]["mode"] == "ask"


class TestCli:
    def test_prints_updated_then_unchanged(self, tmp_path):
        import subprocess
        import sys
        cfg = write_config(tmp_path / "o.json", {})
        first = subprocess.run([sys.executable, str(ENSURE), str(cfg), SECRET],
                               capture_output=True, text=True)
        second = subprocess.run([sys.executable, str(ENSURE), str(cfg), SECRET],
                                capture_output=True, text=True)
        assert (first.returncode, first.stdout.strip()) == (0, "updated")
        assert (second.returncode, second.stdout.strip()) == (0, "unchanged")

    def test_missing_config_is_a_script_error(self, tmp_path):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, str(ENSURE), str(tmp_path / "nope.json"), SECRET],
                           capture_output=True, text=True)
        assert r.returncode == 2

    def test_malformed_config_is_a_script_error(self, tmp_path):
        import subprocess
        import sys
        cfg = tmp_path / "bad.json"
        cfg.write_text("{not json")
        r = subprocess.run([sys.executable, str(ENSURE), str(cfg), SECRET],
                           capture_output=True, text=True)
        assert r.returncode == 2
