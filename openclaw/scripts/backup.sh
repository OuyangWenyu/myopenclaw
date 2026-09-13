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
    echo "   ✅ SQLite 热备完成 (Hermes memory)"
  else
    # sqlite3 不可用时 fallback 到 cp
    cp "${SQLITE_SRC}" "${DEST}/memory/main.sqlite"
    echo "   ✅ SQLite 文件复制（sqlite3 未安装，使用 cp fallback）"
  fi
fi

# ── memory-tdai/memories.sqlite（虾酱 TencentDB Agent Memory）─────
# 独立于 Hermes 内置 memory/main.sqlite，物理隔离
TDAI_SQLITE_SRC="${OPENCLAW_DATA}/memory-tdai/memories.sqlite"
if [[ -f "${TDAI_SQLITE_SRC}" ]]; then
  mkdir -p "${DEST}/memory-tdai"
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "${TDAI_SQLITE_SRC}" ".backup '${DEST}/memory-tdai/memories.sqlite'"
    echo "   ✅ SQLite 热备完成 (虾酱 memory-tdai)"
  else
    echo "   ❌ sqlite3 未安装，无法安全备份 虾酱 memory 数据库" >&2
    exit 1
  fi
fi

echo "   ✅ 快照完成: ${DEST}"

# ── 同步到 latest/（--delete 确保不残留旧文件）─────────────────
rsync -a --delete "${DEST}/" "${LATEST}/"
echo "   ✅ latest/ 已更新"

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
    CUTOFF="$(date -r "${_cut}" +%Y-%m-%d 2>/dev/null || true)"    # macOS / BSD
  fi
  if [[ -z "${CUTOFF}" ]]; then
    echo "   ⚠️  无法计算保留截止日期，跳过本次旧快照清理（不猜、不删）" >&2
  fi
fi

if [[ -n "${CUTOFF:-}" ]]; then
  for d in "${BACKUP_ROOT}/openclaw"/*/; do
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

echo "   备份完成 (openclaw)"
