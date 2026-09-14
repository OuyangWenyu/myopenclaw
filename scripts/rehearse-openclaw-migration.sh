#!/usr/bin/env bash
# =============================================================
# rehearse-openclaw-migration.sh — 在**数据目录副本**上演练 OpenClaw 迁移
#
# 全程不碰生产：把 ~/.openclaw 复制到临时目录，挂到一次性容器里跑新版
# openclaw 的 `doctor --fix`，然后断言「关键资产零丢失」。演练不过就不升级。
#
# 为什么必须整目录复制（而不是只挂一个配置文件）：首次演练只挂了 openclaw.json，
# 数据目录里没有 extensions/ —— doctor 看到 `plugins.installs.dingtalk-connector`
# 这条记录却找不到磁盘上的插件，就把它连同 `channels.dingtalk-connector`
# （含 clientId/clientSecret）当成孤儿配置清掉了。**那是演练环境的假象，不是迁移
# 行为**；用完整数据目录重跑，钉钉通道与凭据完好。所以本脚本复制整目录。
#
# 已知的正确迁移（断言据此写，勿当成丢失去修）：
#   messages.tts.*                    → tts.*
#   gateway.nodes.denyCommands        → gateway.nodes.commands.deny
#   channels.<ch>.dmPolicy/groupPolicy → channels.<ch>.accounts.<acct>.dmPolicy/groupPolicy
#   tools.exec{security,ask}          → tools.exec.mode（ask ≡ allowlist/on-miss，见
#                                       镜像内 docs/tools/permission-modes.md 的模式表）
#   agents/<n>/agent/*.sqlite         ← 由 sessions.json 迁移而来
#   plugins.installs.*                → 被丢弃（记账信息；插件仍从 extensions/ 加载）
#
# doctor 可能要跑**两遍**：第一遍标记出「Legacy session store requires migration」
# 并报 "could not complete maintenance"，第二遍才完成会话迁移并输出 "Doctor complete."
#
# 用法:
#   bash scripts/rehearse-openclaw-migration.sh                 # 默认 2026.9.1
#   OPENCLAW_REHEARSE_TAG=2026.9.2 bash scripts/rehearse-openclaw-migration.sh
#
# 退出码: 0 通过 / 1 演练失败（**不要升级**）/ 2 脚本自身错误
# =============================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

TAG="${OPENCLAW_REHEARSE_TAG:-2026.9.1}"
IMAGE="ghcr.io/openclaw/openclaw:${TAG}"
SRC_DIR="${HOME}/.openclaw"
SRC_CONFIG="${SRC_DIR}/openclaw.json"

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

echo "=== OpenClaw 迁移演练（在数据目录副本上，不触碰生产） ==="
echo "  目标镜像 : ${IMAGE}"
echo "  源数据   : ${SRC_DIR}"
echo

[[ -f "${SRC_CONFIG}" ]] || { echo "❌ 源配置不存在: ${SRC_CONFIG}" >&2; exit 2; }

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "📥 拉取镜像（本地无缓存）..."
    docker pull "${IMAGE}" >/dev/null 2>&1 || { echo "❌ 镜像拉取失败: ${IMAGE}" >&2; exit 2; }
fi

WORK="$(mktemp -d)/data"
trap 'rm -rf "$(dirname "${WORK}")"' EXIT
mkdir -p "${WORK}"
# workspace/ 与 media/ 体积大且与配置迁移无关；extensions/ 与 npm/ **必须带上**
# （否则会复现上文那个"孤儿插件"假象）
rsync -a --exclude 'workspace' --exclude 'media' --exclude 'logs' "${SRC_DIR}/" "${WORK}/"
cp "${WORK}/openclaw.json" "${WORK}/openclaw.before.json"
chmod -R 777 "${WORK}" 2>/dev/null || true

run_oc() {
    docker run --rm -v "${WORK}:/home/node/.openclaw" \
        --entrypoint node "${IMAGE}" /app/openclaw.mjs "$@" 2>&1
}

echo "── 1. 新版本对现有配置的判断（信息性）──"
VALIDATE_OUT="$(run_oc config validate)"
echo "${VALIDATE_OUT}" | head -8 | sed 's/^/  | /'
echo "  （新版本不认旧键是**预期**的 —— 那正是 doctor --fix 要迁移的东西）"
echo

echo "── 2. 跑 doctor --fix（可能需要两遍）──"
FIRST="$(run_oc doctor --fix)"
SECOND=""
if grep -q "could not complete maintenance\|requires migration" <<< "${FIRST}"; then
    echo "  第一遍报告有待迁移的会话存储，跑第二遍..."
    SECOND="$(run_oc doctor --fix)"
fi
FINAL_DOC="${SECOND:-${FIRST}}"
echo "${FINAL_DOC}" | grep -E "Doctor complete|could not complete|requires migration" | tail -3 | sed 's/^/  | /'
echo

