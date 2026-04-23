#!/usr/bin/env bash
# =============================================================
# scripts/launchd/install-dailyinfo.sh
# Render the 4 dailyinfo plist templates and load them via launchctl.
#
# Env overrides:
#   DAILYINFO_DIR   Absolute path to the dailyinfo repo
#                   (default: sibling of myopenclaw at ../dailyinfo)
#   UV_BIN          Absolute path to the uv binary
#                   (default: resolved via `command -v uv`)
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DEFAULT_DAILYINFO_DIR="$(cd "${REPO_ROOT}/.." && pwd)/dailyinfo"
DAILYINFO_DIR="${DAILYINFO_DIR:-${DEFAULT_DAILYINFO_DIR}}"

if [[ ! -d "${DAILYINFO_DIR}" ]]; then
    echo "❌ dailyinfo 目录不存在: ${DAILYINFO_DIR}" >&2
    echo "   请通过 DAILYINFO_DIR=/absolute/path/to/dailyinfo 指定" >&2
    exit 1
fi

if [[ ! -d "${DAILYINFO_DIR}/scripts" || ! -f "${DAILYINFO_DIR}/pyproject.toml" ]]; then
    echo "❌ ${DAILYINFO_DIR} 不像 dailyinfo 仓（缺少 scripts/ 或 pyproject.toml）" >&2
    exit 1
fi

UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "${UV_BIN}" || ! -x "${UV_BIN}" ]]; then
    echo "❌ 未找到可执行的 uv。请先安装 uv（https://docs.astral.sh/uv/）或设置 UV_BIN" >&2
    exit 1
fi

LAUNCH_DIR="${HOME}/Library/LaunchAgents"
mkdir -p "${LAUNCH_DIR}"
mkdir -p "${DAILYINFO_DIR}/logs"

PLIST_PATH="/usr/local/bin:/opt/homebrew/bin:/Users/${USER}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# sed safe-escape helper: escape &, /, \ for sed replacement string.
sed_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\&/\\&}"
    s="${s//\//\\/}"
    printf '%s' "${s}"
}

DAILYINFO_DIR_ESC="$(sed_escape "${DAILYINFO_DIR}")"
UV_BIN_ESC="$(sed_escape "${UV_BIN}")"
PATH_ESC="$(sed_escape "${PLIST_PATH}")"

# Optional warning: dailyinfo .env may override DAILYINFO_DATA_ROOT to
# something outside ~/.myagentdata/, which would break the default
# backup-cron coverage.
if [[ -f "${DAILYINFO_DIR}/.env" ]]; then
    if grep -qE '^[[:space:]]*DAILYINFO_DATA_ROOT=' "${DAILYINFO_DIR}/.env"; then
        current_root="$(grep -E '^[[:space:]]*DAILYINFO_DATA_ROOT=' "${DAILYINFO_DIR}/.env" | tail -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
        case "${current_root}" in
            "${HOME}/.myagentdata/"*|"~/.myagentdata/"*|"\${HOME}/.myagentdata/"*)
                ;;
            "")
                ;;
            *)
                echo "⚠️  dailyinfo/.env 里 DAILYINFO_DATA_ROOT=${current_root}"
                echo "   不在 ~/.myagentdata/ 下，backup-cron 默认不会备份到。"
                echo "   请手动在 docker-compose.yml 里补挂载该路径。"
                ;;
        esac
    fi
fi

JOBS=(run-p1 run-p2 run-p3 push)

for job in "${JOBS[@]}"; do
    tmpl="${SCRIPT_DIR}/ai.dailyinfo.${job}.plist.template"
    dest="${LAUNCH_DIR}/ai.dailyinfo.${job}.plist"

    if [[ ! -f "${tmpl}" ]]; then
        echo "❌ 模板不存在: ${tmpl}" >&2
        exit 1
    fi

    # Unload any existing version (ignore failures).
    launchctl unload -w "${dest}" >/dev/null 2>&1 || true

    sed \
        -e "s/__DAILYINFO_DIR__/${DAILYINFO_DIR_ESC}/g" \
        -e "s/__UV_BIN__/${UV_BIN_ESC}/g" \
        -e "s/__PATH__/${PATH_ESC}/g" \
        "${tmpl}" > "${dest}"

    chmod 644 "${dest}"
    launchctl load -w "${dest}"
    echo "✅ 已加载 ${dest}"
done

echo ""
echo "📋 当前已加载的 dailyinfo 任务："
launchctl list | awk 'NR==1 || /ai\.dailyinfo/' || true

cat <<EOF

ℹ️  调度表：
    06:00  ai.dailyinfo.run-p1   uv run dailyinfo run -p 1
    06:15  ai.dailyinfo.run-p2   uv run dailyinfo run -p 2
    06:30  ai.dailyinfo.run-p3   uv run dailyinfo run -p 3
    07:00  ai.dailyinfo.push     uv run dailyinfo push

📂 日志: ${DAILYINFO_DIR}/logs/dailyinfo-*.log

提示：退出码 0（有内容）和 1（无新内容）都属于正常情况，不要作为失败告警来源。
EOF
