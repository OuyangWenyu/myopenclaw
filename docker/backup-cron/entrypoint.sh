#!/usr/bin/env bash
# =============================================================
# backup-cron/entrypoint.sh — 写入 crontab 并启动 crond
# 环境变量:
#   BACKUP_CRON      cron 表达式（默认 "0 9,21 * * *"，每天 9:00 和 21:00）
#   BACKUP_KEEP_DAYS 快照保留天数（默认 30）
#   BACKUP_ROOT      /backup（由 docker-compose volumes 挂载提供）
# =============================================================
set -euo pipefail

CRON_EXPR="${BACKUP_CRON:-0 2 * * 0}"
export BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
export BACKUP_ROOT="/backup"

echo "⏰ 定时备份已配置: ${CRON_EXPR}"
echo "🗂  快照保留天数: ${BACKUP_KEEP_DAYS}"
echo "📂 备份目标: ${BACKUP_ROOT}"

# ── 写入 crontab ─────────────────────────────────────────────
# 通过环境变量将 BACKUP_ROOT / BACKUP_KEEP_DAYS 传递给 cron job
cat > /etc/crontabs/root <<EOF
# backup-cron: 定时快照备份
BACKUP_ROOT=${BACKUP_ROOT}
BACKUP_KEEP_DAYS=${BACKUP_KEEP_DAYS}

${CRON_EXPR} /bin/bash /scripts/backup-all-docker.sh >> /proc/1/fd/1 2>> /proc/1/fd/2
EOF

# ── 立即执行一次备份（容器启动时）──────────────────────────────
echo "▶ 容器启动，执行初始备份..."
/bin/bash /scripts/backup-all-docker.sh || echo "⚠️  初始备份失败，不影响定时任务"

echo "✅ crond 启动，进入守护模式..."
exec crond -f -l 2
