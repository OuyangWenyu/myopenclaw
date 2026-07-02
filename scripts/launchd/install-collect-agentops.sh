#!/usr/bin/env bash
# =============================================================
# scripts/launchd/install-collect-agentops.sh
# 安装 AgentOps 自动采集定时任务（每天 7:45 AM）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LAUNCH_DIR="${HOME}/Library/LaunchAgents"
LOGS_DIR="${REPO_ROOT}/logs"
TEMPLATE="${SCRIPT_DIR}/ai.myopenclaw.collect-agentops.plist.template"
PLIST_FILE="${LAUNCH_DIR}/ai.myopenclaw.collect-agentops.plist"
PLIST_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON3="$(which python3 2>/dev/null || echo '/usr/bin/python3')"

# ── 前置检查 ────────────────────────────────────────────────
if [[ ! -f "${TEMPLATE}" ]]; then
    echo "❌ 模板文件不存在: ${TEMPLATE}" >&2
    exit 1
fi

# ── 创建目录 ────────────────────────────────────────────────
mkdir -p "${LAUNCH_DIR}" "${LOGS_DIR}"

# ── 安全转义路径 ────────────────────────────────────────────
sed_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\&/\\&}"
    s="${s//\//\\/}"
    printf '%s' "${s}"
}

MYOPENCLAW_DIR_ESC="$(sed_escape "${REPO_ROOT}")"
PATH_ESC="$(sed_escape "${PLIST_PATH}")"
PYTHON3_ESC="$(sed_escape "${PYTHON3}")"

# ── 卸载旧任务（如果存在）───────────────────────────────────
if [[ -f "${PLIST_FILE}" ]]; then
    launchctl unload -w "${PLIST_FILE}" >/dev/null 2>&1 || true
    echo "📎 已卸载旧任务"
fi

# ── 渲染模板 → LaunchAgents ─────────────────────────────────
sed \
    -e "s|__MYOPENCLAW_DIR__|${MYOPENCLAW_DIR_ESC}|g" \
    -e "s|__PATH__|${PATH_ESC}|g" \
    -e "s|__PYTHON3__|${PYTHON3_ESC}|g" \
    "${TEMPLATE}" > "${PLIST_FILE}"

chmod 644 "${PLIST_FILE}"

# ── 加载任务 ────────────────────────────────────────────────
launchctl load -w "${PLIST_FILE}"

echo ""
echo "✅ AgentOps 自动采集定时任务已安装"
echo ""
echo "📋 调度:  每天 7:45 AM（morning-triage 前 5 分钟）"
echo "📂 日志:  ${LOGS_DIR}/collect-agentops.log"
echo "📄 plist: ${PLIST_FILE}"
echo ""
echo "🔍 验证:"
echo "   launchctl list | grep collect-agentops"
echo "   tail -f ${LOGS_DIR}/collect-agentops.log"
echo ""
echo "🧪 手动触发:"
echo "   launchctl start ai.myopenclaw.collect-agentops"
echo ""
echo "ℹ️  采集写入: ~/code/myloop/memory/agentops-ledger/inbox.md"
