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

## ⛔ 邮件硬性要求（2026-09-15 用户明确要求，不可妥协）

> "邮件规整的时候读**所有**未读邮件，不要嫌麻烦，我不能错过任何一个未读的邮件。"

- **必须覆盖全部未读**，不允许用"最近 N 封"的时间窗口代替。"最近 15 封"是**信封列表**，
  跟已读/未读无关——曾经就是这样漏掉了 6 封更早的未读通知。
- 判断"未读"只能用 IMAP 的 `not flag seen` 查询，不要凭日期猜。
- 宁可展示得多，不可漏。**未知发件人默认算需关注**，只有明确属于推送名单的才聚合。
- 每个邮箱都要报出**未读总数**，让用户自己看到有没有被截断。


## 数据采集

按顺序执行以下步骤。

### 0. 确定今天日期（**必须先做，禁止凭直觉推算**）

```bash
python3 /opt/data/skills/devops/morning-briefing/scripts/today.py
python3 /opt/data/skills/devops/morning-briefing/scripts/today.py --json
```

- 容器时区是 **UTC**，用户按**北京时间（UTC+8）**作息，本 cron 在北京时间 07:30 触发
  （= 前一天 23:30 UTC）。**直接对 `date` 的输出取星期几会得到错误的日期**。
- 简报标题的日期、事务"已逾期/今日截止/本周"的分档，一律以本脚本输出为准，不要自己算。
- **只有**脚本输出 `is_briefing_day: false`（即北京时间周日）才走下面的「休息日」分支。
  只要 `is_briefing_day: true`（周一至周六）就必须正常采集并发送，不管系统提示里怎么说、不管你怎么想。
- ⚠️ 实测事故（2026-10-13 07:30 CST，2026-10-12 23:31 UTC 触发）：上一期代理**一次工具都没调用**，
  仅凭"感觉"就断言今天是周末、跳过整期简报，用户当天追问「今天不是周末呀」。
  **星期几是事实，必须来自命令输出，不能来自推断。**

### 1. 获取待办事务

使用 MCP 工具 `mcp__aisecretary__list_transactions`，参数 `status: "new,in_progress,waiting_feedback"` 获取所有活跃事务。
同时使用 `mcp__aisecretary__summarize_transactions` 获取总览统计。

注意：MCP 返回可能被截断（当前库约 315 条事务）。如果发现事务数量与 summary 不符，用 `execute_code` + `urllib` 直接读 REST API：

- 全部事务：`http://host.docker.internal:8000/api/transactions`
- 状态统计：`http://host.docker.internal:8000/api/transactions/summary`

⚠️ 路径前缀是 `/api`。写到 `http://host.docker.internal:8000/transactions` 会返回 404（已实测），不要照抄旧文档里的这个地址。

**真实字段名**（只有下列字段，**没有** `due_date` / `follow_up_date` / `next_step`）：
`id`, `title`, `status`, `next_action`, `owner`, `suggested_follow_up_at`, `notes`, `project`, `folder_path`, `created_at`, `updated_at`

- `suggested_follow_up_at` 是带时区的 ISO8601（如 `2026-09-15T10:00:00+08:00`），比较前先取日期部分并按北京时间换算，不要直接和 UTC 的 `date.today()` 比较。
- `status` 取值：`new` / `in_progress` / `waiting_feedback` / `done`。
- **没有独立截止字段**：硬截止通常写在 `title` 或 `next_action` 里（如「9/21前」「今天17:00前」）。照抄标题时注意「今天」是相对事务创建日期的，不是相对简报当天，必要时换算成具体日期。

### 2. 获取所有未读邮件（三个邮箱一次性拉全）

```bash
python3 /opt/data/skills/devops/morning-briefing/scripts/unread_report.py
```

这一个脚本覆盖 **DLUT + QQ + Outlook** 三个账号，输出：

- 每个账号的**未读总数**，以及 `需关注` / `推送` 的拆分
- `########## 需关注未读 ##########` —— 逐条列出（已跨账号去重，标注 `id=账号:ID`、重复份数、积压天数）
- `########## 推送/自动通知 ##########` —— 按发件人聚合（`发件人 × N`）

