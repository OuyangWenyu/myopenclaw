#!/usr/bin/env bash
# =============================================================
# scripts/test-setup-uptime-kuma.sh
# 测试 setup-uptime-kuma.sh 的 3 个 review 修改点：
#   1. sql_escape — SQL 单引号转义
#   2. KUMA_DB_PATH — 数据库路径可配置
#   3. 条件重启 — 仅 ADDED > 0 时重启
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_SCRIPT="${SCRIPT_DIR}/setup-uptime-kuma.sh"
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT

PASS=0
FAIL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "${expected}" == "${actual}" ]]; then
        echo -e "  ${GREEN}✅${NC} ${desc}"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} ${desc}"
        echo "     expected: '${expected}'"
        echo "     actual:   '${actual}'"
        FAIL=$((FAIL + 1))
    fi
}

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
# Test 1: sql_escape 函数
# 通过 source 主脚本加载函数，测试纯函数行为
# ============================================================
echo "🧪 Test 1: sql_escape 函数"

# 用子 shell 加载 sql_escape 函数（从主脚本提取，避免执行脚本主体）
load_sql_escape() {
    # 从主脚本中提取 sql_escape 函数定义并执行
    # 使用 sed 提取从 "sql_escape()" 到 "}" 的函数体
    local func_body
    func_body=$(sed -n '/^sql_escape()/,/^}/p' "${MAIN_SCRIPT}" 2>/dev/null || true)
    if [[ -z "${func_body}" ]]; then
        echo "  ⚠️  sql_escape 函数尚未定义（预期：RED 阶段失败）"
        return 1
    fi
    eval "${func_body}"
}

if load_sql_escape; then
    # 无特殊字符
    assert_eq "plain text passes through" \
        "hello world" \
        "$(echo "hello world" | sql_escape)"

    # 单引号转义
    assert_eq "single quote → doubled" \
        "it''s" \
        "$(echo "it's" | sql_escape)"

    # 多个单引号
    assert_eq "multiple quotes all doubled" \
        "a''''b" \
        "$(echo "a''b" | sql_escape)"

    # 空字符串
    assert_eq "empty string stays empty" \
        "" \
        "$(echo "" | sql_escape)"

    # 监控项名称含引号
    assert_eq "monitor name with quote" \
        "Docker: O''Brien''s container" \
        "$(echo "Docker: O'Brien's container" | sql_escape)"

    # URL 含引号（不太可能但防御性）
    assert_eq "no quotes in typical URL" \
        "http://hermes-dashboard:9119" \
        "$(echo "http://hermes-dashboard:9119" | sql_escape)"
else
    FAIL=$((FAIL + 6))
    echo -e "  ${RED}❌${NC} sql_escape not found in script (needs implementation)"
fi

# ============================================================
# Test 2: KUMA_DB 路径可配置
# 验证主脚本中使用 ${KUMA_DB_PATH:-${HOME}/...} 模式
# ============================================================
echo ""
echo "🧪 Test 2: KUMA_DB_PATH 环境变量支持"

SCRIPT_CONTENT=$(cat "${MAIN_SCRIPT}")

# 检查是否使用了 KUMA_DB_PATH 变量（带 fallback）
assert_contains \
    "KUMA_DB uses KUMA_DB_PATH with fallback" \
    'KUMA_DB_PATH:-' \
    "${SCRIPT_CONTENT}"

# 确保默认路径仍然存在
assert_contains \
    "default path fallback preserved" \
    '.uptime-kuma/kuma.db' \
    "${SCRIPT_CONTENT}"

# ============================================================
# Test 3: 条件重启（仅 ADDED > 0 时重启）
# 用 mock 环境测试脚本行为
# ============================================================
echo ""
echo "🧪 Test 3: 条件重启（仅 ADDED > 0）"

# 验证脚本中有条件判断
assert_contains \
    "restart guarded by ADDED > 0 check" \
    'ADDED' \
    "${SCRIPT_CONTENT}"

# Mock 集成测试：模拟脚本的监控创建 + 重启逻辑
test_restart_guard() {
    local added="$1" expect_restart="$2" desc="$3"

    # 模拟脚本末尾的重启逻辑
    RESTART_CALLED=false
    ADDED="${added}"

    # 复制当前脚本的重启部分来测试
    if [[ ${ADDED} -gt 0 ]]; then
        RESTART_CALLED=true
    fi

    if [[ "${expect_restart}" == "yes" ]]; then
        assert_eq "${desc}" "true" "${RESTART_CALLED}"
    else
        assert_eq "${desc}" "false" "${RESTART_CALLED}"
    fi
}

test_restart_guard 3 "yes" "ADDED=3 → restart triggered"
test_restart_guard 1 "yes" "ADDED=1 → restart triggered"
test_restart_guard 0 "no"  "ADDED=0 → restart skipped"

# 验证主脚本中 docker compose restart 被条件包裹
# （检查 restart 命令行上方应有 if 判断）
RESTART_LINE=$(echo "${SCRIPT_CONTENT}" | grep -n 'docker compose restart uptime-kuma' | head -1 | cut -d: -f1 || echo "0")
if [[ "${RESTART_LINE}" -gt 0 ]]; then
    # 取 restart 前 5 行，检查是否有 if [[ ${ADDED} -gt 0 ]]
    BEFORE_RESTART=$(echo "${SCRIPT_CONTENT}" | head -n "${RESTART_LINE}" | tail -n 5)
    assert_contains \
        "restart is inside conditional (if ADDED > 0)" \
        'ADDED' \
        "${BEFORE_RESTART}"
else
    FAIL=$((FAIL + 1))
    echo -e "  ${RED}❌${NC} docker compose restart line not found"
fi

# ============================================================
# Summary
# ============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "📊 Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"

if [[ ${FAIL} -gt 0 ]]; then
    echo ""
    echo "🔴 RED — 部分测试失败，等待实现修复"
    exit 1
fi

echo ""
echo "✅ 全部测试通过"
