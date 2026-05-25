#!/usr/bin/env bash
# =============================================================
# scripts/launchd/uninstall-dailyinfo.sh
# Unload and remove the 7 dailyinfo LaunchAgents.
# =============================================================
set -euo pipefail

LAUNCH_DIR="${HOME}/Library/LaunchAgents"
JOBS=(run-p1-arxiv run-p3 run-p2 run-p1 push-early push-papers push-arxiv)
# Also clean up legacy single-push job if it still exists.
LEGACY_JOBS=(push)

for job in "${LEGACY_JOBS[@]}"; do
    dest="${LAUNCH_DIR}/ai.dailyinfo.${job}.plist"
    if [[ -f "${dest}" ]]; then
        launchctl unload -w "${dest}" >/dev/null 2>&1 || true
        rm -f "${dest}"
        echo "🗑  已移除旧版 ${dest}"
    fi
done

for job in "${JOBS[@]}"; do
    dest="${LAUNCH_DIR}/ai.dailyinfo.${job}.plist"
    if [[ -f "${dest}" ]]; then
        launchctl unload -w "${dest}" >/dev/null 2>&1 || true
        rm -f "${dest}"
        echo "🗑  已移除 ${dest}"
    else
        echo "   跳过（未安装）: ${dest}"
    fi
done

echo ""
echo "✅ dailyinfo LaunchAgents 已卸载"
