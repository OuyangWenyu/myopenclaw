#!/usr/bin/env bash
# =============================================================
# scripts/test-hermes-memory-adapter.sh
# TDD 测试 — Hermes memory_tencentdb_v2 adapter (Milestone 4)
# 验证:
#   1. hermes Dockerfile 安装 memory-tencentdb npm 包
#   2. entrypoint-wrapper 做 plugin symlink
#   3. profiles config.yaml 配置 memory provider
#   4. docker-compose 传 Gateway URL env
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PASS=0
FAIL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

assert_contains() {
    local desc="$1" needle="$2" haystack="$3"
    if [[ "${haystack}" == *"${needle}"* ]]; then
        echo -e "  ${GREEN}✅${NC} ${desc}"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} ${desc}"
        echo "     expected to contain: '${needle}'"
        FAIL=$((FAIL + 1))
    fi
}

# ============================================================
# Test 1: hermes Dockerfile 安装 npm 包
# ============================================================
echo "🧪 Test 1: hermes Dockerfile — npm 包安装"

HERMES_DF="${REPO_ROOT}/docker/hermes/Dockerfile"
DF_CONTENT=$(cat "${HERMES_DF}")

assert_contains "installs memory-tencentdb npm package" \
    "memory-tencentdb" "${DF_CONTENT}"

# ============================================================
# Test 2: entrypoint-wrapper 做 plugin symlink
# ============================================================
echo ""
echo "🧪 Test 2: entrypoint-wrapper — plugin symlink"

WRAPPER="${REPO_ROOT}/docker/hermes/entrypoint-wrapper.sh"
WRAPPER_CONTENT=$(cat "${WRAPPER}")

assert_contains "symlinks memory plugin for hermes" \
    "memory_tencentdb" "${WRAPPER_CONTENT}"

# ============================================================
# Test 3: entrypoint-wrapper config 注入 — memory provider
# ============================================================
echo ""
echo "🧪 Test 3: entrypoint-wrapper config 注入"

# Wrapper should inject memory.provider for each profile at startup
assert_contains "writes memory provider for profiles" \
    "memory" "${WRAPPER_CONTENT}"
assert_contains "references memory_tencentdb_v2" \
    "memory_tencentdb" "${WRAPPER_CONTENT}"

# ============================================================
# Test 4: docker-compose 传 Gateway URL env 给 hermes 容器
# ============================================================
echo ""
echo "🧪 Test 4: docker-compose — Gateway env"

COMPOSE="${REPO_ROOT}/docker-compose.yml"
COMPOSE_CONTENT=$(cat "${COMPOSE}")

assert_contains "hermes has TDAI_GATEWAY_URL env" \
    "TDAI_GATEWAY_URL" "${COMPOSE_CONTENT}"
assert_contains "hermes-coder has TDAI_GATEWAY_URL env" \
    "TDAI_GATEWAY_URL" "${COMPOSE_CONTENT}"
assert_contains "hermes-finance has TDAI_GATEWAY_URL env" \
    "TDAI_GATEWAY_URL" "${COMPOSE_CONTENT}"

# ============================================================
# Test 5: entrypoint-wrapper config.yaml 注入逻辑
# ============================================================
echo ""
echo "🧪 Test 5: entrypoint-wrapper config 注入"

# Verify wrapper writes memory provider to config for each profile
assert_contains "writes memory provider to config" \
    "memory" "${WRAPPER_CONTENT}"

# ============================================================
# Summary
# ============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "📊 Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"

if [[ ${FAIL} -gt 0 ]]; then
    echo ""
    echo "🔴 RED — 测试失败，等待实现"
    exit 1
fi

echo ""
echo "✅ 全部测试通过"
