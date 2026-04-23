#!/usr/bin/env bash
# =============================================================
# backup-all-docker.sh — 容器内版本的备份总入口
# 由 backup-cron 容器的 crond 调用
# 不依赖 .cloud.conf，直接使用 BACKUP_ROOT=/backup（由 volume 挂载）
# =============================================================
set -euo pipefail

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
export BACKUP_ROOT="${BACKUP_ROOT:-/backup}"
export BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
export TIMESTAMP

mkdir -p "${BACKUP_ROOT}/hermes" "${BACKUP_ROOT}/openclaw" "${BACKUP_ROOT}/data"

echo "📦 [$(date '+%Y-%m-%d %H:%M:%S')] 开始备份"
echo "   备份根目录: ${BACKUP_ROOT}"
echo "   时间戳: ${TIMESTAMP}"

echo ""
echo "▶ 备份 hermes..."
HOME=/root bash /hermes-scripts/backup.sh "${TIMESTAMP}" || echo "⚠️  hermes 备份失败，继续..."

echo ""
echo "▶ 备份 openclaw..."
HOME=/root bash /openclaw-scripts/backup.sh "${TIMESTAMP}" || echo "⚠️  openclaw 备份失败，继续..."

echo ""
echo "▶ 备份 /.myagentdata..."
DATA_ROOT=/.myagentdata bash /scripts/backup-data.sh "${TIMESTAMP}" || echo "⚠️  data 备份失败，继续..."

echo ""
echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] 全部备份完成"
