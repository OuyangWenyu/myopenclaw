#!/usr/bin/env bash
# =============================================================
# scripts/test-cc-capture.sh
# TDD 测试 — CC飞总 Stop hook 双向记忆捕获
# 验证:
#   1. capture-to-gateway.py 存在 + 语法正确
#   2. content list 解析（跳过 thinking/tool_use/tool_result，只取 text）
#   3. 缺字段/异常防御（静默 exit 0）
#   4. Gateway env 读取
#   5. settings.json Stop hook 注册
#   6. 端到端 mock transcript → /capture
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PASS=0
FAIL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

HOOK_PY="${REPO_ROOT}/docker/claude-code/capture-to-gateway.py"
PY="${PYTHON:-python3}"

assert_eq() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "${expected}" == "${actual}" ]]; then
        echo -e "  ${GREEN}✅${NC} ${desc}"; PASS=$((PASS + 1))
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
        echo -e "  ${GREEN}✅${NC} ${desc}"; PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} ${desc}"
        echo "     expected to contain: '${needle}'"
        FAIL=$((FAIL + 1))
    fi
}

assert_file_exists() {
    local desc="$1" path="$2"
    if [[ -f "${path}" ]]; then
        echo -e "  ${GREEN}✅${NC} ${desc}"; PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} ${desc}"
        echo "     file not found: ${path}"
        FAIL=$((FAIL + 1))
    fi
}

assert_exit0() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo -e "  ${GREEN}✅${NC} ${desc}"; PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} ${desc} (exit $?)"
        FAIL=$((FAIL + 1))
    fi
}

# ============================================================
# Test 1: 源文件存在 + 语法
# ============================================================
echo "🧪 Test 1: 源文件"
assert_file_exists "capture-to-gateway.py exists" "${HOOK_PY}"
if [[ -f "${HOOK_PY}" ]]; then
    if ${PY} -c "import ast; ast.parse(open('${HOOK_PY}').read())" 2>/dev/null; then
        echo -e "  ${GREEN}✅${NC} python syntax OK"; PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} python syntax error"; FAIL=$((FAIL + 1))
    fi
fi

# ============================================================
# Test 2: 关键内容检查
# ============================================================
echo ""
echo "🧪 Test 2: 关键实现"
if [[ -f "${HOOK_PY}" ]]; then
    CODE=$(cat "${HOOK_PY}")
    assert_contains "posts to /capture endpoint" '/capture' "${CODE}"
    assert_contains "sets session_id personal_ccfeizong" 'personal_ccfeizong' "${CODE}"
    assert_contains "reads MEMORY_TENCENTDB_GATEWAY_HOST env" 'MEMORY_TENCENTDB_GATEWAY_HOST' "${CODE}"
    assert_contains "extracts text blocks only" 'text' "${CODE}"
    assert_contains "has timeout protection" 'timeout' "${CODE}"
else
    FAIL=$((FAIL + 5))
fi

