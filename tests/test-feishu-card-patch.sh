#!/usr/bin/env bash
# Test: 飞书 markdown 卡片化补丁必须在部署镜像里**真的生效且行为正确**
#
# 背景（本测试要钉死的缺陷与机制）：
#
#   飞书 ``post`` + ``md`` 渲染器不支持 GFM 表格 —— 实测（向 DM 发探针 → API
#   拉回服务端存储逐行 dump）表格被存成**一整段字面管道符文本**、标题降级为
#   普通文字、行内代码失去等宽。飞书卡片（interactive, Card JSON 2.0）的
#   markdown 组件能真渲染这些（表格 ≤5 行数据 + 分页、每组件 ≤4 表）。
#   上游 PR #21000/#29630 均未 merge，所以我们构建时对 adapter.py 做文本手术
#   （见 docker/hermes/patch-feishu-adapter.py）。
#
#   失效模式（本测试存在的理由）：base image ``nousresearch/hermes-agent:latest``
#   无 pin，若锚点失配 —— 补丁脚本会**在构建时**非零退出（fail-loud）；但若有人
#   绕过构建（本地热改容器、回滚镜像 tag 回旧层），补丁会**静默消失**，消息回落
#   post。本测试因此不只验"哨兵字符串在文件里"，还验**行为**：假 client 捕获
#   发出的 msg_type，table markdown 必须走 interactive、纯文本必须 text、
#   >4 表必须回落 post、开关 off 必须 post、卡片被拒必须降级 post 重试。
#
#   兼容性断言（防未来上游变化）：_build_outbound_payload 签名不得变（edit_message
#   路径靠它保持 post）；补丁不得引入 allow_card 之类新参数。
#
# 依赖: 运行中的 hermes 容器，或本地 myopenclaw/hermes:latest 镜像（docker run 回退）
# 用法: bash tests/test-feishu-card-patch.sh
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

echo "=== Test: 飞书 markdown 卡片化补丁（静态 + 行为） ==="
echo

# 探测脚本：直接在容器里加载**部署中的** adapter，用假 client 跑 send()。
# 只写单引号安全的形式（整个赋值用单引号包裹，内部一律双引号）。
PYCODE='import asyncio, importlib.util, inspect, json, os, sys, tempfile, types

os.environ["HERMES_HOME"] = tempfile.mkdtemp()
os.environ.pop("FEISHU_MARKDOWN_CARD", None)
sys.path.insert(0, "/opt/hermes")

import plugins.platforms.feishu.adapter as A
from gateway.config import PlatformConfig

SRC_PATH = "/opt/hermes/plugins/platforms/feishu/adapter.py"
src = open(SRC_PATH, encoding="utf-8").read()
print("RESULT sentinel", 1 if "[myopenclaw] feishu markdown card patch" in src else 0)
print("RESULT no-allow-card", 1 if "allow_card" not in src else 0)
gw_src = open("/opt/hermes/gateway/run.py", encoding="utf-8").read()
print("RESULT topic-marker", 1 if "[myopenclaw] feishu topic reply anchor patch" in gw_src else 0)
_sig = inspect.signature(A.FeishuAdapter._build_outbound_payload)
print("RESULT outbound-signature", 1 if (list(_sig.parameters) == ["self", "content", "prefer_post"]
      and _sig.parameters["prefer_post"].kind == inspect.Parameter.KEYWORD_ONLY
      and _sig.parameters["prefer_post"].default is False) else 0)
print("RESULT retry-guard", 1 if "msg_type in (\"interactive\", \"post\")"
      in inspect.getsource(A.FeishuAdapter._feishu_send_with_retry) else 0)
print("RESULT funcs", 1 if (hasattr(A, "build_markdown_card_payload") and hasattr(A, "markdown_cards_enabled")) else 0)
print("RESULT regex-card", 1 if (A._POST_CONTENT_INVALID_RE.search("card table number over limit")
      and A._POST_CONTENT_INVALID_RE.search("content format of the card type is incorrect")
      and A._POST_CONTENT_INVALID_RE.search("content format of the post type is incorrect")) else 0)

try:
    A._load_lark_oapi()
except Exception:
    pass

TABLE = "| 表头 | 值 |\n|---|---|\n| A | 1 |"
FIVE = "\n\n".join(TABLE for _ in range(5))

class FakeResp:
    def __init__(self, ok=True, code=0, msg="success"):
        self._ok, self.code, self.msg = ok, code, msg
        self.data = types.SimpleNamespace(message_id="om_probe")
    def success(self):
        return self._ok

def _body(request):
    return getattr(request, "body", None) or getattr(request, "request_body", None)

class FakeAPI:
    def __init__(self):
        self.requests = []
        self.fail_on = None
    def create(self, request):
        b = _body(request)
        self.requests.append((b.msg_type, b.content))
        if self.fail_on == b.msg_type:
            self.fail_on = None
            return FakeResp(ok=False, code=230099, msg="card table number over limit")
        return FakeResp()

