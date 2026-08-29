#!/usr/bin/env bash
# start.sh 可选键 grep 保护回归测试
#
# 背景：scripts/start.sh 使用 set -euo pipefail。所有「键可选」的 .env 读取必须
#       以 || true 收尾——否则 .env 缺键时 grep 的退出码 1 经 pipefail 传播到
#       命令替换，赋值失败导致整个脚本中断。
# 关联：a55f93c（首次修复，仅覆盖部分行）、PR #58 双轴审查发现同类共 4 处。
#
# 用法：bash scripts/tests/test-start-sh-optional-greps.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
START_SH="${REPO_ROOT}/scripts/start.sh"
FAILED=0

# ── 测试 1：静态扫描 — 所有 grep '^KEY=' … | cut 赋值行必须带 || true ──────────
echo "🧪 测试 1（静态扫描）：start.sh 中 grep|cut 可选键赋值行的 || true 保护"
violations="$(grep -n "cut -d'=' -f2-" "${START_SH}" | grep -v '|| true' || true)"
if [[ -n "${violations}" ]]; then
  echo "❌ 以下行缺少 || true 保护（.env 缺键将中断脚本）："
  echo "${violations}" | sed 's/^/   /'
  FAILED=1
else
  echo "✅ 全部受保护"
fi

# ── 测试 2：行为级 — 逐行放入「缺键 .env」沙箱执行，脚本不得中断 ──────────────
echo "🧪 测试 2（行为级）：空 .env 沙箱下逐行执行（应优雅降级为空值，而非中断）"
sandbox="$(mktemp -d)"
: > "${sandbox}/.env"
trap 'rm -rf "${sandbox}"' EXIT

checked=0
while IFS= read -r line; do
  line="$(sed -E 's/^[0-9]+://' <<< "${line}")"
  checked=$((checked + 1))
  tmp_script="$(mktemp)"
  {
    echo "set -euo pipefail"
    echo "REPO_ROOT='${sandbox}'"
    echo "${line}"
  } > "${tmp_script}"
  if bash "${tmp_script}" >/dev/null 2>&1; then
    :
  else
    echo "❌ 缺键时中断：$(sed -E 's/^[0-9]+://' <<< "${line}" | cut -c1-60)…"
    FAILED=1
  fi
  rm -f "${tmp_script}"
done < <(grep -n "cut -d'=' -f2-" "${START_SH}")
echo "   共沙箱执行 ${checked} 行"

if [[ "${checked}" -eq 0 ]]; then
  echo "❌ 未扫描到任何 grep|cut 赋值行——扫描模式失效，请检查测试"
  FAILED=1
fi

# ── 汇总 ──────────────────────────────────────────────────────────────────────
if [[ "${FAILED}" -ne 0 ]]; then
  echo "💥 测试未通过"
  exit 1
fi
echo "🎉 全部通过：start.sh 可选键读取在缺键场景下安全降级"
