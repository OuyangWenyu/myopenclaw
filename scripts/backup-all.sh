#!/usr/bin/env bash
# =============================================================
# backup-all.sh — 快照备份 hermes + openclaw 数据到云盘
# 用法: ./scripts/backup-all.sh
# 依赖: .cloud.conf（复制自 .cloud.conf.example 并填写本机路径）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONF_FILE="${REPO_ROOT}/.cloud.conf"
TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"

# ── 读取云盘配置 ─────────────────────────────────────────────
if [[ ! -f "${CONF_FILE}" ]]; then
  echo "❌ 未找到 .cloud.conf，请先复制模板并填写本机路径："
  echo "   cp .cloud.conf.example .cloud.conf"
  exit 1
fi

# shellcheck source=/dev/null
source "${CONF_FILE}"

BACKUP_SUBDIR="${BACKUP_SUBDIR:-myopenclaw-backups}"

case "${CLOUD_PROVIDER:-google_drive}" in
  google_drive) CLOUD_ROOT="${GOOGLE_DRIVE_PATH}" ;;
  onedrive)     CLOUD_ROOT="${ONEDRIVE_PATH}" ;;
  custom)       CLOUD_ROOT="${CUSTOM_CLOUD_PATH}" ;;
  *)
    echo "❌ 未知的 CLOUD_PROVIDER: ${CLOUD_PROVIDER}"
    exit 1
    ;;
esac

CLOUD_ROOT="${CLOUD_ROOT/#\~/$HOME}"
export BACKUP_ROOT="${CLOUD_ROOT}/${BACKUP_SUBDIR}"
export BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
export TIMESTAMP

if [[ ! -d "${CLOUD_ROOT}" ]]; then
  echo "❌ 云盘根目录不存在: ${CLOUD_ROOT}"
  echo "   请确认云盘客户端已登录并完成同步"
  exit 1
fi

mkdir -p "${BACKUP_ROOT}/hermes" "${BACKUP_ROOT}/openclaw"

echo "📦 备份根目录: ${BACKUP_ROOT}"
echo "⏰ 时间戳: ${TIMESTAMP}"
echo "🗂  快照保留天数: ${BACKUP_KEEP_DAYS}"

# ── hermes 备份 ──────────────────────────────────────────────
echo ""
echo "▶ 备份 hermes..."
bash "${REPO_ROOT}/hermes/scripts/backup.sh" "${TIMESTAMP}" || echo "⚠️  hermes 备份失败，继续..."

# ── openclaw 备份 ────────────────────────────────────────────
echo ""
echo "▶ 备份 openclaw..."
bash "${REPO_ROOT}/openclaw/scripts/backup.sh" "${TIMESTAMP}" || echo "⚠️  openclaw 备份失败，继续..."

echo ""
echo "✅ 全部备份完成 → ${BACKUP_ROOT}"
