#!/usr/bin/env bash
# =============================================================
# openclaw/scripts/backup.sh — 快照备份 ~/.openclaw 关键数据到云盘
# 由 scripts/backup-all.sh 调用，也可单独运行
# 用法: ./openclaw/scripts/backup.sh [TIMESTAMP]
# 环境变量:
#   BACKUP_ROOT     备份目标根目录（必须，由 backup-all.sh 传入）
#   BACKUP_KEEP_DAYS  保留天数（默认 30）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="${1:-$(date +%Y-%m-%d_%H%M%S)}"
OPENCLAW_DATA="${HOME}/.openclaw"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"

if [[ -z "${BACKUP_ROOT:-}" ]]; then
  echo "❌ BACKUP_ROOT 未设置，请通过 scripts/backup-all.sh 调用，或手动 export BACKUP_ROOT=/path/to/backup"
  exit 1
fi

DEST="${BACKUP_ROOT}/openclaw/${TIMESTAMP}"
LATEST="${BACKUP_ROOT}/openclaw/latest"

if [[ ! -d "${OPENCLAW_DATA}" ]]; then
  echo "   ⚠️  ~/.openclaw 不存在，跳过 openclaw 备份"
  exit 0
fi

mkdir -p "${DEST}"

echo "   📂 备份目标: ${DEST}"

# ── openclaw.json（主配置）──────────────────────────────────
if [[ -f "${OPENCLAW_DATA}/openclaw.json" ]]; then
  rsync -a "${OPENCLAW_DATA}/openclaw.json" "${DEST}/"
fi

# ── agents/（排除运行时临时文件和 session）─────────────────────
if [[ -d "${OPENCLAW_DATA}/agents" ]]; then
  rsync -a \
    --exclude="*/agent/*.tmp" \
    --exclude="*/agent/auth-state.json" \
    --exclude="*/sessions/" \
    "${OPENCLAW_DATA}/agents/" "${DEST}/agents/"
fi

# ── flows/ 和 extensions/（用户自定义配置）────────────────────
for dir in flows extensions; do
  if [[ -d "${OPENCLAW_DATA}/${dir}" ]]; then
    rsync -a "${OPENCLAW_DATA}/${dir}/" "${DEST}/${dir}/"
  fi
done

# ── memory/main.sqlite（用 sqlite3 热备，避免备份写中副本）───────
SQLITE_SRC="${OPENCLAW_DATA}/memory/main.sqlite"
if [[ -f "${SQLITE_SRC}" ]]; then
  mkdir -p "${DEST}/memory"
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "${SQLITE_SRC}" ".backup '${DEST}/memory/main.sqlite'"
    echo "   ✅ SQLite 热备完成"
  else
    # sqlite3 不可用时 fallback 到 cp
    cp "${SQLITE_SRC}" "${DEST}/memory/main.sqlite"
    echo "   ✅ SQLite 文件复制（sqlite3 未安装，使用 cp fallback）"
  fi
fi

echo "   ✅ 快照完成: ${DEST}"

# ── 同步到 latest/（--delete 确保不残留旧文件）─────────────────
rsync -a --delete "${DEST}/" "${LATEST}/"
echo "   ✅ latest/ 已更新"

# ── 清理超过保留天数的旧快照 ────────────────────────────────────
find "${BACKUP_ROOT}/openclaw" -mindepth 1 -maxdepth 1 -type d \
  ! -name "latest" -mtime "+${BACKUP_KEEP_DAYS}" \
  -exec echo "   🗑  删除旧快照: {}" \; \
  -exec rm -rf {} \;

echo "   备份完成 (openclaw)"
