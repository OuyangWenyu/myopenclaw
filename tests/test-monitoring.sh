#!/usr/bin/env bash
# =============================================================
# tests/test-monitoring.sh — TDD tests for Issue #6 monitoring
# Run: bash tests/test-monitoring.sh
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PASS=0
FAIL=0

green() { printf '\033[32m%s\033[0m\n' "$1"; }
red()   { printf '\033[31m%s\033[0m\n' "$1"; }

assert_pass() {
    local desc="$1"
    green "  ✅ PASS: ${desc}"
    PASS=$((PASS + 1))
}

assert_fail() {
    local desc="$1"
    local detail="${2:-}"
    red "  ❌ FAIL: ${desc}"
    if [ -n "${detail}" ]; then
        red "     ${detail}"
    fi
    FAIL=$((FAIL + 1))
}

# =============================================================
# Test Suite 1: docker-compose.yml — uptime-kuma service
# =============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Suite 1: docker-compose.yml"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

if [ ! -f "${COMPOSE_FILE}" ]; then
    assert_fail "docker-compose.yml exists"
    echo ""
    echo "Results: ${PASS} passed, ${FAIL} failed"
    exit 1
fi
assert_pass "docker-compose.yml exists"

# 1.1 Service block exists
if grep -q 'uptime-kuma:' "${COMPOSE_FILE}"; then
    assert_pass "uptime-kuma service defined"
else
    assert_fail "uptime-kuma service defined" "service 'uptime-kuma:' not found in docker-compose.yml"
fi

# 1.2 Uses official image
if grep -A 20 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'louislam/uptime-kuma'; then
    assert_pass "uptime-kuma uses official louislam/uptime-kuma image"
else
    assert_fail "uptime-kuma uses official image" "expected louislam/uptime-kuma"
fi

# 1.3 Container name
if grep -A 20 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'container_name: uptime-kuma'; then
    assert_pass "uptime-kuma has container_name: uptime-kuma"
else
    assert_fail "uptime-kuma container_name" "expected 'container_name: uptime-kuma'"
fi

# 1.4 Restart policy
if grep -A 20 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'restart: unless-stopped'; then
    assert_pass "uptime-kuma restart policy: unless-stopped"
else
    assert_fail "uptime-kuma restart policy" "expected 'restart: unless-stopped'"
fi

# 1.5 Port mapping with env var
if grep -A 20 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'UPTIME_KUMA_PORT'; then
    assert_pass "uptime-kuma port uses UPTIME_KUMA_PORT variable"
else
    assert_fail "uptime-kuma port variable" "expected \${UPTIME_KUMA_PORT:-3001}"
fi

# 1.6 Data volume
if grep -A 20 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q '.uptime-kuma:/app/data'; then
    assert_pass "uptime-kuma data volume: ~/.uptime-kuma:/app/data"
else
    assert_fail "uptime-kuma data volume" "expected ~/.uptime-kuma:/app/data mount"
fi

# 1.7 Docker socket (read-only)
if grep -A 20 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'docker.sock.*:ro'; then
    assert_pass "uptime-kuma Docker socket mounted read-only"
else
    assert_fail "uptime-kuma Docker socket" "expected /var/run/docker.sock:/var/run/docker.sock:ro"
fi

# 1.8 Network
if grep -A 20 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'myopenclaw-net'; then
    assert_pass "uptime-kuma on myopenclaw-net network"
else
    assert_fail "uptime-kuma network" "expected myopenclaw-net"
fi

# 1.9 Resource limits
if grep -A 25 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'memory: 512M'; then
    assert_pass "uptime-kuma memory limit: 512M"
else
    assert_fail "uptime-kuma memory limit" "expected memory: 512M"
fi

if grep -A 25 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'cpus:.*0.5'; then
    assert_pass "uptime-kuma CPU limit: 0.5"
else
    assert_fail "uptime-kuma CPU limit" "expected cpus: 0.5"
fi

# 1.10 TZ environment
if grep -A 25 'uptime-kuma:' "${COMPOSE_FILE}" | grep -q 'TZ='; then
    assert_pass "uptime-kuma TZ environment set"
else
    assert_fail "uptime-kuma TZ env" "expected TZ=\${TZ:-Asia/Shanghai}"
fi

# =============================================================
# Test Suite 2: .env.example
# =============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Suite 2: .env.example"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

ENV_EXAMPLE="${REPO_ROOT}/.env.example"

