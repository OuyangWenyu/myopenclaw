#!/usr/bin/env bash
# =============================================================
# scripts/launchd/install-repo-triage.sh
# 安装仓库动态推送定时任务（每天 7:55 AM）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LAUNCH_DIR="${HOME}/Library/LaunchAgents"
LOGS_DIR="${REPO_ROOT}/logs"
TEMPLATE="${SCRIPT_DIR}/ai.myopenclaw.repo-triage.plist.template"
PLIST_FILE="${LAUNCH_DIR}/ai.myopenclaw.repo-triage.plist"
PLIST_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

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

# ── 卸载旧任务（如果存在）───────────────────────────────────
if [[ -f "${PLIST_FILE}" ]]; then
    launchctl unload -w "${PLIST_FILE}" >/dev/null 2>&1 || true
    echo "📎 已卸载旧任务"
fi

# ── 渲染模板 → LaunchAgents ─────────────────────────────────
sed \
    -e "s|__MYOPENCLAW_DIR__|${MYOPENCLAW_DIR_ESC}|g" \
    -e "s|__PATH__|${PATH_ESC}|g" \
    "${TEMPLATE}" > "${PLIST_FILE}"

chmod 644 "${PLIST_FILE}"

# ── 加载任务 ────────────────────────────────────────────────
launchctl load -w "${PLIST_FILE}"

echo ""
echo "✅ 仓库动态推送定时任务已安装"
echo ""
echo "📋 调度:  每天 7:55 AM（collect-repos 后 10 分钟）"
echo "📂 日志:  ${LOGS_DIR}/repo-triage.log"
echo "📄 plist: ${PLIST_FILE}"
echo ""
echo "🔍 验证:"
echo "   launchctl list | grep repo-triage"
echo "   tail -f ${LOGS_DIR}/repo-triage.log"
echo ""
echo "🧪 手动触发:"
echo "   launchctl start ai.myopenclaw.repo-triage"
echo ""
echo "🧪 预览（不推送）:"
echo "   python3 scripts/repo-triage-send.py --dry-run"
