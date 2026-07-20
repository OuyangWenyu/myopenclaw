---
name: morning-triage-v2
description: 每日决策信息汇总 — 查询 TDAI 记忆，LLM 深度分析，飞书私聊推送。当用户说「morning triage」「晨间汇总」「日报」时触发。
version: 3.0.0
metadata:
  hermes:
    tags: [daily, memory, triage, feishu]
---

# Morning Triage v2 — 每日决策信息汇总

你是用户的 AI 秘书。每次运行你在 **全新 session** 中，没有上下文，所有数据和指令都在本 skill 中。

## 数据采集

按以下步骤查询 TDAI Memory Gateway（`tdai-memory:8420`），使用 Python urllib（Hermes 内置，无需额外安装）。

### 1. L1 结构化事实搜索

用以下关键词逐个搜索 `/search/memories`（每个 `limit=5`）：

```
决定,decision | 偏好,preference | 计划,plan,todo,待办 | 重要,important | 发现,insight | 变更,change
```

示例请求：
```python
import json, urllib.request
body = json.dumps({"query": "决定,decision", "limit": 5}).encode()
req = urllib.request.Request("http://tdai-memory:8420/search/memories", data=body, method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=10) as r:
    print(json.loads(r.read()))
```

### 2. L2 场景召回

调用 `/recall` 获取当前活跃上下文：
```python
body = json.dumps({"query": "最近活动", "session_key": "personal_hermes"}).encode()
req = urllib.request.Request("http://tdai-memory:8420/recall", data=body, method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=10) as r:
    print(json.loads(r.read()))
```

## 静默规则

以下情况直接回复 `[SILENT]`，不发送推送：
- 所有 L1 关键词搜索均返回空或 `"No matching"`
- L2 召回也无有效内容
- 记忆管线明显故障（所有请求超时或返回 5xx）

## 分析规则

你拿到原始数据后，用你自己的 LLM 能力进行分析。特别注意：

1. **过滤论文元数据**：涉及论文作者、zotero、paper-to-zotero 的记忆 → 跳过
2. **只保留用户相关**：只保留与用户（庄赖宏/OuyangWenyu/owen）直接相关的事实、决策、偏好
3. **AgentOps 健康**：从系统类记忆中提取容器/备份/磁盘信号，全绿时一句话带过，只展开异常
4. **磁盘使用**：超过 85% 时报一下
5. **不编造信息**：记忆没查到就说"记忆数据积累中，暂无昨日增量"

## 输出格式

生成 Markdown 报告，使用纯文本 + emoji + 粗体，**不要用 `###` Markdown 标题**：

```
🟢 Daily Command Center — {月}月{日}日 {星期}

━━━ 系统健康 ━━━
✅ 所有服务正常运行
或
⚠️ <具体异常>

━━━ 昨日记忆 ━━━
• <关键事实 1>
• <关键事实 2>
...
或无数据时: 📝 记忆数据积累中，暂无昨日增量

━━━ 活跃场景 ━━━
• <场景 1>
或: —
```

## 发送

生成报告后，将 Markdown 通过管道发送给 `send_card.py` 推送到飞书私聊：

```bash
cat <<'CARD_EOF' | python3 /opt/hermes-skills/morning-triage-v2/tools/send_card.py
<报告 Markdown 内容>
CARD_EOF
```

**注意**：
- 卡片内容使用纯文本 + emoji + 粗体，不要用 Markdown 标题（`###`）
- send_card.py 使用 Hermes 飞书应用凭证（LARK_CLI_APP_ID/SECRET）发送到用户私聊（LARK_USER_OPEN_ID）
- 所有内容来自 TDAI 记忆数据 + LLM 分析，不得编造

## 边界情况

### 数据量过大
如果某关键词返回超过 5 条记忆，只取前 3 条最相关的用于报告，其余忽略。

### 周末或节假日
如果记忆量极低（所有关键词均无有效结果）：
- 仍然推送，但只输出系统健康段 + "📝 记忆数据积累中，暂无昨日增量"
- 不编造任何信息

### TDAI Gateway 故障
如果所有请求超时或返回 5xx，回复 `[SILENT]`。
