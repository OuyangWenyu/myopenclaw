"""OpenClaw 配置模板必须与目标版本的 schema 对齐。

Run: uv run --with pytest --with pyyaml pytest tests/test_openclaw_schema.py -v

这三个文件是**新部署的唯一来源**：`openclaw/config/openclaw.json.example` 由
`start.sh` 首启时 `cp` 成 `~/.openclaw/openclaw.json`，两个 bot 模板由
`render-config.mjs` 每次启动渲染。它们含旧版键的后果是「新部署从第一天就是
invalid」—— 而 invalid 在旧版只是警告、在新版会让网关拒绝启动或回退到
last-known-good 配置（参见 2026.7.1→2026.8.x 的迁移事故）。

下列 retired key 来自 2026.9.1 的 `config validate` 对我们真实配置的实测输出
（`scripts/rehearse-openclaw-migration.sh` 会复跑那条路径）：

    meta.lastTouchedAt           移除
    messages.tts                 重命名 → tts
    commands.ownerDisplay        移除
    gateway.tailscale.resetOnExit 移除
    gateway.nodes.denyCommands   重命名 → gateway.nodes.commands.deny

注意后两条是**重命名**：用新名字合法，用旧名字 invalid。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

TEMPLATES = {
    "主模板 openclaw.json.example": REPO_ROOT / "openclaw" / "config" / "openclaw.json.example",
    "zhixun 模板": REPO_ROOT / "docker" / "zhixun-bot" / "openclaw.json.template",
    "tianyi 模板": REPO_ROOT / "docker" / "tianyi-bot" / "openclaw.json.template",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.mark.parametrize("label", sorted(TEMPLATES))
class TestNoRetiredOpenclawKeys:
    def test_no_meta_last_touched_at(self, label):
        cfg = _load(TEMPLATES[label])
        assert "lastTouchedAt" not in (cfg.get("meta") or {}), (
            f"{label}: meta.lastTouchedAt 已被 2.0 移除"
        )

    def test_tts_is_top_level_not_under_messages(self, label):
        cfg = _load(TEMPLATES[label])
        messages = cfg.get("messages")
        assert not messages, (
            f"{label}: messages 段已被 2.0 废弃（messages.tts → 顶层 tts）；"
            f"实际仍有 {sorted(messages) if isinstance(messages, dict) else messages}"
        )

    def test_no_commands_owner_display(self, label):
        cfg = _load(TEMPLATES[label])
        assert "ownerDisplay" not in (cfg.get("commands") or {}), (
            f"{label}: commands.ownerDisplay 已被 2.0 移除"
        )

    def test_no_gateway_tailscale_reset_on_exit(self, label):
        cfg = _load(TEMPLATES[label])
        tailscale = (cfg.get("gateway") or {}).get("tailscale") or {}
        assert "resetOnExit" not in tailscale, (
            f"{label}: gateway.tailscale.resetOnExit 已被 2.0 移除"
        )

    def test_nodes_deny_commands_uses_new_path(self, label):
        """`gateway.nodes.denyCommands` → `gateway.nodes.commands.deny`。"""
        cfg = _load(TEMPLATES[label])
        nodes = (cfg.get("gateway") or {}).get("nodes") or {}
        assert "denyCommands" not in nodes, (
            f"{label}: gateway.nodes.denyCommands 应改为 gateway.nodes.commands.deny"
        )
