#!/usr/bin/env bash
# =============================================================
# scripts/launchd/install-ai-news-weekly.sh
# Install AI News 周报推送 launchd job (Hermes identity).
# Replaces the old cc-connect cron "AI News 周报润色+飞书推送" job.
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LAUNCH_DIR="${HOME}/Library/LaunchAgents"
LOGS_DIR="${REPO_ROOT}/logs"
PLIST_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "${LAUNCH_DIR}"
mkdir -p "${LOGS_DIR}"

sed_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\&/\\&}"
    s="${s//\//\\/}"
    printf '%s' "${s}"
}

MYOPENCLAW_DIR_ESC="$(sed_escape "${REPO_ROOT}")"
PATH_ESC="$(sed_escape "${PLIST_PATH}")"

TMPL="${SCRIPT_DIR}/ai.myopenclaw.ai-news-weekly.plist.template"
DEST="${LAUNCH_DIR}/ai.myopenclaw.ai-news-weekly.plist"

if [[ ! -f "${TMPL}" ]]; then
    echo "❌ 模板不存在: ${TMPL}" >&2
    exit 1
fi

launchctl unload -w "${DEST}" >/dev/null 2>&1 || true

sed \
    -e "s|__MYOPENCLAW_DIR__|${MYOPENCLAW_DIR_ESC}|g" \
    -e "s|__PATH__|${PATH_ESC}|g" \
    "${TMPL}" > "${DEST}"

chmod 644 "${DEST}"
launchctl load -w "${DEST}"

echo "✅ 已加载 ${DEST}"
echo ""
echo "📋 调度: 每周日 08:10 (北京时间)"
echo "📂 日志: ${LOGS_DIR}/ai-news-weekly.log"
echo "🚀 执行: python3 scripts/ai_news_weekly_push.py (Hermes 飞书身份)"
echo ""
echo "ℹ️  手动触发测试:"
echo "   launchctl start ai.myopenclaw.ai-news-weekly"
