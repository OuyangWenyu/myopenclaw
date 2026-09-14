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

    def test_feishu_streaming_is_object_not_bool(self, label):
        """2.0 起 `channels.feishu.streaming` 是**对象** `{mode: partial|off}`，不是布尔。

        实测：模板原样送 2026.9.1 的 `config validate` 会报
        `× channels.feishu.streaming: invalid config: must be object`。
        （渲染脚本会覆盖这个键，所以生产侧不可达 —— 但模板自身应当与目标 schema 对齐，
        否则它是一份"看着能抄、抄了就 invalid"的样例。）
        """
        feishu = (_load(TEMPLATES[label]).get("channels") or {}).get("feishu") or {}
        if "streaming" in feishu:
            assert isinstance(feishu["streaming"], dict), (
                f"{label}: channels.feishu.streaming 必须是对象（如 {{\"mode\": \"partial\"}}），"
                "布尔是 2.0 已淘汰的写法"
            )

    def test_agents_uses_keyed_entries_not_list(self, label):
        """2.0 把 `agents.list`（数组 + `default: true`）改为按 id 键控的 `agents.entries`。

        旧写法不是 invalid（2.0 会自动迁移并打警告），但每次启动都要迁一次、
        日志常驻一条 warning，且模板与目标 schema 不对齐。实测两个 bot 模板都是旧写法
        （见 2026.9.1 的 `Moved agents.list to keyed agents.entries`）。
        """
        agents = _load(TEMPLATES[label]).get("agents") or {}
        assert "list" not in agents, (
            f"{label}: agents.list 应改为键控的 agents.entries（去掉 default: true，id 作为键）"
        )
        if "entries" in agents:
            assert isinstance(agents["entries"], dict), (
                f"{label}: agents.entries 应为按键控的映射"
            )


BOT_TEMPLATES = {
    "zhixun": REPO_ROOT / "docker" / "zhixun-bot" / "openclaw.json.template",
    "tianyi": REPO_ROOT / "docker" / "tianyi-bot" / "openclaw.json.template",
}

# 主模板里那条节点命令拒绝清单 —— 2.0 把它从 `nodes.denyCommands` 挪到
# `nodes.commands.deny`。只断言"没有旧键"是查不出"新键被删"的（实测：删掉整条，
# 旧断言仍全绿），所以这里把内容也钉住。
NODE_DENY_COMMANDS = [
    "camera.snap",
    "camera.clip",
    "screen.record",
    "contacts.add",
    "calendar.add",
    "reminders.add",
    "sms.send",
]


class TestMainExampleNodeDenylist:
    """授权面不能只靠"旧键不存在"来守 —— 新键被删也该红。"""

    def test_denylist_present_with_expected_commands(self):
        cfg = _load(TEMPLATES["主模板 openclaw.json.example"])
        deny = ((cfg.get("gateway") or {}).get("nodes") or {}).get("commands", {}).get("deny")
        assert deny == NODE_DENY_COMMANDS, (
            f"主模板的 gateway.nodes.commands.deny 应保留全部 {len(NODE_DENY_COMMANDS)} 条，"
            f"实际 {deny!r}"
        )


class TestBotTemplatesCarryMeta:
    """bot 模板必须带 `meta`，否则**每次渲染都会被 last-known-good 静默覆盖**。

    两个 bot 的 entrypoint 每次启动都从模板重渲染 `openclaw.json`；而 2026.9.1 的配置
    写入会用 `missing-meta-vs-last-good` 判据把「疑似异常」的写入回滚到上一份好配置
    （镜像内 `dist/io.runtime-*.js` 的 `resolveConfigObserveSuspiciousReasons`：

        if (baseline.hasMeta && !params.hasMeta) reasons.push("missing-meta-vs-last-good");

    ）。模板天生没有 `meta` ⇒ 渲染产物**每次都被丢弃**，于是：
      - 改模板（如本次删 `resetOnExit`）对运行中的 bot 零效果
      - 在 `.env.*-bot` 里轮换凭据（如 `*_FEISHU_APP_SECRET`）会被静默丢弃
    实测证据：重启后数据目录里出现 `openclaw.json.clobbered.<时间戳>`，
    live 配置是被恢复的 last-good（含 `meta`/`wizard`），不是渲染产物。

    主模板 `openclaw.json.example` 不在此列 —— 它只在首启 `cp` 一次，不走"每次重渲染"
    这条路径，因此不触发该判据。
    """

    @pytest.mark.parametrize("label", sorted(BOT_TEMPLATES))
    def test_bot_template_has_meta_object(self, label):
        cfg = _load(BOT_TEMPLATES[label])
        assert isinstance(cfg.get("meta"), dict), (
            f"{label}: 缺少 meta 段 —— 渲染产物会被 last-known-good 静默覆盖"
        )

    @pytest.mark.parametrize("label", sorted(BOT_TEMPLATES))
    def test_bot_template_does_not_claim_a_version(self, label):
        """模板**不得**写 `meta.lastTouchedVersion`。

        OpenClaw 用它做「未来版本保护」：若该值比当前二进制新，网关会**拒绝启动**
        （实测服务模式 exit 78）。而模板是每次渲染都重写的 —— 一旦把它钉成镜像 tag，
        「升级 → 出问题 → 回滚 tag」就会让 bot 起不来，且仓库里没有任何逃生口。

        `hasConfigMeta` 只要求 `meta` 是对象（镜像内 `io.read-helpers-*.js`），
        所以 `meta: {}` 既满足回滚判据，又不做不实的版本声明 —— OpenClaw 自己写配置时
        会把它替换成真实版本。
        """
        meta = _load(BOT_TEMPLATES[label]).get("meta") or {}
        assert "lastTouchedVersion" not in meta, (
            f"{label}: 模板不应声明 meta.lastTouchedVersion（会变成降级闸门）"
        )
