#!/usr/bin/env bash
# =============================================================
# hermes/scripts/backup.sh
# 由 scripts/backup-all.sh 调用，也可单独运行
# 用法: ./hermes/scripts/backup.sh [TIMESTAMP]
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TIMESTAMP="${1:-$(date +%Y%m%d_%H%M%S)}"
BACKUP_DIR="${REPO_ROOT}/.data/hermes/db/backups"

mkdir -p "${BACKUP_DIR}"

# ── SQLite 备份（如使用 sqlite） ────────────────────────────
SQLITE_FILE="${REPO_ROOT}/.data/hermes/db/hermes.db"
if [[ -f "${SQLITE_FILE}" ]]; then
  DEST="${BACKUP_DIR}/hermes_${TIMESTAMP}.db"
  cp "${SQLITE_FILE}" "${DEST}"
  echo "   ✅ SQLite 备份: ${DEST}"
fi

# ── PostgreSQL 备份（如使用 postgres，取消注释） ─────────────
# docker exec hermes-db pg_dump -U postgres hermes \
#   > "${BACKUP_DIR}/hermes_${TIMESTAMP}.sql"
# echo "   ✅ Postgres 备份: ${BACKUP_DIR}/hermes_${TIMESTAMP}.sql"

echo "   备份完成 (hermes)"