if [ ! -f "${ENV_EXAMPLE}" ]; then
    assert_fail ".env.example exists"
else
    assert_pass ".env.example exists"

    if grep -q 'UPTIME_KUMA_PORT' "${ENV_EXAMPLE}"; then
        assert_pass "UPTIME_KUMA_PORT defined in .env.example"
    else
        assert_fail "UPTIME_KUMA_PORT in .env.example"
    fi

    if grep -q 'HEALTHCHECKS_PING_URL' "${ENV_EXAMPLE}"; then
        assert_pass "HEALTHCHECKS_PING_URL defined in .env.example"
    else
        assert_fail "HEALTHCHECKS_PING_URL in .env.example"
    fi
fi

# =============================================================
# Test Suite 3: healthchecks-ping.sh
# =============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Suite 3: healthchecks-ping.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PING_SCRIPT="${REPO_ROOT}/scripts/healthchecks-ping.sh"

if [ ! -f "${PING_SCRIPT}" ]; then
    assert_fail "healthchecks-ping.sh exists"
else
    assert_pass "healthchecks-ping.sh exists"

    # 3.1 Has shebang
    if head -1 "${PING_SCRIPT}" | grep -q '#!/usr/bin/env bash'; then
        assert_pass "healthchecks-ping.sh has bash shebang"
    else
        assert_fail "healthchecks-ping.sh shebang"
    fi

    # 3.2 Has set -euo pipefail
    if grep -q 'set -euo pipefail' "${PING_SCRIPT}"; then
        assert_pass "healthchecks-ping.sh has set -euo pipefail"
    else
        assert_fail "healthchecks-ping.sh set -euo pipefail"
    fi

    # 3.3 Reads HEALTHCHECKS_PING_URL from .env
    if grep -q 'HEALTHCHECKS_PING_URL' "${PING_SCRIPT}"; then
        assert_pass "healthchecks-ping.sh references HEALTHCHECKS_PING_URL"
    else
        assert_fail "healthchecks-ping.sh HEALTHCHECKS_PING_URL reference"
    fi

    # 3.4 Handles missing URL
    if grep -q 'PING_URL.*-z\|PING_URL.*empty\|not set\|not configured' "${PING_SCRIPT}" || grep -q 'exit 1' "${PING_SCRIPT}"; then
        assert_pass "healthchecks-ping.sh handles missing URL"
    else
        assert_fail "healthchecks-ping.sh missing URL handling" "should exit non-zero when URL is missing"
    fi

    # 3.5 Uses curl to ping
    if grep -q 'curl' "${PING_SCRIPT}"; then
        assert_pass "healthchecks-ping.sh uses curl"
    else
        assert_fail "healthchecks-ping.sh uses curl"
    fi

    # 3.6 Sends system info in body
    if grep -q 'hostname\|uptime\|disk\|load' "${PING_SCRIPT}"; then
        assert_pass "healthchecks-ping.sh sends system info in ping body"
    else
        assert_fail "healthchecks-ping.sh system info" "should include hostname/uptime/disk/load"
    fi

    # 3.7 Uses timeout on curl
    if grep -q 'curl.*-m\|curl.*--max-time\|curl.*--connect-timeout' "${PING_SCRIPT}"; then
        assert_pass "healthchecks-ping.sh curl has timeout"
    else
        assert_fail "healthchecks-ping.sh curl timeout" "should use -m or --max-time"
    fi

    # 3.8 Has execute permission
    if [ -x "${PING_SCRIPT}" ]; then
        assert_pass "healthchecks-ping.sh is executable"
    else
        assert_fail "healthchecks-ping.sh is executable" "run: chmod +x ${PING_SCRIPT}"
    fi
fi

# =============================================================
# Test Suite 4: launchd plist template
# =============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Suite 4: launchd plist template"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PLIST_TMPL="${REPO_ROOT}/scripts/launchd/ai.myopenclaw.healthchecks-ping.plist.template"

if [ ! -f "${PLIST_TMPL}" ]; then
    assert_fail "healthchecks-ping plist template exists"
