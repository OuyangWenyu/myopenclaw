#!/usr/bin/env python3
"""
Morning Triage v2 — 记忆驱动的每日自动汇总脚本。

数据流:
  TDAI Gateway (localhost:8420) → L1 记忆搜索 + L2 场景召回
  collect_agentops.py          → 容器/备份/磁盘/网关 健康信号
  DeepSeek API                  → 自然语言摘要生成
  Feishu Bot API                → 交互卡片推送

运行环境: macOS 宿主机（需要 Docker socket 访问 + host 路径）
  - TDAI Gateway 通过 docker-compose 端口映射 localhost:8420
  - AgentOps 采集复用 scripts/collect_agentops.py
  - DEEPSEEK_API_KEY 从 .env 读取

Usage:
  python3 scripts/morning_triage_summary.py            # 正常推送
  python3 scripts/morning_triage_summary.py --dry-run  # 输出到 stdout
"""

import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# TDAI Gateway (port mapped to host via docker-compose)
GATEWAY_URL = os.environ.get(
    "TDAI_GATEWAY_URL", "http://localhost:8420"
)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
)
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# Feishu — Hermes identity (LARK_CLI credentials, same as Hermes bot)
FEISHU_APP_ID = os.environ.get(
    "FEISHU_APP_ID",
    os.environ.get("LARK_CLI_APP_ID", ""),
)
FEISHU_APP_SECRET = os.environ.get(
    "FEISHU_APP_SECRET",
    os.environ.get("LARK_CLI_APP_SECRET", ""),
)
FEISHU_AUTH_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
FEISHU_MSG_URL = (
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
)
TARGET_OPEN_ID = "ou_dbaed85f08cfdd46a38a3a8c47d5fe9a"

# Skill + manual override paths (relative to repo root)
SKILL_DIR = REPO_ROOT / "skills" / "morning-triage-v2"
MANUAL_OVERRIDE_PATH = SKILL_DIR / "manual-override.md"

MORNING_TRIAGE_PROMPT = """
你是用户的每日决策信息编辑。从以下原始数据生成一份 3-5 分钟可读完的飞书推送。

## 用户身份（Ground Truth — 以此为唯一锚点）

{persona_raw}

⚠️ 上述是用户本人的画像。记忆搜索可能混入论文作者、协作者、第三方人员的信息。
**绝不要把论文作者/第三方的属性当作用户的属性。**

## 数据

### AgentOps 系统健康
{agentops_raw}

### TDAI 记忆（L1 结构化事实）
{memory_raw}

### L2 活跃场景
{scenarios_raw}

## 规则
1. AgentOps 全绿时一句话带过"系统健康"，只展开异常信号
2. 记忆要点按重要性排列，3-5 条，每条 1-2 句。无数据时写"记忆数据积累中，暂无昨日增量"
3. **过滤规则（关键）**：
   - 论文下载/元数据修复/zotero 相关的事实 → 跳过（不是用户决策信息）
   - 涉及论文作者姓名（如 Fanxuan Zeng 等）的记忆 → 跳过（是论文协作者，非用户本人）
   - 只保留与用户（庄赖宏/OuyangWenyu/owen）直接相关的事实、决策、偏好、计划
4. L2 活跃场景列出当前上下文，无时省略此段
5. 不要编造任何信息——原始数据没有的就是没有
6. 输出纯文本，不要 Markdown 标题符号（##），用 emoji + 分段

## 输出格式
🟢 Daily Command Center — {{日期}} {{星期}}

━━━ 系统健康 ━━━
...

━━━ 昨日记忆 ━━━
...

━━━ 活跃场景 ━━━
...
"""

# 搜索关键词 — 覆盖常见交互主题
MEMORY_SEARCH_KEYWORDS = [
    "decision,made,决定",
    "偏好,preference,喜欢,不喜欢",
    "计划,plan,todo,待办,要做",
    "重要,important,关键",
    "发现,insight,注意到,观察到",
    "变更,change,修改,更新",
    "提醒,reminder,别忘了",
    "完成,done,解决,修复",
]

