#!/usr/bin/env bash
# Test: member-check wrapper 的对外承诺（退出码契约——文档明说"适合脚本/监控直接消费"）
#
# 覆盖面刻意保持零依赖（不需要 docker 运行中）：
#   1. 语法合法；
#   2. 缺 env 文件 = 配置错误 → 退出码 2（且发生在任何 docker 调用之前）。
# docker 不可达 → 2 的映射（rc≥125）与 0/1/2 透传在实现里由 rc 分支保证，
# 真机验证见 2026-10-14 验收记录（DOCKER_HOST=tcp://127.0.0.1:9 → exit 2）。
#
# 用法: bash tests/test-member-check.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

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

echo "=== Test: member-check wrapper 退出码契约 ==="
echo

check "wrapper 语法合法（bash -n）" 'bash -n scripts/member-check.sh'

check "缺 env 文件 → 退出码 2（配置错误，发生在 docker 调用之前）" \
    'MEMBER_CHECK_ENV_FILE=/nonexistent-member-check-env bash scripts/member-check.sh >/dev/null 2>&1; [ $? -eq 2 ]'

# ⚠️ 先捕获输出再 grep：直接 `cmd | grep -q` 在 pipefail 下取的是 cmd 的退出码（2），
# 会把"grep 命中"误判成失败。
check "缺 env 文件的引导信息指向实际使用的文件路径" \
    'out=$(MEMBER_CHECK_ENV_FILE=/nonexistent-member-check-env bash scripts/member-check.sh 2>&1); grep -q "/nonexistent-member-check-env" <<< "${out}"'

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[[ ${FAIL} -eq 0 ]] || exit 1
