#!/usr/bin/env bash
# =============================================================
# scripts/test-tdai-backup.sh
# TDD 测试 — tdai-memory backup 管线 (Milestone 3)
# 验证:
#   1. backup 脚本存在 + 语法正确
#   2. sqlite3 .backup 热备模式（同 OpenClaw）
#   3. backup-all-docker.sh 已接线
#   4. backup-cron docker-compose 卷挂载正确
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
# Test 1: backup 脚本存在 + 语法
# ============================================================
echo "🧪 Test 1: backup 脚本"

BACKUP_SH="${REPO_ROOT}/tdai-memory/scripts/backup.sh"
assert_file_exists "backup.sh exists" "${BACKUP_SH}"

if [[ -f "${BACKUP_SH}" ]]; then
    if bash -n "${BACKUP_SH}" 2>&1; then
        echo -e "  ${GREEN}✅${NC} backup.sh syntax OK"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} backup.sh has syntax errors"
        FAIL=$((FAIL + 1))
    fi
    CONTENT=$(cat "${BACKUP_SH}")
    assert_contains "uses set -euo pipefail" 'set -euo pipefail' "${CONTENT}"
    assert_contains "uses sqlite3 .backup" '.backup' "${CONTENT}"
    assert_contains "backs up memories.sqlite" 'memories.sqlite' "${CONTENT}"
    assert_contains "backs up persona.md" 'persona.md' "${CONTENT}"
    assert_contains "requires sqlite3 (no cp fallback)" 'sqlite3 未安装' "${CONTENT}"
    assert_contains "syncs to latest/" 'latest' "${CONTENT}"
    assert_contains "prunes old snapshots" 'BACKUP_KEEP_DAYS' "${CONTENT}"
else
    FAIL=$((FAIL + 8))
fi

# ============================================================
# Test 2: backup-all-docker.sh 接线
# ============================================================
echo ""
echo "🧪 Test 2: backup-all-docker.sh 接线"

BACKUP_ALL="${REPO_ROOT}/scripts/backup-all-docker.sh"
BA_CONTENT=$(cat "${BACKUP_ALL}")

assert_contains "calls tdai-memory backup script" 'tdai' "${BA_CONTENT}"

# ============================================================
# Test 3: backup-cron docker-compose 卷挂载
# ============================================================
echo ""
echo "🧪 Test 3: backup-cron 卷挂载"

COMPOSE="${REPO_ROOT}/docker-compose.yml"
COMPOSE_CONTENT=$(cat "${COMPOSE}")

assert_contains "backup-cron mounts tdai scripts" 'tdai-scripts' "${COMPOSE_CONTENT}"
assert_contains "backup-cron mounts .myagentdata (ro)" '.myagentdata' "${COMPOSE_CONTENT}"

# ============================================================
# Test 4: 模拟备份执行
# ============================================================
echo ""
echo "🧪 Test 4: 模拟备份执行"

if [[ -f "${BACKUP_SH}" ]]; then
    TEST_TMP=$(mktemp -d)
    trap 'rm -rf "$TEST_TMP"' EXIT

    # 创建假的源数据
    mkdir -p "${TEST_TMP}/tdai-memory/scene_blocks"
    echo "# Test Persona" > "${TEST_TMP}/tdai-memory/persona.md"
    echo '{"test":true}' > "${TEST_TMP}/tdai-memory/checkpoint.json"
    # 创建假的 SQLite 数据库
    sqlite3 "${TEST_TMP}/tdai-memory/memories.sqlite" "CREATE TABLE IF NOT EXISTS test(id INTEGER);" 2>/dev/null || true

    # 运行备份脚本
    BACKUP_ROOT="${TEST_TMP}/backups" \
    TDAI_DATA_SRC="${TEST_TMP}/tdai-memory" \
        bash "${BACKUP_SH}" "test-ts" 2>&1 || true

    # 验证输出
    if [[ -f "${TEST_TMP}/backups/tdai-memory/test-ts/memories.sqlite" ]]; then
        echo -e "  ${GREEN}✅${NC} memories.sqlite backed up"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} memories.sqlite missing from backup"
        FAIL=$((FAIL + 1))
    fi

    if [[ -f "${TEST_TMP}/backups/tdai-memory/test-ts/persona.md" ]]; then
        echo -e "  ${GREEN}✅${NC} persona.md backed up"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} persona.md missing from backup"
        FAIL=$((FAIL + 1))
    fi

    if [[ -f "${TEST_TMP}/backups/tdai-memory/test-ts/checkpoint.json" ]]; then
        echo -e "  ${GREEN}✅${NC} checkpoint.json backed up"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} checkpoint.json missing from backup"
        FAIL=$((FAIL + 1))
    fi

    # 验证 latest/ 符号链接
    if [[ -L "${TEST_TMP}/backups/tdai-memory/latest" ]] || [[ -d "${TEST_TMP}/backups/tdai-memory/latest" ]]; then
        echo -e "  ${GREEN}✅${NC} latest/ symlink created"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} latest/ symlink missing"
        FAIL=$((FAIL + 1))
    fi
else
    FAIL=$((FAIL + 4))
fi

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
