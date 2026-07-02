#!/usr/bin/env python3
"""
Morning Triage — Daily Command Center 飞书推送脚本
由 cc-connect cron (--exec) 触发，不依赖 cc-connect session→platform 解析。

流程：
  1. 读取 MyLoop 四个 ledger + projects.toml
  2. 按分类规则归类（Needs Decision / Today / Watch / Resolved）
  3. 生成飞书交互卡片（Markdown 格式）
  4. 通过飞书 Bot API 发送到用户

运行环境：claude-code 容器内
  - CC_CONNECT_FEISHU_APP_ID / CC_CONNECT_FEISHU_APP_SECRET 环境变量
  - 目标用户 open_id 硬编码（从 cc-connect session 文件提取）
"""

import os
import re
import sys
import json
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

# ── 配置 ──────────────────────────────────────────────────────────
MYLOOP_ROOT = "/home/node/code/myloop"
LEDGER_DIR = f"{MYLOOP_ROOT}/memory"
PROJECTS_FILE = f"{MYLOOP_ROOT}/configs/projects.toml"

# 从 cc-connect session 提取的目标用户 open_id
# feishu:oc_710194e84841147b1e16ee5d5eaac1e5:ou_dbaed85f08cfdd46a38a3a8c47d5fe9a
TARGET_OPEN_ID = "ou_dbaed85f08cfdd46a38a3a8c47d5fe9a"

# 飞书 API
FEISHU_AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"

# ── 飞书 API ──────────────────────────────────────────────────────

