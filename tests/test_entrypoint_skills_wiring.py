"""entrypoint-wrapper 的 skills 接线守卫。

回归 2026-10-12（晨间简报撞名复发）：
  wrapper 里的 morning-briefing symlink 块（PR #16 时代遗留）每次容器启动都把
  `~/.hermes/skills/morning-briefing` 软链回仓库版，与 agent 自维护的原生副本
  `~/.hermes/skills/devops/morning-briefing` 同名 → skill 解析报
  "Ambiguous skill name … Refusing to guess" → cron 预加载被静默跳过
  （`skill not found, skipping`，job last_status 仍显 ok）。

仓库版不是 live 源：live 始终是 `~/.hermes/skills/devops/morning-briefing`
原生副本（job prompt 硬编码的 unread_report.py 等只在原生副本里）。仓库版自
2026-10-13 起同步了日期修复文件（SKILL.md + scripts/{today,cst_today}.py，
见 docs/hermes-skills.md），但接线块 **不得**因「仓库版更全了」而复活 ——
它属 PR #16 时代历史遗留，2026-10-12 已从 wrapper 删除。本测试钉住：
  ① morning-briefing 的 symlink 块不得复活；
  ② 其余三个 symlink 块（triage/dev-report/yuque）与 gitcode 拷贝块保留（防误删）；
  ③ 文档已记录谜底（来源 = wrapper，已移除），不再写「尚未查明」。

Run: uv run --with pytest pytest tests/test_entrypoint_skills_wiring.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"
DOCS = REPO_ROOT / "docs" / "hermes-skills.md"


class TestMorningBriefingSymlinkRemoved:
    """撞名复发的根因块：仓库版 → ~/.hermes/skills/morning-briefing 的软链。"""

    def test_no_symlink_block_for_morning_briefing(self):
        text = WRAPPER.read_text()
        assert "hermes-skills/morning-briefing" not in text, (
            "morning-briefing symlink 块复活了 —— 会与原生 devops/morning-briefing "
            "撞名，每条 07:30 晨间简报的 skill 预加载都会被拒（2026-10-12 事故）"
        )

    def test_no_ln_command_for_morning_briefing(self):
        for line in WRAPPER.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("ln ") and "morning-briefing" in stripped:
                pytest.fail(f"wrapper 里残留 morning-briefing 链接命令: {line!r}")


class TestOtherSkillBlocksRemain:
    """其余接线块是有意保留的 —— 防止修 morning-briefing 时误删。"""

    @pytest.mark.parametrize(
        "name", ["morning-triage-v2", "daily-dev-report", "yuque-daily-digest"]
    )
    def test_symlink_block_still_present(self, name: str):
        assert f"hermes-skills/{name}" in WRAPPER.read_text(), (
            f"{name} 的 symlink 接线块被误删了"
        )

    def test_gitcode_copy_block_remains(self):
        assert (
            "/usr/local/lib/node_modules/gitcode-cli/skills/gitcode-cli"
            in WRAPPER.read_text()
        ), "gitcode-cli 拷贝块被误删了"


class TestDocsRecordTheResolution:
    """文档必须交代真实来源；「尚未查明」是 2026-10-10 的旧状态。"""

    def test_docs_no_longer_say_source_unknown(self):
        docs = DOCS.read_text()
        assert "尚未查明" not in docs, (
            "docs/hermes-skills.md 仍写着 symlink 来源未查明 —— 2026-10-12 已查明"
            "（entrypoint-wrapper 的 morning-briefing 块）并移除"
        )

    def test_docs_mention_the_wrapper_source(self):
        docs = DOCS.read_text()
        assert "entrypoint-wrapper" in docs, (
            "文档必须记录 morning-briefing symlink 的真实重建来源（entrypoint-wrapper）"
        )
