#!/usr/bin/env bash
# =============================================================
# scripts/test-tdai-memory.sh
# TDD 测试 — tdai-memory Gateway 容器冒烟
# 验证:
#   1. Dockerfile + entrypoint.sh 存在且语法正确
#   2. docker-compose.yml 含 tdai-memory service
#   3. 镜像构建成功
#   4. 容器启动 + /health 返回 200
#   5. docker compose ps 显示 healthy
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
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

# ============================================================
# Test 1: 源文件存在 + 语法检查
# ============================================================
echo "🧪 Test 1: 源文件是否存在"

DOCKERFILE="${REPO_ROOT}/docker/tdai-memory/Dockerfile"
ENTRYPOINT="${REPO_ROOT}/docker/tdai-memory/entrypoint.sh"

assert_file_exists "Dockerfile exists" "${DOCKERFILE}"
assert_file_exists "entrypoint.sh exists" "${ENTRYPOINT}"

if [[ -f "${ENTRYPOINT}" ]]; then
    if bash -n "${ENTRYPOINT}" 2>&1; then
        echo -e "  ${GREEN}✅${NC} entrypoint.sh syntax OK"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} entrypoint.sh has syntax errors"
        FAIL=$((FAIL + 1))
    fi
else
    echo -e "  ${RED}❌${NC} entrypoint.sh syntax check skipped (file missing)"
    FAIL=$((FAIL + 1))
fi

# ============================================================
# Test 2: Dockerfile 关键内容检查
# ============================================================
echo ""
echo "🧪 Test 2: Dockerfile 关键内容"

if [[ -f "${DOCKERFILE}" ]]; then
    DF=$(cat "${DOCKERFILE}")
    assert_contains "uses ubuntu:24.04 base" "ubuntu:24.04" "${DF}"
    assert_contains "installs memory-tencentdb package" "memory-tencentdb" "${DF}"
    assert_contains "exposes port 8420" "8420" "${DF}"
    assert_contains "sets TDAI_GATEWAY_HOST=0.0.0.0" "TDAI_GATEWAY_HOST" "${DF}"
    assert_contains "sets TDAI_DATA_DIR" "TDAI_DATA_DIR" "${DF}"
else
    FAIL=$((FAIL + 5))
    echo -e "  ${RED}❌${NC} Dockerfile not found, skipping content checks"
fi

# ============================================================
# Test 3: entrypoint.sh 关键内容检查
# ============================================================
echo ""
echo "🧪 Test 3: entrypoint.sh 关键内容"

if [[ -f "${ENTRYPOINT}" ]]; then
    EP=$(cat "${ENTRYPOINT}")
    assert_contains "cd to package dir or references memory-tencentdb" "memory-tencentdb" "${EP}"
    assert_contains "runs gateway server.ts" "server.ts" "${EP}"
else
    FAIL=$((FAIL + 2))
    echo -e "  ${RED}❌${NC} entrypoint.sh not found, skipping content checks"
fi

# ============================================================
# Test 4: docker-compose.yml 含 tdai-memory service
# ============================================================
echo ""
echo "🧪 Test 4: docker-compose.yml tdai-memory service"

COMPOSE="${REPO_ROOT}/docker-compose.yml"
COMPOSE_CONTENT=$(cat "${COMPOSE}")

assert_contains "has tdai-memory service block" "tdai-memory:" "${COMPOSE_CONTENT}"
assert_contains "joins myopenclaw-net" "myopenclaw-net" "${COMPOSE_CONTENT}"
assert_contains "has healthcheck" "healthcheck" "${COMPOSE_CONTENT}"
assert_contains "has TDAI_LLM_API_KEY env" "TDAI_LLM_API_KEY" "${COMPOSE_CONTENT}"
assert_contains "has TDAI_LLM_BASE_URL env" "TDAI_LLM_BASE_URL" "${COMPOSE_CONTENT}"
assert_contains "has TDAI_LLM_MODEL env" "TDAI_LLM_MODEL" "${COMPOSE_CONTENT}"
assert_contains "mounts .myagentdata/tdai-memory" ".myagentdata/tdai-memory" "${COMPOSE_CONTENT}"

# ============================================================
# Test 5: .env.example 更新
# ============================================================
echo ""
echo "🧪 Test 5: .env.example"

ENV_EXAMPLE="${REPO_ROOT}/.env.example"
ENV_CONTENT=$(cat "${ENV_EXAMPLE}")
assert_contains "has TDAI_MEMORY_PORT" "TDAI_MEMORY_PORT" "${ENV_CONTENT}"

# ============================================================
# Test 6: start.sh 数据目录预创建
# ============================================================
echo ""
echo "🧪 Test 6: start.sh 数据目录预创建"

START_SH="${REPO_ROOT}/scripts/start.sh"
START_CONTENT=$(cat "${START_SH}")
assert_contains "creates tdai-memory data dir" "tdai-memory" "${START_CONTENT}"

# ============================================================
# Test 7: CLAUDE.md 文档更新
# ============================================================
echo ""
echo "🧪 Test 7: CLAUDE.md 文档更新"

CLAUDE_MD="${REPO_ROOT}/CLAUDE.md"
CLAUDE_CONTENT=$(cat "${CLAUDE_MD}")
assert_contains "documents tdai-memory service" "tdai-memory" "${CLAUDE_CONTENT}"

# ============================================================
# Test 8: 镜像构建 + 容器冒烟（需要 Docker）
# ============================================================
echo ""
echo "🧪 Test 8: 镜像构建 + 容器冒烟"

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    # 构建镜像
    echo "   🔨 构建 tdai-memory 镜像..."
    if docker build -t myopenclaw/tdai-memory:test \
        -f "${DOCKERFILE}" \
        "$(dirname "${DOCKERFILE}")" >/dev/null 2>&1; then
        echo -e "  ${GREEN}✅${NC} docker build succeeded"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} docker build failed"
        FAIL=$((FAIL + 1))
    fi

    # 启动冒烟容器
    echo "   🚀 启动冒烟容器..."
    docker rm -f tdai-smoke-test 2>/dev/null || true
    if docker run --rm -d --name tdai-smoke-test \
        -p 18420:8420 \
        myopenclaw/tdai-memory:test >/dev/null 2>&1; then
        echo -e "  ${GREEN}✅${NC} container started"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} container failed to start"
        FAIL=$((FAIL + 1))
    fi

    # 等容器就绪 + 测 /health
    echo "   ⏳ 等待 Gateway 就绪..."
    HEALTH=""
    for _ in $(seq 1 15); do
        if HEALTH=$(curl -s http://127.0.0.1:18420/health 2>/dev/null); then
            break
        fi
        sleep 2
    done

    if echo "${HEALTH}" | grep -q '"status":"ok"'; then
        echo -e "  ${GREEN}✅${NC} /health returns ok: ${HEALTH}"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} /health failed, got: ${HEALTH:-timeout}"
        FAIL=$((FAIL + 1))
    fi

    # 清理
    docker stop tdai-smoke-test 2>/dev/null || true
else
    echo "   ⚠️  Docker 不可用，跳过冒烟测试"
    FAIL=$((FAIL + 3))
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
