#!/usr/bin/env bash
# =============================================================
# start.sh — 启动所有服务
# 用法: ./scripts/start.sh [--build]
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 检查 .env ────────────────────────────────────────────────
if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "⚠️  .env 不存在，从模板创建..."
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  echo "   请编辑 .env 填写配置后重新运行"
  exit 1
fi

# ── 从 .cloud.conf 解析 BACKUP_ROOT ─────────────────────────
CONF_FILE="${REPO_ROOT}/.cloud.conf"
if [[ ! -f "${CONF_FILE}" ]]; then
  echo "❌ 未找到 .cloud.conf，请先运行 ./scripts/setup-cloud.sh"
  exit 1
fi

# shellcheck source=/dev/null
source "${CONF_FILE}"

case "${CLOUD_PROVIDER:-google_drive}" in
  google_drive) CLOUD_ROOT="${GOOGLE_DRIVE_PATH}" ;;
  onedrive)     CLOUD_ROOT="${ONEDRIVE_PATH}" ;;
  custom)       CLOUD_ROOT="${CUSTOM_CLOUD_PATH}" ;;
esac
CLOUD_ROOT="${CLOUD_ROOT/#\~/$HOME}"
export BACKUP_ROOT="${CLOUD_ROOT}/${BACKUP_SUBDIR:-myopenclaw-backups}"

if [[ ! -d "${CLOUD_ROOT}" ]]; then
  echo "❌ 云盘目录不存在: ${CLOUD_ROOT}，请确认云盘客户端已登录"
  exit 1
fi

mkdir -p "${BACKUP_ROOT}/hermes" "${BACKUP_ROOT}/openclaw"

cd "${REPO_ROOT}"

BUILD_FLAG=""
if [[ "${1:-}" == "--build" ]]; then
  BUILD_FLAG="--build"
fi

echo "🚀 启动服务..."
echo "   备份目录: ${BACKUP_ROOT}"
docker compose up -d ${BUILD_FLAG}
echo "✅ 服务已启动"
docker compose ps
