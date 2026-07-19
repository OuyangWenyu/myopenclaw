#!/usr/bin/env python3
"""
AI News 周报推送 — 从 dailyinfo 周报生成并推送到飞书（Hermes 身份）。

流程:
  dailyinfo weekly recap .md  → DeepSeek 润色 → Hermes 飞书推送

替代原 cc-connect cron "AI News 周报润色+飞书推送" job。

Usage:
  python3 scripts/ai_news_weekly_push.py               # 正常推送
  python3 scripts/ai_news_weekly_push.py --dry-run     # 输出到 stdout
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DAILYINFO_BRIEFINGS = os.path.expanduser(
    os.environ.get(
        "DAILYINFO_BRIEFINGS_DIR",
        "~/.myagentdata/dailyinfo/briefings",
    )
)

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
)
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# Feishu — Hermes identity
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

POLISH_PROMPT = """
你是 AI News 周报润色编辑。以下是本周的 AI 领域新闻汇总原始草稿。

## 润色规则
1. 导读用 2-3 个具体数字切入（如"本周 47 篇顶会论文中，3 篇涉及 Agent 架构突破"）
2. 跨日事件要体现演化脉络（"某技术从周一的预印本到周五的正式发表"）
3. 冷门但重要的实体/技术加一句话背景（让读者知道为什么重要）
4. 消除 AI 套话（"delve into""showcasing""groundbreaking"等）
5. 保留所有原文中的链接和引用
6. 输出格式：适合飞书交互卡片的 Markdown，长度控制在 2000 字以内

## 原始草稿

{raw_content}

## 输出

请输出润色后的完整周报，格式为飞书交互卡片 Markdown。开头加一行 "🤖 AI News 周报 — {date_str}"。
"""

logger = logging.getLogger("ai-news-weekly")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)


# ── 文件查找 ─────────────────────────────────────────────────────


def find_latest_recap(weekly_dir: str) -> str | None:
    """Find the most recent weekly_recap_*.md file in the directory.

    Returns the path string of the newest recap, or None if none found.
    """
    d = Path(weekly_dir)
    if not d.is_dir():
        return None

    recaps = sorted(d.glob("weekly_recap_*.md"), reverse=True)
    if not recaps:
        return None

    # Prefer today's recap, then fall back to newest
    today_recap = f"weekly_recap_{date.today().isoformat()}.md"
    for r in recaps:
        if r.name == today_recap:
            return str(r)

    return str(recaps[0])


# ── LLM 润色 ─────────────────────────────────────────────────────


def polish_via_llm(raw_content: str) -> str | None:
    """Polish the weekly recap via DeepSeek LLM.

    Returns polished text or None if LLM is unavailable.
    """
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set, skipping polish")
        return None

    today = date.today()
    date_str = f"{today.month}月{today.day}日"

    prompt = POLISH_PROMPT.format(
        raw_content=raw_content[:15000],
        date_str=date_str,
    )

    try:
        body = json.dumps({
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请输出润色后的周报。"},
            ],
            "max_tokens": 3000,
            "temperature": 0.4,
        }).encode("utf-8")

        url = f"{DEEPSEEK_BASE_URL}/chat/completions"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {DEEPSEEK_API_KEY}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())

        polished = resp["choices"][0]["message"]["content"].strip()
        logger.info("LLM polish complete (%d chars)", len(polished))
        return polished

    except Exception as e:
        logger.warning("LLM polish failed: %s", e)
        return None


# ── 输出格式化 ──────────────────────────────────────────────────


def format_output(raw_content: str, polished: str | None, today: date) -> str:
    """Format the final output for Feishu push.

    Args:
        raw_content: Original weekly recap content
        polished: Polished version from LLM, or None
        today: Date for header
    """
    date_str = f"{today.month}月{today.day}日"

    if polished:
        # LLM output already includes the header per prompt instruction
        return polished

    # Fallback: raw content with minimal wrapper
    return (
        f"🤖 **AI News 周报 — {date_str}**\n\n"
        f"{raw_content[:2000]}"
    )


# ── 飞书推送 ─────────────────────────────────────────────────────


def get_tenant_token(app_id: str, app_secret: str) -> str:
    """Get Feishu tenant_access_token."""
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(FEISHU_AUTH_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["tenant_access_token"]


def build_feishu_card(content: str, today: date) -> dict:
    """Build Feishu interactive card JSON."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"AI News 周报 — {today.strftime('%-m月%-d日')}",
            },
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ],
    }


def send_feishu_message(token: str, open_id: str, card: dict) -> dict:
    """Send Feishu interactive card message."""
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


# ── 主流程 ────────────────────────────────────────────────────────


def main():
    dry_run = "--dry-run" in sys.argv

    logger.info("AI News 周报推送开始...")

    # 1. Find latest recap (files are in briefings/weekly/)
    recap_path = find_latest_recap(os.path.join(DAILYINFO_BRIEFINGS, "weekly"))
    if not recap_path:
        logger.error(
            "未找到周报文件 (weekly_recap_*.md) in %s/weekly/",
            DAILYINFO_BRIEFINGS,
        )
        sys.exit(1)

    logger.info("找到周报: %s", recap_path)
    raw_content = Path(recap_path).read_text()
    logger.info("  原始内容: %d chars", len(raw_content))

    # 2. Polish via LLM
    logger.info("LLM 润色中...")
    polished = polish_via_llm(raw_content)
    if polished:
        logger.info("  润色后: %d chars", len(polished))
    else:
        logger.info("  跳过润色，使用原始内容")

    # 3. Format output
    today = date.today()
    content = format_output(raw_content, polished, today)

    if dry_run:
        print(content)
        logger.info("DRY RUN — 未推送飞书")
        return

    # 4. Push to Feishu
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        logger.error("缺少 Feishu 凭证 (FEISHU_APP_ID/LARK_CLI_APP_ID)")
        sys.exit(1)

    logger.info("获取飞书 tenant token...")
    try:
        token = get_tenant_token(FEISHU_APP_ID, FEISHU_APP_SECRET)
    except Exception as e:
        logger.error("获取飞书 token 失败: %s", e)
        sys.exit(1)

    logger.info("推送飞书消息...")
    try:
        card = build_feishu_card(content, today)
        result = send_feishu_message(token, TARGET_OPEN_ID, card)
        code = result.get("code", -1)
        if code == 0:
            logger.info("✅ AI News 周报推送成功")
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