logger = logging.getLogger("morning-triage")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)


# ── TDAI Gateway 查询 ────────────────────────────────────────────


def _gateway_post(endpoint: str, body: dict, timeout: int = 10) -> dict:
    """POST to TDAI Gateway, return JSON response. Raises RuntimeError on failure."""
    url = f"{GATEWAY_URL}{endpoint}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        raise RuntimeError(f"Gateway unreachable at {url}: {e}") from None
    except json.JSONDecodeError:
        raise RuntimeError(f"Gateway returned non-JSON for {endpoint}") from None


def build_search_requests() -> list[dict]:
    """Build list of search requests to run against TDAI Gateway.

    Returns list of {"endpoint": str, "body": dict}.
    """
    requests = []
    for kw in MEMORY_SEARCH_KEYWORDS:
        requests.append({
            "endpoint": "/search/memories",
            "body": {"query": kw, "limit": 10},
        })
    # Also search conversations for broader coverage
    for kw in ["昨天", "今天", "最近"]:
        requests.append({
            "endpoint": "/search/conversations",
            "body": {"query": kw, "limit": 5},
        })
    # Recall L2 scenarios
    requests.append({
        "endpoint": "/recall",
        "body": {"query": "最近活动", "session_key": "personal_hermes"},
    })
    return requests


def search_memories_batch() -> list[str]:
    """Execute all search requests, returning deduplicated memory fact strings."""
    requests = build_search_requests()
    seen = set()
    facts = []

    for req in requests:
        try:
            result = _gateway_post(req["endpoint"], req["body"])
        except RuntimeError as e:
            logger.warning("Gateway query failed (%s): %s", req["endpoint"], e)
            continue

        text = _extract_text(result)
        if text and text not in seen:
            if "No matching" not in text and "暂无" not in text:
                seen.add(text)
                facts.append(text)

    return facts


def _extract_text(result: dict) -> str:
    """Extract meaningful text from a Gateway search response."""
    if not isinstance(result, dict):
        return ""

    # /recall response
    if "context" in result and result.get("context"):
        return str(result["context"])

    # /search/* response
    results = result.get("results", "")
    if isinstance(results, str) and results.strip():
        return results.strip()

    return ""


# ── AgentOps 采集（复用 collect_agentops.py）────────────────────


def collect_agentops_signals() -> list[dict]:
    """Collect AgentOps health signals using the existing collect_agentops module.

    Mirrors: scripts/collect_agentops.py:494 (collect_all_signals)
    """
    try:
        import collect_agentops as ca
        items = ca.collect_all_signals()
        return [
            {
                "title": item["title"],
                "status": item.get("status", "watch"),
                "evidence": item.get("evidence", ""),
                "why_it_matters": item.get("why_it_matters", ""),
                "suggested_next_action": item.get("suggested_next_action", ""),
            }
            for item in items
        ]
    except ImportError as e:
        logger.warning("Cannot import collect_agentops: %s", e)
        return []
    except Exception as e:
        logger.warning("AgentOps collection failed: %s", e)
        return []


def collect_agentops_signals_safe() -> list[dict]:
    """Safe wrapper that never raises."""
    try:
        return collect_agentops_signals()
    except Exception as e:
        logger.warning("AgentOps collection unexpected error: %s", e)
        return []


# ── 报告生成 ──────────────────────────────────────────────────────


def _weekday_cn(d: date) -> str:
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return days[d.weekday()]


