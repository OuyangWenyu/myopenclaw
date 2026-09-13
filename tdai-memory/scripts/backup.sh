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
TDAI_DATA="${TDAI_DATA_SRC:-/.myagentdata/tdai-memory}"
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
        echo "   ❌ sqlite3 未安装，无法安全备份 SQLite 数据库" >&2
        exit 1
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

# ── 同步到 latest/（仅在备份有数据时更新，防止空备份覆盖历史）───
if [ "$(ls -A "${DEST}" 2>/dev/null)" ]; then
    rsync -a --delete "${DEST}/" "${LATEST}/"
    echo "   ✅ latest/ 已更新"
else
    echo "   ⚠️  本次备份无数据，latest/ 未更新（保护已有备份）"
fi

# ── 清理超过保留天数的旧快照 ────────────────────────────────────
# 按目录名里的时间戳判定，不按文件系统 mtime：`rsync -a` 的 -t 会把快照目录的
# mtime 覆盖成源目录的 mtime，源目录顶层长期不变时新快照会继承陈旧 mtime，
# 被 `find -mtime +KEEP_DAYS` 误判为过期而当场删掉自己。
# BACKUP_SKIP_PRUNE=1 时跳过保留策略：容器启动的初始备份不该有删除副作用
# （2026-09-13 一次 `up -d backup-cron` 触发的初始备份曾当场删掉 17 个快照）。
if [[ "${BACKUP_SKIP_PRUNE:-0}" == "1" ]]; then
    echo "   ⏭  跳过旧快照清理（初始备份不执行保留策略）"
else
    _cut=$(( $(date +%s) - BACKUP_KEEP_DAYS * 86400 ))
    CUTOFF="$(date -d "@${_cut}" +%Y-%m-%d 2>/dev/null || true)"     # GNU / busybox
    if [[ -z "${CUTOFF}" ]]; then
        CUTOFF="$(date -r "${_cut}" +%Y-%m-%d 2>/dev/null || true)"  # macOS / BSD
    fi
    if [[ -z "${CUTOFF}" ]]; then
        echo "   ⚠️  无法计算保留截止日期，跳过本次旧快照清理（不猜、不删）" >&2
    fi
fi

if [[ -n "${CUTOFF:-}" ]]; then
    for d in "${BACKUP_ROOT}/tdai-memory"/*/; do
        [[ -d "${d}" ]] || continue
        name="$(basename "${d}")"
        case "${name}" in
            latest | "${TIMESTAMP}") continue ;;          # 永不删除本次快照
            [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_*) ;;  # 形如 2026-09-13_020000
            *) continue ;;                                # 非快照目录，不碰
        esac
        if [[ "${name%%_*}" < "${CUTOFF}" ]]; then
            echo "   🗑  删除旧快照: ${d%/}"
            rm -rf "${d}" || echo "   ⚠️  删除失败: ${d%/}" >&2
        fi
    done
fi

echo "   备份完成 (tdai-memory)"
