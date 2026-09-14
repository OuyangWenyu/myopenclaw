#!/usr/bin/env bash
# Test: 三栈装的官方插件（@openclaw/*）必须与核心同版本
#
# 背景（本测试要钉死的缺陷 —— 同类已经炸过两次）：
#
#   官方插件与核心是**同版本号配套发布**的，各自声明 `peerDependencies.openclaw`。
#   核心升到 2.0 后，插件若留在旧版，就会出现「插件按旧 schema 校验、核心按新 schema
#   校验」的**要求相反**状态。两次实测：
#
#     - `@openclaw/discord` 2026.7.1 + 核心 2026.9.1
#       → 插件加载失败（`plugin-sdk/security-runtime` 无 `privateFileStore` 导出），
#         虾酱的 Discord 通道直接不可用
#     - `@openclaw/feishu` 2026.7.1 + 核心 2026.9.1
#       → 插件要求 `channels.feishu.streaming` 是**布尔**、核心要求是**对象**，
#         两个校验器要求相反：写对象则通道崩溃重启，写布尔则 config validate 报 invalid
#
#   两次都是「升级核心时忘了插件也要跟着升」。本测试把这条不变量钉住。
#
# 用法: bash tests/test-openclaw-plugin-versions.sh
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

echo "=== Test: 官方插件版本必须与 OpenClaw 核心一致 ==="
echo

CORE="$(sed -n 's/^OPENCLAW_IMAGE=.*:\(.*\)$/\1/p' .env 2>/dev/null | head -1)"
echo "  核心版本（.env pin）: ${CORE:-未设置}"
echo

if [[ -z "${CORE}" ]]; then
    echo "  ✗ FAIL: 无法从 .env 读出 OPENCLAW_IMAGE 的 tag"
    exit 1
fi

report="$(python3 - "${CORE}" <<'PY'
import json, pathlib, sys

core = sys.argv[1]
stacks = {
    "主网关": pathlib.Path.home() / ".openclaw",
    "zhixun": pathlib.Path.home() / ".openclaw-zhixun",
    "tianyi": pathlib.Path.home() / ".openclaw-tianyi",
}
mismatched = 0
scanned = 0
for label, base in stacks.items():
    projects = base / "npm" / "projects"
    if not projects.is_dir():
        print(f"SKIP {label}（无 npm/projects）")
        continue
    for pkg in sorted(projects.glob("*/node_modules/@openclaw/*/package.json")):
        try:
            meta = json.loads(pkg.read_text())
        except Exception:
            continue
        name, ver = meta.get("name", "?"), meta.get("version", "?")
        scanned += 1
        if ver != core:
            print(f"MISMATCH {label} {name} {ver}")
            mismatched += 1
print(f"SCANNED {scanned}")
print(f"MISMATCHED {mismatched}")
PY
)"

echo "${report}" | grep -vE '^MISMATCHED|^SCANNED' | sed 's/^/  /'
scanned="$(grep '^SCANNED ' <<< "${report}" | cut -d' ' -f2)"
mismatched="$(grep '^MISMATCHED ' <<< "${report}" | cut -d' ' -f2)"

check "扫到了官方插件（${scanned} 个；为 0 说明落盘布局变了、本守卫已失去覆盖面）" \
    "[[ '${scanned:-0}' -gt 0 ]]"

check "所有官方插件都与核心 ${CORE} 同版本（不一致 ${mismatched} 个）" \
    "[[ '${mismatched}' == '0' ]]"

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败"
if [[ ${FAIL} -ne 0 ]]; then
    echo "  修法（每个不一致的包，在对应栈的容器内）："
    echo "    docker compose exec <容器> node /app/openclaw.mjs plugins install <包名>@${CORE} --force --accept-capabilities"
    echo "  注意：① 若配置当前被**旧插件**判为 invalid，install 会被拒绝 ——"
    echo "          先临时移走该插件的配置段（如 channels.feishu），装完恢复；"
    echo "        ② 刚在运行中的容器里装完、**还没重启**时，旧 generation 目录仍在盘上，"
    echo "          本守卫会报它的旧版本号（假红）—— 重启网关后再跑（启动时会清理残留）。"
    exit 1
fi
