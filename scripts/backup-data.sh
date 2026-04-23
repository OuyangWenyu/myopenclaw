#!/usr/bin/env bash
# =============================================================
# scripts/backup-data.sh — 快照备份 ~/.myagentdata 到云盘
# 由 scripts/backup-all.sh 或 scripts/backup-all-docker.sh 调用
# 用法: ./scripts/backup-data.sh [TIMESTAMP]
# 环境变量:
#   BACKUP_ROOT      备份目标根目录（必须）
#   BACKUP_KEEP_DAYS 保留天数（默认 30）
#   DATA_ROOT        源数据根目录（默认 ~/.myagentdata；容器内为 /.myagentdata）
# =============================================================
set -euo pipefail

TIMESTAMP="${1:-$(date +%Y-%m-%d_%H%M%S)}"
DATA_ROOT="${DATA_ROOT:-${HOME}/.myagentdata}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"

if [[ -z "${BACKUP_ROOT:-}" ]]; then
  echo "❌ BACKUP_ROOT 未设置"
  exit 1
fi

if [[ ! -d "${DATA_ROOT}" ]]; then
  echo "   ⚠️  ${DATA_ROOT} 不存在，跳过 data 备份"
  exit 0
fi

DEST="${BACKUP_ROOT}/data/${TIMESTAMP}"
LATEST="${BACKUP_ROOT}/data/latest"

mkdir -p "${DEST}"
echo "   📂 备份目标: ${DEST}"

rsync -a --delete "${DATA_ROOT}/" "${DEST}/"
echo "   ✅ 快照完成: ${DEST}"

# ── 同步到 latest/ ───────────────────────────────────────────
rsync -a --delete "${DEST}/" "${LATEST}/"
echo "   ✅ latest/ 已更新"

# ── 清理超过保留天数的旧快照 ─────────────────────────────────
find "${BACKUP_ROOT}/data" -mindepth 1 -maxdepth 1 -type d \
  ! -name "latest" -mtime "+${BACKUP_KEEP_DAYS}" \
  -exec echo "   🗑  删除旧快照: {}" \; \
  -exec rm -rf {} \;

echo "   备份完成 (data)"
