#!/usr/bin/env python3
"""
Validate morning-triage-v2 SKILL.md for Hermes cron execution.

Requirements:
  - Valid YAML frontmatter
  - Self-contained: all data collection commands inline (no external script refs)
  - Includes TDAI Gateway queries, AgentOps commands, output format
  - Cron-compatible: prompt works in fresh session (no context dependencies)

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
    if not check(SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"):
        print("\n".join(ERRORS))
        sys.exit(1)

    content = SKILL_FILE.read_text()

    # ── YAML frontmatter ─────────────────────────────────────
    has_fm = content.startswith("---")
    check(has_fm, "Missing YAML frontmatter")
    if has_fm:
        parts = content.split("---", 2)
        check(len(parts) >= 3, "Frontmatter not closed")
        if len(parts) >= 3:
            fm = parts[1].strip()
            for field in ["name", "description"]:
                check(
                    re.search(rf"^{field}:", fm, re.MULTILINE),
                    f"Frontmatter missing: {field}"
                )

    # ── Self-contained: NO external script references ─────────
    banned_refs = [
        "morning_triage_summary.py",
        "send_card.py",
        "launchd",
        "docker compose exec",
    ]
    for ref in banned_refs:
        check(
            ref not in content,
            f"Must not reference external script: {ref}"
        )

    # ── Data collection inline ───────────────────────────────
    data_sources = {
        "TDAI Gateway": r"tdai-memory|8420|/search/memories|/recall",
        "AgentOps": r"AgentOps|docker compose ps|docker ps|collect_agentops",
        "LLM summary": r"汇总|总结|摘要|生成.*日报|Daily Command",
    }
    for label, pattern in data_sources.items():
        check(
            re.search(pattern, content, re.IGNORECASE),
            f"No inline reference to: {label}"
        )

    # ── Output format specified ──────────────────────────────
    output_markers = ["系统健康", "昨日记忆", "Daily Command Center"]
    for marker in output_markers:
        check(
            marker in content,
            f"Missing output section marker: {marker}"
        )

    # ── Cron-compatible ──────────────────────────────────────
    cron_hints = [
        "fresh session",
        "全新",
        "new session",
        "no context",
        "自包含",
    ]
    # At least one mention that prompt must be self-contained
    has_cron_hint = any(h in content.lower() for h in cron_hints)
    check(has_cron_hint, "No mention of fresh-session/cron compatibility")

    # ── Report ───────────────────────────────────────────────
    if ERRORS:
        print("\n".join(ERRORS))
        print(f"\n{len(ERRORS)} validation error(s)")
        sys.exit(1)

    print("✅ SKILL.md validation passed")
    print("   Frontmatter: OK")
    print("   Self-contained: OK (no external script refs)")
    print("   Data sources: OK")
    print("   Output format: OK")
    print("   Cron-compatible: OK")


if __name__ == "__main__":
    main()
