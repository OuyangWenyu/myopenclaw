#!/usr/bin/env bash
# Test: 两个 bot 的**渲染产物**必须能通过目标版本的 `config validate`
#
# 背景（本测试要钉死的缺陷）：
#
#   两个 bot 的 entrypoint 每次启动都用 `render-config.mjs` 从模板渲染配置。
#   模板本身是静态的、由 `tests/test_openclaw_schema.py` 静态守；但**渲染脚本会往里
#   写一些模板里没有的字段**（如 `channels.feishu.streaming`），这些字段的形状
#   只有目标版本的校验器说了算。
#
#   实证（2026.9.1 升级后）：render 脚本写的是**布尔** `streaming: false`，而 2.0 把它
#   改成了对象 `{mode: "off"|"partial"}`（镜像内 docs/channels/feishu.md 原文：
#   "Legacy boolean `streaming` … migrate to this nested shape via openclaw doctor --fix"）。
#   结果是**每次启动渲染出的配置都是 schema-invalid** —— 静态守卫看不到（模板里没这个
#   字段），pin 守卫也看不到。这类「版本-形状漂移」只有真跑一次目标版本的校验器能防住。
#
# 依赖: node（宿主机，仅用 stdlib）+ docker + 目标镜像可拉。
# 用法: bash tests/test-bot-rendered-config.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

PASS=0
FAIL=0
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

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

echo "=== Test: bot 渲染产物必须通过目标版本的 config validate ==="
echo

if ! command -v node >/dev/null 2>&1; then
    echo "  ⏭️  宿主机无 node，跳过（渲染脚本需要 node 执行）"
    exit 0
fi

# 目标版本取自 .env 的 pin —— 守卫跟着部署版本走，升级后自动校验新版本的 schema
TAG="$(sed -n 's/^OPENCLAW_IMAGE=.*:\(.*\)$/\1/p' .env 2>/dev/null | head -1)"
IMAGE="ghcr.io/openclaw/openclaw:${TAG:-latest}"
echo "  目标镜像: ${IMAGE}"
echo

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "  ⏭️  本地无 ${IMAGE}，跳过（避免测试触发大镜像拉取）"
    exit 0
fi

# 渲染 + 校验一组（bot 名 + 模板路径 + 该 bot 的必需环境变量）
# $1=标签 $2=模板 $3=渲染脚本 $4=额外环境变量（换行分隔 KEY=VALUE）
render_and_validate() {
    local label="$1" template="$2" renderer="$3" envs="$4"
    local d="${WORK}/${label}"
    mkdir -p "${d}/data"
    # docker 容器以 uid 1000 运行，放开写权限
    chmod 777 "${d}/data"

    if ! env ${envs} node "${renderer}" "${template}" "${d}/openclaw.json" >/dev/null 2>"${d}/render.err"; then
        echo "render-failed: $(tail -1 "${d}/render.err")"
        return
    fi
    cp "${d}/openclaw.json" "${d}/data/openclaw.json"

    docker run --rm -v "${d}/data:/home/node/.openclaw" \
        --entrypoint node "${IMAGE}" /app/openclaw.mjs config validate 2>&1 \
        | grep -vE "^ Container|Creating|Created"
}

for spec in \
    "zhixun|docker/zhixun-bot/openclaw.json.template|docker/zhixun-bot/render-config.mjs|ZHIXUN_BOT_FEISHU_APP_ID=dummy_id ZHIXUN_BOT_FEISHU_APP_SECRET=dummy_secret ZHIXUN_BOT_MODEL_API_KEY=dummy_key ZHIXUN_BOT_MODEL_ID=deepseek-flash ZHIXUN_BOT_MODEL_BASE_URL=https://api.deepseek.com ZHIXUN_BOT_FEISHU_STREAMING=STREAMING_VAL" \
    "tianyi|docker/tianyi-bot/openclaw.json.template|docker/tianyi-bot/render-config.mjs|TIANYI_BOT_FEISHU_APP_ID=dummy_id TIANYI_BOT_FEISHU_APP_SECRET=dummy_secret TIANYI_BOT_MODEL_API_KEY=dummy_key TIANYI_BOT_MODEL_ID=deepseek-flash TIANYI_BOT_MODEL_BASE_URL=https://api.deepseek.com TIANYI_BOT_FEISHU_STREAMING=STREAMING_VAL"
do
    label="${spec%%|*}"; rest="${spec#*|}"
    template="${rest%%|*}"; rest="${rest#*|}"
    renderer="${rest%%|*}"; envs="${rest#*|}"

    for streaming in false true; do
        out="$(render_and_validate "${label}-${streaming}" "${template}" "${renderer}" \
            "${envs//STREAMING_VAL/${streaming}}")"
        if grep -qiE "invalid|Unrecognized key|must be" <<< "${out}"; then
            echo "  ✗ FAIL: ${label}（streaming=${streaming}）渲染产物 schema-invalid："
            echo "${out}" | grep -iE "×|invalid|must be|Unrecognized" | head -4 | sed 's/^/      /'
            FAIL=$((FAIL + 1))
        else
            echo "  ✓ ${label}（streaming=${streaming}）渲染产物通过 config validate"
            PASS=$((PASS + 1))
        fi
    done
done

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[[ ${FAIL} -eq 0 ]] || exit 1
