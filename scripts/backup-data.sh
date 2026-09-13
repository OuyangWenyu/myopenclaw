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
# 按目录名里的时间戳判定，不按文件系统 mtime。本脚本尤其致命：
# `rsync -a --delete "${DATA_ROOT}/" "${DEST}/"` 会把 DEST 自身的 mtime
# 覆盖成 DATA_ROOT 的 mtime，源目录顶层长期不变时新快照会继承陈旧 mtime，
# 被 `find -mtime +KEEP_DAYS` 误判为过期而当场删掉自己。
# BACKUP_SKIP_PRUNE=1 时跳过保留策略：容器启动的初始备份不该有删除副作用
# （2026-09-13 一次 `up -d backup-cron` 触发的初始备份曾当场删掉 17 个快照）。
if [[ "${BACKUP_SKIP_PRUNE:-0}" == "1" ]]; then
  echo "   ⏭  跳过旧快照清理（初始备份不执行保留策略）"
else
  _cut=$(( $(date +%s) - BACKUP_KEEP_DAYS * 86400 ))
  CUTOFF="$(date -d "@${_cut}" +%Y-%m-%d 2>/dev/null || true)"     # GNU / busybox
  if [[ -z "${CUTOFF}" ]]; then
    CUTOFF="$(date -r "${_cut}" +%Y-%m-%d 2>/dev/null || true)"    # macOS / BSD
  fi
  if [[ -z "${CUTOFF}" ]]; then
    echo "   ⚠️  无法计算保留截止日期，跳过本次旧快照清理（不猜、不删）" >&2
  fi
fi

if [[ -n "${CUTOFF:-}" ]]; then
  for d in "${BACKUP_ROOT}/data"/*/; do
    [[ -d "${d}" ]] || continue
    name="$(basename "${d}")"
    case "${name}" in
      latest | "${TIMESTAMP}") continue ;;          # 永不删除本次快照
      [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_*) ;;  # 形如 2026-09-13_020000
      *) continue ;;                                # 非快照目录，不碰
    esac
    if [[ "${name%%_*}" < "${CUTOFF}" ]]; then
      echo "   🗑  删除旧快照: ${d%/}"
      rm -rf "${d}"
    fi
  done
fi

echo "   备份完成 (data)"
