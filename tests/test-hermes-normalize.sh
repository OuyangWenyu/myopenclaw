#!/usr/bin/env bash
# Test: 部署中的 Hermes 必须原样接受 canonical model id ``deepseek-flash``
#
# 背景（本测试要钉死的缺陷）：
#
#   Hermes 的 ``hermes_cli/model_normalize.py`` 用一张硬编码名单归一化 DeepSeek 模型名 ——
#
#       _DEEPSEEK_CANONICAL_MODELS = {deepseek-chat, deepseek-reasoner,
#                                     deepseek-v4-pro, deepseek-v4-flash}
#       _DEEPSEEK_V_SERIES_RE = ^deepseek-v\d+([-.].+)?$
#
#   两条判据都不满足的名字会被兜底代填 —— 而 canonical 的 ``deepseek-flash``
#   （V4.1 Flash，2026-09-10 发布）**没有版本段**，恰好落在这个分支；又因为它是
#   config.yaml 里的 default model，改写告警被 ``_model_is_default`` 抑制，肉眼看不出来。
#
#   症状的准确表述：被代填之后**实际服务哪个模型由服务端别名表决定** —— 实测服务端
#   今天把两个旧别名都解析到 Flash 系，所以这不是"必然降级"，而是
#   「**配置里写的 id 不保证原样到达线上**，随时可能被服务端别名策略静默改变」。
#   本测试钉的就是这个契约。
#
#   上游在源码注释里把这个缺陷记为 **#107206**（"a shape-based allow-list swallowed
#   the vendor's own deepseek-flash the day it shipped"）；现底座 v0.21.2（2026-09-11）
#   已改为 ``_DEEPSEEK_RETIRED_ALIASES`` 退役别名折叠表、其余原样透传。
#   底座 ``nousresearch/hermes-agent:latest`` 一旦回退到 2026-07-20 的旧层就会复现，
#   因此换底座 / 改 pin / 回滚镜像之后都必须复跑。
#
# 用法: bash tests/test-hermes-normalize.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

PASS=0
FAIL=0
PROBE_TARGET=""

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

echo "=== Test: Hermes model_normalize 不得吞掉 canonical id ==="
echo

# 探测脚本：把两个模型名喂给 Hermes 自己的归一化函数，打印实际发出的名字。
# 只写单引号安全的形式（整个赋值用单引号包裹，内部一律双引号）。
PYCODE='import sys
sys.path.insert(0, "/opt/hermes")
from hermes_cli.model_normalize import normalize_model_for_provider as n
for m in ("deepseek-flash", "deepseek-chat"):
    print("PROBE " + m + " " + n(m, "deepseek"))
'

PROBE_TARGET=""
if [[ -n "$(docker compose ps -q hermes 2>/dev/null)" ]]; then
    PROBE_TARGET="运行中的 hermes 容器"
    OUTPUT="$(printf '%s' "${PYCODE}" | docker compose exec -T hermes \
        /opt/hermes/.venv/bin/python3 - 2>/dev/null)" || true
elif docker image inspect myopenclaw/hermes:latest >/dev/null 2>&1; then
    PROBE_TARGET="镜像 myopenclaw/hermes:latest（容器未运行）"
    OUTPUT="$(printf '%s' "${PYCODE}" | docker run --rm -i -w /opt/hermes \
        --entrypoint /opt/hermes/.venv/bin/python3 \
        myopenclaw/hermes:latest - 2>/dev/null)" || true
else
    PROBE_TARGET="无可探测目标"
    OUTPUT=""
fi
echo "探测目标: ${PROBE_TARGET:-未知}"
echo "${OUTPUT:-（探测无输出）}" | sed 's/^/  | /'
echo

if [[ -z "${OUTPUT}" ]]; then
    echo "  ✗ FAIL: 探测失败（既无运行中的 hermes 容器，也找不到镜像）"
    echo
    echo "结果: 0 通过, 1 失败"
    exit 1
fi

check "deepseek-flash 原样通过（未被改写成 deepseek-chat）" \
    "grep -q '^PROBE deepseek-flash deepseek-flash$' <<< \"\${OUTPUT}\""

check "deepseek-flash 未被折叠为 deepseek-chat" \
    "! grep -q '^PROBE deepseek-flash deepseek-chat$' <<< \"\${OUTPUT}\""

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[[ ${FAIL} -eq 0 ]] || exit 1
