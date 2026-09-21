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
# 会话库 openclaw-agent.sqlite（146MB 级、回滚模式、活库）**不裸 rsync**：
# 撞上写事务会拷到没有 journal 可回滚的半写状态。它改由下面的 .backup 热备。
# 同目录的 0 字节 *.lock.sqlite 与 4KB generation-writer（实测无表、空库）是
# 占位文件，裸 rsync 就是对它们状态的忠实还原，不另行热备。
if [[ -d "${OPENCLAW_DATA}/agents" ]]; then
  rsync -a \
    --exclude="*/agent/*.tmp" \
    --exclude="*/agent/auth-state.json" \
    --exclude="*/sessions/" \
    --exclude="*/agent/openclaw-agent.sqlite" \
    --exclude="*/agent/openclaw-agent.sqlite-journal" \
    "${OPENCLAW_DATA}/agents/" "${DEST}/agents/"
fi

# ── 活库热备（先写 .tmp、成功才 mv，理由同 backup-data.sh）────
# 会话库是回滚模式，`-readonly` 即可（挂 ro 上也能 .backup，实测）。读事务会
# 短暂挡住写入者 —— 02:00 虾酱空闲，可接受。memory/ 两库若出现同样处理；若将来
# 发现它们是 WAL（`-readonly` 会 CANTOPEN），按 backup-data.sh 的先例给对应子
# 目录加一条窄 rw 挂载再改用普通打开。
if ! command -v sqlite3 &>/dev/null; then
  echo "   ❌ sqlite3 未安装，无法安全备份 OpenClaw 活库（不 cp 兜底）" >&2
  exit 1
fi

hot_copy() {   # hot_copy <rw|readonly> <相对 OPENCLAW_DATA 的路径模式>
  local mode="$1" rel="$2" failed=0 src rel_actual tmp
  shopt -s nullglob
  for src in "${OPENCLAW_DATA}"/${rel}; do
    [[ -f "${src}" ]] || continue
    rel_actual="${src#"${OPENCLAW_DATA}"/}"
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

hot_copy readonly "agents/*/agent/openclaw-agent.sqlite"

# ── flows/ 和 extensions/（用户自定义配置）────────────────────
for dir in flows extensions; do
  if [[ -d "${OPENCLAW_DATA}/${dir}" ]]; then
    rsync -a "${OPENCLAW_DATA}/${dir}/" "${DEST}/${dir}/"
  fi
done

# ── memory 库（Hermes 内置 + 虾酱 TencentDB Agent Memory）────────
# 物理隔离的两库，走同一套热备。本机 2.0 迁移后它们已不存在（main.sqlite 只剩
# 一个 .migrated 残留、memory-tdai/ 目录都没有）—— 存在即热备、不存在即跳过。
# ⚠️ 旧写法是 cp 兜底 + 普通打开，两处都错：前者静默降级成半写副本（CLAUDE.md
# 明文禁 cp 兜底），后者在 :ro 挂载上连打开都做不到。
hot_copy readonly "memory/main.sqlite"
hot_copy readonly "memory-tdai/memories.sqlite"

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