else
    assert_pass "healthchecks-ping plist template exists"

    # 4.1 Valid XML (basic check: has plist doctype or <plist>)
    if grep -q '<plist' "${PLIST_TMPL}"; then
        assert_pass "plist template contains <plist> root element"
    else
        assert_fail "plist template XML structure"
    fi

    # 4.2 Label
    if grep -q 'ai.myopenclaw.healthchecks-ping' "${PLIST_TMPL}"; then
        assert_pass "plist Label: ai.myopenclaw.healthchecks-ping"
    else
        assert_fail "plist Label"
    fi

    # 4.3 StartInterval = 60
    if grep -A 1 'StartInterval' "${PLIST_TMPL}" | grep -q '60'; then
        assert_pass "plist StartInterval: 60 seconds"
    else
        assert_fail "plist StartInterval" "expected <integer>60</integer>"
    fi

    # 4.4 RunAtLoad = true
    if grep -A 1 'RunAtLoad' "${PLIST_TMPL}" | grep -q 'true'; then
        assert_pass "plist RunAtLoad: true"
    else
        assert_fail "plist RunAtLoad" "expected <true/>"
    fi

    # 4.5 KeepAlive = false (not a daemon)
    if grep -A 1 'KeepAlive' "${PLIST_TMPL}" | grep -q 'false'; then
        assert_pass "plist KeepAlive: false"
    else
        assert_fail "plist KeepAlive" "expected <false/>"
    fi

    # 4.6 References the ping script
    if grep -q 'healthchecks-ping.sh' "${PLIST_TMPL}"; then
        assert_pass "plist references healthchecks-ping.sh"
    else
        assert_fail "plist references ping script"
    fi

    # 4.7 Has PATH environment variable
    if grep -q 'PATH' "${PLIST_TMPL}" || grep -q '__PATH__' "${PLIST_TMPL}"; then
        assert_pass "plist has PATH env var"
    else
        assert_fail "plist PATH env var"
    fi
fi

# =============================================================
# Test Suite 5: install-healthchecks-ping.sh
# =============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Suite 5: install-healthchecks-ping.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

INSTALL_SCRIPT="${REPO_ROOT}/scripts/launchd/install-healthchecks-ping.sh"

if [ ! -f "${INSTALL_SCRIPT}" ]; then
    assert_fail "install-healthchecks-ping.sh exists"
else
    assert_pass "install-healthchecks-ping.sh exists"

    if [ -x "${INSTALL_SCRIPT}" ]; then
        assert_pass "install-healthchecks-ping.sh is executable"
    else
        assert_fail "install-healthchecks-ping.sh is executable"
    fi

    if grep -q 'set -euo pipefail' "${INSTALL_SCRIPT}"; then
        assert_pass "install-healthchecks-ping.sh has set -euo pipefail"
    else
        assert_fail "install-healthchecks-ping.sh set -euo pipefail"
    fi

    if grep -q 'launchctl load' "${INSTALL_SCRIPT}"; then
        assert_pass "install-healthchecks-ping.sh loads via launchctl"
    else
        assert_fail "install-healthchecks-ping.sh launchctl load"
    fi

    if grep -q 'launchctl list\|launchctl start' "${INSTALL_SCRIPT}"; then
        assert_pass "install-healthchecks-ping.sh has verification output"
    else
        assert_fail "install-healthchecks-ping.sh verification"
    fi
fi

# =============================================================
# Test Suite 6: docs/monitoring.md
# =============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Suite 6: docs/monitoring.md"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

MONITORING_DOC="${REPO_ROOT}/docs/monitoring.md"

if [ ! -f "${MONITORING_DOC}" ]; then
    assert_fail "docs/monitoring.md exists"
else
    assert_pass "docs/monitoring.md exists"

    # Check for required sections
    for section in "Uptime Kuma" "Healthchecks" "监控" "告警" "飞书" "故障"; do
        if grep -q "${section}" "${MONITORING_DOC}"; then
            assert_pass "docs/monitoring.md contains '${section}'"
        else
            assert_fail "docs/monitoring.md section '${section}'" "missing required section"
        fi
    done
fi

# =============================================================
# Test Suite 7: .gitignore includes logs/
# =============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Suite 7: .gitignore"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

GITIGNORE="${REPO_ROOT}/.gitignore"

if grep -q 'logs/' "${GITIGNORE}" 2>/dev/null; then
    assert_pass ".gitignore includes logs/"
else
    assert_fail ".gitignore includes logs/" "add 'logs/' to .gitignore"
fi

# =============================================================
# Results
# =============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Results: ${PASS} passed, ${FAIL} failed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "${FAIL}" -gt 0 ]; then
    echo ""
    red "❌ ${FAIL} test(s) failed — implement the missing pieces"
    exit 1
else
    echo ""
    green "✅ All ${PASS} tests passed"
    exit 0
fi
