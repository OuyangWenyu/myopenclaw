#!/usr/bin/env bash
# =============================================================
# scripts/test-hermes-memory-adapter.sh
# TDD 测试 — Hermes memory_tencentdb adapter (Milestone 4, 实测修正版)
# 验证（匹配集成实测后的真实实现）:
#   1. entrypoint-wrapper 运行时安装 memory-tencentdb npm 包
#   2. entrypoint-wrapper 用 cp（非 symlink）部署 plugin
#   3. entrypoint-wrapper 注入 provider: memory_tencentdb 到 config
#   4. docker-compose 传 MEMORY_TENCENTDB_GATEWAY_HOST/PORT env
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

count_occurrences() {
    local needle="$1" haystack="$2"
    grep -c "${needle}" <<< "${haystack}" || true
}

WRAPPER="${REPO_ROOT}/docker/hermes/entrypoint-wrapper.sh"
WRAPPER_CONTENT=$(cat "${WRAPPER}")
COMPOSE="${REPO_ROOT}/docker-compose.yml"
COMPOSE_CONTENT=$(cat "${COMPOSE}")

# ============================================================
# Test 1: entrypoint-wrapper 运行时安装 npm 包
# ============================================================
echo "🧪 Test 1: entrypoint-wrapper — 运行时 npm 安装"
assert_contains "installs memory-tencentdb at runtime" \
    "npm install -g" "${WRAPPER_CONTENT}"
assert_contains "references memory-tencentdb package" \
    "memory-tencentdb" "${WRAPPER_CONTENT}"

# ============================================================
# Test 2: plugin 用 cp 部署（非 symlink，Hermes 扫描器要求）
# ============================================================
echo ""
echo "🧪 Test 2: plugin 部署方式 — cp（非 symlink）"
assert_contains "copies plugin dir (cp -r)" \
    "cp -r" "${WRAPPER_CONTENT}"
assert_contains "targets plugins/memory/memory_tencentdb" \
    "plugins/memory/memory_tencentdb" "${WRAPPER_CONTENT}"

# ============================================================
# Test 3: provider 注入 memory_tencentdb（非 _v2）
# ============================================================
echo ""
echo "🧪 Test 3: config 注入 provider"
assert_contains "injects provider: memory_tencentdb" \
    "provider: memory_tencentdb" "${WRAPPER_CONTENT}"
# 确保没有残留错误的 _v2 名
if grep -q 'memory_tencentdb_v2' <<< "${WRAPPER_CONTENT}"; then
    echo -e "  ${RED}❌${NC} 残留错误 provider 名 memory_tencentdb_v2"
    FAIL=$((FAIL + 1))
else
    echo -e "  ${GREEN}✅${NC} 无残留 _v2 错误名"
    PASS=$((PASS + 1))
fi

# ============================================================
# Test 4: 只改 memory: 段，不误伤 delegation.provider
# ============================================================
echo ""
echo "🧪 Test 4: 精确限定 memory section"
assert_contains "tracks top-level section for injection" \
    "section == 'memory'" "${WRAPPER_CONTENT}"

# ============================================================
# Test 5: docker-compose 传 Gateway env（env 变量，非 config gateway_url）
# ============================================================
echo ""
echo "🧪 Test 5: docker-compose — Gateway env"
GATEWAY_HOST_COUNT=$(count_occurrences 'MEMORY_TENCENTDB_GATEWAY_HOST=tdai-memory' "${COMPOSE_CONTENT}")
# 3 个 hermes 服务 + 1 个 claude-code = 至少 3（hermes 三兄弟）
if [[ "${GATEWAY_HOST_COUNT}" -ge 3 ]]; then
    echo -e "  ${GREEN}✅${NC} Gateway host env 在 ≥3 个服务 (found ${GATEWAY_HOST_COUNT})"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}❌${NC} Gateway host env 数量不足 (found ${GATEWAY_HOST_COUNT}, need ≥3)"
    FAIL=$((FAIL + 1))
fi
assert_contains "Gateway port env present" \
    "MEMORY_TENCENTDB_GATEWAY_PORT=8420" "${COMPOSE_CONTENT}"

# ============================================================
# Summary
# ============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "📊 Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"

if [[ ${FAIL} -gt 0 ]]; then
    echo ""
    echo "🔴 测试失败"
    exit 1
fi

echo ""
echo "✅ 全部测试通过"
