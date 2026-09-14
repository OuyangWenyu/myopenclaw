#!/usr/bin/env bash
# Test: claude-code entrypoint 必须把**已下线的 model id 迁移掉**，且不误伤操作者的主动选择
#
# 背景（本测试要钉死的缺陷）：
#
#   entrypoint 在容器启动时用一段内嵌 Node 脚本恢复 `settings.json` 的模型默认值，
#   原判定条件是「值为空 or 含 glm」。它遗漏了第三种必须重写的状态 ——
#   **值本身是一个已下线的 id**：cc-connect 与历史配置留下的
#   `deepseek-v4-pro[1M]` 既不空、也不含 glm，于是永久绕过重写，
#   **重建容器也修不好**（线上 CC飞总的 Opus tier 就这样长期停在旧 id 上）。
#
#   同时必须守住反面：操作者主动选的其它模型（非下线 id、非 glm）不得被覆盖 ——
#   这与 `scripts/ensure_hermes_web_search.py` 的「不覆盖操作者选择」原则一致。
#
# 本测试直接跑 entrypoint 里**真实的那段 JS**（只把硬编码的 settings.json 路径
# 换成临时 fixture），不是复刻一份逻辑 —— 复刻的测试会在实现改动时假绿。
#
# 依赖: node（宿主机）。无 node 时跳过。
# 用法: bash tests/test-cc-model-migration.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

# 可用 ENTRYPOINT_OVERRIDE 指向历史版本，用来验证本测试确实能捕获该缺陷（红→绿）
ENTRYPOINT="${ENTRYPOINT_OVERRIDE:-docker/claude-code/entrypoint.sh}"
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

echo "=== Test: CC settings.json 的下线 model id 迁移 ==="
echo

if ! command -v node >/dev/null 2>&1; then
    echo "  ⏭️  宿主机无 node，跳过（该测试需要 node 才能执行真实 JS）"
    exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# 抽取真实 JS，并把硬编码路径换成可注入的环境变量
python3 - "${ENTRYPOINT}" "${WORK}/hook.js" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
m = re.search(r"node -e '\n(.*?)\n'\s*\)", src, re.S)
if not m:
    sys.exit("未能在 entrypoint 中定位内嵌 JS")
js = m.group(1)
needle = 'const path = "/home/node/.claude/settings.json";'
if needle not in js:
    sys.exit("内嵌 JS 的 settings 路径常量变了，本测试的注入点需要同步")
js = js.replace(needle, "const path = process.env.TEST_SETTINGS_PATH;")
open(sys.argv[2], "w").write(js)
PY
[[ -s "${WORK}/hook.js" ]] || { echo "抽取失败"; exit 1; }

# 给定 fixture，跑真实 JS，回读指定键
run_case() {
    local fixture="$1" key="$2"
    local f="${WORK}/settings.json"
    printf '%s' "${fixture}" > "${f}"
    TEST_SETTINGS_PATH="${f}" node -e "$(cat "${WORK}/hook.js")" >/dev/null 2>&1
    python3 -c "
import json,sys
try:
    d=json.load(open('${f}'))
    print(d.get('env',{}).get('${key}','<missing>'))
except Exception as e:
    print('<parse-error>')
"
}

BASE='"permissions":{"allow":[]},"env":{'

echo "── 应被迁移 ──"

OUT="$(run_case "{${BASE}\"ANTHROPIC_DEFAULT_OPUS_MODEL\":\"deepseek-v4-pro[1M]\"}}" ANTHROPIC_DEFAULT_OPUS_MODEL)"
check "已下线的 deepseek-v4-pro[1M] 被迁移为 deepseek-flash[1M]（实际: ${OUT}）" \
    "[[ '${OUT}' == 'deepseek-flash[1M]' ]]"

OUT="$(run_case "{${BASE}\"ANTHROPIC_MODEL\":\"deepseek-v4-flash[1M]\"}}" ANTHROPIC_MODEL)"
check "已下线的 deepseek-v4-flash[1M] 被迁移（实际: ${OUT}）" \
    "[[ '${OUT}' == 'deepseek-flash[1M]' ]]"

OUT="$(run_case "{${BASE}\"ANTHROPIC_DEFAULT_FABLE_MODEL\":\"deepseek-v4-flash-vision-exp[1M]\"}}" ANTHROPIC_DEFAULT_FABLE_MODEL)"
check "已下线的 vision-exp 变体被迁移（实际: ${OUT}）" \
    "[[ '${OUT}' == 'deepseek-flash[1M]' ]]"

OUT="$(run_case "{${BASE}}}" ANTHROPIC_DEFAULT_HAIKU_MODEL)"
check "未设置时写入 deepseek-flash（haiku 层不带 [1M]，实际: ${OUT}）" \
    "[[ '${OUT}' == 'deepseek-flash' ]]"

OUT="$(run_case "{${BASE}\"ANTHROPIC_MODEL\":\"glm-4.7\"}}" ANTHROPIC_MODEL)"
check "历史 glm 默认值被迁移（实际: ${OUT}）" \
    "[[ '${OUT}' == 'deepseek-flash[1M]' ]]"

OUT="$(run_case "{${BASE}\"ANTHROPIC_DEFAULT_OPUS_MODEL\":\"deepseek-flash[1M]\",\"ANTHROPIC_DEFAULT_OPUS_MODEL_NAME\":\"deepseek-v4-pro\"}}" ANTHROPIC_DEFAULT_OPUS_MODEL_NAME)"
check "配套的 *_MODEL_NAME 残留旧 id 时被纠正（实际: ${OUT}）" \
    "[[ '${OUT}' == 'deepseek-flash' ]]"

echo
echo "── 不应被误伤 ──"

OUT="$(run_case "{${BASE}\"ANTHROPIC_MODEL\":\"claude-opus-4.6\"}}" ANTHROPIC_MODEL)"
check "操作者主动选的其它模型不被覆盖（实际: ${OUT}）" \
    "[[ '${OUT}' == 'claude-opus-4.6' ]]"

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[[ ${FAIL} -eq 0 ]] || exit 1
