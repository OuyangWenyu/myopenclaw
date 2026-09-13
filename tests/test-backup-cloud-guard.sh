#!/usr/bin/env bash
# Test: backup-cron 的备份根目录守卫
#
# 背景：docker-compose.yml 写的是
#     - ${BACKUP_ROOT:-/tmp/myopenclaw-backups}:/backup:rw
# `BACKUP_ROOT` 是**宿主机环境变量**，由 scripts/start.sh 解析 .cloud.conf 后
# export。不带它直接跑 `docker compose up -d backup-cron`，compose 会静默回退到
# /tmp/myopenclaw-backups —— 而 macOS 每天清理 /tmp 里 3 天未访问的文件。
#
# 致命之处在于**完全静默**：容器内部永远只看到 /backup，BACKUP_ROOT 永远非空，
# crontab 照常触发、退出码 0、日志「✅ 全部备份完成」、容器 healthy ——
# 数据却写进了一个会被系统清掉的目录。2026-09-13 实测踩中并丢了 17 个快照。
#
# 守卫机制：start.sh 在配置备份根目录时写入哨兵文件
# `.myopenclaw-backup-root`，entrypoint 启动时校验。哨兵校验的是
# 「这个目录经过配置」，与是否云盘无关 —— 本地目录同样合法。
#
# 用法: bash tests/test-backup-cloud-guard.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="${REPO_ROOT}/docker/backup-cron/check-cloud-root.sh"
ENTRYPOINT="${REPO_ROOT}/docker/backup-cron/entrypoint.sh"
START_SH="${REPO_ROOT}/scripts/start.sh"
BACKUP_ROOT_DOCKER="${REPO_ROOT}/scripts/backup-all-docker.sh"
SENTINEL_NAME=".myopenclaw-backup-root"
PASS=0
FAIL=0

check() {
    local desc="$1"
    if eval "$2"; then
        echo "  ✓ $desc"
        PASS=$((PASS + 1))
    else
        echo "  ✗ FAIL: $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Test: backup-cron 备份根目录守卫 ==="
echo "脚本: ${CHECK}"
echo

# ══ 场景 1：未经配置的目录必须被拒绝 ═════════════════════════════════
echo "── 场景 1: 无哨兵 → 拒绝启动且给出可诊断信息 ──"

check "校验脚本存在" "[[ -f '${CHECK}' ]]"
if [[ ! -f "${CHECK}" ]]; then
    echo
    echo "=== FAIL: 校验脚本尚未实现，后续断言无法执行 ==="
    exit 1
fi

UNCONFIGURED="$(mktemp -d)"
OUT="$(bash "${CHECK}" "${UNCONFIGURED}" 2>&1)"
RC=$?
check "退出码非 0（绝不能继续备份）" "[[ ${RC} -ne 0 ]]"
check "报错指明是哪个目录" "grep -q '${UNCONFIGURED}' <<< '${OUT}'"
check "报错点明 /tmp 回退这一危险默认值" "grep -q '/tmp' <<< '${OUT}'"
check "报错给出正确启动方式 start.sh" "grep -q 'start.sh' <<< '${OUT}'"
check "报错提到 .cloud.conf（配置入口）" "grep -q 'cloud.conf' <<< '${OUT}'"
check "报错说明了为什么危险（会被系统清理/不会同步）" \
      "grep -qE '清理|同步|丢失' <<< '${OUT}'"
rm -rf "${UNCONFIGURED}"
echo

# ══ 场景 2：经配置的目录必须放行 ═════════════════════════════════════
echo "── 场景 2: 有哨兵 → 放行（本地目录同样合法）──"

CONFIGURED="$(mktemp -d)"
printf '/some/host/backup/path\n' > "${CONFIGURED}/${SENTINEL_NAME}"
OUT="$(bash "${CHECK}" "${CONFIGURED}" 2>&1)"
RC=$?
check "退出码为 0" "[[ ${RC} -eq 0 ]]"
check "打印出记录的宿主机路径（便于确认备份落点）" \
      "grep -q '/some/host/backup/path' <<< '${OUT}'"
rm -rf "${CONFIGURED}"
echo

# ══ 场景 3：接线断言（权威判据，防止守卫被摘掉）═════════════════════
echo "── 场景 3: start.sh 写入哨兵 / entrypoint 调用守卫 ──"

check "start.sh 解析 BACKUP_ROOT 后写入哨兵文件" \
      "grep -q '${SENTINEL_NAME}' '${START_SH}'"
check "entrypoint 在跑备份前调用守卫" \
      "grep -q 'check-cloud-root.sh' '${ENTRYPOINT}'"
check "守卫调用发生在初始备份之前" \
      "[[ \$(grep -n 'check-cloud-root.sh' '${ENTRYPOINT}' | head -1 | cut -d: -f1) -lt \$(grep -n 'backup-all-docker.sh' '${ENTRYPOINT}' | head -1 | cut -d: -f1) ]]"
echo

# ══ 场景 4：容器重启不得触发 prune ═══════════════════════════════════
echo "── 场景 4: 初始备份跳过 prune（重启不产生删除副作用）──"

check "entrypoint 初始备份传入 BACKUP_SKIP_PRUNE=1" \
      "grep -q 'BACKUP_SKIP_PRUNE=1' '${ENTRYPOINT}'"
for f in hermes/scripts/backup.sh openclaw/scripts/backup.sh claude/scripts/backup.sh \
         scripts/backup-data.sh tdai-memory/scripts/backup.sh; do
    check "${f} 遵守 BACKUP_SKIP_PRUNE" \
          "grep -q 'BACKUP_SKIP_PRUNE' '${REPO_ROOT}/${f}'"
done
echo

if [[ ${FAIL} -eq 0 ]]; then
    echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
    echo "=== PASS: 备份根目录守卫生效 ==="
    exit 0
else
    echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
    echo "=== FAIL: 守卫缺失或未接线 ==="
    exit 1
fi
