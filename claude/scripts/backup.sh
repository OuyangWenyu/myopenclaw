#!/usr/bin/env bash
# =============================================================
# claude/scripts/backup.sh — 快照备份 ~/.claude + ~/.cc-connect 关键数据到云盘
# 由 scripts/backup-all.sh 调用，也可单独运行
# 用法: ./claude/scripts/backup.sh [TIMESTAMP]
# 环境变量:
#   BACKUP_ROOT     备份目标根目录（必须，由 backup-all.sh 传入）
#   BACKUP_KEEP_DAYS  保留天数（默认 30）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="${1:-$(date +%Y-%m-%d_%H%M%S)}"
CLAUDE_DATA="${HOME}/.claude"
CC_DATA="${HOME}/.cc-connect"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"

if [[ -z "${BACKUP_ROOT:-}" ]]; then
  echo "❌ BACKUP_ROOT 未设置，请通过 scripts/backup-all.sh 调用，或手动 export BACKUP_ROOT=/path/to/backup"
  exit 1
fi

DEST="${BACKUP_ROOT}/claude/${TIMESTAMP}"
LATEST="${BACKUP_ROOT}/claude/latest"

mkdir -p "${DEST}"

echo "   📂 备份目标: ${DEST}"

# ── 备份 ~/.claude 关键数据（选择性）──────────────────────────
if [[ -d "${CLAUDE_DATA}" ]]; then
  # settings.json
  if [[ -f "${CLAUDE_DATA}/settings.json" ]]; then
    rsync -a "${CLAUDE_DATA}/settings.json" "${DEST}/"
  fi

  # projects/, skills/, plans/, tasks/
  for dir in projects skills plans tasks; do
    if [[ -d "${CLAUDE_DATA}/${dir}" ]]; then
      rsync -a --exclude=".git/" "${CLAUDE_DATA}/${dir}/" "${DEST}/${dir}/"
    fi
  done

  echo "   ✅ ~/.claude 快照完成"
else
  echo "   ⚠️  ~/.claude 不存在，跳过"
fi

# ── 备份 ~/.cc-connect 配置 ────────────────────────────────────
if [[ -d "${CC_DATA}" ]]; then
  if [[ -f "${CC_DATA}/config.toml" ]]; then
    rsync -a "${CC_DATA}/config.toml" "${DEST}/"
  fi
  echo "   ✅ ~/.cc-connect 快照完成"
else
  echo "   ⚠️  ~/.cc-connect 不存在，跳过"
fi

echo "   ✅ 快照完成: ${DEST}"

# ── 同步到 latest/ ────────────────────────────────────────────
rsync -a --delete "${DEST}/" "${LATEST}/"
echo "   ✅ latest/ 已更新"

# ── 清理超过保留天数的旧快照 ────────────────────────────────────
find "${BACKUP_ROOT}/claude" -mindepth 1 -maxdepth 1 -type d \
  ! -name "latest" -mtime "+${BACKUP_KEEP_DAYS}" \
  -exec echo "   🗑  删除旧快照: {}" \; \
  -exec rm -rf {} \;

echo "   备份完成 (claude)"
