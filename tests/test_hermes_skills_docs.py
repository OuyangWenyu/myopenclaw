"""Hermes skill 接线的文档守卫：live 执行源是原生副本，跨文档必须写清楚且口径一致。

背景（2026-10-10 实测，issue #79 排查中发现）：仓库 `skills/` 只挂载到 `/opt/hermes-skills`
（**未注册进 skills.external_dirs**），Hermes 实际加载/执行的是 `~/.hermes/skills` 原生副本
（`hermes skills list` 里 source=local）。「改仓库版 ≠ 改线上行为」——文档若回到"挂载即生效"
的旧说法，会再次误导排查（2026-10-10 早晨那条误诊日报即源于同类过时认知）。

Run: uv run --with pytest --with pyyaml pytest tests/test_hermes_skills_docs.py -v
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestHermesSkillsDoc:
    def test_page_exists_with_core_claims(self):
        doc = (REPO_ROOT / "docs" / "hermes-skills.md").read_text()
        assert "原生副本" in doc, "必须写明 live 执行源是 ~/.hermes/skills 原生副本"
        assert "/opt/hermes-skills" in doc, "必须交代仓库挂载的位置与（非）作用"
        assert "external_dirs" in doc, "必须交代外部目录注册机制（现状未注册）"


class TestCrossReferences:
    def test_yuque_doc_points_to_skills_doc(self):
        yuque = (REPO_ROOT / "docs" / "yuque-mcp-hermes.md").read_text()
        assert "hermes-skills.md" in yuque
        assert "原生副本" in yuque, "语雀日报章节必须说明执行源是原生副本（带脚本 fallback）"

    def test_claude_md_records_the_wiring(self):
        claude = (REPO_ROOT / "CLAUDE.md").read_text()
        assert "hermes-skills.md" in claude or "原生副本" in claude

    def test_mkdocs_nav_includes_the_page(self):
        nav = (REPO_ROOT / "mkdocs.yml").read_text()
        assert "hermes-skills.md" in nav


class TestCronRunDeliveryWording:
    """2026-10-10 实测：手动 `hermes cron run` 会**真投递**（delivery_outcome=delivered，
    10-09/10-10 各一次）。文档不得再留「只跑不投 / 不会真投递」的旧结论。"""

    def test_no_stale_claims_in_docs(self):
        for rel in ("CLAUDE.md", "docs/yuque-mcp-hermes.md"):
            text = (REPO_ROOT / rel).read_text()
            assert "只跑不投" not in text, f"{rel} 残留旧结论「只跑不投」"
            assert "不会真投递" not in text, f"{rel} 残留旧结论「不会真投递」"
