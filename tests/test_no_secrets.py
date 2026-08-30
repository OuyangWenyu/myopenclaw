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
