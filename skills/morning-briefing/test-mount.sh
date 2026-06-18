#!/usr/bin/env bash
# Test: morning-briefing skill is mounted and discoverable inside hermes container
set -euo pipefail

CONTAINER="hermes"
SKILL_PATH="/opt/data/skills/morning-briefing/SKILL.md"
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

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "=== PASS: Skill is properly mounted ==="
