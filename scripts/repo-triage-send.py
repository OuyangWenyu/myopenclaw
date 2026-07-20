#!/usr/bin/env python3
"""
仓库动态自动推送 — 从 SQLite 读取每日仓库活动，LLM 摘要 + 飞书推送。

数据流:
  collect-repos.py (launchd 7:45) → SQLite
  repo-triage-send.py (launchd 7:55) → import repo_summary → DeepSeek → Feishu

运行环境: macOS 宿主机（需要 .env 中的 DEEPSEEK_API_KEY + 飞书凭证）

Usage:
  python3 scripts/repo-triage-send.py            # 正常推送
  python3 scripts/repo-triage-send.py --dry-run  # 输出到 stdout，不推送
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import date

# ── Configuration ──────────────────────────────────────────────────────────

from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

# DeepSeek LLM — env vars read at call time so tests can monkeypatch (see summarize_with_llm)

# Feishu credentials — same fallback priority as morning_triage_summary.py
# Priority: explicit FEISHU_APP_ID > CC_CONNECT (cc-connect bot) > LARK_CLI (Hermes bot)
FEISHU_APP_ID = os.environ.get(
    "FEISHU_APP_ID",
    os.environ.get("CC_CONNECT_FEISHU_APP_ID",
        os.environ.get("LARK_CLI_APP_ID", "")),
)
FEISHU_APP_SECRET = os.environ.get(
    "FEISHU_APP_SECRET",
    os.environ.get("CC_CONNECT_FEISHU_APP_SECRET",
        os.environ.get("LARK_CLI_APP_SECRET", "")),
)
FEISHU_AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
# Hermes 应用下庄赖宏的 open_id（私聊，从 .env 读取）
TARGET_OPEN_ID = os.environ["LARK_USER_OPEN_ID"]

logger = logging.getLogger("repo-triage")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _weekday_cn(d: date) -> str:
    """Return Chinese weekday string for the given date."""
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return days[d.weekday()]


def _repo_key(repo: dict) -> str:
    """Return a display key for a repo dict."""
    return f"{repo['platform']}/{repo['owner']}/{repo['repo']}"


def _truncate_msg(msg: str, max_len: int = 80) -> str:
    """Truncate a commit/PR message to max_len, appending '...' if truncated."""
    return msg[:max_len] + ("..." if len(msg) > max_len else "")


# ── LLM prompt builder ─────────────────────────────────────────────────────


def format_activity_for_prompt(repos: list[dict]) -> str:
    """Convert structured summary data into flat text for the LLM prompt.

    Args:
        repos: list of per-repo dicts from build_summary().

    Returns:
        str ready for embedding in a system prompt, or "" if no activity.
    """
    lines = []
    for repo in repos:
        parts: list[str] = []
        if repo["commits"]:
            msgs = [_truncate_msg(c["message"])
                    for c in repo["commits"]]
            parts.append(f"  Commits ({len(repo['commits'])}): {', '.join(msgs)}")
        if repo["new_issues"]:
            titles = [f"#{i['number']} {i['title']}" for i in repo["new_issues"]]
            parts.append(f"  New Issues ({len(repo['new_issues'])}): {', '.join(titles)}")
        if repo["closed_issues"]:
            titles = [f"#{i['number']} {i['title']}" for i in repo["closed_issues"]]
            parts.append(f"  Closed Issues ({len(repo['closed_issues'])}): {', '.join(titles)}")
        if repo["new_prs"]:
            titles = [f"#{p['number']} {p['title']}" for p in repo["new_prs"]]
            parts.append(f"  New PRs ({len(repo['new_prs'])}): {', '.join(titles)}")
        if repo["merged_prs"]:
            titles = [f"#{p['number']} {p['title']}" for p in repo["merged_prs"]]
            parts.append(f"  Merged PRs ({len(repo['merged_prs'])}): {', '.join(titles)}")

        if not parts:
            continue

        lines.append(f"## {_repo_key(repo)}")
        lines.extend(parts)
        lines.append("")

    return "\n".join(lines)


# ── Template-based report (LLM fallback) ───────────────────────────────────


def format_template_report(summary: dict | None) -> str:
    """Deterministic template-based report when LLM is unavailable.

    Args:
        summary: dict from build_summary() or None.

    Returns:
        str: formatted report text.
    """
    if summary is None or not summary.get("has_activity"):
        return "📭 无仓库活动"

    today = date.today()
    lines = [
        f"🟢 仓库动态 — {today.month}月{today.day}日 {_weekday_cn(today)}",
        "",
    ]

    for repo in summary.get("repos", []):
        activity_lines: list[str] = []
        if repo["commits"]:
            msgs = [_truncate_msg(c["message"])
                    for c in repo["commits"]]
            activity_lines.append(f"  📝 {len(repo['commits'])} commits: {', '.join(msgs)}")
        if repo["new_issues"]:
            titles = [f"#{i['number']} {i['title']}" for i in repo["new_issues"]]
            activity_lines.append(f"  🆕 {len(repo['new_issues'])} new issues: {', '.join(titles)}")
        if repo["closed_issues"]:
            titles = [f"#{i['number']} {i['title']}" for i in repo["closed_issues"]]
            activity_lines.append(f"  🔒 {len(repo['closed_issues'])} closed issues: {', '.join(titles)}")
        if repo["new_prs"]:
            titles = [f"#{p['number']} {p['title']}" for p in repo["new_prs"]]
            activity_lines.append(f"  🔀 {len(repo['new_prs'])} new PRs: {', '.join(titles)}")
        if repo["merged_prs"]:
            titles = [f"#{p['number']} {p['title']}" for p in repo["merged_prs"]]
            activity_lines.append(f"  ✅ {len(repo['merged_prs'])} merged PRs: {', '.join(titles)}")

        if not activity_lines:
            continue

        lines.append(f"📦 {_repo_key(repo)}")
        lines.extend(activity_lines)
        lines.append("")

    totals = summary.get("totals", {})
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"📊 总计: {totals.get('repos_scanned', 0)} 仓库 | "
        f"{totals.get('total_commits', 0)} commits | "
        f"{totals.get('total_new_issues', 0)} 新建 issue | "
        f"{totals.get('total_closed_issues', 0)} 关闭 issue | "
        f"{totals.get('total_new_prs', 0)} 新建 PR | "
        f"{totals.get('total_merged_prs', 0)} 合并 PR"
    )

    return "\n".join(lines)


# ── Feishu card builder ────────────────────────────────────────────────────


def build_feishu_card(content: str, today: date) -> dict[str, object]:
    """Build a Feishu interactive card JSON.

    Args:
        content: Markdown report body.
        today: Date to put in the card header.

    Returns:
        dict: Feishu card structure.
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"仓库动态 — {today.month}月{today.day}日 {_weekday_cn(today)}",
            },
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ],
    }


