#!/usr/bin/env bash
# =============================================================
# backup-all-docker.sh — 容器内版本的备份总入口
# 由 backup-cron 容器的 crond 调用
# 不依赖 .cloud.conf，直接使用 BACKUP_ROOT=/backup（由 volume 挂载）
#
# 跑完写一份心跳到 ${AGENTOPS_HEARTBEAT_DIR:-/.agentops}，宿主的
# collect_agentops.py 读它判断备份新鲜度。心跳刻意**不放在云盘目录**：
# 实测云盘目录对宿主进程的可见性按进程上下文分裂 —— launchd 能 readdir 云盘根
# 目录、却读不了里面的文件（EPERM）；交互式 shell 恰好反过来。放云盘的信号
# 总有一类宿主进程读不到，所以心跳落本地路径（见 docker-compose.yml 的挂载）。
#
# 心跳是**尽力而为**：写不了一律只告警，绝不让它影响备份本身。
# =============================================================
set -euo pipefail

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
export BACKUP_ROOT="${BACKUP_ROOT:-/backup}"
export BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
export TIMESTAMP
FAILED=0
STARTED_AT="$(date +%s)"
RESULTS=""

run_step() {
    local name="$1"; shift
    echo ""
    echo "▶ 备份 ${name}..."
    if "$@"; then
        RESULTS="${RESULTS}${name}=ok"$'\n'
    else
        echo "⚠️  ${name} 备份失败，继续..." >&2
        RESULTS="${RESULTS}${name}=failed"$'\n'
        FAILED=1
    fi
}

write_heartbeat() {
    local dir="${AGENTOPS_HEARTBEAT_DIR:-/.agentops}"
    local target="${dir}/backup-heartbeat.json"
    local tmp="${target}.tmp.$$"

    if [ ! -d "${dir}" ]; then
        echo "⚠️  心跳目录不存在，跳过心跳: ${dir}" >&2
        return 0
    fi

    local name state status="ok" services="" failed="" first=1
    while IFS='=' read -r name state; do
        [ -n "${name}" ] || continue
        [ "${state}" = "ok" ] || { status="failed"; failed="${failed}${failed:+,}\"${name}\""; }
        [ ${first} -eq 1 ] || services="${services},"
        services="${services}\"${name}\":\"${state}\""
        first=0
    done <<< "${RESULTS}"

    # epoch 是给机器读的绝对时刻 —— 容器与宿主时区可能不同，
    # 字符串会被读的一方按自己的时区解释，只有 epoch 不会错。
    if ! cat > "${tmp}" <<EOF
{
  "epoch": $(date +%s),
  "timestamp": "$(date '+%Y-%m-%dT%H:%M:%S%z')",
  "snapshot_name": "${TIMESTAMP}",
  "status": "${status}",
  "failed": [${failed}],
  "services": {${services}},
  "backup_root": "${BACKUP_ROOT}",
  "duration_seconds": $(( $(date +%s) - STARTED_AT ))
}
EOF
    then
        echo "⚠️  心跳写入失败，跳过: ${target}" >&2
        rm -f "${tmp}"
        return 0
    fi

    if ! mv -f "${tmp}" "${target}"; then
        echo "⚠️  心跳落盘失败: ${target}" >&2
        rm -f "${tmp}"
        return 0
    fi
    echo "💓 心跳已写入: ${target}"
    return 0
}

mkdir -p "${BACKUP_ROOT}/hermes" "${BACKUP_ROOT}/openclaw" "${BACKUP_ROOT}/claude" "${BACKUP_ROOT}/data" "${BACKUP_ROOT}/tdai-memory"

echo "📦 [$(date '+%Y-%m-%d %H:%M:%S')] 开始备份"
echo "   备份根目录: ${BACKUP_ROOT}"
echo "   时间戳: ${TIMESTAMP}"

run_step "hermes"      env HOME=/root bash /hermes-scripts/backup.sh "${TIMESTAMP}"
run_step "openclaw"    env HOME=/root bash /openclaw-scripts/backup.sh "${TIMESTAMP}"
run_step "claude"      env HOME=/root bash /claude-scripts/backup.sh "${TIMESTAMP}"
run_step "data"        env DATA_ROOT=/.myagentdata bash /scripts/backup-data.sh "${TIMESTAMP}"
run_step "tdai-memory" env TDAI_DATA_SRC=/.myagentdata/tdai-memory bash /tdai-scripts/backup.sh "${TIMESTAMP}"

write_heartbeat

echo ""
if [ $FAILED -eq 0 ]; then
    echo "✅ [$(date '+%Y-%m-%d %H:%M:%S')] 全部备份完成"
else
    echo "❌ [$(date '+%Y-%m-%d %H:%M:%S')] 备份完成，但有部分失败" >&2
    exit 1
fi
