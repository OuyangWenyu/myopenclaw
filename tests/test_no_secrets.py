"""Regression guard: no hardcoded credentials in tracked files.

本仓库是公开的。历史上 skills/morning-briefing/test-cleanup.sh 曾把真实
邮箱密码硬编码为测试夹具（2026-06-18 起公开暴露，PR #62 时发现）。
本测试防止再次发生。故意不嵌入真实密码值——那等于再次泄漏。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 会被明文密码击中的赋值形态：X_PWD="非变量字面量"（变量引用 $ 开头放行）
_PASSWORD_LITERAL = re.compile(r"^[A-Z0-9_]*(PWD|PASSWORD|TOKEN|SECRET)=\"[^$]", re.M)


def test_test_cleanup_script_has_no_hardcoded_password_literals():
    content = (REPO_ROOT / "skills" / "morning-briefing" / "test-cleanup.sh").read_text()
    assert not _PASSWORD_LITERAL.search(content), (
        "test-cleanup.sh 不得硬编码密码（应运行时从 himalaya 配置提取）"
    )


def test_docs_use_placeholder_email_addresses():
    docs = (REPO_ROOT / "docs" / "email.md").read_text()
    assert "wenyuouyang" not in docs, "docs 不得包含真实邮箱地址，用占位符"


# ── 公开面文档不得出现真实个人邮箱（2026-10-13 隐私检查固化）────────────
# 事故：晨间简报 skill 同步回仓库时，把一条真实第三方个人信息（姓名 + 单位 +
# 邮箱）带进了 skills/morning-briefing/SKILL.md，靠人工扫描才发现。本测试把它
# 自动化：skills/ + docs/ + 根目录的 *.md 里出现的任何邮箱，只允许——
#   ① RFC 2606 保留域（*@example.com/org/net，永远不可能指向真实个人）；
#   ② 下面清单里的服务商通用地址 / 文档占位地址 / 工具地址。
# 命中其他地址即红：先匿名化；确属通用服务商地址时，把它加进清单（一行）。
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ALLOWED_EMAILS = frozenset({
    # 服务商通用地址（非个人）
    "noreply@github.com",
    "em@editorialmanager.com",
    "pnctaxoperations@pnc.com",
    "discover@services.discover.com",
    # 文档占位 / 工具地址
    "git@github.com",
    "git@gitcode.com",
    "your@dlut.edu.cn",  # docs/email.md 示例行（占位 local part）
    "you@outlook.com",   # docs/email.md 示例行（占位 local part）
})
_ALLOWED_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "example.net"})


def _public_markdown_files():
    for base in ("skills", "docs"):
        yield from (REPO_ROOT / base).rglob("*.md")
    yield from REPO_ROOT.glob("*.md")


def test_no_personal_emails_in_public_docs():
    offenders: list[str] = []
    for path in _public_markdown_files():
        for m in _EMAIL_RE.finditer(path.read_text(errors="replace")):
            addr = m.group(0).lower()
            if addr in _ALLOWED_EMAILS:
                continue
            if addr.rsplit("@", 1)[1] in _ALLOWED_EMAIL_DOMAINS:
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)} → {addr}")
    assert not offenders, (
        "公开面文档出现非白名单邮箱（疑似第三方个人信息）：\n  "
        + "\n  ".join(sorted(set(offenders)))
        + "\n→ 匿名化，或确为通用服务商地址时加入 tests/test_no_secrets.py 的 _ALLOWED_EMAILS"
    )


# ── 公开面文档不得出现内网部署用户名（2026-10-13 补，同一隐私事故的姓氏层）──
# 邮箱守卫拦不住用户名：server-monitoring 接入文档曾把服务器部署账号路径
# （~qupengfei/.config/...）写进 docs/。出现服务器上的部署账号一律匿名化为
# 「维护者私有目录」之类表述；确需指名时先进本清单（一行）并说明理由。
_INTERNAL_USERNAMES = ("qupengfei",)


def test_no_internal_usernames_in_public_docs():
    offenders: list[str] = []
    for path in _public_markdown_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in _INTERNAL_USERNAMES:
            if name in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} → {name}")
    assert not offenders, (
        "公开面文档出现内网部署用户名：\n  " + "\n  ".join(sorted(offenders))
        + "\n→ 匿名化为「维护者私有目录」等表述，或确需指名时加入 _INTERNAL_USERNAMES"
    )