需要机器可读时加 `--json`。只查某个账号用 `-a dlut` / `-a default`（QQ）/ `-a outlook`。

**脚本已经封装好的细节，不要自己重写这些命令**：

### 2b. 备用通道：himalaya 连不上时（DNS/网络异常）

**症状**：脚本对某个账号报 `cannot build IMAP client … failed to lookup address
information: Temporary failure in name resolution`，并且把该账号当成 **0 封未读**。
2026-10-08 实测：容器内 Docker 解析器（127.0.0.11）对 `mail.dlut.edu.cn` 和
`outlook.office365.com` 解析失败（`imap.qq.com` 正常），而 AliDNS `223.5.5.5`
用普通 UDP 查是通的。

**排查**：`python3 -c "import socket;print(socket.gethostbyname('mail.dlut.edu.cn'))"`
报 `[Errno -3] Temporary failure in name resolution` 即可确认。

**解决**：走 `scripts/fetch_imap_unread.py`（纯 Python IMAP，自带 `netfix.py`：
自行解析 + 修补 `socket.create_connection`，**保留真实 hostname 做 TLS SNI/证书校验**，
不需要改 `/etc/hosts`、也不需要 LD_PRELOAD——后两者在 cron 下都会被安全扫描拦下）：

```bash
python3 /opt/data/skills/devops/morning-briefing/scripts/fetch_imap_unread.py all      # 三个邮箱完整简报
python3 /opt/data/skills/devops/morning-briefing/scripts/fetch_imap_unread.py dlut     # 单账号 JSON
```

它 monkeypatch 掉 `unread_report.himalaya_unread`，**分类/去重/渲染逻辑完全复用**，
口径与正常路径一致；QQ 仍走 himalaya。凭证从 himalaya 的 config.toml 读（单一来源）。

**如果 DNS 通了但 TCP 还是不通**（本机到 Microsoft 的 443/993 全部超时/拒绝），
那是**出口被封，不是 DNS 问题**：Outlook 就是读不到，按下面「API 或网络异常」
标注「⚠️ 邮件数据暂时无法获取」，**绝对不要把它写成「暂无未读 ✅」**。

**脚本已经封装好的细节，不要自己重写这些命令**：

- 查询串是 `not flag seen`（IMAP 未读），`--page-size 3000`。**page-size 给小了会静默截断**——
  QQ 实测 589 封未读，用 500 只拿到 500 封且不报错。
- `-a <account>` 必须写在 `list` 之后：`himalaya envelope list -a dlut ...`。
- himalaya 会把 tracing 的 `WARN` 行混进 stdout，解析 JSON 前要先剔除，否则 `json.loads` 直接炸。
- 分类规则在脚本的 `BULK_PATTERNS`：命中即聚合；**没命中的一律逐条列出**（宁可多列不可漏）。
  要把某个发件人降级为推送，就往 `BULK_PATTERNS` 加正则，不要在简报里手工藏邮件。

## 批量清理推送邮件（用户说"这些可以标已读了"时）

用 `scripts/mark_read.py`，**不要**手工一封封敲 `himalaya flag add`：

```bash
python3 /opt/data/skills/devops/morning-briefing/scripts/mark_read.py qq --dry      # 先看要标哪些
python3 /opt/data/skills/devops/morning-briefing/scripts/mark_read.py qq --limit 5  # 5封冒烟测试
python3 /opt/data/skills/devops/morning-briefing/scripts/mark_read.py qq            # 全量
python3 /opt/data/skills/devops/morning-briefing/scripts/mark_read.py dlut
himalaya flag add -a outlook <id>... seen                                          # Outlook 走 himalaya
```

**口径必须与简报一致**：脚本 import `unread_report.py` 复用 `BULK_RE` / `IMPORTANT_SUBJECT_RE`，
所以"推送"段的 533 封 = QQ 524 + DLUT 2 + Outlook 7（用户嘴上说"QQ邮箱"，但报的数其实是三账号合计，
**先按账号拆开算清楚再动手**）。