# ============================================================
# Test 3: content 解析逻辑（单元测试，import 函数）
# ============================================================
echo ""
echo "🧪 Test 3: content 解析"
if [[ -f "${HOOK_PY}" ]]; then
    PARSE_TEST=$(${PY} - "${HOOK_PY}" << 'PYEOF' 2>&1
import sys, importlib.util
spec = importlib.util.spec_from_file_location("cap", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# assistant content: list of blocks — only 'text' extracted
asst = [
    {"type": "thinking", "thinking": "internal reasoning"},
    {"type": "text", "text": "这是助手的回答"},
    {"type": "tool_use", "name": "Bash", "input": {}},
]
r1 = m.extract_text(asst)
print("ASST:", repr(r1))

# user content: plain string
r2 = m.extract_text("用户的问题")
print("USER_STR:", repr(r2))

# user content: list of tool_result — should yield empty (not real user text)
tool_result = [{"type": "tool_result", "content": "命令输出"}]
r3 = m.extract_text(tool_result)
print("TOOL_RESULT:", repr(r3))

# is_real_user_message: slash command / caveat wrappers rejected
print("SLASH:", m.is_real_user_text("<command-name>/clear</command-name>"))
print("CAVEAT:", m.is_real_user_text("<local-command-caveat>Caveat: ...</local-command-caveat>"))
print("REAL:", m.is_real_user_text("目前的 plan 能看到吗？"))
PYEOF
)
    assert_contains "assistant: only text block extracted" "ASST: '这是助手的回答'" "${PARSE_TEST}"
    assert_contains "user string passes through" "USER_STR: '用户的问题'" "${PARSE_TEST}"
    assert_contains "tool_result yields empty" "TOOL_RESULT: ''" "${PARSE_TEST}"
    assert_contains "slash command rejected" "SLASH: False" "${PARSE_TEST}"
    assert_contains "caveat rejected" "CAVEAT: False" "${PARSE_TEST}"
    assert_contains "real user text accepted" "REAL: True" "${PARSE_TEST}"
else
    FAIL=$((FAIL + 6))
fi

# ============================================================
# Test 3b: read_last_turn — 多条 assistant 合并 + uuid 幂等键
# ============================================================
echo ""
echo "🧪 Test 3b: read_last_turn 合并 + uuid"
if [[ -f "${HOOK_PY}" ]]; then
    TMPD=$(mktemp -d)
    trap 'rm -rf "${TMPD}"' EXIT
    # 一轮：user + 3 条 assistant（text/tool_use/text，中间无真实 user）
    cat > "${TMPD}/t.jsonl" << 'JSONL'
{"message":{"role":"user","content":"帮我查一下"},"uuid":"u1","sessionId":"s-merge"}
{"message":{"role":"assistant","content":[{"type":"text","text":"好的，我先看看"}]},"uuid":"a1","sessionId":"s-merge"}
{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}}]},"uuid":"a2","sessionId":"s-merge"}
{"message":{"role":"user","content":[{"type":"tool_result","content":"输出"}]},"uuid":"tr1","sessionId":"s-merge"}
{"message":{"role":"assistant","content":[{"type":"text","text":"查到了，结果是X"}]},"uuid":"a3","sessionId":"s-merge"}
JSONL
    MERGE_TEST=$(${PY} - "${HOOK_PY}" "${TMPD}/t.jsonl" << 'PYEOF' 2>&1
import sys, importlib.util
spec = importlib.util.spec_from_file_location("cap", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
u, a, sk, uid = m.read_last_turn(sys.argv[2])
print("USER:", u)
print("ASST:", a)
print("SK:", sk)
print("UUID:", uid)
PYEOF
)
    assert_contains "merges contiguous assistant text" "查到了，结果是X" "${MERGE_TEST}"
    assert_contains "merge includes earlier assistant text" "好的，我先看看" "${MERGE_TEST}"
    assert_contains "user paired correctly" "USER: 帮我查一下" "${MERGE_TEST}"
    assert_contains "session_key from record" "SK: s-merge" "${MERGE_TEST}"
    assert_contains "uuid captured as idempotency key" "UUID: a3" "${MERGE_TEST}"
else
    FAIL=$((FAIL + 5))
fi

# ============================================================
# Test 4: 异常防御 — 任何输入都 exit 0
# ============================================================
echo ""
echo "🧪 Test 4: 异常防御（静默 exit 0）"
if [[ -f "${HOOK_PY}" ]]; then
    # 空输入
    assert_exit0 "empty stdin → exit 0" bash -c "echo '' | ${PY} '${HOOK_PY}'"
    # 非法 JSON
    assert_exit0 "invalid json → exit 0" bash -c "echo 'not json' | ${PY} '${HOOK_PY}'"
    # transcript_path 不存在
    assert_exit0 "missing transcript → exit 0" bash -c "echo '{\"transcript_path\":\"/nonexistent.jsonl\"}' | ${PY} '${HOOK_PY}'"
    # 缺 transcript_path 字段
    assert_exit0 "no transcript_path key → exit 0" bash -c "echo '{\"session_id\":\"x\"}' | ${PY} '${HOOK_PY}'"
else
    FAIL=$((FAIL + 4))
fi

# ============================================================
# Test 5: settings.json Stop hook 注册（entrypoint.sh）
# ============================================================
echo ""
echo "🧪 Test 5: entrypoint.sh Stop hook 注册"
ENTRYPOINT="${REPO_ROOT}/docker/claude-code/entrypoint.sh"
EP=$(cat "${ENTRYPOINT}")
assert_contains "registers Stop hook" 'Stop' "${EP}"
assert_contains "references capture-to-gateway" 'capture-to-gateway' "${EP}"

# ============================================================
# Test 6: docker-compose 挂载 + env
# ============================================================
echo ""
echo "🧪 Test 6: docker-compose 配置"
COMPOSE=$(cat "${REPO_ROOT}/docker-compose.yml")
assert_contains "mounts capture-to-gateway.py" 'capture-to-gateway.py:/opt/capture-to-gateway.py' "${COMPOSE}"
# claude-code 服务块含 Gateway host（挂载脚本紧邻 env，用挂载行做锚点确认在同一服务）
assert_contains "claude-code has Gateway host env" 'MEMORY_TENCENTDB_GATEWAY_HOST' "${COMPOSE}"

# ============================================================
# Test 7: 端到端 — mock transcript → 真实 Gateway /capture
# ============================================================
echo ""
echo "🧪 Test 7: 端到端 capture"
if [[ -f "${HOOK_PY}" ]] && curl -s http://localhost:8420/health >/dev/null 2>&1; then
    TMPDIR=$(mktemp -d)
    trap 'rm -rf "${TMPDIR}"' EXIT
    TRANSCRIPT="${TMPDIR}/session.jsonl"
    # mock transcript: 一轮真实 user + assistant
    cat > "${TRANSCRIPT}" << 'JSONL'
{"message":{"role":"user","content":"测试双向互通E2E标记词zzyptest"},"timestamp":"2026-07-11T15:00:00.000Z","sessionId":"e2e-test-session"}
{"message":{"role":"assistant","content":[{"type":"thinking","thinking":"思考"},{"type":"text","text":"收到你的E2E测试标记词zzyptest"}]},"timestamp":"2026-07-11T15:00:05.000Z","sessionId":"e2e-test-session"}
JSONL
    # 从宿主机调用需要 localhost，容器内是 tdai-memory；用 env 覆盖
    # MEMORY_CAPTURE_LOG 指向临时目录（宿主机无 /home/node）
    echo "{\"transcript_path\":\"${TRANSCRIPT}\",\"session_id\":\"e2e-test-session\"}" | \
        MEMORY_TENCENTDB_GATEWAY_HOST=localhost MEMORY_TENCENTDB_GATEWAY_PORT=8420 \
        MEMORY_CAPTURE_LOG="${TMPDIR}/capture.log" \
        ${PY} "${HOOK_PY}" >/dev/null 2>&1
    sleep 2
    # 验证 Gateway L0 是否收到
    RESULT=$(curl -s -X POST http://localhost:8420/search/conversations \
        -H 'Content-Type: application/json' -d '{"query":"zzyptest","limit":5}' 2>&1)
    if echo "${RESULT}" | grep -q 'zzyptest'; then
        echo -e "  ${GREEN}✅${NC} E2E: 对话已写入 Gateway L0"; PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} E2E: Gateway 未收到（可能 FTS 索引延迟）"
        echo "     result: ${RESULT:0:120}"
        FAIL=$((FAIL + 1))
    fi
    # 验证成功心跳日志（可诊断性）
    if [[ -f "${TMPDIR}/capture.log" ]] && grep -q 'captured session=' "${TMPDIR}/capture.log"; then
        echo -e "  ${GREEN}✅${NC} E2E: 成功心跳已写入日志"; PASS=$((PASS + 1))
    else
        echo -e "  ${RED}❌${NC} E2E: 心跳日志缺失"
        FAIL=$((FAIL + 1))
    fi
else
    echo "   ⚠️  Gateway 不可达或脚本缺失，跳过 E2E"
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