def get_tenant_token(app_id, app_secret):
    """获取 tenant_access_token"""
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(FEISHU_AUTH_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["tenant_access_token"]


def send_feishu_message(token, open_id, content):
    """发送飞书交互卡片消息"""
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"Daily Command Center — {date.today().strftime('%-m月%-d日')} {_weekday_cn()}"},
            "template": "blue"
        },
        "elements": [
            {"tag": "markdown", "content": content}
        ]
    }
    body = json.dumps({
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False)
    }).encode("utf-8")
    req = urllib.request.Request(FEISHU_MSG_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
    return result


def _weekday_cn():
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return days[date.today().weekday()]


# ── Ledger 解析 ───────────────────────────────────────────────────

def parse_ledger(filepath):
    """
    解析 ledger markdown 文件，返回 item 列表。
    每个 ## 标题开始一个新的 item，后续的 `- key: value` 行是属性。
    """
    items = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return items

    current = None
    for line in content.split("\n"):
        # 新的 item 标题
        if line.startswith("## "):
            if current and current.get("title"):
                items.append(current)
            current = {"title": line[3:].strip(), "raw_fields": {}}
        elif current and line.startswith("- ") and ": " in line:
            key_val = line[2:].strip()
            idx = key_val.index(": ")
            key = key_val[:idx].strip()
            val = key_val[idx+2:].strip()
            current["raw_fields"][key] = val
            # 映射到标准字段
            if key == "date":
                current["date"] = val
            elif key == "source":
                current["source"] = val
            elif key == "project":
                current["project"] = val
            elif key == "axis":
                current["axis"] = val
            elif key == "status":
                current["status"] = val
            elif key == "owner":
                current["owner"] = val
            elif key == "evidence":
                current["evidence"] = val
            elif key == "why_it_matters":
                current["why_it_matters"] = val
            elif key == "suggested_next_action":
                current["suggested_next_action"] = val
            elif key == "needs_human_decision":
                current["needs_human_decision"] = (val.lower() == "yes")

    if current and current.get("title"):
        items.append(current)

    return items


def is_meta_item(item):
    """判断 item 是否为讨论 myloop 自身的元数据"""
    title = item.get("title", "")
    evidence = item.get("evidence", "")
    why = item.get("why_it_matters", "")

    # 空内容 = 元数据
    if not evidence and not why:
        return True
    # 讨论 MVP / Daily Command Center 自身的
    meta_patterns = [
        "MVP", "Daily Command Center", "myloop",
        "元数据", "meta", "dry run", "implementation dry run",
        "manual-first", "report format"
    ]
    # 如果 title + evidence 全是元讨论，跳过
    text = f"{title} {evidence} {why}".lower()
    meta_count = sum(1 for p in meta_patterns if p.lower() in text)
    return meta_count >= 2


# ── 分类规则（对齐 SKILL.md §4） ────────────────────────────────────

def classify(items, today_str):
    """将 items 分类为 [needs_decision, today_candidates, watch, resolved]"""
    needs = []
    today_list = []
    watch = []
    resolved = []

    for item in items:
        status = item.get("status", "").lower()

        if status == "done":
            resolved.append(item)
            continue

        # 跳过元数据 item
        if is_meta_item(item):
            continue

        # Needs Human Decision
        if (
            status in ("blocked", "waiting_feedback")
            or item.get("needs_human_decision") is True
        ):
            needs.append(item)
            continue

        # Today Candidates: in_progress 或 ongoing
        if status in ("in_progress", "ongoing"):
            today_list.append(item)
            continue

        # Watch: 显式标记 watch 或未分类
        if status == "watch":
            watch.append(item)
            continue

        # 默认 → Watch
        watch.append(item)

    return needs, today_list, watch, resolved


# ── 报告生成 ──────────────────────────────────────────────────────

def format_item(item, idx=None):
    """格式化单个 item 为 Markdown 片段"""
    lines = []
    prefix = f"**{idx}.** " if idx else "**•** "
    lines.append(f"{prefix}**{item.get('title', '无标题')}**")

    parts = []
    if item.get("source"):
        parts.append(f"来源：{item['source']}")
    if item.get("project"):
        parts.append(f"项目：{item['project']}")
    if parts:
        lines.append("  " + " | ".join(parts))

    if item.get("why_it_matters"):
        lines.append(f"  ⚠️ {item['why_it_matters']}")

    if item.get("suggested_next_action"):
        lines.append(f"  💡 {item['suggested_next_action']}")

    if item.get("evidence"):
        lines.append(f"  📎 {item['evidence']}")

    return "\n".join(lines)


def generate_report(needs, today, watch, resolved, stats):
    """生成完整 Markdown 报告"""
    today_date = date.today()
    sections = []

    sections.append(f"📋 **Daily Command Center — {today_date.month}月{today_date.day}日 {_weekday_cn()}**")
    sections.append("")

    # ── Needs Human Decision ──
    sections.append("━━━━━━━━━━━━━━━━━━")
    sections.append(f"**⚡ 需要决策 ({len(needs)}项)**")
    sections.append("")
    if needs:
        for i, item in enumerate(needs, 1):
            sections.append(format_item(item, i))
            sections.append("")
    else:
        sections.append("无 ✅")
        sections.append("")

    # ── Today Candidates ──
    sections.append("━━━━━━━━━━━━━━━━━━")
    sections.append(f"**📋 今日关注 ({len(today)}项)**")
    sections.append("")
    if today:
        for i, item in enumerate(today, 1):
            sections.append(format_item(item, i))
            sections.append("")
    else:
        sections.append("无 ✅")
        sections.append("")

    # ── Watch ──
    sections.append("━━━━━━━━━━━━━━━━━━")
    sections.append(f"**🔭 观察 ({len(watch)}项)**")
    sections.append("")
    if watch:
        for item in watch:
            title = item.get("title", "无标题")
            why = item.get("why_it_matters", "")
            action = item.get("suggested_next_action", "")
            sections.append(f"• **{title}**")
            if why:
                sections.append(f"  {why}")
            if action:
                sections.append(f"  → {action}")
            sections.append("")
    else:
        sections.append("无 ✅")
        sections.append("")

    # ── Resolved ──
    sections.append("━━━━━━━━━━━━━━━━━━")
    sections.append(f"**✅ 已解决 ({len(resolved)}项)**")
    sections.append("")
    if resolved:
        for item in resolved:
            sections.append(f"• ~~{item.get('title', '无标题')}~~")
        sections.append("")
    else:
        sections.append("无")
        sections.append("")

    # ── 快照 ──
    sections.append("━━━━━━━━━━━━━━━━━━")
    sections.append(f"**📊 快照**")
    sections.append("")
    total = len(needs) + len(today) + len(watch) + len(resolved)
    sections.append(f"总 item：{total} | 需决策：{len(needs)} | 今日关注：{len(today)} | 观察：{len(watch)} | 已解决：{len(resolved)}")
    sections.append(f"AgentOps：{stats.get('agentops', '无数据')}")
    sections.append(f"数据面：{stats.get('data_gap', '无数据')}")
    sections.append(f"人员面：{stats.get('people_gap', '无数据')}")

    return "\n".join(sections)


# ── 汇总统计 ──────────────────────────────────────────────────────

def compute_stats(needs, today, watch, resolved, all_items):
    """计算快照统计"""
    # AgentOps 健康评估
    agentops_items = [i for i in all_items if i.get("project") == "myopenclaw" and i.get("axis") == "agentops"]
    p1_count = sum(1 for i in agentops_items if i.get("status") in ("ongoing", "blocked"))
    if p1_count >= 2:
        agentops_status = f"🔴 {p1_count}个活跃问题"
    elif p1_count == 1:
        agentops_status = f"🟡 {p1_count}个活跃问题"
    else:
        agentops_status = "🟢 正常"

    # 数据面
    data_items = [i for i in all_items if i.get("axis") in ("data", "literature", "model")]
    has_real_data = any(
        i.get("evidence") and "无真实" not in i.get("evidence", "")
        for i in data_items
    )
    data_status = "🟢 有真实信号" if has_real_data else "🔴 无真实项目信号（PC 端不可见）"

    # 人员面
    people_items = [i for i in all_items if i.get("axis") == "org"]
    has_real_people = any(
        i.get("evidence") and "无真实" not in i.get("evidence", "")
        for i in people_items
    )
    people_status = "🟢 有记录" if has_real_people else "🔴 无真实人员状态"

    return {
        "agentops": agentops_status,
        "data_gap": data_status,
        "people_gap": people_status,
    }


# ── 主流程 ────────────────────────────────────────────────────────

def main():
    app_id = os.environ.get("CC_CONNECT_FEISHU_APP_ID", "")
    app_secret = os.environ.get("CC_CONNECT_FEISHU_APP_SECRET", "")

    if not app_id or not app_secret:
        print("❌ 缺少 CC_CONNECT_FEISHU_APP_ID / CC_CONNECT_FEISHU_APP_SECRET 环境变量", file=sys.stderr)
        sys.exit(1)

    # 1. 读取 ledger
    ledgers = {
        "task": parse_ledger(f"{LEDGER_DIR}/task-ledger/inbox.md"),
        "data": parse_ledger(f"{LEDGER_DIR}/data-ledger/inbox.md"),
        "people": parse_ledger(f"{LEDGER_DIR}/people-ledger/inbox.md"),
        "agentops": parse_ledger(f"{LEDGER_DIR}/agentops-ledger/inbox.md"),
    }

    all_items = []
    for source, items in ledgers.items():
        for item in items:
            item["ledger_source"] = source
            if "source" not in item or not item["source"]:
                item["source"] = source
        all_items.extend(items)

    if not all_items:
        print("[SILENT] — 所有 ledger 无数据", file=sys.stderr)
        sys.exit(0)

    # 2. 分类
    today_str = date.today().isoformat()
    needs, today_list, watch, resolved = classify(all_items, today_str)

    # 3. 生成报告
    stats = compute_stats(needs, today_list, watch, resolved, all_items)
    report = generate_report(needs, today_list, watch, resolved, stats)

    # 4. 发送飞书
    print(f"🔑 获取飞书 tenant token...", file=sys.stderr)
    try:
        token = get_tenant_token(app_id, app_secret)
    except Exception as e:
        print(f"❌ 获取飞书 token 失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"📤 发送 Daily Command Center 报告到飞书...", file=sys.stderr)
    try:
        result = send_feishu_message(token, TARGET_OPEN_ID, report)
        code = result.get("code", -1)
        if code == 0:
            print(f"✅ 报告已发送到飞书", file=sys.stderr)
        else:
            print(f"❌ 飞书发送失败: code={code} msg={result.get('msg', 'unknown')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"❌ 发送飞书消息失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 输出报告摘要到 stdout（cc-connect 日志）
    total = len(needs) + len(today_list) + len(watch) + len(resolved)
    print(f"Daily Command Center — {today_str}: "
          f"total={total} needs={len(needs)} today={len(today_list)} "
          f"watch={len(watch)} resolved={len(resolved)}")


if __name__ == "__main__":
    main()
