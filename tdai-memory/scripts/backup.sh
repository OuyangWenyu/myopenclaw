#!/usr/bin/env bash
# =============================================================
# tdai-memory/scripts/backup.sh — 快照备份 tdai-memory 数据到云盘
# 由 scripts/backup-all-docker.sh 调用，也可单独运行
# 用法: ./tdai-memory/scripts/backup.sh [TIMESTAMP]
# 环境变量:
#   BACKUP_ROOT       备份目标根目录（必须）
#   BACKUP_KEEP_DAYS  保留天数（默认 30）
#   TDAI_DATA_SRC     数据源路径（默认 ~/.myagentdata/tdai-memory）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="${1:-$(date +%Y-%m-%d_%H%M%S)}"
TDAI_DATA="${TDAI_DATA_SRC:-${HOME}/.myagentdata/tdai-memory}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"

if [[ -z "${BACKUP_ROOT:-}" ]]; then
    echo "❌ BACKUP_ROOT 未设置，请通过 scripts/backup-all-docker.sh 调用，或手动 export BACKUP_ROOT=/path/to/backup"
    exit 1
fi

DEST="${BACKUP_ROOT}/tdai-memory/${TIMESTAMP}"
LATEST="${BACKUP_ROOT}/tdai-memory/latest"

if [[ ! -d "${TDAI_DATA}" ]]; then
    echo "   ⚠️  ${TDAI_DATA} 不存在，跳过 tdai-memory 备份"
    exit 0
fi

mkdir -p "${DEST}"

echo "   📂 备份目标: ${DEST}"

# ── memories.sqlite（sqlite3 热备，同 OpenClaw 模式）─────────
SQLITE_SRC="${TDAI_DATA}/memories.sqlite"
if [[ -f "${SQLITE_SRC}" ]]; then
    if command -v sqlite3 &>/dev/null; then
        sqlite3 "${SQLITE_SRC}" ".backup '${DEST}/memories.sqlite'"
        echo "   ✅ SQLite 热备完成"
    else
        cp "${SQLITE_SRC}" "${DEST}/memories.sqlite"
        echo "   ✅ SQLite 文件复制（sqlite3 未安装，使用 cp 回退）"
    fi
else
    echo "   ℹ️  memories.sqlite 尚不存在，跳过"
fi

# ── scene_blocks/（L2 场景归档）──────────────────────────────
if [[ -d "${TDAI_DATA}/scene_blocks" ]]; then
    rsync -a "${TDAI_DATA}/scene_blocks/" "${DEST}/scene_blocks/"
    echo "   ✅ scene_blocks/ 已备份"
fi

# ── persona.md（L3 用户画像）─────────────────────────────────
if [[ -f "${TDAI_DATA}/persona.md" ]]; then
    rsync -a "${TDAI_DATA}/persona.md" "${DEST}/"
    echo "   ✅ persona.md 已备份"
fi

# ── checkpoint.json（管线状态）────────────────────────────────
if [[ -f "${TDAI_DATA}/checkpoint.json" ]]; then
    rsync -a "${TDAI_DATA}/checkpoint.json" "${DEST}/"
    echo "   ✅ checkpoint.json 已备份"
fi

echo "   ✅ 快照完成: ${DEST}"

# ── 同步到 latest/（--delete 确保不残留旧文件）─────────────────
rsync -a --delete "${DEST}/" "${LATEST}/"
echo "   ✅ latest/ 已更新"

# ── 清理超过保留天数的旧快照 ────────────────────────────────────
find "${BACKUP_ROOT}/tdai-memory" -mindepth 1 -maxdepth 1 -type d \
    ! -name "latest" -mtime "+${BACKUP_KEEP_DAYS}" \
    -exec echo "   🗑  删除旧快照: {}" \; \
    -exec rm -rf {} \; 2>/dev/null || true

echo "   备份完成 (tdai-memory)"