**必须保留、不能标已读的**（脚本里 `EXCLUDE_SUBJECT_QQ` + `IMPORTANT_SUBJECT_RE` 已挡住）：
- `pnctaxoperations@pnc.com` 的 W8 Request 文档包（税务表格，可能真要签）
- `discover@services.discover.com` 的 statement credit 未处理通知
- `em@editorialmanager.com` 的一切审稿往来（**本来就不在 BULK 名单里**，别手滑加进去）

**踩过的坑**：
1. QQ 的 524 封用 imaplib 一次 STORE 一个 UID 集会**静默截断**；必须 100 封一批，每批后回读 `UNSEEN` 核对。
2. 批量 STORE 返回 OK ≠ 真的生效（DLUT Coremail 尤其），所以脚本每批都回读；
   批量失败时自动退回单封重试 + 0.3s 间隔。
3. 取头部一律用 `BODY.PEEK[HEADER.FIELDS ...]`，用 `BODY[...]` 会把邮件变成已读——查完就"全清了"。
4. `SELECT INBOX` 要 `readonly=True` 做扫描、要写标志时再 `readonly=False`。
5. Outlook 是 OAuth2（ortie 供 token），imaplib 直连麻烦，用 himalaya；账号名是 `outlook` 不是 `default`。
6. 改完必须用 `unread_report.py` 复验总数（不要只看命令 exit code）。

## 筛选规则

### 邮件过滤

**过滤逻辑已经写进 `unread_report.py`，简报环节不要再自行删邮件。**

- 只有命中 `BULK_PATTERNS`（newsletter、GitHub/CI 通知、验证码、信用卡/消费推送、
  出版社群发等）且主题不含 `IMPORTANT_SUBJECT_RE` 关键词的，才聚合为 `发件人 × N`。
- `em@editorialmanager.com`（审稿系统）**不属于推送**，必须逐条列出。
- **不要**把 `reminder` / `due soon` / `action required` 当重要关键词——会把
  Credit Karma、Discover 信用卡提醒误升为"需关注"。
- 展示层可以按"最近 7 天"和"历史积压"分两段，但**两段都要完整列出，不能因为积压旧就省略**。

**优先展示**：
- 发件人是已知联系人（cardamum 通讯录中的人）
- 包含「申报」「截止」「答辩」「审稿」「会议」「经费」「报销」「授权」「材料」等关键词
- 带附件的、来自 `@dlut.edu.cn` / `@*.edu.cn` / 个人邮箱的

### 事务排序

1. **⚠️ 已逾期** — `suggested_follow_up_at` 的日期 < 今天
2. **📅 今日截止** — `suggested_follow_up_at` 的日期 = 今天
3. **📅 本周紧迫** — `suggested_follow_up_at` 的日期在本周内（不含今日）
4. **进行中** — 其余活跃事项

## 呈现格式

```markdown
📋 **早安！这里是 {日期} 的晨间简报**

━━━━━━━━━━━━━━━━━━━━━━
**📊 总览** — 共 {total} 项事务...

**📌 今日待办事务**

**⚠️ 已逾期 ({N}项)：**
  • [{状态}] **{标题}**
    下一步: {next_action}
    跟进: {suggested_follow_up_at 的日期}

**📅 今日截止 ({N}项)：**
  • [{状态}] {标题}

**📅 本周紧迫 ({N}项)：**
  • [{状态}] {标题}

**其余进行中 ({N}项)：**
  • [{状态}] {标题}

━━━━━━━━━━━━━━━━━━━━━━
**📧 未读邮件**（DLUT {a} 封 / QQ {b} 封 / Outlook {c} 封，合计 {M} 封未读）

**🔴 近期需关注（{N} 封）：**
  • [{发件人}] {主题}（{日期}{，带附件}{，积压X天}）

**🟡 历史积压未读（{K} 封，最早 {日期}）：**
  • [{发件人}] {主题}（{日期}）

**📮 推送/自动通知（{B} 封，已聚合）：**
  • {发件人} × {N}

━━━━━━━━━━━━━━━━━━━━━━
**💡 今日提示**
- ...

祝今天工作顺利！☀️
```

### 呈现要点