def generate_report(
    agentops_signals: list[dict],
    memory_items: list[str],
    scenarios: list[str],
    manual_override: str,
) -> str:
    """Generate the three-section Markdown report.

    Args:
        agentops_signals: List of AgentOps signal dicts
        memory_items: List of memory fact strings from TDAI
        scenarios: List of active L2 scenario names
        manual_override: Content of manual-override.md, or ""
    """
    today = date.today()
    sections = []

    # Header
    sections.append(
        f"🟢 **Daily Command Center — {today.month}月{today.day}日 {_weekday_cn(today)}**"
    )
    sections.append("")

    # ── 系统健康 ──
    sections.append("━━━ 系统健康 ━━━")
    sections.append("")
    if agentops_signals:
        for s in agentops_signals:
            sections.append(f"• **{s['title']}**")
            if s.get("why_it_matters"):
                sections.append(f"  {s['why_it_matters']}")
            if s.get("suggested_next_action"):
                sections.append(f"  → {s['suggested_next_action']}")
            sections.append("")
    else:
        sections.append("✅ 所有服务正常运行")
        sections.append("")
    sections.append("")

    # ── 昨日记忆 ──
    sections.append("━━━ 昨日记忆 ━━━")
    sections.append("")
    if memory_items:
        for item in memory_items[:5]:
            text = item[:300] + "..." if len(item) > 300 else item
            sections.append(f"• {text}")
            sections.append("")
    else:
        sections.append("📝 记忆数据积累中，暂无昨日增量")
        sections.append("")
    sections.append("")

    # ── 活跃场景 ──
    sections.append("━━━ 活跃场景 ━━━")
    sections.append("")
    if scenarios:
        for s in scenarios:
            sections.append(f"• {s}")
            sections.append("")
    else:
        sections.append("—")
        sections.append("")

    # ── 手动备注 ──
    if manual_override.strip():
        sections.append("")
        sections.append("━━━ 手动备注 ━━━")
        sections.append("")
        sections.append(manual_override.strip())
        sections.append("")

    return "\n".join(sections)


# ── 飞书推送 ─────────────────────────────────────────────────────


