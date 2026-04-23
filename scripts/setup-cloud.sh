#!/usr/bin/env bash
# =============================================================
# setup-cloud.sh — 验证云盘目录并初始化备份目录结构
# 用法: ./scripts/setup-cloud.sh
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONF_FILE="${REPO_ROOT}/.cloud.conf"

# ── 读取配置 ─────────────────────────────────────────────────
if [[ ! -f "${CONF_FILE}" ]]; then
  echo "❌ 未找到 .cloud.conf，请先复制模板并填写本机路径："
  echo "   cp .cloud.conf.example .cloud.conf"
  exit 1
fi

# shellcheck source=/dev/null
source "${CONF_FILE}"

CLOUD_PROVIDER="${CLOUD_PROVIDER:-google_drive}"
BACKUP_SUBDIR="${BACKUP_SUBDIR:-myopenclaw-backups}"

# ── 根据 provider 选择云盘根目录 ─────────────────────────────
case "${CLOUD_PROVIDER}" in
  google_drive) CLOUD_ROOT="${GOOGLE_DRIVE_PATH}" ;;
  onedrive)     CLOUD_ROOT="${ONEDRIVE_PATH}" ;;
  custom)       CLOUD_ROOT="${CUSTOM_CLOUD_PATH}" ;;
  *)
    echo "❌ 未知的 CLOUD_PROVIDER: ${CLOUD_PROVIDER}，请设置为 google_drive / onedrive / custom"
    exit 1
    ;;
esac

# 展开 ~ 为实际路径
CLOUD_ROOT="${CLOUD_ROOT/#\~/$HOME}"
BACKUP_ROOT="${CLOUD_ROOT}/${BACKUP_SUBDIR}"

# ── 验证云盘根目录存在 ────────────────────────────────────────
if [[ ! -d "${CLOUD_ROOT}" ]]; then
  echo "❌ 云盘根目录不存在: ${CLOUD_ROOT}"
  echo "   请确认 ${CLOUD_PROVIDER} 客户端已登录并完成同步"
  exit 1
fi

# ── 创建备份目录结构 ─────────────────────────────────────────
echo "📂 备份根目录: ${BACKUP_ROOT}"
mkdir -p "${BACKUP_ROOT}/hermes"
mkdir -p "${BACKUP_ROOT}/openclaw"
echo "✅ 备份目录已就绪"