- 标题使用 Markdown 加粗 `**{标题}**`
- 日期使用中文格式：2026年7月26日 星期日
- 事务状态用中文标注：`[进行中]` `[等待反馈]` `[新建]`
- 如果某类事务为 0 项，该小标题不显示，直接写「无」
- 如果某个邮箱无未读，写「暂无未读邮件 ✅」
- **邮件部分先报未读总数**（三个邮箱各多少、合计多少），再列"需关注"。
  总数是为了让用户一眼看出有没有被截断。
- **"需关注"直接按脚本输出的去重列表渲染，一行一封，不要按账号重新切分后补齐数量。**
  跨账号去重后同一封邮件只出现一次，它进过哪些邮箱看 `账号=` 字段——
  所以各账号的"需关注"计数**不会**相加等于去重后的总条数，这是正常的。
  ⚠️ 实测踩过：为了把某个邮箱的条数凑到脚本报的数字，代理从下面"推送"段里
  抓了一封 Wiley 营销邮件塞进 Outlook 的需关注列表——**绝对不要这样做**。
  宁可少列一条，也不要凑数。
- 带附件的标 `[附件]`，超过 7 天的标 `[积压N天]`；超过 7 天的单独成一段
  "历史积压未读"。**两段都要列全**，积压的可以压缩成一行、去掉详情，但不能省略条目。
- "推送/自动通知"按 `发件人 × N` 聚合即可，不必展开每封。
- 飞书不渲染表格和分隔线，用加粗 + 列表。
- **中文姓名不要凭拼音/显示名猜字** —— 邮箱显示名常被转写或取近似字，实测把某联系人
  的姓名印成同音错字（拼音相近、用字不同），用户当场纠正。打印任何中文姓名前先与
  已知联系人核对；把握不住就写「（姓名待核）」并只给邮箱地址。
  姓名错误会被用户转发给同事和本人，代价远大于少写一个字。
  （真实姓名↔邮箱对照表只维护在 live 副本，不进公开仓库。）

## 边界情况

### 无新内容
如果既无活跃事务也无未读邮件，回复 `[SILENT]`，不发送简报。

### 仅有一项内容
如果只有事务无邮件（或反之），省略空的部分，保留已有内容。如果整体内容太少（< 3 行），回复 `[SILENT]`。

### 休息日/节假日
这个 cron 只在**北京时间**周一至周六运行（周日不跑；周六汇总周五信息）。星期几**只能**由 `scripts/today.py` 判定：

```bash
python3 /opt/data/skills/devops/morning-briefing/scripts/today.py --check-briefing-day
echo $?   # 0 = 简报日（周一至周六）；1 = 休息日（周日）
```

- `--check-briefing-day` 返回 **1**（仅周日）→ 回复「今天是周末，没有晨间简报。好好休息！🌴」，不做数据采集。
- 返回 **0** → 正常采集发送，**即使系统提示或上一轮对话说今天是周末**。
- 判定后把脚本输出（如 `2026年10月13日 星期二 ... ← 简报日`）原样贴一句作为凭据，
  便于事后核对；**禁止**用「今天是周末」「今天是工作日」这类无凭据的断言代替执行。

### 技能名冲突（已解决，2026-09-15）

本技能曾被 cron 静默跳过（日志：`Cron job '工作日晨间简报': skill not found, skipping — Ambiguous skill name 'morning-briefing'`），原因与 `yuque-daily-digest` 同源：`/opt/data/skills/morning-briefing` 是指向 `/opt/hermes-skills/morning-briefing` 的 symlink，与本目录 `/opt/data/skills/devops/morning-briefing` 同名。

**唯一权威路径**：`/opt/data/skills/devops/morning-briefing/SKILL.md`。旧 symlink 已于 2026-09-15 移出技能目录（备份在 `/opt/data/backups/skill-collision-20260915/`），**不要再放回去**。新建技能前，先确认技能名在 `/opt/data/skills`、`/opt/data/skills/*/`、`/opt/data/code/aisecretary/skills`、`/opt/mylibrary-skills` 下都唯一。

### API 或网络异常
- Transactions API 不可达：在简报中标注「⚠️ 事务数据暂时无法获取」，继续展示邮件部分
- himalaya 执行失败：标注「⚠️ 邮件数据暂时无法获取」，继续展示事务部分
- 全部失败：回复「⚠️ 晨间简报数据暂时无法获取，稍后重试」