def build_feishu_card(content: str, today: date) -> dict:
    """Build Feishu interactive card JSON."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"Daily Command Center — {today.strftime('%-m月%-d日')} {_weekday_cn(today)}",
            },
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ],
    }


def get_tenant_token(app_id: str, app_secret: str) -> str:
    """Get Feishu tenant_access_token."""
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(FEISHU_AUTH_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["tenant_access_token"]


def send_feishu_message(token: str, open_id: str, card: dict) -> dict:
    """Send Feishu interactive card message. Returns API response."""
    body = json.dumps({
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }).encode("utf-8")
    req = urllib.request.Request(FEISHU_MSG_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


# ── LLM 汇总 ─────────────────────────────────────────────────────


def summarize_with_llm(
    agentops_signals: list[dict],
    memory_items: list[str],
    scenarios: list[str],
    persona: str,
) -> str:
    """Use DeepSeek to generate a natural language summary of the raw data.

    Falls back to generate_report() if LLM is unavailable.
    """
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set, using template-based report")
        return generate_report(agentops_signals, memory_items, scenarios, "")

    today = date.today()

    agentops_raw = "正常运行" if not agentops_signals else "\n".join(
        f"- {s['title']}: {s.get('why_it_matters', '')}" for s in agentops_signals
    )
    memory_raw = "暂无" if not memory_items else "\n".join(
        f"- {m[:200]}" for m in memory_items[:10]
    )
    scenarios_raw = "暂无" if not scenarios else "\n".join(f"- {s}" for s in scenarios)
    persona_raw = persona[:2000] if persona else "（用户画像尚未生成，请仅根据记忆事实判断哪些与用户本人相关）"

    prompt = MORNING_TRIAGE_PROMPT.format(
        agentops_raw=agentops_raw,
        memory_raw=memory_raw,
        scenarios_raw=scenarios_raw,
        persona_raw=persona_raw,
    ).replace("{{日期}}", f"{today.month}月{today.day}日").replace(
        "{{星期}}", _weekday_cn(today)
    )

    try:
        body = json.dumps({
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请根据上述数据生成今日推送。"},
            ],
            "max_tokens": 1200,
            "temperature": 0.3,
        }).encode("utf-8")

        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {DEEPSEEK_API_KEY}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())

        summary = resp["choices"][0]["message"]["content"].strip()
        logger.info("LLM summary generated (%d chars)", len(summary))
        return summary

    except Exception as e:
        logger.warning("LLM summary failed, falling back to template: %s", e)
        return generate_report(agentops_signals, memory_items, scenarios, "")


# ── 主流程 ────────────────────────────────────────────────────────


def read_manual_override() -> str:
    """Read manual-override.md, stripping HTML comments and blank lines."""
    try:
        if MANUAL_OVERRIDE_PATH.is_file():
            raw = MANUAL_OVERRIDE_PATH.read_text()
            # Strip HTML comments
            cleaned = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
            # Strip leading/trailing whitespace per line
            lines = [l.rstrip() for l in cleaned.split("\n")]
            # Remove leading blank lines and trailing blank lines
            while lines and not lines[0].strip():
                lines.pop(0)
            while lines and not lines[-1].strip():
                lines.pop()
            content = "\n".join(lines).strip()
            if content:
                return content
    except OSError:
        pass
    return ""


def main():
    dry_run = "--dry-run" in sys.argv

    logger.info("Morning Triage v2 开始采集...")

    # 1. Query TDAI Memory
    logger.info("查询 TDAI Memory Gateway (%s)...", GATEWAY_URL)
    memory_items = search_memories_batch()
    logger.info("  记忆事实: %d 条", len(memory_items))

    # 2. Recall L2 scenarios (includes persona profile)
    scenarios = []
    persona_text = ""
    try:
        recall = _gateway_post("/recall", {"query": "最近活动", "session_key": "personal_hermes"})
        ctx = _extract_text(recall)
        if ctx and "No matching" not in ctx:
            # /recall returns composite context — first chunk is usually persona
            if "<user-persona>" in ctx:
                parts = ctx.split("<user-persona>", 1)
                persona_text = ("<user-persona>" + parts[1])[:3000] if len(parts) > 1 else ""
                if persona_text:
                    logger.info("  persona: %d chars", len(persona_text))
            else:
                scenarios.append(ctx[:500])
    except RuntimeError as e:
        logger.warning("L2 recall failed: %s", e)

    logger.info("  活跃场景: %d 个", len(scenarios))

    # 3. AgentOps health (imports collect_agentops.py — host-side execution)
    logger.info("采集 AgentOps 健康信号...")
    agentops_signals = collect_agentops_signals_safe()
    logger.info("  异常信号: %d 个", len(agentops_signals))
    for s in agentops_signals:
        logger.info("    %s: %s", s["title"], s.get("why_it_matters", ""))

    # 4. Read manual override
    manual_override = read_manual_override()
    if manual_override:
        logger.info("  手动备注: %d 字符", len(manual_override))

    # 5. Generate report (LLM with template fallback)
    logger.info("生成汇总报告...")
    report = summarize_with_llm(agentops_signals, memory_items, scenarios, persona_text)

    # Append manual override to output
    if manual_override.strip():
        report += f"\n\n━━━ 手动备注 ━━━\n\n{manual_override.strip()}"

    if dry_run:
        print(report)
        logger.info("DRY RUN — 未推送飞书")
        return

    # 6. Push to Feishu
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        logger.error(
            "缺少 FEISHU_APP_ID / FEISHU_APP_SECRET / LARK_CLI_APP_ID "
            "环境变量，无法推送（需要 Hermes 飞书应用凭证）"
        )
        sys.exit(1)

    logger.info("获取飞书 tenant token...")
    try:
        token = get_tenant_token(FEISHU_APP_ID, FEISHU_APP_SECRET)
    except Exception as e:
        logger.error("获取飞书 token 失败: %s", e)
        sys.exit(1)

    logger.info("推送飞书消息...")
    try:
        today = date.today()
        card = build_feishu_card(report, today)
        result = send_feishu_message(token, TARGET_OPEN_ID, card)
        code = result.get("code", -1)
        if code == 0:
            logger.info("✅ 推送成功")
        else:
            logger.error(
                "飞书推送失败: code=%s msg=%s",
                code,
                result.get("msg", "unknown"),
            )
            sys.exit(1)
    except Exception as e:
        logger.error("飞书推送异常: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