# ── DeepSeek LLM summarizer ────────────────────────────────────────────────

REPO_TRIAGE_PROMPT = """你是用户的代码仓库动态助手。根据以下仓库活动原始数据，生成一段 2-3 分钟可读完的中文摘要。

## 数据

{activity_raw}

## 规则
1. 按仓库分组，每个仓库 1-3 句，用 emoji 作为视觉分隔符
2. 重点突出以下内容：
   - 与用户本人（庄赖宏 / OuyangWenyu / owen / iHeadWater）相关的 commits/PRs/Issues（作者匹配）
   - 社区重要动态（高 stars 仓库的 breaking changes、安全修复）
   - 被合并的 PR（已完成的进展）
3. 忽略无关紧要的 commits（如 "update docs"、"bump version"、bot 自动提交），除非当天只有这些
4. 如果某个仓库没有任何活动，不需要提及
5. 输出纯文本，不要 Markdown 标题符号（##），用 emoji + 分段
6. 不要编造任何信息——原始数据没有的就是没有

## 输出格式
🟢 仓库动态 — {date_info}

📦 [仓库名]
  📝 commits 摘要...
  🆕 新建 issues...
  🔒 关闭 issues...
  🔀 新建 PRs...
  ✅ 合并 PRs...

━━━━━━━━━━━━━━━━━━
📊 总计: [一句话统计]"""


