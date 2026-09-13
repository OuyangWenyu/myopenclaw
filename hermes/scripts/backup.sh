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

# hooks/, cron/, .contacts/ (cardamum vdir),
# .config/himalaya (mail accounts) and .config/ortie (Outlook OAuth tokens)
for dir in hooks cron .contacts .config/himalaya .config/ortie; do
  if [[ -d "${HERMES_DATA}/${dir}" ]]; then
    # rsync 3.x 只创建目标的最后一级目录，不会补出中间父目录：目标是
    # ${DEST}/.config/himalaya 时必须先建出 .config/，否则报
    # "mkdir ... No such file or directory" 并因 set -e 终止整个备份。
    # （macOS 的 openrsync 会替我们补父目录，所以本地跑不出这个问题。）
    mkdir -p "${DEST}/${dir}"
    rsync -a "${HERMES_DATA}/${dir}/" "${DEST}/${dir}/"
  fi
done

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
  for d in "${BACKUP_ROOT}/hermes"/*/; do
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

echo "   备份完成 (hermes)"
