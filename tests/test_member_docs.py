"""成员文档与文档结构守卫（TDD：先于内容编写）。

守卫五类承诺（2026-10-14 文档结构重整；2026-10-15 增补服务器侧口径）：
1. 「成员接入指南」在天一知识库（openclaw-tianyi/workspace/ACCESS.md）——
   含工具清单两拨 + 三服务 + member-check 指路，且**绝不含任何凭据样式串**
   （token 只写"向谁获取"）；
2. `.env.member.example` 只含占位/空值（token 一律留空，防"复制即用旧 token"）；
3. 文档结构一致性：docs/ 下**每个** .md 都必须出现在 mkdocs.yml nav（无孤儿页），
   nav 引用的文件也必须存在——孤儿页在 --strict 下不报错，只会静默不可达；
4. 服务器部署路由页存在且把「天一（服务器侧）」与「hermes 个人栈」的差别写清楚；
   README 有「我该从哪开始」的读者路由；
5. 服务器侧**口径一致**（2026-10-15 Owen 更正）：能力层=团队服务四项
   （语雀/仓库统计/服务监控/**水文 MCP（zhixun-agent）**），接入层=公共 bot
   （天一、知汛）——曾因只盯着天一接的三项，把 zhixun-agent 写成"构建依赖"、
   服务器侧组成句漏掉知汛。

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


# 历史/易漏禁词词形清单——逐个钉「必须被抓」，防止将来改正则时又漏词形
# （「三个团队 MCP 服务」是 HEAD docs/server-deploy.md:9 的原话，带空格——
#  2026-10-15 二轮 review 实证：旧紧贴式正则对它整套守卫全绿放行）
BANNED_WORD_FORMS = (
    "三项团队服务",
    "三个团队服务",
    "三个团队 MCP 服务",
    "三项团队 MCP服务",
    "三个 MCP 服务",
    "三项服务",  # CONTEXT 级：靠含「服务器侧」的行喂上下文
)


class TestTeamSidePositioning:
    """服务器侧口径守卫（2026-10-15 Owen 更正：知汛背后的 zhixun-agent 也是团队服务）。

    正典 = 两层结构：
    - **能力层「团队服务」**：语雀 MCP / git-contribution-stats / 服务监控 /
      水文 MCP（zhixun-agent）——四项，全部属团队、目标都在团队服务器；
    - **接入层「公共 bot」**：天一（消费前三项）、知汛（消费水文 MCP），均 OpenClaw 栈；
    - **成员可直连 = 前三项**（member-check / ACCESS.md 范围；水文走知汛 bot）。

    曾经的口径分裂：README 路由表/两条线句、docs 首页都写「天一 + 三项团队服务」
    （漏知汛）；zhixun-agent 被写成纯"构建依赖"（不是团队服务）；zhixun 文档通篇
    无「团队」二字，读起来像个人可选功能。

    **扫描面**：README + docs/**/*.md + docker/**（Dockerfile 注释也是给读者看的）+
    两个 bot 的 workspace/。**豁免**：CLAUDE.md 与 tests/ 不在扫描面——维护者元叙述
    需要引用禁词本身（如「禁用裸写『三项团队服务』」），扫进去会自咬。

    **已知边界（刻意接受的残余）**：组成句按**单行**匹配——语义上是组成枚举、
    却换用全新词面（且避开禁词）的句子抓不到；每轮独立 review 人查这个面。
    """

    # 能力地图四项团队能力（🌐 图例 = 团队侧能力；缺一 = 读者按 🌐 扫只见部分）
    TEAM_CAPABILITY_LABELS = ("语雀知识库", "研发日报", "水文智能问答", "服务器监控")

    # 禁词两级（2026-10-15 二轮 review 补）：
    # ① STRICT——任何出现即禁（项/个、团队-MCP 紧贴与含空格的历史原词形都算）；
    # ② CONTEXT——裸「三[项个]服务」只在同行含 团队/服务器侧 时判禁，
    #    避免无关句子（"重启三个服务"）假红。
    BANNED_STRICT_PAT = re.compile(r"三[项个]\s*团队\s*(?:服务|MCP)|三[项个]\s*MCP\s*服务")
    BANNED_CONTEXT_PAT = re.compile(r"三[项个]服务")
    # 「成员直连三项团队能力」合法，但必须同句带「成员」
    MEMBER_THREE_PAT = re.compile(r"三[项个]团队能力")
    # 组成句词面（无前置条件命中即要求知汛——加前置会被「只部署三项服务」绕过）
    COMPOSITION_FACES_PAT = re.compile(r"服务器侧团队能力|服务器只部署|服务器上部署的是")
    # 条件词面：单看任一侧都不构成组成枚举，合取才构成（现状 0 假红，probe 于 2026-10-15）
    SERVER_PLUS_PUBBOT_PAT = re.compile(r"服务器.*公共 bot")
    SERVER_SIDE_PAREN_PAT = re.compile(r"服务器侧（.*(?:天一|公共 bot|团队服务)")

    def _scan_files(self) -> list[Path]:
        files = [README, *sorted(DOCS_DIR.rglob("*.md"))]
        for base in (
            REPO_ROOT / "docker",
            REPO_ROOT / "openclaw-tianyi" / "workspace",
            REPO_ROOT / "openclaw-zhixun" / "workspace",
        ):
            files.extend(p for p in sorted(base.rglob("*")) if p.is_file())
        files.extend(sorted(REPO_ROOT.glob(".env.*.example")))
        return files

    def _banned_hit(self, line: str) -> str | None:
        """返回该行命中的禁词（无则 None）。两级规则见类常量注释。"""
        m = self.BANNED_STRICT_PAT.search(line)
        if m:
            return m.group(0)
        if re.search(r"团队|服务器侧", line):
            m = self.BANNED_CONTEXT_PAT.search(line)
            if m:
                return m.group(0)
        return None

    def _lines(self, path: Path) -> list[tuple[int, str]]:
        return list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1))

    def test_server_side_composition_names_both_bots(self):
        """凡枚举服务器侧组成的句子，天一、知汛必须并列出现（两公共 bot，缺一即错）。

        三类词面：① 无前置的组成句（服务器侧团队能力/服务器只部署/服务器上部署的是）；
        ② 服务器 + 公共 bot；③ 服务器侧（ + 天一/公共 bot/团队服务。
        """
        for path in [README, *sorted(DOCS_DIR.rglob("*.md"))]:
            for i, line in self._lines(path):
                hit = (
                    self.COMPOSITION_FACES_PAT.search(line)
                    or (self.SERVER_PLUS_PUBBOT_PAT.search(line))
                    or (self.SERVER_SIDE_PAREN_PAT.search(line))
                )
                if hit and "知汛" not in line:
                    raise AssertionError(
                        f"{path.name}:{i} 服务器侧组成句漏了知汛（应为天一/知汛两公共 bot）"
                        f"——命中词面 /{hit.group(0)}/: {line.strip()[:100]}"
                    )

    def test_no_bare_three_team_services(self):
        """禁词两级 + 成员限定（扫描面见类 docstring；CLAUDE.md/tests 豁免）。"""
        for path in self._scan_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # 二进制（如夹带的 pyc/镜像内文件）
            for i, line in enumerate(text.splitlines(), 1):
                hit = self._banned_hit(line)
                assert hit is None, (
                    f"{path.relative_to(REPO_ROOT)}:{i} 出现禁词「{hit}」——团队服务=四项"
                    "（含 zhixun-agent 水文 MCP）；成员范围请写「成员直连三项」"
                )
                if self.MEMBER_THREE_PAT.search(line):
                    assert "成员" in line, (
                        f"{path.relative_to(REPO_ROOT)}:{i}「三项团队能力」未限定成员范围"
                        f"（水文走知汛、不在其中）: {line.strip()[:100]}"
                    )

    @pytest.mark.parametrize("form", BANNED_WORD_FORMS)
    def test_banned_word_forms_are_caught(self, form: str):
        """词形清单逐个钉「必须被抓」——防止将来改正则时又漏词形
        （一轮 review 的实证：带空格的历史原词形「三个团队 MCP 服务」整套守卫全绿放行）。"""
        line = f"| 服务器侧（团队）示例行 | {form} |"  # 喂 CONTEXT 需要的「服务器侧/团队」上下文
        assert self._banned_hit(line) is not None, f"禁词词形漏网: {form!r}"

    def test_team_services_include_water_mcp(self):
        """zhixun-agent（水文 MCP）必须在两处被写成团队服务：server-deploy 与 README 仓库配合。"""
        sd_lines = self._lines(SERVER_DEPLOY_MD)
        # ① 两条线对比表的「组成」行必须点名水文（列名按正则匹配，容忍空格变化）
        compose_row = next(
            (line for _, line in sd_lines if re.match(r"\|\s*组成\s*\|", line)), ""
        )
        assert "水文" in compose_row, (
            f"server-deploy 组成行漏了水文 MCP（回显命中行）: {compose_row[:120] or '（未找到组成行）'}"
        )
        # ② 正文须有一行把 zhixun-agent 明确写成团队服务
        assert any(
            "zhixun-agent" in line and "团队服务" in line
            for _, line in sd_lines
        ), "server-deploy 应有一行写明「zhixun-agent … 团队服务」（水文 MCP 是能力层，不是 bot 的附属品）"
        # ③ README 仓库配合：zhixun-agent/ 行标注团队服务（而非纯构建依赖）
        readme_zhixun = [
            line for _, line in self._lines(README) if line.startswith("zhixun-agent/")
        ]
        assert readme_zhixun, "README 仓库配合缺 zhixun-agent 行"
        assert any(
            "团队服务" in line for line in readme_zhixun
        ), f"README 仓库配合的 zhixun-agent 行未标注团队服务: {readme_zhixun[0][:100]}"

    def test_zhixun_doc_declares_team_positioning(self):
        """知汛文档的团队侧定位段必须真实存在（钉标题本身——只断言『团队』二字的话
        把定位段整段删掉、留下别处的『团队服务器』也可能假绿）。"""
        text = (DOCS_DIR / "zhixun-feishu-bot.md").read_text(encoding="utf-8")
        assert "## 定位（团队侧）" in text, "知汛文档缺「定位（团队侧）」段"
        assert "团队侧公共 bot" in text, "定位段应写明知汛=团队侧公共 bot"
        assert "server-deploy.md" in text, "知汛文档应指回 server-deploy 分界页"

    def test_access_offers_zhixun_water_path(self):
        """成员指南须交代水文能力走知汛**且显式排除在三项外**——否则成员以为只有 MCP 三项。"""
        text = ACCESS_MD.read_text(encoding="utf-8")
        assert "知汛" in text, "ACCESS.md 未提知汛：水文问答不在三 MCP 直连里，应指引找知汛"
        assert "不在上面三项" in text, "ACCESS.md 应显式说明水文不在三项 MCP 直连里"

    def test_zhixun_agent_links_point_to_gitcode(self):
        """zhixun-agent 的正典地址是 GitCode dlut-water（2026-10-15 review H1：
        GitHub 上的 OuyangWenyu/zhixun-agent 不存在，链接/克隆命令全是死的）。"""
        for path in [README, *sorted(DOCS_DIR.rglob("*.md"))]:
            text = path.read_text(encoding="utf-8")
            assert "github.com/OuyangWenyu/zhixun-agent" not in text, (
                f"{path.relative_to(REPO_ROOT)} 用了不存在的 GitHub zhixun-agent 地址"
                "——应为 gitcode.com/dlut-water/zhixun-agent"
            )
        for path in (README, DOCS_DIR / "setup.md", DOCS_DIR / "portability.md"):
            assert "gitcode.com/dlut-water/zhixun-agent" in path.read_text(encoding="utf-8"), (
                f"{path.relative_to(REPO_ROOT)} 缺 zhixun-agent 的 GitCode 地址"
            )

    def test_member_scope_stays_three(self):
        """反向守卫：成员直连仍是三项——水文不得混进 .env.member / member-check 清单
        （正向已有「有三个 URL」断言，但那不排斥第 4 个；这里钉上限与排除项）。"""
        env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
        url_keys = re.findall(r"^[A-Z_]+_MCP_URL=", env_text, re.M)
        assert len(url_keys) == 3, f".env.member.example 的 MCP_URL 数量应恰为 3，实为 {len(url_keys)}: {url_keys}"
        assert not re.search(r"WATER|ZHIXUN", env_text, re.I), (
            ".env.member.example 出现水文/知汛变量——水文走知汛 bot，不进成员直连"
        )
        mc_text = (DOCS_DIR / "member-check.md").read_text(encoding="utf-8")
        assert "不在本清单" in mc_text, "member-check 文档应显式排除水文（不在本清单）"

    def test_capability_map_marks_team_capabilities(self):
        """能力地图里四项团队能力必须带 🌐（图例：🌐 = 团队侧能力）——
        否则读者按 🌐 扫只见部分，误判团队能力全集。只查能力地图段内，避开服务一览表。"""
        text = README.read_text(encoding="utf-8")
        m = re.search(r"## 能力地图.*?(?=\n## )", text, re.S)
        assert m, "README 缺能力地图段"
        section = m.group(0)
        for label in self.TEAM_CAPABILITY_LABELS:
            row = next(
                (line for line in section.splitlines() if label in line), None
            )
            assert row is not None, f"能力地图缺 {label} 行"
            assert "🌐" in row, f"能力地图「{label}」行缺 🌐（团队侧标注）: {row[:80]}"
