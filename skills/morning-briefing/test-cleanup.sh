#!/usr/bin/env bash
# Test: Old script deleted, no hardcoded passwords
set -euo pipefail

SCRIPT_PATH="${HOME}/.hermes/scripts/morning_briefing.py"
SCRIPTS_DIR="${HOME}/.hermes/scripts"
PASS=0
FAIL=0

# These values appear in this test script itself (for verification).
# grep patterns must exclude this file to avoid false positives.
QQ_PWD="dbecofbvljbqffaa"
DLUT_PWD="CjZGZT@g!vsy9q8"

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

echo "=== Test: Old Script Cleanup ==="
echo ""

# 1. Old script deleted
check "morning_briefing.py deleted" \
    "test ! -f '$SCRIPT_PATH'"

# 2. No QQ password in scripts dir
check "No QQ password in $SCRIPTS_DIR" \
    "! grep -r --include='*.py' --include='*.sh' '$QQ_PWD' '$SCRIPTS_DIR' 2>/dev/null"

# 3. No DLUT password in scripts dir
check "No DLUT password in $SCRIPTS_DIR" \
    "! grep -r --include='*.py' --include='*.sh' '$DLUT_PWD' '$SCRIPTS_DIR' 2>/dev/null"

# 4. Passwords properly stored in himalaya config (auto-generated from env vars)
HIMALAYA_CONFIG="${HOME}/.hermes/.config/himalaya/config.toml"
check "QQ password in himalaya config (auto-generated from env vars)" \
    "grep -q '$QQ_PWD' '$HIMALAYA_CONFIG' 2>/dev/null"

# 5. DLUT password properly stored in himalaya config
check "DLUT password in himalaya config (auto-generated from env vars)" \
    "grep -q '$DLUT_PWD' '$HIMALAYA_CONFIG' 2>/dev/null"

# 6. No passwords in git-tracked skills/*.md files
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
check "No passwords in repo skills/*.md" \
    "! grep -r --include='*.md' -E '$QQ_PWD|$DLUT_PWD' '$REPO_ROOT/skills/' 2>/dev/null"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "=== PASS: Cleanup verified ==="
