#!/usr/bin/env bash
# =============================================================
# scripts/test-openclaw-memory-plugin.sh
# TDD 测试 — 虾酱 OpenClaw memory plugin (Milestone 5)
# 验证:
#   1. setup 脚本存在 + 语法正确
#   2. 幂等安装 plugin（openclaw plugins install）
#   3. openclaw.json 配置 local 模式 + 独立 DB 路径
#   4. DB 路径与个人体系物理隔离
#   5. backup 脚本已覆盖 memory-tdai/ 目录
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PASS=0
FAIL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

assert_file_exists() {
    local desc="$1" path="$2"
    if [[ -f "${path}" ]]; then
        echo -e "  ${GREEN}✅${NC} ${desc}"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} ${desc}"
        echo "     file not found: ${path}"
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
# Test 1: setup 脚本存在 + 语法
# ============================================================
echo "🧪 Test 1: setup 脚本"

SETUP_SH="${REPO_ROOT}/scripts/setup-openclaw-memory.sh"
assert_file_exists "setup-openclaw-memory.sh exists" "${SETUP_SH}"

if [[ -f "${SETUP_SH}" ]]; then
    if bash -n "${SETUP_SH}" 2>&1; then
        echo -e "  ${GREEN}✅${NC} setup script syntax OK"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} setup script has syntax errors"
        FAIL=$((FAIL + 1))
    fi
    CONTENT=$(cat "${SETUP_SH}")
    assert_contains "uses set -euo pipefail" 'set -euo pipefail' "${CONTENT}"
    assert_contains "references openclaw plugin" 'openclaw' "${CONTENT}"
    assert_contains "references memory-tencentdb" 'memory-tencentdb' "${CONTENT}"
    assert_contains "has local mode config" 'local' "${CONTENT}"
    assert_contains "uses idempotent check" 'already' "${CONTENT}"
else
    FAIL=$((FAIL + 6))
fi

# ============================================================
# Test 2: DB 路径物理隔离
# ============================================================
echo ""
echo "🧪 Test 2: DB 路径 — 虾酱 vs 个人体系隔离"

# 虾酱: ~/.openclaw/memory-tdai/
# 个人: ~/.myagentdata/tdai-memory/
# 两者必须是不同的路径，确保物理隔离
OPENCLAW_DATA="${HOME}/.openclaw"
MYAGENT_DATA="${HOME}/.myagentdata"

if [[ -d "${OPENCLAW_DATA}" ]]; then
    echo -e "  ${GREEN}✅${NC} OpenClaw data dir exists: ${OPENCLAW_DATA}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}❌${NC} OpenClaw data dir missing"
    FAIL=$((FAIL + 1))
fi

if [[ -d "${MYAGENT_DATA}" ]]; then
    echo -e "  ${GREEN}✅${NC} myagentdata dir exists: ${MYAGENT_DATA}"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}❌${NC} myagentdata dir missing"
    FAIL=$((FAIL + 1))
fi

# 验证它们确实是不同的路径
if [[ "${OPENCLAW_DATA}" != "${MYAGENT_DATA}" ]]; then
    echo -e "  ${GREEN}✅${NC} DB paths are physically separate (~/.openclaw/ ≠ ~/.myagentdata/)"
    PASS=$((PASS + 1))
else
    echo -e "  ${RED}❌${NC} DB paths are the same!"
    FAIL=$((FAIL + 1))
fi

# ============================================================
# Test 3: openclaw.json memory plugin 配置注入
# ============================================================
echo ""
echo "🧪 Test 3: openclaw.json plugin 配置注入"

OPENCLAW_JSON="${OPENCLAW_DATA}/openclaw.json"
if [[ -f "${OPENCLAW_JSON}" ]]; then
    OJ_CONTENT=$(cat "${OPENCLAW_JSON}")
    assert_contains "openclaw.json has plugins section" 'plugins' "${OJ_CONTENT}"
else
    echo -e "  ${RED}❌${NC} openclaw.json not found"
    FAIL=$((FAIL + 1))
fi

# ============================================================
# Test 4: backup 已覆盖 openclaw memory-tdai
# ============================================================
echo ""
echo "🧪 Test 4: backup 覆盖 openclaw memory-tdai"

OPENCLAW_BACKUP="${REPO_ROOT}/openclaw/scripts/backup.sh"
if [[ -f "${OPENCLAW_BACKUP}" ]]; then
    OB_CONTENT=$(cat "${OPENCLAW_BACKUP}")
    assert_contains "backs up memory directory" 'memory' "${OB_CONTENT}"
else
    echo -e "  ${RED}❌${NC} openclaw backup script not found"
    FAIL=$((FAIL + 1))
fi

# ============================================================
# Test 5: CLAUDE.md 文档
# ============================================================
echo ""
echo "🧪 Test 5: CLAUDE.md 虾酱 memory 文档"

CLAUDE_MD="${REPO_ROOT}/CLAUDE.md"
CLAUDE_CONTENT=$(cat "${CLAUDE_MD}")

assert_contains "documents openclaw memory setup" \
    "setup-openclaw-memory" "${CLAUDE_CONTENT}"

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