def summarize_with_llm(summary: dict) -> str:
    """Generate a natural-language Chinese summary via DeepSeek API.

    Falls back to format_template_report() if the LLM is unavailable.

    Env vars are read at call time (not module load) so tests can monkeypatch.

    Args:
        summary: dict from build_summary().

    Returns:
        str: natural language summary or template-based report.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        logger.warning("DEEPSEEK_API_KEY not set, using template-based report")
        return format_template_report(summary)

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    activity_raw = format_activity_for_prompt(summary.get("repos", []))

    if not activity_raw:
        return format_template_report(summary)

    today = date.today()
    date_display = f"{today.month}月{today.day}日 {_weekday_cn(today)}"
    prompt = REPO_TRIAGE_PROMPT.format(
        activity_raw=activity_raw,
        date_info=date_display,
    )

    try:
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "请根据上述仓库数据生成今日推送。"},
            ],
            "max_tokens": 800,
            "temperature": 0.3,
        }).encode("utf-8")

        url = f"{base_url.rstrip('/')}/chat/completions"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())

        content = resp["choices"][0]["message"]["content"]
        summary_text = (content or "").strip()
        logger.info("LLM summary generated (%d chars)", len(summary_text))
        if not summary_text:
            logger.warning("LLM returned empty content, falling back to template")
            return format_template_report(summary)
        return summary_text

    except Exception as e:
        logger.warning("LLM summary failed, falling back to template: %s", e)
        return format_template_report(summary)


# ── Feishu API helpers ─────────────────────────────────────────────────────


def get_tenant_token(app_id: str, app_secret: str) -> str:
    """Get Feishu tenant_access_token."""
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(FEISHU_AUTH_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["tenant_access_token"]


def send_feishu_message(token: str, open_id: str, card: dict[str, object]) -> dict[str, object]:
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


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    # 1. Build summary from SQLite
    logger.info("读取仓库活动数据...")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "repo_summary",
            os.path.join(REPO_ROOT, "scripts", "repo-summary.py"),
        )
        repo_summary = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(repo_summary)
    except Exception:
        logger.error("无法导入 repo-summary.py，请确认 scripts/repo-summary.py 存在")
        sys.exit(1)

    # Support --date YYYY-MM-DD override (default: today)
    date_str = None
    for i, arg in enumerate(sys.argv):
        if arg == "--date" and i + 1 < len(sys.argv):
            date_str = sys.argv[i + 1]
            break
    if date_str is None:
        today = date.today()
        date_str = today.strftime("%Y-%m-%d")
    else:
        from datetime import datetime
        today = datetime.strptime(date_str, "%Y-%m-%d").date()
    summary = repo_summary.build_summary(date_str)

    if summary is None:
        logger.info("[SILENT] 数据库不存在，跳过（需先运行 collect-repos.py 采集数据）")
        sys.exit(0)

    if not summary.get("has_activity"):
        logger.info("[SILENT] 今日无仓库活动")
        sys.exit(0)

    logger.info(
        "仓库: %d | Commits: %d | Issues: %d新建/%d关闭 | PRs: %d新建/%d合并",
        summary["totals"]["repos_scanned"],
        summary["totals"]["total_commits"],
        summary["totals"]["total_new_issues"],
        summary["totals"]["total_closed_issues"],
        summary["totals"]["total_new_prs"],
        summary["totals"]["total_merged_prs"],
    )

    # 2. Generate report (LLM with template fallback)
    logger.info("生成摘要...")
    report = summarize_with_llm(summary)

    if dry_run:
        print(report)
        logger.info("DRY RUN — 未推送飞书")
        return

    # 3. Push to Feishu
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        logger.error("缺少 CC_CONNECT_FEISHU_APP_ID / CC_CONNECT_FEISHU_APP_SECRET 环境变量，无法推送")
        sys.exit(1)

    logger.info("获取飞书 tenant token...")
    try:
        token = get_tenant_token(FEISHU_APP_ID, FEISHU_APP_SECRET)
    except Exception as e:
        logger.error("获取飞书 token 失败: %s", e)
        sys.exit(1)

    logger.info("推送飞书消息...")
    try:
        card = build_feishu_card(report, today)
        result = send_feishu_message(token, TARGET_OPEN_ID, card)
        code = result.get("code", -1)
        if code == 0:
            logger.info("推送成功")
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
