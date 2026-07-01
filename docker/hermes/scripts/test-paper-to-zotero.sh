#!/usr/bin/env bash
# =============================================================
# test-paper-to-zotero.sh — TDD tests for paper-to-zotero.py
# Run inside hermes-coder container:
#   docker compose exec hermes-coder bash /opt/hermes/scripts/test-paper-to-zotero.sh
# =============================================================
set -euo pipefail

PY_SCRIPT="/opt/hermes/scripts/paper-to-zotero.py"
# Must use the Python from zotero-cli-cc (has pyzotero installed)
PYTHON=/opt/uv-tools/zotero-cli-cc/bin/python
PASS=0
FAIL=0

assert_contains() {
    local label="$1" output="$2" expected="$3"
    if echo "$output" | grep -qF "$expected"; then
        echo "  ✅ $label"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $label — expected output to contain '$expected'"
        echo "     got: $output"
        FAIL=$((FAIL + 1))
    fi
}

assert_not_contains() {
    local label="$1" output="$2" unexpected="$3"
    if echo "$output" | grep -qF "$unexpected"; then
        echo "  ❌ $label — output should NOT contain '$unexpected'"
        echo "     got: $output"
        FAIL=$((FAIL + 1))
    else
        echo "  ✅ $label"
        PASS=$((PASS + 1))
    fi
}

# ── Test 1: metadata-only without PDF (existing behavior, must not break) ──
echo "Test 1: --metadata-only (no PDF)"
output=$($PYTHON $PY_SCRIPT --dry-run --metadata-only "10.1038/s41467-026-74336-x" 2>&1)
assert_contains "prints metadata-only hint" "$output" "metadata only"
assert_not_contains "has no linked_file" "$output" "linked_file → attachments:"
echo ""

# ── Test 2: metadata-only WITH --pdf-filename (NEW behavior) ──
echo "Test 2: --metadata-only --pdf-filename (NEW)"
output=$($PYTHON $PY_SCRIPT --dry-run --metadata-only --pdf-filename "hess-30-3945-2026.pdf" "10.5194/hess-30-3945-2026" 2>&1)
assert_contains "prints linked_file attachment" "$output" "linked_file → attachments:hess-30-3945-2026.pdf"
assert_not_contains "no metadata-only hint" "$output" "metadata only"
echo ""

# ── Test 3: normal paper-fetch JSON mode (existing behavior, must not break) ──
echo "Test 3: normal mode (paper-fetch JSON)"
# Create a minimal valid paper-fetch JSON
cat > /tmp/test-pf-valid.json << 'EOF'
{"ok": true, "data": {"results": [{"doi": "10.48550/arXiv.2501.12948", "file": "/tmp/test-paper.pdf", "meta": {"title": "Test Paper", "year": 2025}}]}}
EOF
output=$($PYTHON $PY_SCRIPT --dry-run /tmp/test-pf-valid.json 2>&1)
assert_contains "prints linked_file" "$output" "linked_file → attachments:test-paper.pdf"
rm -f /tmp/test-pf-valid.json
echo ""

# ── Test 4: normal mode without PDF file in JSON ──
echo "Test 4: normal mode (paper-fetch JSON, no PDF)"
cat > /tmp/test-pf-nopdf.json << 'EOF'
{"ok": true, "data": {"results": [{"doi": "10.48550/arXiv.2501.12948", "file": "", "meta": {"title": "Test Paper", "year": 2025}}]}}
EOF
output=$($PYTHON $PY_SCRIPT --dry-run /tmp/test-pf-nopdf.json 2>&1)
assert_contains "prints metadata-only hint" "$output" "metadata only"
assert_not_contains "no linked_file" "$output" "linked_file → attachments:"
rm -f /tmp/test-pf-nopdf.json
echo ""

# ── Test 5: usage message ──
echo "Test 5: usage message"
output=$($PYTHON $PY_SCRIPT 2>&1 || true)
if echo "$output" | grep -qF 'metadata-only'; then
    echo "  ✅ shows metadata-only"
    PASS=$((PASS + 1))
else
    echo "  ❌ shows metadata-only — missing"
    FAIL=$((FAIL + 1))
fi
if echo "$output" | grep -qF 'pdf-filename'; then
    echo "  ✅ shows --pdf-filename"
    PASS=$((PASS + 1))
else
    echo "  ❌ shows --pdf-filename — missing"
    FAIL=$((FAIL + 1))
fi
echo ""

# ── Summary ──────────────────────────────────────────────────────
echo "========================================"
echo "Results: $PASS passed, $FAIL failed"
echo "========================================"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
