#!/usr/bin/env bash
# =============================================================
# setup-cloud.sh — 读取 .cloud.conf，创建 .data → 云盘目录的软链接
# 用法: ./scripts/setup-cloud.sh
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONF_FILE="${REPO_ROOT}/.cloud.conf"
CONF_EXAMPLE="${REPO_ROOT}/.cloud.conf.example"

# ── 读取配置 ─────────────────────────────────────────────────
if [[ ! -f "${CONF_FILE}" ]]; then
  echo "❌ 未找到 .cloud.conf，请先复制模板并填写本机路径："
  echo "   cp .cloud.conf.example .cloud.conf"
  exit 1
fi

# shellcheck source=/dev/null
source "${CONF_FILE}"

CLOUD_PROVIDER="${CLOUD_PROVIDER:-google_drive}"
DATA_SUBDIR="${DATA_SUBDIR:-myopenclaw/data}"

# ── 根据 provider 选择云盘根目录 ─────────────────────────────
case "${CLOUD_PROVIDER}" in
  google_drive)
    CLOUD_ROOT="${GOOGLE_DRIVE_PATH}"
    ;;
  onedrive)
    CLOUD_ROOT="${ONEDRIVE_PATH}"
    ;;
  custom)
    CLOUD_ROOT="${CUSTOM_CLOUD_PATH}"
    ;;
  *)
    echo "❌ 未知的 CLOUD_PROVIDER: ${CLOUD_PROVIDER}，请设置为 google_drive / onedrive / custom"
    exit 1
    ;;
esac

# 展开 ~ 为实际路径
CLOUD_ROOT="${CLOUD_ROOT/#\~/$HOME}"
CLOUD_DATA_DIR="${CLOUD_ROOT}/${DATA_SUBDIR}"

# ── 检查云盘目录是否存在 ─────────────────────────────────────
if [[ ! -d "${CLOUD_ROOT}" ]]; then
  echo "❌ 云盘根目录不存在: ${CLOUD_ROOT}"
  echo "   请确认 ${CLOUD_PROVIDER} 客户端已登录并完成同步"
  exit 1
fi

# ── 创建云盘数据目录（含子目录） ─────────────────────────────
echo "📂 云盘数据目录: ${CLOUD_DATA_DIR}"
mkdir -p "${CLOUD_DATA_DIR}/openclaw/db"
mkdir -p "${CLOUD_DATA_DIR}/openclaw/logs"
mkdir -p "${CLOUD_DATA_DIR}/hermes/db"
mkdir -p "${CLOUD_DATA_DIR}/hermes/logs"

# ── 创建 / 更新 .data 软链接 ─────────────────────────────────
DATA_LINK="${REPO_ROOT}/.data"

if [[ -L "${DATA_LINK}" ]]; then
  current_target="$(readlink "${DATA_LINK}")"
  if [[ "${current_target}" == "${CLOUD_DATA_DIR}" ]]; then
    echo "✅ .data 软链接已是最新，无需更新"
    exit 0
  fi
  echo "🔄 更新 .data 软链接: ${current_target} → ${CLOUD_DATA_DIR}"
  rm "${DATA_LINK}"
elif [[ -e "${DATA_LINK}" ]]; then
  echo "⚠️  .data 存在但不是软链接，将备份为 .data.bak"
  mv "${DATA_LINK}" "${DATA_LINK}.bak"
fi

ln -s "${CLOUD_DATA_DIR}" "${DATA_LINK}"
echo "✅ 软链接已创建: .data → ${CLOUD_DATA_DIR}"
