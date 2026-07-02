#!/usr/bin/env bash
# =============================================================
# scripts/launchd/install-healthchecks-ping.sh
# 安装 Healthchecks.io ping 定时任务（每 60 秒）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LAUNCH_DIR="${HOME}/Library/LaunchAgents"
LOGS_DIR="${REPO_ROOT}/logs"
TEMPLATE="${SCRIPT_DIR}/ai.myopenclaw.healthchecks-ping.plist.template"
PLIST_FILE="${LAUNCH_DIR}/ai.myopenclaw.healthchecks-ping.plist"
PLIST_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ── 前置检查 ────────────────────────────────────────────────
if [[ ! -f "${TEMPLATE}" ]]; then
    echo "❌ 模板文件不存在: ${TEMPLATE}" >&2
    exit 1
fi

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
    echo "⚠️  .env 文件不存在，请先配置环境变量"
fi

if ! grep -q '^HEALTHCHECKS_PING_URL=' "${REPO_ROOT}/.env" 2>/dev/null; then
    echo "⚠️  HEALTHCHECKS_PING_URL 未在 .env 中配置"
    echo "   请先在 https://healthchecks.io 创建 Check，将 Ping URL 填入 .env"
    echo "   格式: HEALTHCHECKS_PING_URL=https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    echo ""
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
echo "✅ Healthchecks.io ping 定时任务已安装"
echo ""
echo "📋 调度:  每 60 秒"
echo "📂 日志:  ${LOGS_DIR}/healthchecks-ping.log"
echo "📄 plist: ${PLIST_FILE}"
echo ""
echo "🔍 验证:"
echo "   launchctl list | grep healthchecks"
echo "   tail -f ${LOGS_DIR}/healthchecks-ping.log"
echo ""
echo "🧪 手动触发:"
echo "   launchctl start ai.myopenclaw.healthchecks-ping"
