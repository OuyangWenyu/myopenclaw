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
#   实证（2026.9.1 升级后）：render 脚本写的是**布尔** `streaming: <bool>`，而 2.0 把它
#   改成了对象 `{mode: "off"|"partial"}`（镜像内 docs/channels/feishu.md 原文：
#   "Legacy boolean `streaming` … migrate to this nested shape via openclaw doctor --fix"）。
#   结果是**每次启动渲染出的配置都是 schema-invalid**。
#
#   注意当时**模板里也有**一份布尔 `"streaming": true`（已一并修正）—— 也就是说这条
#   漂移静态守卫本该能抓到，只是 `test_openclaw_schema.py` 当时没有针对该键的断言。
#   本测试的价值在于覆盖**只有渲染器才决定形状**的那类漂移（渲染器可以写模板里没有的
#   字段），而静态守卫只能覆盖模板里写死的键 —— 两者互补，不能互相替代。
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

# ── 静态接线守卫（不需要 node/docker，先跑）：渲染器 / entrypoint / 模板引用的
#    每个环境变量都必须在对应 compose 的**显式 environment 条目**里转发。
#    漏转发不会报错——只会让覆盖静默回落到默认值（2026-10-14 repo-scanner 参数化
#    时差点踩到：模板/渲染器/entrypoint 全改好，compose 忘加 = 线上覆盖无效）。
#    三层扫描面 + 两道防假绿（空集合 / 注释误命中），tianyi 与 zhixun 同款架构一起守。
guard_wiring() {  # $1=bot标签 $2=docker目录 $3=compose文件 $4=变量前缀
    local label="$1" dir="$2" compose="$3" prefix="$4"
    local vars tmpl_vars missing=""
    vars="$(grep -hoE "${prefix}_[A-Z0-9_]+" "${dir}/render-config.mjs" "${dir}/entrypoint.sh" 2>/dev/null | sort -u || true)"
    tmpl_vars="$(grep -hoE '\$\{[A-Z0-9_]+\}' "${dir}/openclaw.json.template" 2>/dev/null | tr -d '${}' | sort -u || true)"
    # 防假绿①：文件被改名/挪走时 grep 失败只留空集合，零循环 = 误报绿
    if [ ! -f "${dir}/render-config.mjs" ] || [ -z "${vars}" ]; then
        missing=" (渲染器缺失或未扫到任何 ${prefix}_* 变量——文件被改名/挪走？)"
    fi
    # 防假绿②：锚定 environment 条目（^缩进 VAR:）——注释里提一嘴变量名不算接线
    for v in ${vars} ${tmpl_vars}; do
        grep -qE "^[[:space:]]*${v}:" "${compose}" || missing="${missing} ${v}"
    done
    check "${label}: 渲染器/entrypoint/模板引用的变量全部被 compose 转发" '[ -z "${missing}" ]'
    [ -n "${missing}" ] && echo "      问题:${missing}"
    return 0
}
guard_wiring "tianyi" "docker/tianyi-bot" "docker-compose.tianyi-bot.yml" "TIANYI_BOT"
guard_wiring "zhixun" "docker/zhixun-bot" "docker-compose.zhixun-bot.yml" "ZHIXUN_BOT"

# 默认端点字面量对账：渲染器与 entrypoint 各持一份默认值，漂移会让就绪探测
# 目标 ≠ 网关实际连接目标（表现为假 "not reachable" + 60s 延时）。
check "tianyi: repo-scanner 默认端点在渲染器与 entrypoint 中一致" \
    'grep -q "http://repo-scanner-mcp:8001/mcp" docker/tianyi-bot/render-config.mjs && grep -q "http://repo-scanner-mcp:8001/mcp" docker/tianyi-bot/entrypoint.sh'

if ! command -v node >/dev/null 2>&1; then
    echo "  ⏭️  宿主机无 node，跳过渲染断言（接线守卫结果仍计入）"
    exit ${FAIL}
fi