echo "── 3. 迁移结果断言 ──"
python3 - "${WORK}/openclaw.before.json" "${WORK}/openclaw.json" "${WORK}" <<'PY' > "${WORK}/assert.txt" 2>&1
import json, os, sys
before = json.load(open(sys.argv[1]))
after  = json.load(open(sys.argv[2]))
work   = sys.argv[3]

def val(d, path):
    cur = d
    for k in path.split("."):
        cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur

def servers(d):
    return sorted(((d.get("mcp") or {}).get("servers") or {}).keys())

tts = val(after, "tts.providers.xiaomi") or val(after, "messages.tts.providers.xiaomi") or {}
print("TTS_MODEL " + str(tts.get("model")))
print("TTS_KEY " + str(tts.get("apiKey")))

for ch in ("feishu", "discord", "dingtalk-connector"):
    print(f"CHANNEL_{ch.upper().replace('-', '_')} " + ("yes" if val(after, f"channels.{ch}") else "no"))

# 凭据：只判断"非空字符串"，不打印值
cred = val(after, "channels.dingtalk-connector.accounts.__default__.clientId")
print("DINGTALK_CRED " + ("yes" if isinstance(cred, str) and len(cred) > 8 else "no"))

print("EXEC_MODE " + str(val(after, "tools.exec.mode")))
print("PRIMARY " + str(val(after, "agents.defaults.model.primary")))
print("MCP_BEFORE " + ",".join(servers(before)))
print("MCP_AFTER " + ",".join(servers(after)))
print("PLUGINS " + ",".join(sorted((val(after, "plugins.entries") or {}).keys())))

sqlite = []
for root, _, files in os.walk(os.path.join(work, "agents")):
    sqlite += [f for f in files if f.endswith(".sqlite")]
print("SESSION_SQLITE " + str(len(sqlite)))

def flat(d, p=""):
    for k, v in d.items():
        kp = f"{p}.{k}" if p else k
        if isinstance(v, dict) and v:
            yield from flat(v, kp)
        else:
            yield kp, v
fb, fa = dict(flat(before)), dict(flat(after))
print("GONE " + ",".join(sorted(set(fb) - set(fa))[:14]))
print("NEW " + ",".join(sorted(set(fa) - set(fb))[:8]))
PY
sed 's/^/  | /' "${WORK}/assert.txt"
echo

g() { grep -E "^$1 " "${WORK}/assert.txt" | head -1 | cut -d' ' -f2-; }

check "迁移后仍是合法 JSON" "! grep -q PARSE_FAIL \"${WORK}/assert.txt\""

check "TTS 块存活且 model 完好（$(g TTS_MODEL)）" \
    "[[ \"\$(g TTS_MODEL)\" == 'mimo-v2.5-tts' ]]"

check "TTS 的 \${XIAOMI_API_KEY} 占位符未被落盘成明文" \
    "[[ \"\$(g TTS_KEY)\" == '\${XIAOMI_API_KEY}' ]]"

check "三条通道全在（飞书/$(g CHANNEL_DISCORD)/$(g CHANNEL_DINGTALK_CONNECTOR)）" \
    "[[ \"\$(g CHANNEL_FEISHU)\" == yes && \"\$(g CHANNEL_DISCORD)\" == yes && \"\$(g CHANNEL_DINGTALK_CONNECTOR)\" == yes ]]"

check "钉钉凭据仍在（值非空，不打印）" \
    "[[ \"\$(g DINGTALK_CRED)\" == yes ]]"

check "exec 策略等价于 allowlist/on-miss（mode=$(g EXEC_MODE)）" \
    "[[ \"\$(g EXEC_MODE)\" == 'ask' ]]"

check "主模型仍是 canonical（$(g PRIMARY)）" \
    "[[ \"\$(g PRIMARY)\" == 'deepseek/deepseek-flash' ]]"

check "MCP server 集合前后一致（$(g MCP_BEFORE) → $(g MCP_AFTER)）" \
    "[[ \"\$(g MCP_BEFORE)\" == \"\$(g MCP_AFTER)\" ]]"

check "五个已启用插件都在（$(g PLUGINS)）" \
    "[[ \"\$(g PLUGINS | tr ',' '\n' | grep -cE '^(deepseek|discord|feishu|moonshot|dingtalk-connector)$')\" == '5' ]]"

check "会话已迁移到 SQLite（$(g SESSION_SQLITE) 个）" \
    "[[ \"\$(g SESSION_SQLITE)\" -gt 0 ]]"

echo
echo "── 4. 键变化（供人工过目）──"
echo "  消失: $(g GONE)"
echo "  新增: $(g NEW)"

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败"
if [[ ${FAIL} -eq 0 ]]; then
    echo "✅ 演练通过 —— 可以执行正式迁移"
else
    echo "❌ 演练失败 —— **不要升级**，先报告这些差异"
    exit 1
fi
