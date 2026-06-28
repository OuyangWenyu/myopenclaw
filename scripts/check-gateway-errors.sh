#!/usr/bin/env bash
# =============================================================
# check-gateway-errors.sh — 检测 OpenClaw 网关错误日志循环
#
# 检查 gateway.err.log 是否被同一条错误反复刷屏。
# 不修改、不删除、不截断日志 — 只读检测。
#
# 用法:
#   ./scripts/check-gateway-errors.sh            # 检查 Docker + launchd 日志
#   ./scripts/check-gateway-errors.sh --json     # JSON 输出（适合 cron/监控）
#   ./scripts/check-gateway-errors.sh --help     # 帮助
#
# 退出码:
#   0 — 未检测到错误循环（或日志不存在/太小）
#   1 — 检测到错误循环
#   2 — 脚本自身错误（如 jq 缺失）
# =============================================================
set -euo pipefail

OPENCLAW_LOG_DIR="${HOME}/.openclaw/logs"
ERR_LOG="${OPENCLAW_LOG_DIR}/gateway.err.log"
OUTPUT_JSON=false

# ── 参数解析 ─────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --json) OUTPUT_JSON=true ;;
    --help|-h)
      echo "用法: $0 [--json]"
      echo ""
      echo "检查 gateway.err.log 是否被同一条错误反复刷屏。"
      echo "只读检测，不修改任何日志文件。"
      echo ""
      echo "退出码: 0=正常, 1=检测到错误循环, 2=脚本错误"
      exit 0
      ;;
  esac
done

# ── 检查日志是否存在且足够大 ─────────────────────────────────
if [[ ! -f "${ERR_LOG}" ]]; then
  if $OUTPUT_JSON; then
    echo '{"status":"ok","reason":"no_log_file"}'
  fi
  exit 0
fi

LOG_SIZE=$(wc -c < "${ERR_LOG}" 2>/dev/null || echo 0)
MIN_SIZE=$((10 * 1024 * 1024))  # 10MB 以下不检查，正常使用不会这么大

if [[ "${LOG_SIZE}" -lt "${MIN_SIZE}" ]]; then
  if $OUTPUT_JSON; then
    echo "{\"status\":\"ok\",\"reason\":\"below_threshold\",\"size_bytes\":${LOG_SIZE},\"threshold_bytes\":${MIN_SIZE}}"
  fi
  exit 0
fi

# ── 检测错误循环 ─────────────────────────────────────────────
# 取最后 5000 行，统计每行出现次数
SAMPLE_LINES=5000
REPEAT_THRESHOLD=500  # 同一行出现超过此次数视为循环

# 用 awk 找出现次数最多的行（排除时间戳前缀的干扰）
# 去掉形如 "2026-03-31T09:15:54.781+08:00 " 的时间戳前缀后再统计
MOST_FREQUENT=$(tail -n "${SAMPLE_LINES}" "${ERR_LOG}" 2>/dev/null \
  | sed 's/^[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}T[0-9:.]\{12\}+[0-9:]\{5\} //' \
  | sort | uniq -c | sort -rn | head -1)

REPEAT_COUNT=$(echo "${MOST_FREQUENT}" | awk '{print $1}')
ERR_MSG=$(echo "${MOST_FREQUENT}" | cut -d' ' -f2-)

if [[ "${REPEAT_COUNT}" -lt "${REPEAT_THRESHOLD}" ]]; then
  if $OUTPUT_JSON; then
    echo "{\"status\":\"ok\",\"reason\":\"no_repeat_loop\",\"max_repeat_count\":${REPEAT_COUNT},\"sample_size\":${SAMPLE_LINES}}"
  fi
  exit 0
fi

# ── 找到错误循环：收集信息 ───────────────────────────────────
# 找到该错误首次出现的时间戳
FIRST_TS=$(grep -F "${ERR_MSG}" "${ERR_LOG}" 2>/dev/null | head -1 | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{12}\+[0-9:]{5}' || echo "未知")
TOTAL_OCCURRENCES=$(grep -cF "${ERR_MSG}" "${ERR_LOG}" 2>/dev/null || echo 0)

if $OUTPUT_JSON; then
  cat << JSONEND
{
  "status": "error_loop_detected",
  "log_file": "${ERR_LOG}",
  "log_size_bytes": ${LOG_SIZE},
  "error_message": "${ERR_MSG}",
  "repeat_count_in_sample": ${REPEAT_COUNT},
  "sample_size": ${SAMPLE_LINES},
  "total_occurrences": ${TOTAL_OCCURRENCES},
  "first_seen": "${FIRST_TS}",
  "advice": "不要从 host 运行 openclaw doctor --fix。在容器内操作：docker compose run --rm --entrypoint \"node\" openclaw-gateway openclaw.mjs doctor --fix"
}
JSONEND
else
  echo ""
  echo "🔴 检测到 OpenClaw 网关错误循环！"
  echo "   日志文件: ${ERR_LOG}"
  echo "   日志大小: $(du -h "${ERR_LOG}" | cut -f1)"
  echo "   错误内容: ${ERR_MSG}"
  echo "   采样重复: ${REPEAT_COUNT} / ${SAMPLE_LINES} 行"
  echo "   总出现次数: ${TOTAL_OCCURRENCES}"
  echo "   首次出现: ${FIRST_TS}"
  echo ""
  echo "   修复方法（在容器内操作，不要从 host 执行）："
  echo "     docker compose run --rm --entrypoint \"node\" openclaw-gateway openclaw.mjs doctor --fix"
  echo ""
fi

exit 1
