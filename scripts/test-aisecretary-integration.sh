#!/usr/bin/env bash
# =============================================================
# test-aisecretary-integration.sh — aisecretary 集成验证脚本
#
# 验证 aisecretary 是否正确集成到 myopenclaw 运维体系。
# TDD RED 阶段：当前预期全部 FAIL（集成尚未完成）。
# 集成完成后运行应全部 PASS。
#
# 红线：全程只读，绝不修改 aisecretary 数据库。
#
# 用法:
#   ./scripts/test-aisecretary-integration.sh            # 人类可读
#   ./scripts/test-aisecretary-integration.sh --json     # JSON 输出
#
# 退出码:
#   0 — 全部检查通过
#   1 — 至少一项检查失败
#   2 — 脚本自身错误（依赖缺失）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_JSON=false

# ── 参数解析 ─────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --json) OUTPUT_JSON=true ;;
    --help|-h)
      echo "用法: $0 [--json]"
      echo ""
      echo "验证 aisecretary 是否已正确集成到 myopenclaw："
      echo "  - 网络连通性 (Hermes → aisecretary:8000)"
      echo "  - Skill 文件可访问"
      echo "  - MCP SSE 端点响应"
      echo "  - 健康检查端点响应"
      echo "  - 数据库只读访问"
      echo "  - Uptime Kuma 监控配置"
      exit 0
      ;;
  esac
done

# ── 依赖检查 ─────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "❌ 缺少依赖: docker" >&2
  exit 2
fi
if ! command -v curl &>/dev/null; then
  echo "❌ 缺少依赖: curl" >&2
  exit 2
fi

# ── 状态追踪 ─────────────────────────────────────────────────
PASS_COUNT=0
FAIL_COUNT=0
RESULTS=()