async def _direct(self, func, *args):
    return func(*args)

async def main():
    adapter = A.FeishuAdapter(PlatformConfig())
    api = FakeAPI()
    adapter._client = types.SimpleNamespace(im=types.SimpleNamespace(v1=types.SimpleNamespace(message=api)))
    adapter._run_blocking = types.MethodType(_direct, adapter)

    api.requests.clear()
    r = await adapter.send("oc_probe", "## 标题\n\n" + TABLE)
    mt, content = api.requests[0]
    print("RESULT send-card", 1 if (mt == "interactive" and r.success and r.message_id == "om_probe") else "0")
    try:
        card = json.loads(content)
        shape = card.get("schema") == "2.0" and card["body"]["elements"][0]["tag"] == "markdown"
    except Exception:
        shape = False
    print("RESULT card-shape", 1 if shape else 0)

    api.requests.clear()
    await adapter.send("oc_probe", "just plain text 你好")
    print("RESULT send-text", 1 if api.requests[0][0] == "text" else "0")

    api.requests.clear()
    await adapter.send("oc_probe", FIVE)
    print("RESULT send-post-5tables", 1 if api.requests[0][0] == "post" else "0")

    os.environ["FEISHU_MARKDOWN_CARD"] = "off"
    api.requests.clear()
    await adapter.send("oc_probe", "## T\n\n**bold**\n\n" + TABLE)
    print("RESULT kill-switch-off", 1 if api.requests[0][0] == "post" else "0")
    os.environ.pop("FEISHU_MARKDOWN_CARD")

    api.requests.clear()
    api.fail_on = "interactive"
    r = await adapter.send("oc_probe", "## 标题\n\n" + TABLE)
    types_seq = [m for m, _ in api.requests]
    print("RESULT fallback-chain", 1 if (types_seq == ["interactive", "post"] and r.success) else "0")

    mt, _ = adapter._build_outbound_payload("## 标题\n\n" + TABLE)
    print("RESULT outbound-not-cardified", 1 if mt == "post" else "0")

    from gateway.run import GatewayRunner
    from gateway.config import Platform
    class _Stub:
        _is_telegram_dm_topic_target = staticmethod(lambda *a, **k: False)
    md = GatewayRunner._thread_metadata_for_target(
        _Stub(), Platform.FEISHU, "oc_x", "omt_x", reply_to_message_id="om_msg")
    print("RESULT topic-anchor", 1 if md == {"thread_id": "omt_x", "reply_to_message_id": "om_msg"} else "0")
    md2 = GatewayRunner._thread_metadata_for_target(_Stub(), Platform.FEISHU, "oc_x", "omt_x")
    print("RESULT topic-no-anchor", 1 if md2 == {"thread_id": "omt_x"} else "0")

asyncio.run(main())
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

r() { grep -q "^RESULT $1 1$" <<< "${OUTPUT}"; }

# --- 静态：补丁存在且没有破坏性的结构改动 ---
check "哨兵注释在 adapter.py 里（补丁已应用）" 'r sentinel'
check "话题回复锚点已在 gateway 生效（标记）" 'r topic-marker'
check "未引入 allow_card 新参数（edit 路径语义不变）" 'r no-allow-card'
check "_build_outbound_payload 签名未变（edit_message 仍走 post/text）" 'r outbound-signature'
check "retry helper 已覆盖 interactive（内容错误立即降级）" 'r retry-guard'
check "卡片构造函数存在（build_markdown_card_payload / markdown_cards_enabled）" 'r funcs'
check "内容错误正则覆盖 post/card 两类报文" 'r regex-card'

# --- 行为：假 client 捕获真实发出的 msg_type ---
check "表格 markdown → 卡片（interactive）且发送成功" 'r send-card'
check "卡片 payload 是合法 Card JSON 2.0（markdown 组件）" 'r card-shape'
check "纯文本 → text（不被卡片化）" 'r send-text'
check "5 个表格 → 回落 post（>4 表超卡片限制）" 'r send-post-5tables'
check "FEISHU_MARKDOWN_CARD=off → 回落 post（kill switch）" 'r kill-switch-off'
check "卡片被 API 拒绝 → 降级 post 重试且最终成功" 'r fallback-chain'
check "edit 路径（_build_outbound_payload 直调）仍为 post" 'r outbound-not-cardified'
check "feishu 话题 metadata 带上 reply_to_message_id 锚点" 'r topic-anchor'
check "无锚点参数时 metadata 保持原样（不画蛇添足）" 'r topic-no-anchor'

echo
echo "结果: ${PASS} 通过, ${FAIL} 失败"
[[ ${FAIL} -eq 0 ]] || exit 1
