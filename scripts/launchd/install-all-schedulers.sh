#!/usr/bin/env bash
# =============================================================
# scripts/launchd/install-all-schedulers.sh
# 一键安装所有宿主机 launchd 定时任务（幂等）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "============================================"
echo "  myopenclaw 宿主机调度一键安装"
echo "============================================"
echo ""

SUCCESS=()
SKIPPED=()
FAILED=()

install_task() {
  local label="$1"
  local script="$2"
  local dependency_check="$3"

  echo "── ${label} ──"

  # 检查依赖
  if ! eval "${dependency_check}" 2>/dev/null; then
    echo "   ⏭  依赖未满足，跳过"
    SKIPPED+=("${label}")
    return
  fi

  if bash "${script}"; then
    echo "   ✅ 已安装"
    SUCCESS+=("${label}")
  else
    echo "   ❌ 安装失败"
    FAILED+=("${label}")
  fi
  echo ""
}

# ── 1. Healthchecks.io 心跳 ────────────────────────────────
install_task \
  "healthchecks-ping" \
  "${SCRIPT_DIR}/install-healthchecks-ping.sh" \
  "grep -q 'HEALTHCHECKS_PING_URL' \"${REPO_ROOT}/.env\" 2>/dev/null"

# ── 2. AgentOps 健康采集 ────────────────────────────────────
install_task \
  "collect-agentops" \
  "${SCRIPT_DIR}/install-collect-agentops.sh" \
  "true"  # 无外部依赖

# ── 3. git-contribution-stats 每日采集 —— 已退役（2026-11-03）──────────
#    每日全量采集改由容器 repo-scanner-mcp 的 07:45 daily sync 负责。
#    宿主 launchd 与容器双写同一 SQLite（跨 virtiofs）会损坏数据库
#    （database disk image is malformed），不要再安装此任务。

# ── 4. dailyinfo 情报聚合 —— 已迁到 dailyinfo 自己的 compose 栈 ─────────
#    调度（5 个 run + 3 个 Discord push）由 dailyinfo-scheduler 容器内
#    supercronic 负责；本仓不再安装任何 dailyinfo launchd 任务。

# ── 汇总 ────────────────────────────────────────────────────
echo "============================================"
echo "  安装汇总"
echo "============================================"
echo "  ✅ 成功:  ${#SUCCESS[@]} 项"
for item in "${SUCCESS[@]}"; do
  echo "     - ${item}"
done
if [[ ${#SKIPPED[@]} -gt 0 ]]; then
  echo "  ⏭  跳过:  ${#SKIPPED[@]} 项"
  for item in "${SKIPPED[@]}"; do
    echo "     - ${item}"
  done
fi
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "  ❌ 失败:  ${#FAILED[@]} 项"
  for item in "${FAILED[@]}"; do
    echo "     - ${item}"
  done
fi

echo ""
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "完成。验证: launchctl list | grep -E 'ai\.(dailyinfo|myopenclaw)'"
  echo "详见: docs/scheduling.md"
else
  echo "部分任务安装失败，请检查上面的错误输出。"
  echo "手动重试或查看 docs/scheduling.md"
fi
