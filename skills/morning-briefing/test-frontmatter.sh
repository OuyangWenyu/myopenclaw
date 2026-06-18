#!/usr/bin/env bash
# Test: SKILL.md has valid YAML frontmatter with required fields
set -euo pipefail

SKILL_FILE="$(dirname "$0")/SKILL.md"

echo "=== Test: SKILL.md frontmatter validity ==="

# 1. File exists
if [ ! -f "$SKILL_FILE" ]; then
    echo "FAIL: SKILL.md not found at $SKILL_FILE"
    exit 1
fi
echo "  ✓ File exists"

# 2. Has opening --- on line 1
FIRST_LINE=$(head -1 "$SKILL_FILE")
if [ "$FIRST_LINE" != "---" ]; then
    echo "FAIL: SKILL.md must start with --- (frontmatter opening)"
    exit 1
fi
echo "  ✓ Frontmatter opening --- found"

# 3. Has closing --- after line 1
CLOSING_LINE=$(grep -n '^---$' "$SKILL_FILE" | tail -1 | cut -d: -f1)
if [ "$CLOSING_LINE" = "1" ] || [ -z "$CLOSING_LINE" ]; then
    echo "FAIL: SKILL.md missing closing --- for frontmatter"
    exit 1
fi
echo "  ✓ Frontmatter closing --- found at line $CLOSING_LINE"

# 4. Extract frontmatter and validate required fields
FRONTMATTER=$(sed -n '2,/^---$/p' "$SKILL_FILE" | sed '$d')

# Required fields
for field in "name:" "description:" "version:"; do
    if echo "$FRONTMATTER" | grep -q "^${field}"; then
        echo "  ✓ Required field '$field' present"
    else
        echo "FAIL: Required field '$field' missing from frontmatter"
        exit 1
    fi
done

# 5. name must be "morning-briefing"
NAME_VALUE=$(echo "$FRONTMATTER" | grep '^name:' | sed 's/^name:[[:space:]]*//')
if [ "$NAME_VALUE" != "morning-briefing" ]; then
    echo "FAIL: name should be 'morning-briefing', got '$NAME_VALUE'"
    exit 1
fi
echo "  ✓ name is 'morning-briefing'"

echo "=== PASS: SKILL.md frontmatter is valid ==="
