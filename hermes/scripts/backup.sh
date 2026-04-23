#!/usr/bin/env bash
# =============================================================
# hermes/scripts/backup.sh — 快照备份 ~/.hermes 关键数据到云盘
# 由 scripts/backup-all.sh 调用，也可单独运行
# 用法: ./hermes/scripts/backup.sh [TIMESTAMP]
# 环境变量:
#   BACKUP_ROOT     备份目标根目录（必须，由 backup-all.sh 传入）
#   BACKUP_KEEP_DAYS  保留天数（默认 30）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="${1:-$(date +%Y-%m-%d_%H%M%S)}"
HERMES_DATA="${HOME}/.hermes"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"

if [[ -z "${BACKUP_ROOT:-}" ]]; then
  echo "❌ BACKUP_ROOT 未设置，请通过 scripts/backup-all.sh 调用，或手动 export BACKUP_ROOT=/path/to/backup"
  exit 1
fi

DEST="${BACKUP_ROOT}/hermes/${TIMESTAMP}"
LATEST="${BACKUP_ROOT}/hermes/latest"

if [[ ! -d "${HERMES_DATA}" ]]; then
  echo "   ⚠️  ~/.hermes 不存在，跳过 hermes 备份"
  exit 0
fi

mkdir -p "${DEST}"

echo "   📂 备份目标: ${DEST}"

# ── 选择性 rsync（只备份重要数据）──────────────────────────────
# config.yaml 和 SOUL.md（配置和人格）
for f in config.yaml SOUL.md; do
  if [[ -f "${HERMES_DATA}/${f}" ]]; then
    rsync -a "${HERMES_DATA}/${f}" "${DEST}/"
  fi
done

# memories/（排除 .lock 文件）
if [[ -d "${HERMES_DATA}/memories" ]]; then
  rsync -a --exclude="*.lock" "${HERMES_DATA}/memories/" "${DEST}/memories/"
fi

# skills/（排除 .bundled_manifest，只保留用户安装的技能）
if [[ -d "${HERMES_DATA}/skills" ]]; then
  rsync -a --exclude=".bundled_manifest" "${HERMES_DATA}/skills/" "${DEST}/skills/"
fi

# hooks/ 和 cron/
for dir in hooks cron; do
  if [[ -d "${HERMES_DATA}/${dir}" ]]; then
    rsync -a "${HERMES_DATA}/${dir}/" "${DEST}/${dir}/"
  fi
done

echo "   ✅ 快照完成: ${DEST}"

# ── 同步到 latest/（--delete 确保不残留旧文件）─────────────────
rsync -a --delete "${DEST}/" "${LATEST}/"
echo "   ✅ latest/ 已更新"

# ── 清理超过保留天数的旧快照 ────────────────────────────────────
find "${BACKUP_ROOT}/hermes" -mindepth 1 -maxdepth 1 -type d \
  ! -name "latest" -mtime "+${BACKUP_KEEP_DAYS}" \
  -exec echo "   🗑  删除旧快照: {}" \; \
  -exec rm -rf {} \;

echo "   备份完成 (hermes)"
