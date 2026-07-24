#!/usr/bin/env python3
"""Deterministic mock/prompt checks for the five required Yuque journeys."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "skills" / "yuque-knowledge" / "SKILL.md"


@dataclass
class MockTools:
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def call(self, name: str, **arguments: str) -> dict[str, str]:
        self.calls.append((name, arguments))
        return {"status": "fixture"}


def exercise(prompt: str, tools: MockTools) -> str:
    """Model the tool-selection contract expressed by the checked-in Skill."""
    if "备份" in prompt:
        tools.call("backup_repo", repo_namespace="team/docs", repo_display_name="团队文档")
        return "备份已写入本地持久化目录"
    if "变化" in prompt:
        tools.call("collect_and_get_change_summary", repo_namespace="team/docs")
        return "这是相邻完整快照的净变化；首次调用仅初始化"
    if "总结" in prompt:
        tools.call("get_doc_content", repo_namespace="team/docs", slug="architecture")
        return "已读取指定文档并总结"
    if "搜索" in prompt:
        tools.call("search_docs", repo_namespace="team/docs", query="架构")
        return "按标题搜索；结果可能受前 100 篇列表边界影响"
    if "目录" in prompt:
        tools.call("get_repo_toc", repo_namespace="team/docs")
        return "已返回结构化完整目录"
    raise AssertionError(f"unhandled fixture prompt: {prompt}")


def assert_journey(prompt: str, expected_tool: str, expected_text: str) -> None:
    tools = MockTools()
    response = exercise(prompt, tools)
    assert [name for name, _ in tools.calls] == [expected_tool]
    assert expected_text in response


def main() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    for required_rule in (
        "完整枚举优先使用它",
        "最多可能只覆盖前 100 篇",
        "避免批量读取正文",
        "相邻快照的净变化",
        "仅在用户明确要求“备份”时调用",
    ):
        assert required_rule in skill

    assert_journey("列出团队语雀知识库目录", "get_repo_toc", "完整目录")
    assert_journey("搜索标题包含架构的文档", "search_docs", "100 篇")
    assert_journey("总结 architecture 文档", "get_doc_content", "指定文档")
    assert_journey("查看知识库变化", "collect_and_get_change_summary", "相邻完整快照")
    assert_journey("备份团队文档知识库", "backup_repo", "持久化目录")

    non_backup_tools = MockTools()
    exercise("总结 architecture 文档", non_backup_tools)
    assert all(name != "backup_repo" for name, _ in non_backup_tools.calls)
    print("five mock user journeys passed")


if __name__ == "__main__":
    main()
