"""成员文档与文档结构守卫（TDD：先于内容编写）。

守卫四类承诺（2026-10-14 文档结构重整）：
1. 「成员接入指南」在天一知识库（openclaw-tianyi/workspace/ACCESS.md）——
   含工具清单两拨 + 三服务 + member-check 指路，且**绝不含任何凭据样式串**
   （token 只写"向谁获取"）；
2. `.env.member.example` 只含占位/空值（token 一律留空，防"复制即用旧 token"）；
3. 文档结构一致性：docs/ 下**每个** .md 都必须出现在 mkdocs.yml nav（无孤儿页），
   nav 引用的文件也必须存在——孤儿页在 --strict 下不报错，只会静默不可达；
4. 服务器部署路由页存在且把「天一（服务器侧）」与「hermes 个人栈」的差别写清楚；
   README 有「我该从哪开始」的读者路由。

Run: uv run --with pytest --with pyyaml pytest tests/test_member_docs.py -v
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ACCESS_MD = REPO_ROOT / "openclaw-tianyi" / "workspace" / "ACCESS.md"
ENV_EXAMPLE = REPO_ROOT / ".env.member.example"
GITIGNORE = REPO_ROOT / ".gitignore"
MKDOCS = REPO_ROOT / "mkdocs.yml"
DOCS_DIR = REPO_ROOT / "docs"
INDEX_MD = DOCS_DIR / "index.md"
SERVER_DEPLOY_MD = DOCS_DIR / "server-deploy.md"
README = REPO_ROOT / "README.md"

# 凭据样式串（出现即泄露事故）—— ACCESS.md 是**公开仓库**里的文件（虽然进的是天一容器）
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{10,}",
    r"ghp_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"Bearer\s+[A-Za-z0-9_\-]{20,}",
    r"\b[0-9a-fA-F]{32,}\b",           # 长 hex（token/密钥常见形态；2026-10-14 review 补）
    r"\b[A-Za-z0-9+/]{40,}={0,2}\b",   # 长 base64（够长才匹配，避免误伤普通标识符）
]


class TestAccessGuide:
    def test_file_exists(self):
        assert ACCESS_MD.is_file(), "成员接入指南应位于 openclaw-tianyi/workspace/ACCESS.md（天一知识库）"

    def test_no_credential_shaped_strings(self):
        if not ACCESS_MD.is_file():
            pytest.skip("文件未创建")
        text = ACCESS_MD.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert not re.search(pattern, text), f"ACCESS.md 含凭据样式串（/{pattern}/）——token 只能写'向谁获取'"

    def test_names_the_bot_tianyi(self):
        if not ACCESS_MD.is_file():
            pytest.skip("文件未创建")
        assert "天一" in ACCESS_MD.read_text(encoding="utf-8")

    def test_covers_agent_tool_list_two_groups(self):
        if not ACCESS_MD.is_file():
            pytest.skip("文件未创建")
        text = ACCESS_MD.read_text(encoding="utf-8")
        for tool in ["WorkBuddy", "千问办公", "豆包", "DeepSeek Harness", "Claude Code", "Codex"]:
            assert tool in text, f"工具清单（两拨覆盖）缺少: {tool}"

    def test_mentions_three_services(self):
        if not ACCESS_MD.is_file():
            pytest.skip("文件未创建")
        text = ACCESS_MD.read_text(encoding="utf-8")
        for term in ["服务器监控", "语雀", "仓库"]:
            assert term in text, f"未提到服务: {term}"

    def test_points_to_member_check(self):
        if not ACCESS_MD.is_file():
            pytest.skip("文件未创建")
        assert "member-check" in ACCESS_MD.read_text(encoding="utf-8")

    def test_token_acquisition_is_by_contact_not_value(self):
        if not ACCESS_MD.is_file():
            pytest.skip("文件未创建")
        text = ACCESS_MD.read_text(encoding="utf-8")
        assert "获取" in text, "应写清 token 的获取方式（向谁要）"


class TestEnvMemberExample:
    def test_file_exists(self):
        assert ENV_EXAMPLE.is_file()

    def test_has_three_capability_urls(self):
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        for key in ["SERVER_MONITORING_MCP_URL", "YUQUE_MCP_URL", "REPO_SCANNER_MCP_URL"]:
            assert re.search(rf"^{key}=", text, re.M), f"缺少 {key}"

    def test_all_token_values_are_empty(self):
        """token 一律留空 —— 防'复制模板即用旧 token'，也防误提交。

        正则容忍 `KEY = value`（等号两侧留空格）——否则带空格的赋值写进 token 也发现不了。
        """
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z_]+)\s*=\s*(.*)$", line.strip())
            if m and m.group(1).endswith("_TOKEN"):
                assert m.group(2).strip() == "", f"{m.group(1)} 非空——示例里 token 必须留空"

    def test_no_credential_shaped_strings(self):
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert not re.search(pattern, text)


class TestGitignore:
    def test_env_member_ignored_and_example_allowed(self):
        lines = GITIGNORE.read_text(encoding="utf-8").splitlines()
        # `.env.member*` 覆盖编辑器伴生文件（~ / .swp / .bak——vim/emacs 用户在编辑
        # 这个含 token 的文件时都会产生），`!` 再把示例模板捞回来
        assert ".env.member*" in lines
        assert "!.env.member.example" in lines

    def test_gitignore_semantics_via_git_itself(self):
        """行文本对了还不够——用 git 本身验证语义（一次 `git add .` 就进公开仓库）。"""
        for name in (".env.member", ".env.member~", ".env.member.swp", ".env.member.bak"):
            rc = subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO_ROOT).returncode
            assert rc == 0, f"{name} 未被 gitignore 忽略（rc={rc}）"
        rc = subprocess.run(["git", "check-ignore", "-q", ".env.member.example"], cwd=REPO_ROOT).returncode
        assert rc != 0, ".env.member.example 必须可提交（模板是新部署的唯一来源）"


def _flatten_nav(node) -> list[str]:
    refs: list[str] = []
    if isinstance(node, str):
        refs.append(node)
    elif isinstance(node, list):
        for item in node:
            refs.extend(_flatten_nav(item))
    elif isinstance(node, dict):
        for value in node.values():
            refs.extend(_flatten_nav(value))
    return refs


class TestDocsNavCoverage:
    def _nav_refs(self) -> set[str]:
        nav = yaml.safe_load(MKDOCS.read_text(encoding="utf-8")).get("nav", [])
        return {r for r in _flatten_nav(nav) if r.endswith(".md")}

    def test_every_docs_page_is_in_nav(self):
        """孤儿页在 --strict 下不报错、只是静默不可达 —— 这里钉死（2026-10-14 重整动机之一）。

        rglob 递归：将来加 docs/ 子目录、漏进 nav 同样变红。
        """
        nav_refs = self._nav_refs()
        docs_files = {p.relative_to(DOCS_DIR).as_posix() for p in DOCS_DIR.rglob("*.md")}
        orphans = sorted(docs_files - nav_refs)
        assert not orphans, f"docs/ 下未进 mkdocs nav 的孤儿页: {orphans}"

    def test_every_nav_ref_exists(self):
        missing = sorted(r for r in self._nav_refs() if not (DOCS_DIR / r).is_file())
        assert not missing, f"mkdocs nav 引用了不存在的文件: {missing}"

    def test_index_links_new_struct_pages(self):
        text = INDEX_MD.read_text(encoding="utf-8")
        assert "server-deploy.md" in text, "docs/index 应指向服务器部署路由页"
        assert "member-check.md" in text, "docs/index 应指向 member-check 文档"


class TestServerDeployPage:
    def test_exists(self):
        assert SERVER_DEPLOY_MD.is_file(), "需要 docs/server-deploy.md（服务器部署路由页）"

    def test_separates_server_side_from_personal_stack(self):
        if not SERVER_DEPLOY_MD.is_file():
            pytest.skip("文件未创建")
        text = SERVER_DEPLOY_MD.read_text(encoding="utf-8")
        assert "天一" in text
        assert re.search(r"hermes", text, re.I), "路由页必须写清与 hermes 个人栈的差别（重整的核心目标）"


class TestReadmeRouting:
    def test_has_reader_routing_section(self):
        text = README.read_text(encoding="utf-8")
        assert "我该从哪开始" in text, "README 需要读者路由段（成员→天一 / 部署服务器 / 部署个人栈）"

    def test_member_path_points_to_tianyi(self):
        text = README.read_text(encoding="utf-8")
        assert "天一" in text

    def test_no_html_comment_inside_tables(self):
        """HTML 注释夹在表格行之间会在 GitHub/mkdocs 渲染时**截断表格**，其余行变裸文本
        （2026-10-14 独立 review 抓出的真实事故：能力地图图例注释把整表打碎）。
        覆盖面：README + docs/ 全部页面（mkdocs 不校验表格结构，这里是唯一钉子）。"""
        for path in [README, *sorted(DOCS_DIR.rglob("*.md"))]:
            lines = path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if not line.lstrip().startswith("<!--"):
                    continue
                prev = next((l for l in reversed(lines[:i]) if l.strip()), "")
                nxt = next((l for l in lines[i + 1:] if l.strip()), "")
                assert not (prev.lstrip().startswith("|") and nxt.lstrip().startswith("|")), (
                    f"{path.relative_to(REPO_ROOT)} 第 {i + 1} 行 HTML 注释夹在表格行之间"
                    "——会把表格从注释处截断，把注释移到表格上方"
                )
