#!/usr/bin/env bash
# Test: Claude Code CLI / cc-connect 必须显式 pin，且容器实跑版本与 pin 一致
#
# 背景（本测试要防的是什么）：
#
#   `docker/claude-code/Dockerfile` 曾用无版本号的
#   `npm install -g @anthropic-ai/claude-code cc-connect ...`。后果有两个：
#     1) 镜像内容随构建时间漂移 —— 2026-09-14 构建的镜像一直停在
#        claude-code 2.1.270，到 2026-11 已落后 49 个 patch；
#     2) 反过来，任何一次重建又会**静默**跳到当时的最新版，没人知道
#        行为变化是升级带来的还是代码改动带来的。
#
#   本测试钉住两件事：
#     · Dockerfile 的 npm 安装块里，每个包都带精确 `@<版本>`（禁止裸名 / @latest）；
#     · 容器在跑时，`claude --version` / `cc-connect --version` 与 pin 一致
#       （防止缓存层复活旧版本 —— 这正是 2.1.270 能待两个月的原因）。
#
#   注意：这里只验「版本对齐」，不验功能。升级后必须再跑真机验证
#   （飞书回合 + capture-hook.log + settings.json），因为 Stop hook 的
#   失败是静默的（capture-to-gateway.py 设计为任何错误都 exit 0）。
#
# 用法: bash tests/test-cc-version-pin.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

PASS=0
FAIL=0
SKIP=0

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

skip() {
    echo "  ⏭️  $1（容器未运行）"
    SKIP=$((SKIP + 1))
}

DOCKERFILE="docker/claude-code/Dockerfile"

# 只取 npm 安装块（RUN npm install -g 起，到不以反斜杠结尾的行为止），
# 避免把注释里出现的裸包名（如「claude-code 2.1.270」）误判成裸装。
NPM_BLOCK="$(awk '/^RUN npm install -g/{flag=1} flag{print} flag && !/\\$/{flag=0}' "${DOCKERFILE}")"

extract_pin() {
    local pkg="$1"
    grep -oE "${pkg}@[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?" <<< "${NPM_BLOCK}" 2>/dev/null \
        | head -1 | sed 's/.*@//'
}

CLAUDE_PIN="$(extract_pin '@anthropic-ai/claude-code')"
CONNECT_PIN="$(extract_pin 'cc-connect')"
CODEGRAPH_PIN="$(extract_pin '@stupidloud/codegraph')"
GITCODE_PIN="$(extract_pin 'gitcode-cli')"

echo "=== Test: Claude Code / cc-connect 版本 pin ==="
echo
echo "pin 现状:"
echo "  @anthropic-ai/claude-code → ${CLAUDE_PIN:-（未 pin）}"
echo "  cc-connect                → ${CONNECT_PIN:-（未 pin）}"
echo "  @stupidloud/codegraph     → ${CODEGRAPH_PIN:-（未 pin）}"
echo "  gitcode-cli               → ${GITCODE_PIN:-（未 pin）}"
echo

check "npm 安装块存在" "[[ -n '${NPM_BLOCK}' ]]"
check "claude-code 带精确版本" "[[ -n '${CLAUDE_PIN}' ]]"
check "cc-connect 带精确版本" "[[ -n '${CONNECT_PIN}' ]]"
check "codegraph 带精确版本" "[[ -n '${CODEGRAPH_PIN}' ]]"
check "gitcode-cli 带精确版本" "[[ -n '${GITCODE_PIN}' ]]"
check "npm 安装块里没有 @latest / @next 等漂移 tag" \
    "! grep -qE '@(latest|next|stable|beta)([[:space:]]|$|\\\\)' <<< '${NPM_BLOCK}'"

# 裸名（包名后面直接跟空白/行尾/续行符）= 会安装当时的最新版。
has_bare() {
    grep -qE "$1([[:space:]]|$|\\\\)" <<< "${NPM_BLOCK}"
}
check "claude-code 未裸装" "! has_bare '@anthropic-ai/claude-code'"
check "cc-connect 未裸装" "! has_bare 'cc-connect'"
check "codegraph 未裸装" "! has_bare '@stupidloud/codegraph'"
check "gitcode-cli 未裸装" "! has_bare 'gitcode-cli'"

echo
echo "── 部署实况 ──"

if [[ -n "$(docker compose ps -q claude-code 2>/dev/null)" ]]; then
    RUN_CLAUDE="$(docker compose exec -T claude-code claude --version 2>/dev/null \
        | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
    RUN_CONNECT="$(docker compose exec -T claude-code cc-connect --version 2>/dev/null \
        | grep -oE '[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?' | head -1)"
    check "容器 claude --version == pin（实际 ${RUN_CLAUDE:-读取失败}）" \
        "[[ -n '${RUN_CLAUDE}' && '${RUN_CLAUDE}' == '${CLAUDE_PIN}' ]]"
    check "容器 cc-connect --version == pin（实际 ${RUN_CONNECT:-读取失败}）" \
        "[[ -n '${RUN_CONNECT}' && '${RUN_CONNECT}' == '${CONNECT_PIN}' ]]"
else
    skip "claude-code"
fi

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败, ${SKIP} 跳过"
if [[ ${SKIP} -gt 0 ]]; then
    echo "⚠️  部署实况未验证（容器未运行）。注意：升级后仅版本对齐**不等于**功能正常，"
    echo "    还需真机验证飞书回合、capture-hook.log、settings.json。"
fi
[[ ${FAIL} -eq 0 ]] || exit 1
