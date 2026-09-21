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

# ── 活库清单：只热备，不裸 rsync ────────────────────────────────
# 活着的 SQLite 直接 rsync 只能得到「某一瞬间的文件视图」：
#   · WAL 模式：最新提交可能还在 -wal 里（甚至整库 4MB 级的数据只住在那里）；
#   · 回滚模式：写事务进行中会存在半写状态，副本没有 journal 可回滚。
# 两组分开是因为打开方式不同，也决定是否需要 compose 里的窄 rw 挂载：
#   HOT_DBS_RW —— WAL 库的读者也要在 wal-index 里加锁（写 -shm），必须挂 rw；
#   HOT_DBS_RO —— 回滚模式的库 `-readonly` 打开即可（挂 ro 上也能 .backup，实测）。
HOT_DBS_RW=(
  "paper-queue/queue.sqlite"                    # WAL —— 虾酱论文清单
  "tdai-memory/vectors.db"                      # WAL —— TDAI 向量库
  "tdai-memory/memories.sqlite"                 # WAL —— TDAI 记忆（尚未出现，先收编，
                                                #  否则它一诞生就会被这份 rsync 裸拷）
  "repo-scanner/repos.sqlite"                   # WAL —— 研发日报
)
HOT_DBS_RO=(
  "aisecretary/transactions.sqlite"             # 回滚模式 —— 事务库
  "dailyinfo/freshrss/data/users/*/db.sqlite"   # 回滚模式 —— 每用户一个
)
HOT_DBS=("${HOT_DBS_RW[@]}" "${HOT_DBS_RO[@]}")

# rsync 排除式由清单推导（手写第二份就会漂移）：裸库与 sidecar 都不进快照 ——
# sidecar 是某一瞬间的残影，进快照只会误导恢复方。
RSYNC_EXCLUDES=()
for _db in "${HOT_DBS[@]}"; do
  RSYNC_EXCLUDES+=(--exclude="${_db}*")
done
rsync -a --delete "${RSYNC_EXCLUDES[@]}" "${DATA_ROOT}/" "${DEST}/"
echo "   ✅ 快照完成: ${DEST}"

# ── 逐个热备：先写 .tmp、成功才 mv ──────────────────────────────
# `.backup` 会**先把目标文件建出来**再去读源，源读不了时假文件已经落下了
# （2026-09-18~21 实测：只读挂载上的 WAL 库 CANTOPEN，四个快照里各留一个 0 字节的
# queue.sqlite）。0 字节比缺文件更阴险 —— 恢复方会把它读成「是空的」，而不是
# 「没备份到」。`-cmd .timeout` 是拿读锁时的等待：撞上写事务提交的瞬间不硬失败。
# ⚠️ 源库旁边若躺着**真的** hot journal（上次写到一半崩了），只读热备会响亮地拒绝
# （attempt to write a readonly database）—— 这是 fail-loud，别改成静默跳过或 cp 兜底。
if ! command -v sqlite3 &>/dev/null; then
  echo "   ❌ sqlite3 未安装，无法安全备份 SQLite 库（不 cp 兜底）" >&2
  exit 1
fi

hot_copy() {   # hot_copy <rw|readonly> <相对 DATA_ROOT 的路径模式>
  local mode="$1" rel="$2" src rel_actual tmp failed=0
  shopt -s nullglob
  for src in "${DATA_ROOT}"/${rel}; do
    # 不含通配符的字面路径不会"匹配失败"，nullglob 管不着它 —— 得自己挡：
    # 库还不存在（新部署还没写过）时要跳过，而不是拿一个不存在的路径去 sqlite3。
    [[ -f "${src}" ]] || continue
    rel_actual="${src#"${DATA_ROOT}"/}"
    tmp="${DEST}/${rel_actual}.tmp"
    mkdir -p "$(dirname "${tmp}")"
    rm -f "${tmp}"
    if [[ "${mode}" == "readonly" ]]; then
      sqlite3 -readonly -cmd ".timeout 5000" "${src}" ".backup '${tmp}'" || failed=1
    else
      sqlite3 -cmd ".timeout 5000" "${src}" ".backup '${tmp}'" || failed=1
    fi
    if [[ ${failed} -ne 0 ]]; then
      rm -f "${tmp}"
      echo "   ❌ SQLite 热备失败: ${rel_actual}" >&2
      exit 1
    fi
    mv -f "${tmp}" "${DEST}/${rel_actual}"
    echo "   ✅ SQLite 热备完成 (${rel_actual})"
  done
}

for _rel in "${HOT_DBS_RW[@]}"; do hot_copy rw "${_rel}"; done
for _rel in "${HOT_DBS_RO[@]}"; do hot_copy readonly "${_rel}"; done

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
