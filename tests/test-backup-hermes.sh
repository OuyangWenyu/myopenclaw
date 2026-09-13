#!/usr/bin/env bash
# Test: 备份脚本的快照完整性与保留策略
#
# 覆盖两个已证实的缺陷：
#
#   1. 嵌套目录 rsync 失败（hermes）
#      循环 `for dir in ... .config/himalaya .config/ortie` 的目标父目录
#      `.config/` 不存在。rsync 3.x 只创建目标最后一级目录，报
#      "mkdir ... No such file or directory" 并因 set -e 终止整个脚本，
#      导致 himalaya 邮件配置与 ortie OAuth token 从未进入任何快照
#      （线上 21 个快照实测 0 个含 .config/）。
#      ⚠️ 该缺陷只在 rsync 3.x 下显现，macOS 自带 openrsync 会自动补父目录，
#      因此行为断言在 macOS 上会假绿 —— 权威判据是下面的结构断言。
#
#   2. prune 误删刚创建的快照（全部 5 个备份脚本）
#      `rsync -a` 的 -t 会把目标目录 mtime 覆盖成源目录 mtime，源目录顶层
#      长期不变时新快照继承陈旧 mtime，被 `find -mtime +KEEP_DAYS` 判定为
#      过期而当场删除。改为按目录名里的时间戳判定。
#      该缺陷纯 shell 逻辑，与 rsync 版本无关，可跨平台复现。
#
# 用法: bash tests/test-backup-hermes.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${REPO_ROOT}/hermes/scripts/backup.sh"
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

echo "=== Test: 备份快照完整性与保留策略 ==="
echo "脚本: ${SCRIPT}"
echo

setup_fixture() {
    FAKE="$(mktemp -d)"
    local h="${FAKE}/.hermes"
    mkdir -p "${h}/.config/himalaya" "${h}/.config/ortie/tokens" \
             "${h}/memories" "${h}/skills" "${h}/hooks" "${h}/cron" "${h}/.contacts"
    echo 'model: test'        > "${h}/config.yaml"
    echo '# soul'             > "${h}/SOUL.md"
    echo 'account = qq'       > "${h}/.config/himalaya/config.toml"
    echo '{"token":"secret"}' > "${h}/.config/ortie/tokens/outlook.json"
    echo 'memory'             > "${h}/memories/m.md"
    echo 'hook'               > "${h}/hooks/h.sh"
    echo 'cron'               > "${h}/cron/c.yaml"
    echo 'vcard'              > "${h}/.contacts/a.vcf"
    export BACKUP_ROOT="${FAKE}/backup"
    mkdir -p "${BACKUP_ROOT}"
}

teardown_fixture() { [[ -n "${FAKE:-}" ]] && rm -rf "${FAKE}"; }

run_backup() {
    local ts="$1"
    HOME="${FAKE}" BACKUP_ROOT="${BACKUP_ROOT}" BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}" \
        bash "${SCRIPT}" "${ts}" >"${FAKE}/out.log" 2>&1
    return $?
}

# ══ 场景 1：嵌套目录必须被完整备份 ═══════════════════════════════════
echo "── 场景 1: 嵌套目录 .config/ 备份 ──"

# 结构断言：权威判据，与 rsync 版本无关，能真正防住回退。
check "rsync 前先创建嵌套目标父目录（rsync 3.x 不会自动补）" \
      "grep -q 'mkdir -p \"\${DEST}/\${dir}\"' '${SCRIPT}'"

setup_fixture
run_backup "2026-09-13_120000"
RC=$?
DEST="${BACKUP_ROOT}/hermes/2026-09-13_120000"

check "脚本退出码为 0" "[[ ${RC} -eq 0 ]]"
check "扁平目录 .contacts/ 已备份" "[[ -f '${DEST}/.contacts/a.vcf' ]]"
check "嵌套 .config/himalaya/config.toml 已备份" \
      "[[ -f '${DEST}/.config/himalaya/config.toml' ]]"
check "嵌套 .config/ortie/tokens/outlook.json 已备份（OAuth token，丢失需重新授权）" \
      "[[ -f '${DEST}/.config/ortie/tokens/outlook.json' ]]"
check "latest/ 已更新且含 .config" \
      "[[ -f '${BACKUP_ROOT}/hermes/latest/.config/ortie/tokens/outlook.json' ]]"

if [[ ${RC} -ne 0 ]]; then
    echo "     ── 脚本输出 ──"
    sed 's/^/     /' "${FAKE}/out.log" | tail -8
fi
teardown_fixture
echo

# ══ 场景 2：名字是新快照、mtime 陈旧 → 必须保留 ══════════════════════
echo "── 场景 2: mtime 陈旧的新快照必须保留（prune 按名字判定）──"
setup_fixture
# 造一个"名字很新但 mtime 是 2020 年"的快照 —— 这正是线上发生的情形：
# rsync -a 把源目录的 mtime 传播到快照目录，旧实现据此误判为过期。
mkdir -p "${BACKUP_ROOT}/hermes/2026-09-13_100000"
touch -t 202001010000 "${BACKUP_ROOT}/hermes/2026-09-13_100000"
run_backup "2026-09-13_120000"
check "mtime 停在 2020 但名字是今天的快照没有被误删" \
      "[[ -d '${BACKUP_ROOT}/hermes/2026-09-13_100000' ]]"
teardown_fixture
echo

# ══ 场景 3：真正过期的快照仍应被清理 ═════════════════════════════════
echo "── 场景 3: 过期快照的清理 ──"
setup_fixture
mkdir -p "${BACKUP_ROOT}/hermes/2020-01-01_000000"   # 远超保留期
mkdir -p "${BACKUP_ROOT}/hermes/2026-09-12_020000"   # 保留期内
mkdir -p "${BACKUP_ROOT}/hermes/latest"
run_backup "2026-09-13_120000"
check "过期快照 2020-01-01_000000 已清理" \
      "[[ ! -d '${BACKUP_ROOT}/hermes/2020-01-01_000000' ]]"
check "保留期内快照 2026-09-12_020000 未被误删" \
      "[[ -d '${BACKUP_ROOT}/hermes/2026-09-12_020000' ]]"
check "latest/ 未被误删" "[[ -d '${BACKUP_ROOT}/hermes/latest' ]]"
check "本次快照仍在" "[[ -d '${BACKUP_ROOT}/hermes/2026-09-13_120000' ]]"
teardown_fixture
echo

# ══ 场景 4：其余 4 个备份脚本同样不得再用 -mtime 判定过期 ════════════
echo "── 场景 4: 全部备份脚本的 prune 实现 ──"
for f in hermes/scripts/backup.sh openclaw/scripts/backup.sh claude/scripts/backup.sh \
         scripts/backup-data.sh tdai-memory/scripts/backup.sh; do
    check "${f} 不再用 -mtime 判定过期" \
          "! grep -q '! -name \"latest\" -mtime' '${REPO_ROOT}/${f}'"
    check "${f} 排除本次快照不被删除" \
          "grep -q \"latest | \\\"\\\${TIMESTAMP}\\\"\" '${REPO_ROOT}/${f}'"
done

echo
if [[ ${FAIL} -eq 0 ]]; then
    echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
    echo "=== PASS: 备份完整性与保留策略正确 ==="
    exit 0
else
    echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
    echo "=== FAIL: 备份存在问题 ==="
    exit 1
fi
