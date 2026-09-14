#!/usr/bin/env bash
# Test: 三个 OpenClaw 栈必须钉在同一版本，且部署实际跑的就是那个版本
#
# 背景（本测试要防的是什么）：
#
#   这套部署有三个**独立**的 OpenClaw 栈 —— 主网关（虾酱）/ zhixun / tianyi ——
#   pin 分散在**五个**地方：`.env`、`.env.zhixun-bot`、`.env.tianyi-bot`，
#   以及两个 compose 文件里 `${VAR:-默认值}` 的兜底默认值。
#
#   升级大版本时最容易出的事就是「改了三处漏了两处」。而且漏改**不会报错**：
#   compose 的默认值只在 env 缺失时才生效，所以漏改的结果是静默跑旧版本 ——
#   三个 bot 行为不一致，排查时极难定位。
#
#   为什么必须同一版本：OpenClaw 2.0 对配置 schema 与插件加载有 breaking change
#   （`messages.tts`→`tts`、`gateway.nodes.denyCommands`→`gateway.nodes.commands.deny`
#   等）。混跑两个大版本会让同一份模板渲染出的配置在不同栈上有不同解释。
#
# 用法: bash tests/test-openclaw-pins.sh
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

# 从任意文件里抽出第一个 openclaw 镜像的 tag
extract_tag() {
    grep -oE 'openclaw:[A-Za-z0-9._-]+' "$1" 2>/dev/null | head -1 | cut -d: -f2
}

echo "=== Test: OpenClaw 版本 pin 一致性 ==="
echo

TAG_MAIN="$(extract_tag .env)"
TAG_ZHIXUN="$(extract_tag .env.zhixun-bot)"
TAG_TIANYI="$(extract_tag .env.tianyi-bot)"

# `.example` 是**新部署的唯一来源**（`cp .env.zhixun-bot.example .env.zhixun-bot`），
# 而实际的 `.env*` 是 gitignored 的 —— 只查后者会让「example 漏更新」永远绿，
# 于是新部署装出来就是混版本（正是本守卫存在的理由）。
TAG_MAIN_EX="$(extract_tag .env.example)"
TAG_ZHIXUN_EX="$(extract_tag .env.zhixun-bot.example)"
TAG_TIANYI_EX="$(extract_tag .env.tianyi-bot.example)"

echo "pin 现状（实际部署 / 新部署模板）:"
echo "  .env                  → ${TAG_MAIN:-（未设置）}   / example → ${TAG_MAIN_EX:-（未设置）}"
echo "  .env.zhixun-bot       → ${TAG_ZHIXUN:-（未设置）}   / example → ${TAG_ZHIXUN_EX:-（未设置）}"
echo "  .env.tianyi-bot       → ${TAG_TIANYI:-（未设置）}   / example → ${TAG_TIANYI_EX:-（未设置）}"
echo

check "三处 env 的 pin 都存在且完全一致" \
    "[[ -n '${TAG_MAIN}' && '${TAG_MAIN}' == '${TAG_ZHIXUN}' && '${TAG_MAIN}' == '${TAG_TIANYI}' ]]"

check "三处 .example 的 pin 都存在且完全一致（新部署的来源）" \
    "[[ -n '${TAG_MAIN_EX}' && '${TAG_MAIN_EX}' == '${TAG_ZHIXUN_EX}' && '${TAG_MAIN_EX}' == '${TAG_TIANYI_EX}' ]]"

check ".example 与实际 .env 的 pin 逐对一致（模板没落后于部署）" \
    "[[ '${TAG_MAIN_EX}' == '${TAG_MAIN}' && '${TAG_ZHIXUN_EX}' == '${TAG_ZHIXUN}' && '${TAG_TIANYI_EX}' == '${TAG_TIANYI}' ]]"

check "主 compose 的兜底默认值与 .env 一致（缺失时不得静默回退 latest）" \
    "[[ \"\$(extract_tag docker-compose.yml)\" == '${TAG_MAIN}' ]]"

check "zhixun compose 的兜底默认值与 .env 一致" \
    "[[ \"\$(extract_tag docker-compose.zhixun-bot.yml)\" == '${TAG_MAIN}' ]]"

check "tianyi compose 的兜底默认值与 .env 一致" \
    "[[ \"\$(extract_tag docker-compose.tianyi-bot.yml)\" == '${TAG_MAIN}' ]]"

echo
echo "── 部署实况 ──"

# 主网关
if [[ -n "$(docker compose ps -q openclaw-gateway 2>/dev/null)" ]]; then
    RUN_TAG="$(docker compose ps openclaw-gateway --format '{{.Image}}' 2>/dev/null | extract_tag /dev/stdin)"
    VER="$(docker compose exec -T openclaw-gateway node /app/openclaw.mjs --version 2>/dev/null \
        | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -1)"
    check "主网关运行镜像 tag == pin（实际 ${RUN_TAG}）" "[[ '${RUN_TAG}' == '${TAG_MAIN}' ]]"
    check "主网关 --version == pin（实际 ${VER}）" "[[ '${VER}' == '${TAG_MAIN}' ]]"
else
    skip "主网关"
fi

# 两个 bot 栈
for stack in zhixun tianyi; do
    svc="openclaw-${stack}"
    if docker compose --env-file ".env.${stack}-bot" -f "docker-compose.${stack}-bot.yml" \
        ps -q "${svc}" >/dev/null 2>&1 \
        && [[ -n "$(docker compose --env-file ".env.${stack}-bot" -f "docker-compose.${stack}-bot.yml" ps -q "${svc}" 2>/dev/null)" ]]; then
        RUN_TAG="$(docker compose --env-file ".env.${stack}-bot" -f "docker-compose.${stack}-bot.yml" \
            ps "${svc}" --format '{{.Image}}' 2>/dev/null | extract_tag /dev/stdin)"
        VER="$(docker compose --env-file ".env.${stack}-bot" -f "docker-compose.${stack}-bot.yml" \
            exec -T "${svc}" node /app/openclaw.mjs --version 2>/dev/null \
            | grep -oE '[0-9]{4}\.[0-9]+\.[0-9]+' | head -1)"
        check "${stack} 运行镜像 tag == pin（实际 ${RUN_TAG}）" "[[ '${RUN_TAG}' == '${TAG_MAIN}' ]]"
        check "${stack} --version == pin（实际 ${VER}）" "[[ '${VER}' == '${TAG_MAIN}' ]]"
    else
        skip "${stack}"
    fi
done

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败, ${SKIP} 跳过"
if [[ ${SKIP} -gt 0 ]]; then
    echo "⚠️  有 ${SKIP} 项被跳过（容器未运行）——**部署实况未经验证**，"
    echo "    上面的绿只代表「仓库里的 pin 自洽」。升级流程中途（stop 与 up 之间）属正常。"
fi
[[ ${FAIL} -eq 0 ]] || exit 1
