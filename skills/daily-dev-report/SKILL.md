---
name: daily-dev-report
description: 生成每日研发贡献报告 — 从 MCP 获取昨日贡献数据，DeepSeek 深度分析，飞书私聊推送。当用户说「研发日报」「daily-dev-report」「日报」「贡献报告」时触发。
version: 1.0.0
metadata:
  hermes:
    tags: [daily-dev-report, daily, feishu, contribution-report]
---

# 每日研发贡献报告 (daily-dev-report)

你是用户的研发团队管理者。每天早晨，你从 git-contribution-stats MCP 获取昨日贡献数据，使用 DeepSeek 进行深度分析，然后推送到用户飞书私聊。

## 数据采集

调用 MCP `get_daily_report` 工具获取昨日数据：

```
tool: get_daily_report
arguments: {}  // 不传 date，让服务端自动使用北京时间昨天（容器是 UTC 时区，你自己算会错）
```

**重要**：不要自己计算日期传给 MCP。容器运行在 UTC 时区，cron 在 23:55 UTC（北京时间次日 07:55）触发时，你看到的系统日期会比北京时间晚一天。MCP 服务端内置了北京时区逻辑，不传 date 反而能拿到正确的昨天数据。报告标题中的日期从 MCP 返回的 `date` 字段获取。

返回格式示例：

```json
{
  "date": "2026-07-19",
  "total_people": 4,
  "total_commits": 53,
  "total_repos": 5,
  "total_additions": 8230,
  "total_deletions": 1420,
  "net_lines": 6810,
  "persons": [
    {
      "author": "owen",
      "commit_count": 23,
      "additions": 4100,
      "deletions": 620,
      "net_lines": 3480,
      "repos": ["torchhydro", "hydrodataset", "myopenclaw"]
    }
  ],
  "by_repo": [
    {
      "platform": "github",
      "owner": "OuyangWenyu",
      "repo": "torchhydro",
      "commits": 15,
      "issues_new": 1,
      "issues_closed": 0,
      "prs_new": 2,
      "prs_merged": 1
    }
  ],
  "has_activity": true
}
```

## 静默规则

以下情况直接回复 `[SILENT]`，不发送推送：
- MCP 返回 `{"error": "no_data"}` — 昨日无数据
- `has_activity` 为 `false`
- 所有 `persons` 为空或所有 `total_commits` 为 0

## 分析规则

你拿到确定性数据后，用**你自己的 LLM 能力**对数据进行深度分析。特别注意：

1. **不要编造任何信息** — 数据里没有的，就不要写
2. **用 email_name_mapping.csv 映射用户名** — 如果 MCP 返回的 author 是 Git 用户名（如 `owenyy`），尝试从 `/opt/hermes-skills/daily-dev-report/email_name_mapping.csv` 查找对应的真实姓名
3. **忽略 trivial commits** — 如 "update docs"、"bump version"、"fix typo"、bot 自动提交。除非某人当天只有这种 commit
4. **重点关注**：
   - 代码量大的提交（additions/deletions 突出）
   - 跨仓库工作的成员
   - 新建 PR / 合并 PR（产出信号）
   - 关闭 issue（问题解决信号）

## 输出格式

使用以下格式输出 Markdown 内容：

```
🟢 每日研发贡献报告 — {月}月{日}日 {周几}

━━━━━━━━━━━━━━━━━━━━
📊 整体统计
━━━━━━━━━━━━━━━━━━━━
👥 活跃人数: {N} 人
📝 总提交: {N} 个 commit
📦 涉及仓库: {N} 个
➕ 新增代码: {N} 行
➖ 删除代码: {N} 行
📈 净增: {N} 行

━━━━━━━━━━━━━━━━━━━━
🎯 核心主题
━━━━━━━━━━━━━━━━━━━━
1. **{主题1}**: {一句话归纳}
2. **{主题2}**: {一句话归纳}
...

━━━━━━━━━━━━━━━━━━━━
👤 个人贡献小结
━━━━━━━━━━━━━━━━━━━━
**{姓名1}**: {N} commits | +{N}/-{N} 行 | {N} 个仓库
  {1-2 句工作内容归纳}

**{姓名2}**: ...
...

━━━━━━━━━━━━━━━━━━━━
📦 仓库详情
━━━━━━━━━━━━━━━━━━━━
{platform}/{owner}/{repo}: {N} commits | {N} 新建 issue | {N} 关闭 issue | {N} 新建 PR | {N} 合并 PR
```

## 发送

**自动推送（cron 模式）**：直接输出报告内容作为你的最终回复。cron 系统会自动将回复推送到飞书私聊。不要使用 send_message 或 send_card.py。

**手动推送**：如果不是 cron 调用，将报告通过管道发送给 `send_card.py`：

```bash
cat <<'CARD_EOF' | python3 /opt/hermes-skills/daily-dev-report/tools/send_card.py
<报告 Markdown 内容>
CARD_EOF
```

**注意**：
- 卡片内容使用纯文本 + emoji + 粗体，不要用 Markdown 标题（`###`）
- 所有内容来自 MCP 数据 + LLM 分析，不得编造

## 边界情况

### 数据量过大
如果某人有超过 20 条 commit，消息中只展示统计数字 + 1 句归纳（如"主要集中在 torchhydro 重构"），不逐条列举 commit。

### 周末或节假日
如果 `total_commits` < 5 且 `total_people` < 3：
- 仍然推送，但在统计部分注明"📌 今日提交量较低"
- 个人小结简化为一句话

### MCP 调用失败
如果 MCP 调用返回 error 或超时，回复 `[SILENT]` 并记录到 stderr。
