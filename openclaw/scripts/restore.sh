#!/usr/bin/env bash
# =============================================================
# openclaw/scripts/restore.sh
# 用法: ./openclaw/scripts/restore.sh [TIMESTAMP]
#   TIMESTAMP: 省略则使用最新备份
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKUP_DIR="${REPO_ROOT}/.data/openclaw/db/backups"
SQLITE_DEST="${REPO_ROOT}/.data/openclaw/db/openclaw.db"

if [[ ! -d "${BACKUP_DIR}" ]]; then
  echo "   ❌ 备份目录不存在: ${BACKUP_DIR}"
  exit 1
fi

TIMESTAMP="${1:-}"
if [[ -z "${TIMESTAMP}" ]]; then
  # 选最新备份
  BACKUP_FILE="$(ls -t "${BACKUP_DIR}"/openclaw_*.db 2>/dev/null | head -1 || true)"
else
  BACKUP_FILE="${BACKUP_DIR}/openclaw_${TIMESTAMP}.db"
fi

if [[ -z "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}" ]]; then
  echo "   ❌ 未找到备份文件"
  exit 1
fi

echo "   📥 恢复 openclaw 数据库: ${BACKUP_FILE}"
cp "${BACKUP_FILE}" "${SQLITE_DEST}"
echo "   ✅ 恢复完成 (openclaw)"