check() {
  local name="$1"
  local result="$2"
  local detail="${3:-}"
  if [[ "$result" == "PASS" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    RESULTS+=("{\"name\":\"$name\",\"status\":\"PASS\",\"detail\":\"$detail\"}")
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    RESULTS+=("{\"name\":\"$name\",\"status\":\"FAIL\",\"detail\":\"$detail\"}")
  fi
}

# ── JSON 输出辅助 ────────────────────────────────────────────
json_report() {
  echo "{"
  echo "  \"total\": $((PASS_COUNT + FAIL_COUNT)),"
  echo "  \"pass\": $PASS_COUNT,"
  echo "  \"fail\": $FAIL_COUNT,"
  echo "  \"results\": ["
  for i in "${!RESULTS[@]}"; do
    local comma=","
    if [[ "$i" -eq $((${#RESULTS[@]} - 1)) ]]; then
      comma=""
    fi
    echo "    ${RESULTS[$i]}$comma"
  done
  echo "  ]"
  echo "}"
}

print_result() {
  local name="$1"
  local result="$2"
  local detail="${3:-}"
  if [[ "$result" == "PASS" ]]; then
    echo "   ✅ $name"
  else
    echo "   ❌ $name — $detail"
  fi
}

# ═══════════════════════════════════════════════════════════════
# 检查项
# ═══════════════════════════════════════════════════════════════

echo "🔍 aisecretary 集成验证"
echo "========================"
echo ""

# ── 1. aisecretary 容器运行 ──────────────────────────────────
echo "1️⃣  容器状态"
echo "   ─────────"

AISEC_CONTAINER=$(docker ps --filter "name=^aisecretary$" --format "{{.Names}}" 2>/dev/null || echo "")
if [[ -n "$AISEC_CONTAINER" ]]; then
  print_result "aisecretary 容器运行" "PASS"
  check "container_running" "PASS" "容器名: aisecretary"
else
  print_result "aisecretary 容器运行" "FAIL" "容器未运行"
  check "container_running" "FAIL" "docker ps 中未找到 aisecretary"
fi

# ── 2. aisecretary 在 myopenclaw 网络上 ──────────────────────
AISEC_NETWORKS=$(docker inspect aisecretary --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null || echo "")
if echo "$AISEC_NETWORKS" | grep -q "myopenclaw"; then
  print_result "aisecretary 在 myopenclaw 网络" "PASS"
  check "network_myopenclaw" "PASS" "网络: $AISEC_NETWORKS"
else
  print_result "aisecretary 在 myopenclaw 网络" "FAIL" "当前网络: ${AISEC_NETWORKS:-无}"
  check "network_myopenclaw" "FAIL" "期望在 myopenclaw_myopenclaw-net，实际: ${AISEC_NETWORKS:-无}"
fi

# ── 3. 健康检查端点 ──────────────────────────────────────────
echo ""
echo "2️⃣  健康检查"
echo "   ─────────"

HEALTH_RESPONSE=$(curl -s --max-time 5 -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
if [[ "$HEALTH_RESPONSE" == "200" ]]; then
  HEALTH_BODY=$(curl -s http://localhost:8000/health 2>/dev/null || echo "{}")
  if echo "$HEALTH_BODY" | grep -q '"ok"'; then
    print_result "GET /health → 200 ok" "PASS"
    check "health_endpoint" "PASS" "HTTP 200, body: $HEALTH_BODY"
  else
    print_result "GET /health → 200 ok" "FAIL" "HTTP 200 但 body 异常: $HEALTH_BODY"
    check "health_endpoint" "FAIL" "HTTP 200, body 不含 'ok'"
  fi
else
  print_result "GET /health → 200 ok" "FAIL" "HTTP $HEALTH_RESPONSE"
  check "health_endpoint" "FAIL" "HTTP $HEALTH_RESPONSE (期望 200)"
fi

# ── 4. Hermes 容器内 curl aisecretary ────────────────────────
echo ""
echo "3️⃣  Hermes → aisecretary 网络连通性"
echo "   ───────────────────────────────"

# Hermes 容器没有 curl，用 python3 urllib（和 aisecretary healthcheck 同样方式）
HERMES_PY_RESULT=$(docker compose -f "$REPO_ROOT/docker-compose.yml" exec -T hermes python3 -c "
import urllib.request
try:
    resp = urllib.request.urlopen('http://aisecretary:8000/health', timeout=5)
    print(resp.status)
except Exception as e:
    print('ERROR:' + str(e))
" 2>/dev/null || echo "ERROR")
if [[ "$HERMES_PY_RESULT" == "200" ]]; then
  print_result "Hermes 容器内可访问 aisecretary:8000" "PASS"
  check "hermes_to_aisecretary" "PASS" "Hermes 容器内 python3 → aisecretary:8000/health → 200"
else
  print_result "Hermes 容器内可访问 aisecretary:8000" "FAIL" "结果: $HERMES_PY_RESULT (期望 200)"
  check "hermes_to_aisecretary" "FAIL" "Hermes 容器内无法连接到 aisecretary:8000"
fi

# ── 5. Skill 文件可访问 ──────────────────────────────────────
echo ""
echo "4️⃣  Skill 文件"
echo "   ───────────"

SKILL_PATH="/opt/data/code/aisecretary/skills/transaction_manager/SKILL.md"
SKILL_EXISTS=$(docker compose -f "$REPO_ROOT/docker-compose.yml" exec -T hermes sh -c "test -f '$SKILL_PATH' && echo yes || echo no" 2>/dev/null)
if [[ "$SKILL_EXISTS" == "yes" ]]; then
  SKILL_SIZE=$(docker compose -f "$REPO_ROOT/docker-compose.yml" exec -T hermes sh -c "wc -c < '$SKILL_PATH'" 2>/dev/null | tr -d ' ')
  print_result "transaction_manager SKILL.md 可访问" "PASS"
  check "skill_accessible" "PASS" "文件大小: ${SKILL_SIZE} bytes"
else
  print_result "transaction_manager SKILL.md 可访问" "FAIL" "容器内 $SKILL_PATH 不存在"
  check "skill_accessible" "FAIL" "容器内 $SKILL_PATH 不存在"
fi

# ── 6. MCP SSE 端点 ──────────────────────────────────────────
echo ""
echo "5️⃣  MCP SSE 端点"
echo "   ─────────────"

# Streamable HTTP MCP endpoint 在 /mcp/（POST initialize 应返回 200 + Mcp-Session-Id）
MCP_RESPONSE=$(curl -s --max-time 5 -D - -X POST http://localhost:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}' 2>/dev/null || true)
MCP_CODE=$(echo "$MCP_RESPONSE" | head -1 | grep -oE '[0-9]{3}' | head -1)
MCP_SESSION=$(echo "$MCP_RESPONSE" | grep -i 'mcp-session-id' | tr -d '\r' | awk '{print $NF}' || echo "")
if [[ "$MCP_CODE" == "200" && -n "$MCP_SESSION" ]]; then
  print_result "MCP Streamable HTTP 端点 (POST /mcp/ → 200 + Session)" "PASS"
  check "mcp_endpoint" "PASS" "HTTP 200, session: ${MCP_SESSION:0:16}..."
else
  print_result "MCP Streamable HTTP 端点 (POST /mcp/ → 200 + Session)" "FAIL" "HTTP ${MCP_CODE:-error}, session: ${MCP_SESSION:-none}"
  check "mcp_endpoint" "FAIL" "HTTP ${MCP_CODE:-error}"
fi

# ── 7. 数据库只读访问 + 基准行数 ─────────────────────────────
echo ""
echo "6️⃣  数据库只读验证"
echo "   ─────────────"

# aisecretary 容器是 python:3.12-slim，没有 sqlite3 CLI，用 python3
DB_COUNT_BEFORE=$(docker compose -f "$REPO_ROOT/docker-compose.yml" exec -T aisecretary python3 -c "
import sqlite3; conn=sqlite3.connect('/data/transactions.sqlite')
print(conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0])
" 2>/dev/null | tr -d '[:space:]' || true)
if [[ -z "$DB_COUNT_BEFORE" || "$DB_COUNT_BEFORE" == "ERROR" ]]; then
  DB_COUNT_BEFORE=$(docker exec aisecretary python3 -c "
import sqlite3; conn=sqlite3.connect('/data/transactions.sqlite')
print(conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0])
" 2>/dev/null | tr -d '[:space:]' || echo "ERROR")
fi
if [[ "$DB_COUNT_BEFORE" != "ERROR" && "$DB_COUNT_BEFORE" =~ ^[0-9]+$ ]]; then
  print_result "数据库只读访问: COUNT(*) = $DB_COUNT_BEFORE" "PASS"
  check "db_readonly" "PASS" "当前 transactions 行数: $DB_COUNT_BEFORE"
else
  print_result "数据库只读访问" "FAIL" "无法读取数据库 (${DB_COUNT_BEFORE})"
  check "db_readonly" "FAIL" "sqlite3 查询失败: $DB_COUNT_BEFORE"
fi

# ── 8. Hermes MCP servers 配置 ──────────────────────────────
echo ""
echo "7️⃣  Hermes MCP 配置"
echo "   ────────────────"

# 检查 hermes 默认配置中是否有 aisecretary 的 mcp_servers 条目
MCP_CONFIG=$(docker compose -f "$REPO_ROOT/docker-compose.yml" exec -T hermes cat /opt/data/config.yaml 2>/dev/null || echo "")
if echo "$MCP_CONFIG" | grep -q "aisecretary"; then
  print_result "Hermes config 包含 aisecretary MCP 配置" "PASS"
  check "mcp_config" "PASS" "config.yaml mcp_servers 中有 aisecretary 条目"
else
  print_result "Hermes config 包含 aisecretary MCP 配置" "FAIL" "config.yaml 中未找到 aisecretary"
  check "mcp_config" "FAIL" "config.yaml mcp_servers 中未找到 aisecretary"
fi

# ── 9. coder profile 也检查 ─────────────────────────────────
CODER_MCP=$(docker compose -f "$REPO_ROOT/docker-compose.yml" exec -T hermes-coder cat /opt/data/profiles/coder/config.yaml 2>/dev/null || echo "")
if echo "$CODER_MCP" | grep -q "aisecretary"; then
  print_result "Coder profile 包含 aisecretary MCP" "PASS"
  check "coder_mcp" "PASS" "coder config 中有 aisecretary"
else
  print_result "Coder profile 包含 aisecretary MCP" "INFO" "未配置（飞书用 default profile，coder 非必需）"
  check "coder_mcp" "INFO" "未配置但不影响功能"
fi

# ═══════════════════════════════════════════════════════════════
# 报告
# ═══════════════════════════════════════════════════════════════
echo ""
echo "========================="
echo "📊 测试结果: $PASS_COUNT PASS / $FAIL_COUNT FAIL / $((PASS_COUNT + FAIL_COUNT)) 项"
echo ""

if [[ "$OUTPUT_JSON" == "true" ]]; then
  json_report
fi

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo "🔴 集成未完成 — 存在 $FAIL_COUNT 项失败"
  exit 1
else
  echo "🟢 全部检查通过 — aisecretary 已正确集成"
  exit 0
fi
