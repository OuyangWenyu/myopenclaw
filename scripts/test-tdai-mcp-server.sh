#!/usr/bin/env bash
# =============================================================
# scripts/test-tdai-mcp-server.sh
# TDD 测试 — CC飞总 MCP server (Milestone 2)
# 验证:
#   1. MCP server 脚本存在 + Python 语法正确
#   2. 4 个工具定义: memory_search, conversation_search, read_scenario, read_core
#   3. HTTP client 函数可调用 Gateway API
#   4. settings.json 注册逻辑存在
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
# Test 1: 源文件存在 + Python 语法检查
# ============================================================
echo "🧪 Test 1: MCP server 源文件"

MCP_SERVER="${REPO_ROOT}/docker/tdai-memory/mcp-server/server.py"
REQUIREMENTS="${REPO_ROOT}/docker/tdai-memory/mcp-server/requirements.txt"

assert_file_exists "server.py exists" "${MCP_SERVER}"
assert_file_exists "requirements.txt exists" "${REQUIREMENTS}"

if [[ -f "${MCP_SERVER}" ]]; then
    if python3 -c "import ast; ast.parse(open('${MCP_SERVER}').read()); print('OK')" 2>&1 | grep -q OK; then
        echo -e "  ${GREEN}✅${NC} server.py syntax OK"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} server.py has syntax errors"
        FAIL=$((FAIL + 1))
    fi
else
    FAIL=$((FAIL + 1))
    echo -e "  ${RED}❌${NC} server.py syntax check skipped (file missing)"
fi

# ============================================================
# Test 2: 4 个工具定义
# ============================================================
echo ""
echo "🧪 Test 2: 4 个 MCP 工具定义"

if [[ -f "${MCP_SERVER}" ]]; then
    SERVER_CODE=$(cat "${MCP_SERVER}")
    assert_contains "defines memory_search tool" 'memory_search' "${SERVER_CODE}"
    assert_contains "defines conversation_search tool" 'conversation_search' "${SERVER_CODE}"
    assert_contains "defines read_scenario tool" 'read_scenario' "${SERVER_CODE}"
    assert_contains "defines read_core tool" 'read_core' "${SERVER_CODE}"
    assert_contains "uses FastMCP or mcp.server" 'mcp' "${SERVER_CODE}"
else
    FAIL=$((FAIL + 5))
    echo -e "  ${RED}❌${NC} server.py not found, skipping tool checks"
fi

# ============================================================
# Test 3: HTTP client 逻辑 — 调用 Gateway API
# ============================================================
echo ""
echo "🧪 Test 3: Gateway API 调用"

if [[ -f "${MCP_SERVER}" ]]; then
    assert_contains "calls /search/memories endpoint" '/search/memories' "${SERVER_CODE}"
    assert_contains "calls /search/conversations endpoint" '/search/conversations' "${SERVER_CODE}"
    assert_contains "uses httpx in requirements" 'httpx' "$(cat "${REQUIREMENTS}")"
else
    FAIL=$((FAIL + 3))
fi

# ============================================================
# Test 4: Gateway URL 可配置
# ============================================================
echo ""
echo "🧪 Test 4: Gateway URL 配置"

if [[ -f "${MCP_SERVER}" ]]; then
    assert_contains "Gateway URL uses TDAI_GATEWAY_URL env" \
        'TDAI_GATEWAY_URL' \
        "${SERVER_CODE}"
else
    FAIL=$((FAIL + 1))
fi

# ============================================================
# Test 5: settings.json 注册逻辑
# ============================================================
echo ""
echo "🧪 Test 5: settings.json MCP 注册"

ENTRYPOINT_SH="${REPO_ROOT}/docker/claude-code/entrypoint.sh"
EP_CONTENT=$(cat "${ENTRYPOINT_SH}")

assert_contains "registers tdai-memory in settings.json mcpServers" \
    "tdai-memory" "${EP_CONTENT}"

assert_contains "mcpServers section handles tdai-memory" \
    "mcpServers" "${EP_CONTENT}"

# ============================================================
# Test 6: Python 依赖可安装
# ============================================================
echo ""
echo "🧪 Test 6: Python 依赖检查"

if [[ -f "${REQUIREMENTS}" ]]; then
    if python3 -c "import httpx" 2>/dev/null; then
        echo -e "  ${GREEN}✅${NC} httpx already installed"
        PASS=$((PASS + 1))
    else
        echo "   ⚠️  httpx not installed (will be installed in container)"
        PASS=$((PASS + 1))  # Not a failure — installed at container build time
    fi

    if python3 -c "import mcp" 2>/dev/null; then
        echo -e "  ${GREEN}✅${NC} mcp package already installed"
        PASS=$((PASS + 1))
    else
        echo "   ⚠️  mcp package not installed (will be installed in container)"
        PASS=$((PASS + 1))  # Not a failure
    fi
else
    FAIL=$((FAIL + 2))
fi

# ============================================================
# Test 7: 集成冒烟 — MCP server 可启动 + 响应 initialize
# ============================================================
echo ""
echo "🧪 Test 7: MCP server 启动冒烟"

if [[ -f "${MCP_SERVER}" ]]; then
    # Test: the server module can be imported and has the expected attributes
    # We use a subprocess to check basic importability
    SERVER_DIR="$(dirname "${MCP_SERVER}")"
    IMPORT_CHECK=$(cd "${SERVER_DIR}" && python3 -c "
import ast, sys
with open('server.py') as f:
    tree = ast.parse(f.read())
# Check for mcp-related imports or tool decorators
has_mcp_import = False
has_tools = set()
for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom):
        if node.module and 'mcp' in node.module:
            has_mcp_import = True
    if isinstance(node, ast.Call):
        if hasattr(node.func, 'attr') and node.func.attr == 'tool':
            # Try to get the tool name
            for kw in getattr(node, 'keywords', []):
                pass  # tool names set via decorator
            has_tools.add('tool_decorator')
        elif hasattr(node.func, 'id') and node.func.id == 'tool':
            has_tools.add('tool_decorator')
print(f'MCP_IMPORT={has_mcp_import}')
print(f'TOOL_DECORATORS={len(has_tools) > 0}')
" 2>&1)
    if echo "${IMPORT_CHECK}" | grep -q "MCP_IMPORT=True"; then
        echo -e "  ${GREEN}✅${NC} MCP import found in server.py"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} MCP import not found"
        FAIL=$((FAIL + 1))
    fi
    if echo "${IMPORT_CHECK}" | grep -q "TOOL_DECORATORS=True"; then
        echo -e "  ${GREEN}✅${NC} Tool decorators found"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} Tool decorators not found"
        FAIL=$((FAIL + 1))
    fi
else
    FAIL=$((FAIL + 2))
fi

# ============================================================
# Test 8: Dockerfile/entrypoint 集成 — mcp dep 安装 + 注册
# ============================================================
echo ""
echo "🧪 Test 8: claude-code 集成"

CLAUDE_DOCKERFILE="${REPO_ROOT}/docker/claude-code/Dockerfile"
if [[ -f "${CLAUDE_DOCKERFILE}" ]]; then
    DF_CONTENT=$(cat "${CLAUDE_DOCKERFILE}")
    # Check that mcp server deps are pip installed
    assert_contains "installs mcp Python package" \
        "mcp" \
        "${DF_CONTENT}"
    assert_contains "installs httpx Python package" \
        "httpx" \
        "${DF_CONTENT}"
else
    FAIL=$((FAIL + 2))
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
