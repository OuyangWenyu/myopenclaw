#!/usr/bin/env bash
# =============================================================
# backup-cron/entrypoint.sh — 写入 crontab 并启动 crond
# 环境变量:
#   BACKUP_CRON      cron 表达式（默认 "0 2 * * *"，每天凌晨 2:00）
#   BACKUP_KEEP_DAYS 快照保留天数（默认 30）
#   BACKUP_ROOT      /backup（由 docker-compose volumes 挂载提供）
# =============================================================
set -euo pipefail

CRON_EXPR="${BACKUP_CRON:-0 2 * * *}"
export BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
export BACKUP_ROOT="/backup"

# ── 守卫：确认这是经 start.sh 配置的备份目录 ─────────────────
# 拿不到标记文件说明容器很可能是用默认值起的（compose 会回退到
# /tmp/myopenclaw-backups，那里的备份会被系统静默清理）。宁可拒绝启动
# 也不要静默地把备份写进一个会消失的目录。
if ! bash /usr/local/bin/check-cloud-root.sh "${BACKUP_ROOT}"; then
  echo "❌ backup-cron 启动中止：备份目录未配置（见上方说明）" >&2
  exit 1
fi

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
# BACKUP_SKIP_PRUNE=1：重启是运维动作，不该携带删除副作用。保留策略只由
# 02:00 的定时任务执行 —— 2026-09-13 一次 `up -d backup-cron` 触发的初始
# 备份曾当场删掉 17 个快照。
echo "▶ 容器启动，执行初始备份（不清理旧快照）..."
BACKUP_SKIP_PRUNE=1 /bin/bash /scripts/backup-all-docker.sh \
  || echo "⚠️  初始备份失败，不影响定时任务"

echo "✅ crond 启动，进入守护模式..."
exec crond -f -l 2
