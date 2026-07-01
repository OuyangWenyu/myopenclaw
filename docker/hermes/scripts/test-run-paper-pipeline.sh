#!/usr/bin/env bash
# =============================================================
# test-run-paper-pipeline.sh — TDD tests for run-paper-pipeline.sh
# Run inside hermes-coder container:
#   docker compose exec hermes-coder bash /opt/hermes/scripts/test-run-paper-pipeline.sh
# =============================================================
set -euo pipefail

SCRIPT="/opt/hermes/scripts/run-paper-pipeline.sh"
PASS=0
FAIL=0

assert_contains() {
    local label="$1" output="$2" expected="$3"
    if echo "$output" | grep -qF "$expected"; then
        echo "  ✅ $label"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $label — expected output to contain '$expected'"
        echo "     got (first 300 chars): ${output:0:300}"
        FAIL=$((FAIL + 1))
    fi
}

# ── Test 1: --pdf-url dry-run (NEW) ──
echo "Test 1: --pdf-url dry-run"
output=$($SCRIPT --dry-run --pdf-url "https://example.com/paper.pdf" "10.5194/hess-30-3945-2026" 2>&1 || true)
assert_contains "detects PDF URL mode" "$output" "直链"
assert_contains "skips to download step" "$output" "下载 PDF"
assert_contains "shows dry-run Zotero" "$output" "dry-run"
echo ""

# ── Test 2: --pdf-url without DOI should still work ──
echo "Test 2: --pdf-url without --dry-run (simulate download failure)"
# Use an unreachable URL to test the error path
output=$($SCRIPT --pdf-url "https://nonexistent.example/paper.pdf" "10.5194/hess-30-3945-2026" 2>&1 || true)
assert_contains "falls back on download failure" "$output" "下载失败"
echo ""

# ── Test 3: Existing DOI mode (no --pdf-url) must not break ──
echo "Test 3: existing DOI mode (dry-run, should hit paper-fetch)"
output=$($SCRIPT --dry-run "10.48550/arXiv.2501.12948" 2>&1 || true)
assert_contains "starts paper-fetch" "$output" "下载 PDF"
echo ""

# ── Test 4: --help shows --pdf-url ──
echo "Test 4: --help shows new options"
output=$($SCRIPT --help 2>&1 || true)
if echo "$output" | grep -qF 'pdf-url'; then
    echo "  ✅ shows --pdf-url in help"
    PASS=$((PASS + 1))
else
    echo "  ❌ shows --pdf-url in help — missing"
    FAIL=$((FAIL + 1))
fi
echo ""

# ── Test 5: --pdf-url with dry-run produces expected output structure ──
echo "Test 5: --pdf-url dry-run output structure"
output=$($SCRIPT --dry-run --pdf-url "https://example.com/paper.pdf" "10.5194/hess-30-3945-2026" 2>&1 || true)
assert_contains "shows pipeline start" "$output" "论文流水线开始"
assert_contains "shows DOI" "$output" "10.5194/hess-30-3945-2026"
echo ""

# ── Summary ──────────────────────────────────────────────────────
echo "========================================"
echo "Results: $PASS passed, $FAIL failed"
echo "========================================"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
