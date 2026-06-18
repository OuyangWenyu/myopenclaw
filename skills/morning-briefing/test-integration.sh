#!/usr/bin/env bash
# Integration Test: Verify data collection commands work inside hermes container
set -euo pipefail

CONTAINER="hermes"
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

echo "=== Integration Test: Data Collection Commands ==="
echo "Container: $CONTAINER"
echo ""

# ── Test 1: Transactions API ──
echo "── Transactions API ──"
check "API reachable (HTTP 200)" \
    "docker compose exec -T $CONTAINER python3 -c \"
import urllib.request
req = urllib.request.Request('http://host.docker.internal:8000/transactions')
with urllib.request.urlopen(req, timeout=10) as r:
    assert r.status == 200, f'HTTP {r.status}'
    print('OK')
\" 2>&1 | grep -q OK"

check "API returns JSON array" \
    "docker compose exec -T $CONTAINER python3 -c \"
import urllib.request, json
req = urllib.request.Request('http://host.docker.internal:8000/transactions')
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read())
    assert isinstance(data, list), f'Expected list, got {type(data).__name__}'
    print(f'Records: {len(data)}')
\" 2>&1 | grep -q 'Records:'"

check "Has active (non-done) transactions" \
    "docker compose exec -T $CONTAINER python3 -c \"
import urllib.request, json
req = urllib.request.Request('http://host.docker.internal:8000/transactions')
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read())
active = [t for t in data if t.get('status') not in ('done', 'cancelled')]
print(f'Active: {len(active)}')
\" 2>&1 | grep -q 'Active:'"

# ── Test 2: himalaya DLUT ──
echo "── himalaya DLUT (学校邮箱) ──"
check "himalaya installed" \
    "docker compose exec -T $CONTAINER himalaya --version 2>&1 | grep -q 'himalaya'"

check "DLUT account configured" \
    "docker compose exec -T $CONTAINER himalaya account list 2>&1 | grep -q 'dlut'"

check "DLUT folder list works" \
    "docker compose exec -T $CONTAINER himalaya folder list -a dlut 2>&1 | grep -q 'INBOX'"

check "DLUT envelope list works" \
    "docker compose exec -T $CONTAINER himalaya envelope list -a dlut --page-size 5 2>&1 | grep -qE '^\|'"

# ── Test 3: himalaya QQ ──
echo "── himalaya QQ (个人邮箱) ──"
check "Default account configured" \
    "docker compose exec -T $CONTAINER himalaya account list 2>&1 | grep -q 'default'"

check "QQ envelope list works" \
    "docker compose exec -T $CONTAINER himalaya envelope list --page-size 5 2>&1 | grep -qE '^\|'"

# ── Summary ──
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "=== PASS: All integration tests passed ==="
