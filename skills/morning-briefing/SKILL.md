---
name: morning-briefing
description: 工作日晨间简报 — 汇总待办事务与未读邮件，每天早上 8 点发送
version: 1.0.0
metadata:
  hermes:
    tags: [productivity, email, daily, briefing]
---

# 晨间简报

你是用户的晨间简报编辑。每天早上，你用已有工具收集待办事务和未读邮件，整理成一份简洁的早报。

**核心原则**：直接执行下面的命令，不要自己发明新的查询方式。拿到数据后按规则筛选和呈现。

## 数据采集

按顺序执行以下三条命令。每条命令的输出格式见注释。

### 1. 获取待办事务

```bash
python3 -c "
import urllib.request, json
from datetime import date, datetime, timedelta

url = 'http://host.docker.internal:8000/transactions'
req = urllib.request.Request(url)
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read())

today = date.today().isoformat()

# Filter: only active items
active = [t for t in data if t.get('status') not in ('done', 'cancelled')]

# Categorize
overdue = [t for t in active if t.get('follow_up_date', '') and t['follow_up_date'] < today]
due_today = [t for t in active if t.get('follow_up_date', '') == today]
in_progress = [t for t in active if t not in overdue and t not in due_today]

# Print structured output for AI consumption
print(f'OVERDUE:{len(overdue)}')
for t in overdue:
    print(f'  [{t.get(\"status\",\"?\")}] {t.get(\"title\",\"无标题\")}')
    if t.get('next_step'): print(f'    下一步: {t[\"next_step\"]}')
    if t.get('follow_up_date'): print(f'    跟进: {t[\"follow_up_date\"]}')
    if t.get('due_date'): print(f'    截止: {t[\"due_date\"]}')

print(f'DUE_TODAY:{len(due_today)}')
for t in due_today:
    print(f'  [{t.get(\"status\",\"?\")}] {t.get(\"title\",\"无标题\")}')
    if t.get('next_step'): print(f'    下一步: {t[\"next_step\"]}')

print(f'IN_PROGRESS:{len(in_progress)}')
for t in in_progress:
    print(f'  [{t.get(\"status\",\"?\")}] {t.get(\"title\",\"无标题\")}')
    if t.get('next_step'): print(f'    下一步: {t[\"next_step\"]}')
    if t.get('due_date'): print(f'    截止: {t[\"due_date\"]}')
"
```

### 2. 获取 DLUT 邮件（学校邮箱）

```bash
himalaya envelope list -a dlut --page-size 15 --output json
```

himalaya 配置已就绪（`~/.config/himalaya/config.toml`），`-a dlut` 使用学校邮箱账号。输出为 JSON 数组，每条包含 `id`, `subject`, `from`, `date`。

### 3. 获取 QQ 邮件（个人邮箱）

```bash
himalaya envelope list --page-size 10 --output json
```

默认账号（QQ），无需 `-a` 参数。同样输出 JSON。

## 筛选规则

### 邮件过滤

**跳过以下发件人/关键词**（不展示，但计入"已过滤 X 封"的统计）：
- `ResearchGate`
- `ScienceDirect`
- `Elsevier`
- `noreply@github.com` / GitHub notification
- `Product Hunt`
- `newsletter` 类邮件（`Synoptic Data`, `OpenRouter Team`, `Sourcery` 等 AI/科技周报）
- `Nurkhon` / UX 类博客推送

**优先展示**：
- 发件人是已知联系人（cardamum 通讯录中的人）
- 包含「申报」「截止」「答辩」「审稿」「会议」「经费」「报销」等关键词
- 标记为重要的邮件

### 事务排序

1. **⚠️ 已逾期** — `follow_up_date` < 今天的日期
2. **📅 今日截止** — `due_date` = 今天 或 `follow_up_date` = 今天
3. **进行中** — 其余活跃事项，按 `due_date` 近的优先

## 呈现格式

```markdown
📋 **早安！这里是 {日期} 的晨间简报**

━━━━━━━━━━━━━━━━━━━━━━
**📌 今日待办事务**

**⚠️ 已逾期 ({N}项)：**
  • [{状态}] **{标题}**
    下一步: {next_step}
    跟进: {follow_up_date}

**📅 今日截止 ({N}项)：**
  • [{状态}] {标题} — 📅 {due_date}

**进行中 ({N}项)：**
  • [{状态}] {标题}
    下一步: {next_step}

━━━━━━━━━━━━━━━━━━━━━━
**📧 未读邮件 — DLUT邮箱**

有 {N} 封需关注未读（共 {M} 封，已过滤 {X} 封）：
  • [{发件人}] {主题}

**📧 未读邮件 — QQ邮箱**

有 {N} 封需关注未读（共 {M} 封，已过滤 {X} 封）：
  • [{发件人}] {主题}

━━━━━━━━━━━━━━━━━━━━━━
祝今天工作顺利！☀️
```

### 呈现要点

- 标题使用 Markdown 加粗 `**{标题}**`
- 日期使用中文格式：2026年6月18日 星期四
- 事务状态用中文标注：`[进行中]` `[等待反馈]` `[新建]`
- 如果某类事务为 0 项，该小标题不显示，直接写「无」
- 如果某个邮箱无未读，写「暂无未读邮件 ✅」
- 邮件只展示**筛选后需要关注的**，过滤掉的在括号里说明数量即可

## 边界情况

### 无新内容
如果既无活跃事务也无未读邮件，回复 `[SILENT]`，不发送简报。

### 仅有一项内容
如果只有事务无邮件（或反之），省略空的部分，保留已有内容。如果整体内容太少（< 3 行），回复 `[SILENT]`。

### 周末/节假日
这个 cron 只在工作日运行（周一至周五）。如果在周末被手动触发，回复「今天是周末，没有晨间简报。好好休息！🌴」，不执行数据采集。

### API 或网络异常
- Transactions API 不可达：在简报中标注「⚠️ 事务数据暂时无法获取」，继续展示邮件部分
- himalaya 执行失败：标注「⚠️ 邮件数据暂时无法获取」，继续展示事务部分
- 全部失败：回复「⚠️ 晨间简报数据暂时无法获取，稍后重试」
