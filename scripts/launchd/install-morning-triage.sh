#!/usr/bin/env bash
# =============================================================
# scripts/launchd/install-morning-triage.sh
# Install morning-triage launchd job for daily 7:50 AM execution.
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LAUNCH_DIR="${HOME}/Library/LaunchAgents"
LOGS_DIR="${REPO_ROOT}/logs"
PLIST_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "${LAUNCH_DIR}"
mkdir -p "${LOGS_DIR}"

# sed safe-escape
sed_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\&/\\&}"
    s="${s//\//\\/}"
    printf '%s' "${s}"
}

MYOPENCLAW_DIR_ESC="$(sed_escape "${REPO_ROOT}")"
PATH_ESC="$(sed_escape "${PLIST_PATH}")"

TMPL="${SCRIPT_DIR}/ai.myloop.morning-triage.plist.template"
DEST="${LAUNCH_DIR}/ai.myloop.morning-triage.plist"

if [[ ! -f "${TMPL}" ]]; then
    echo "❌ 模板不存在: ${TMPL}" >&2
    exit 1
fi

# Unload existing version
launchctl unload -w "${DEST}" >/dev/null 2>&1 || true

sed \
    -e "s|__MYOPENCLAW_DIR__|${MYOPENCLAW_DIR_ESC}|g" \
    -e "s|__PATH__|${PATH_ESC}|g" \
    "${TMPL}" > "${DEST}"

chmod 644 "${DEST}"
launchctl load -w "${DEST}"

echo "✅ 已加载 ${DEST}"
echo ""
echo "📋 调度: 每天 07:50 (北京时间)"
echo "📂 日志: ${LOGS_DIR}/morning-triage.log"
echo ""
echo "ℹ️  手动触发测试:"
echo "   launchctl start ai.myloop.morning-triage"
