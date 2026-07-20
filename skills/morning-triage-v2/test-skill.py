#!/usr/bin/env python3
"""
Validate morning-triage-v2 SKILL.md structure and content.

Usage:
  python3 skills/morning-triage-v2/test-skill.py
"""

import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
SKILL_FILE = SKILL_DIR / "SKILL.md"

ERRORS = []


def check(condition, msg):
    if not condition:
        ERRORS.append(f"❌ {msg}")
        return False
    return True


def main():
    # ── File exists ──────────────────────────────────────────
    if not check(SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"):
        print("\n".join(ERRORS))
        sys.exit(1)

    content = SKILL_FILE.read_text()

    # ── YAML frontmatter ─────────────────────────────────────
    has_frontmatter = content.startswith("---")
    check(has_frontmatter, "Missing YAML frontmatter (must start with ---)")

    if has_frontmatter:
        parts = content.split("---", 2)
        check(len(parts) >= 3, "Frontmatter not closed with ---")
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            required_fields = ["name", "description"]
            for field in required_fields:
                check(
                    re.search(rf"^{field}:", frontmatter, re.MULTILINE),
                    f"Frontmatter missing required field: {field}"
                )

    # ── Required sections ────────────────────────────────────
    sections = {
        "数据源": "## 数据源",
        "输出格式": "## 输出",
        "执行方式": "## 执行",
    }
    for label, heading in sections.items():
        check(heading in content, f"Missing required section: {label} ({heading})")

    # ── Key references ───────────────────────────────────────
    refs = {
        "TDAI Gateway": r"tdai-memory|TDAI|8420|Gateway",
        "AgentOps": r"AgentOps|agentops",
        "Feishu": r"飞书|feishu|FEISHU",
    }
    for label, pattern in refs.items():
        check(
            re.search(pattern, content, re.IGNORECASE),
            f"No reference to {label} found in skill file"
        )

    # ── Report ───────────────────────────────────────────────
    if ERRORS:
        print("\n".join(ERRORS))
        print(f"\n{len(ERRORS)} validation error(s)")
        sys.exit(1)

    print("✅ SKILL.md validation passed")
    print(f"   Frontmatter: OK")
    print(f"   Required sections: OK")
    print(f"   Key references: OK")


if __name__ == "__main__":
    main()
