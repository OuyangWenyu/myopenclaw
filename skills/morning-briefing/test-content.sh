#!/usr/bin/env bash
# Test: SKILL.md contains all required content sections
set -euo pipefail

SKILL_FILE="$(dirname "$0")/SKILL.md"

echo "=== Test: SKILL.md content completeness ==="

CONTENT=$(cat "$SKILL_FILE")

# 1. Data collection section
if echo "$CONTENT" | grep -q '数据采集'; then
    echo "  ✓ '数据采集' section found"
else
    echo "FAIL: Missing '数据采集' section"
    exit 1
fi

# 2. References himalaya (the email tool)
if echo "$CONTENT" | grep -q 'himalaya'; then
    echo "  ✓ himalaya command referenced"
else
    echo "FAIL: No himalaya command reference"
    exit 1
fi

# 3. References transactions API
if echo "$CONTENT" | grep -qE 'transactions|host\.docker\.internal:8000'; then
    echo "  ✓ Transactions API referenced"
else
    echo "FAIL: No transactions API reference"
    exit 1
fi

# 4. Filtering rules section
if echo "$CONTENT" | grep -q '筛选规则'; then
    echo "  ✓ '筛选规则' section found"
else
    echo "FAIL: Missing '筛选规则' section"
    exit 1
fi

# 5. Presentation format section
if echo "$CONTENT" | grep -q '呈现'; then
    echo "  ✓ '呈现' section found"
else
    echo "FAIL: Missing '呈现' section"
    exit 1
fi

# 6. Edge cases handled
if echo "$CONTENT" | grep -qE '无.*邮件|周末|边界|节假日|异常|SILENT'; then
    echo "  ✓ Edge cases documented"
else
    echo "FAIL: No edge cases (无邮件/周末/异常) documented"
    exit 1
fi

echo "=== PASS: SKILL.md content is complete ==="