# ── repo-scanner 端点参数化（2026-10-14）────────────────────────────────
# 只依赖 node（不依赖镜像）——放在镜像 gate **之前**，否则本地无镜像时整体静默消失。
# 契约：未设/空串/纯空白 → 默认端点（与参数化前一致）；设了 → 覆盖。
#
# 公共假环境（渲染器必需项 + 兜底模型 dummy key）——主循环与断言共用一份，避免漂移
TIANYI_ENVS_COMMON="TIANYI_BOT_FEISHU_APP_ID=dummy_id TIANYI_BOT_FEISHU_APP_SECRET=dummy_secret TIANYI_BOT_MODEL_API_KEY=dummy_key TIANYI_BOT_MODEL_ID=deepseek-flash TIANYI_BOT_MODEL_BASE_URL=https://api.deepseek.com TIANYI_BOT_FEISHU_STREAMING=STREAMING_VAL TIANYI_BOT_SERVER_MONITORING_MCP_URL=https://10.48.0.81:8765/mcp TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN=dummy_token TIANYI_BOT_OWNER_OPEN_ID=ou_0123456789abcdef0123456789abcdef TIANYI_BOT_GLM_API_KEY=dummy_glm_key"

render_tianyi_to() {  # $1=label $2=额外 env（可空）→ 渲染到 "${WORK}/$1/openclaw.json"
    local label="$1" extra="${2:-}"
    local d="${WORK}/$1"
    mkdir -p "${d}"
    # env -u 隔离调用者 shell 继承的同名变量（否则"未设"用例假红）；extra 会重新赋值。
    # STREAMING_VAL 占位统一取 false（本段不需要流式）。
    env -u TIANYI_BOT_REPO_SCANNER_MCP_URL ${TIANYI_ENVS_COMMON//STREAMING_VAL/false} ${extra} \
        node docker/tianyi-bot/render-config.mjs \
        docker/tianyi-bot/openclaw.json.template "${d}/openclaw.json" \
        >/dev/null 2>"${d}/render.err" || { head -3 "${d}/render.err"; return 1; }
}

check "未设变量 → 渲染默认端点 repo-scanner-mcp:8001（行为与参数化前一致）" \
    'render_tianyi_to default && grep -q "http://repo-scanner-mcp:8001/mcp" "${WORK}/default/openclaw.json"'

check "设了变量 → 渲染覆盖端点且不含默认端点" \
    'render_tianyi_to override "TIANYI_BOT_REPO_SCANNER_MCP_URL=https://chitu.example/repo/mcp" \
        && grep -q "https://chitu.example/repo/mcp" "${WORK}/override/openclaw.json" \
        && ! grep -q "repo-scanner-mcp:8001" "${WORK}/override/openclaw.json"'

check "空串/纯空白 → 回落默认端点（compose 未配置传空串；纯空白是粘贴事故）" \
    'render_tianyi_to emptyurl "TIANYI_BOT_REPO_SCANNER_MCP_URL=" \
        && render_tianyi_to blank "TIANYI_BOT_REPO_SCANNER_MCP_URL=   " \
        && grep -q "http://repo-scanner-mcp:8001/mcp" "${WORK}/emptyurl/openclaw.json" \
        && grep -q "http://repo-scanner-mcp:8001/mcp" "${WORK}/blank/openclaw.json"'

# ── GLM 兜底模型（2026-10-15）───────────────────────────────────────────
# fallbacks 机制与虾酱同款（agents.defaults.model.fallbacks）——主模型不可用时
# OpenClaw 自动切换。key 可选：无 key 时摘除 zhipu provider 与 fallbacks
# 条目（不配兜底 ≠ 起不来）。
check "GLM 兜底：设了 key → 渲染 fallbacks 条目与 zhipu provider（值被替换）" \
    'render_tianyi_to fallback "TIANYI_BOT_GLM_API_KEY=glm_key_custom" \
        && grep -q "zhipu/glm-5.3-flash" "${WORK}/fallback/openclaw.json" \
        && grep -q "glm_key_custom" "${WORK}/fallback/openclaw.json" \
        && grep -q "\"zhipu\"" "${WORK}/fallback/openclaw.json"'

check "GLM 兜底：未设/空 key → 摘除兜底（provider 与 fallbacks 都不渲染）" \
    'render_tianyi_to nofallback "TIANYI_BOT_GLM_API_KEY=" \
        && ! grep -qiE "zhipu|glm-5\.3" "${WORK}/nofallback/openclaw.json"'

# 目标版本取自 .env 的 pin —— 守卫跟着部署版本走，升级后自动校验新版本的 schema
TAG="$(sed -n 's/^OPENCLAW_IMAGE=.*:\(.*\)$/\1/p' .env 2>/dev/null | head -1)"
IMAGE="ghcr.io/openclaw/openclaw:${TAG:-latest}"
echo "  目标镜像: ${IMAGE}"
echo

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "  ⏭️  本地无 ${IMAGE}，跳过渲染+校验断言（此前的守卫结果仍计入退出码）"
    exit ${FAIL}
fi

# 渲染 + 校验一组（bot 名 + 模板路径 + 该 bot 的必需环境变量）
# **失败一律返回非零并在输出里带 `FAILED`** —— 只靠文本 grep 判定会让
# 「渲染根本没跑成」「校验器调用本身坏掉」这两类静默报绿（实测：模板路径传成
# 不存在的文件、或校验器路径写错，旧版都打印 4 个 ✓ 并 exit 0）。
# $1=标签 $2=模板 $3=渲染脚本 $4=额外环境变量（空格分隔 KEY=VALUE）
render_and_validate() {
    local label="$1" template="$2" renderer="$3" envs="$4"
    local d="${WORK}/${label}"
    mkdir -p "${d}/data"
    # docker 容器以 uid 1000 运行，放开写权限
    chmod 777 "${d}/data"

    if ! env ${envs} node "${renderer}" "${template}" "${d}/openclaw.json" >/dev/null 2>"${d}/render.err"; then
        # head -8 而非 tail：Node 把根因（`Error: ENOENT …`）打在**堆栈之前**，
        # 取尾部只会得到 "at Object.readFileSync… / Node.js v22.x" 这类无用信息。
        echo "FAILED 渲染脚本非零退出：$(head -8 "${d}/render.err" | tr '\n' ' ')"
        return 1
    fi
    if [[ ! -s "${d}/openclaw.json" ]]; then
        echo "FAILED 渲染产物为空或不存在：${d}/openclaw.json"
        return 1
    fi
    cp "${d}/openclaw.json" "${d}/data/openclaw.json"

    # 退出码单独判：非零既可能是「配置 invalid」（预期失败路径），也可能是校验器
    # 根本没跑起来 —— 两者都该算 FAIL，输出原样带回去供诊断。
    local rc=0
    docker run --rm -v "${d}/data:/home/node/.openclaw" \
        --entrypoint node "${IMAGE}" /app/openclaw.mjs config validate \
        >"${d}/validate.out" 2>&1 || rc=$?
    grep -vE "^ Container|Creating|Created" "${d}/validate.out"
    return ${rc}
}

# tianyi 带上 server-monitoring 的 URL+token：streamable-http + headers 的组合
# 是本仓第一次用，只有真跑一次目标版本的 config validate 才能证明 schema 认可这条目。
# ⚠️ 注释不能放进 for 的续行列表里（`\` 后面跟注释会直接语法错误）——2026-10-13 恢复存档时修的。
# （TIANYI_ENVS_COMMON 公共假环境定义在上方「端点参数化」段——主循环与断言共用一份。）

for spec in \
    "zhixun|docker/zhixun-bot/openclaw.json.template|docker/zhixun-bot/render-config.mjs|ZHIXUN_BOT_FEISHU_APP_ID=dummy_id ZHIXUN_BOT_FEISHU_APP_SECRET=dummy_secret ZHIXUN_BOT_MODEL_API_KEY=dummy_key ZHIXUN_BOT_MODEL_ID=deepseek-flash ZHIXUN_BOT_MODEL_BASE_URL=https://api.deepseek.com ZHIXUN_BOT_FEISHU_STREAMING=STREAMING_VAL ZHIXUN_BOT_WATER_MCP_URL=https://water.example.com/sse ZHIXUN_BOT_WATER_MCP_TOKEN=dummy_water_token ZHIXUN_BOT_WATER_MCP_TRANSPORT=sse ZHIXUN_BOT_GLM_API_KEY=dummy_glm_key" \
    "tianyi|docker/tianyi-bot/openclaw.json.template|docker/tianyi-bot/render-config.mjs|${TIANYI_ENVS_COMMON}"
do
    label="${spec%%|*}"; rest="${spec#*|}"
    template="${rest%%|*}"; rest="${rest#*|}"
    renderer="${rest%%|*}"; envs="${rest#*|}"

    for streaming in false true; do
        out="$(render_and_validate "${label}-${streaming}" "${template}" "${renderer}" \
            "${envs//STREAMING_VAL/${streaming}}")"
        rc=$?
        # 退出码 **或** 文本任一命中即失败 —— 退出码是「真的跑过」的唯一证据，
        # 文本是「跑出了 invalid」的证据，两者不可互相替代。
        if [[ ${rc} -ne 0 ]] || grep -qiE "invalid|Unrecognized key|must be|FAILED" <<< "${out}"; then
            echo "  ✗ FAIL: ${label}（streaming=${streaming}）"
            echo "${out}" | head -6 | sed 's/^/      /'
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
