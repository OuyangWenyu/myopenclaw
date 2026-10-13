#!/usr/bin/env bash
# Test: morning-briefing 技能在 hermes 容器内可发现、且无历史撞名残留
# 2026-10-13 修正：SKILL_PATH 曾指向已移除的 symlink 路径 /opt/data/skills/morning-briefing
# （该路径存在 = 撞名事故复发，必须为「不存在」）。
set -euo pipefail

CONTAINER="hermes"
SKILL_PATH="/opt/data/skills/devops/morning-briefing/SKILL.md"   # 原生副本（唯一 live 源）
LEGACY_PATH="/opt/data/skills/morning-briefing"                   # 历史 symlink 路径，不得复活
TODAY_SCRIPT="/opt/data/skills/devops/morning-briefing/scripts/today.py"
PASS=0
FAIL=0

check() {
    local desc="$1"
    if eval "$2"; then
        echo "  ✓ $desc"
        PASS=$((PASS + 1))
    else
        echo "  ✗ FAIL: $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Test: Skill Mount & Discoverability ==="
echo "Container: $CONTAINER"
echo ""

# 1. Skill file exists at expected path
check "SKILL.md exists at $SKILL_PATH" \
    "docker compose exec -T $CONTAINER test -f $SKILL_PATH"

# 2. Skill file is readable
check "SKILL.md is readable" \
    "docker compose exec -T $CONTAINER cat $SKILL_PATH > /dev/null 2>&1"

# 3. Skill file starts with frontmatter
check "SKILL.md has valid frontmatter" \
    "docker compose exec -T $CONTAINER head -1 $SKILL_PATH 2>&1 | grep -q '^---$'"

# 4. Skill file references himalaya (content made it through mount)
check "SKILL.md contains himalaya reference" \
    "docker compose exec -T $CONTAINER grep -q 'himalaya' $SKILL_PATH"

# 5. 日期脚本在位（2026-10-13 起 job 依赖它判定星期几）
check "date script exists at $TODAY_SCRIPT" \
    "docker compose exec -T $CONTAINER test -f $TODAY_SCRIPT"

# 6. 历史 symlink 路径不得复活（存在即撞名事故复发）
check "legacy symlink path is absent ($LEGACY_PATH)" \
    "! docker compose exec -T $CONTAINER test -e $LEGACY_PATH"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "=== PASS: Skill is properly mounted ==="
