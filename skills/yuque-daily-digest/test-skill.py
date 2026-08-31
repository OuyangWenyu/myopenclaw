#!/usr/bin/env python3
"""
Validate yuque-daily-digest SKILL.md for Hermes cron execution.

Requirements:
  - Valid YAML frontmatter
  - Self-contained: all orchestration inline (no external script refs)
  - Drives yuque-mcp tools: list_repos / get_change_summary / get_doc_content
  - [SILENT] protocol: no-change silent, failure MUST warn (never silent)
  - Output format: plain text + emoji, no "###" markdown headers
  - Cron-compatible: works in fresh session (no context dependencies)

Usage:
  python3 skills/yuque-daily-digest/test-skill.py
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
            check(
                re.search(r"^name:\s*yuque-daily-digest\s*$", fm, re.MULTILINE),
                "Frontmatter name must be exactly: yuque-daily-digest"
            )

    # ── Self-contained: NO external script references ─────────
    banned_refs = [
        "send_card.py",
        "collect_agentops",
        "launchd",
        "docker compose exec",
    ]
    for ref in banned_refs:
        check(
            ref not in content,
            f"Must not reference external script: {ref}"
        )

    # ── yuque-mcp tool orchestration inline ───────────────────
    tool_refs = {
        "list_repos (namespace resolution)": r"list_repos",
        "get_change_summary (change report)": r"get_change_summary",
        "get_doc_content (digest source)": r"get_doc_content",
    }
    for label, pattern in tool_refs.items():
        check(
            re.search(pattern, content),
            f"No reference to yuque-mcp tool: {label}"
        )

    # ── Per-repo rule: separate MCP calls, no guessing ────────
    check(
        re.search(r"list_repos", content)
        and re.search(r"namespace", content),
        "Must resolve display name to namespace via list_repos"
    )
    check(
        re.search(r"(分别|单独|逐[一库个]).{0,12}调用|不要把多个知识库合并", content),
        "Must call get_change_summary separately per repo"
    )

    # ── [SILENT] protocol ─────────────────────────────────────
    check("[SILENT]" in content, "Missing [SILENT] protocol")
    check(
        re.search(r"仅限|只能|仅.*无变更", content) and "[SILENT]" in content,
        "[SILENT] must be limited to clean no-change case"
    )
    check(
        re.search(r"无变更|没有任何变更|没有变更", content) and "[SILENT]" in content,
        "No-change case must map to [SILENT]"
    )
    # Failure cases must NOT be silent — they must produce a warning push
    failure_markers = ["not_available", "initialized", "401", "未纳入监控"]
    for marker in failure_markers:
        check(
            marker in content,
            f"Missing failure-case handling: {marker}"
        )
    check(
        re.search(r"(不许静默|不得静默|不能静默|必须推|必须输出)", content),
        "Failure cases must explicitly forbid silence"
    )
    check(
        re.search(r"(未知|无法识别).{0,10}状态", content),
        "Unknown/unrecognized status must be treated as failure warning"
    )

    # ── Output format ─────────────────────────────────────────
    check(
        re.search(r"(变更清单|清单)", content),
        "Missing output section: change list"
    )
    check(
        re.search(r"(摘要|总结)", content),
        "Missing output section: AI digest"
    )
    check(
        re.search(r"固定快照|净变化", content),
        "Must state report is snapshot-interval net change (not daily report)"
    )
    check(
        re.search(r"(不用|不要|禁止).{0,8}#{3}|(不使用|无)\s*#{1,3}\s*(Markdown|标题)|不使用 Markdown 标题", content),
        "Must forbid '###' markdown headers in output"
    )

    # ── Security / error honesty (inherited from yuque-knowledge) ──
    check(
        re.search(r"(不伪装|如实)", content),
        "Must report errors honestly (no fake empty results)"
    )
    check(
        re.search(r"send_message", content),
        "Must forbid send_message (cron delivers the final reply)"
    )
    check(
        re.search(r"YUQUE_TOKEN", content),
        "Must clarify YUQUE_TOKEN is never needed client-side"
    )
    check(
        re.search(r"(不.{0,6}(写入|输出).{0,8}日志|不写入普通日志)", content),
        "Must forbid leaking content/keys into logs"
    )

    # ── Cron-compatible ──────────────────────────────────────
    cron_hints = [
        "fresh session",
        "全新",
        "new session",
        "no context",
        "自包含",
    ]
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
    print("   yuque-mcp orchestration: OK")
    print("   [SILENT] protocol: OK")
    print("   Output format: OK")
    print("   Security rules: OK")
    print("   Cron-compatible: OK")


if __name__ == "__main__":
    main()
