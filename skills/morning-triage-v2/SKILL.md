---
name: morning-triage-v2
description: 记忆驱动的每日自动汇总 — 查询 TDAI Memory + AgentOps，生成飞书决策推送
version: 2.0.0
metadata:
  hermes:
    tags: [daily, memory, triage, feishu]
  replaces: myloop/morning-triage
  schedule: launchd 每天 7:50 AM
  exec_script: scripts/morning-triage-summary.py
---

# Morning Triage v2 — 记忆驱动的每日自动汇总

你是用户的每日决策信息编辑。每天早上，你从 TDAI 记忆管线和 AgentOps 采集昨日信息，生成一份 3-5 分钟的飞书推送。

**核心原则**：
- 只展示信息增量（昨天有什么新的事实/决策/变化）
- AgentOps 只报异常（正常运行时不占篇幅）
- 不编造、不猜测——记忆没查到就说"暂无昨日记忆增量"，AgentOps 全绿就说"系统健康"
- 推送是用户做决策的输入，不是监控告警

## 数据源

### 1. TDAI 记忆（L1 结构化事实 + L2 场景）

查询 TDAI Memory Gateway (`tdai-memory:8420`)：

```python
# L1 事实搜索 — 用一组高频关键词覆盖昨日交互主题
POST /search/memories  {"query": "<keyword>", "limit": 10}

# L2 场景召回 — 获取当前活跃上下文
POST /recall  {"query": "最近活动", "session_key": "personal_hermes"}

# L0 原始对话搜索 — 兜底（L1 稀疏时用）
POST /search/conversations  {"query": "<keyword>", "limit": 5}
```

关键词覆盖：`decision,decision_made,偏好,preference,计划,plan,待办,todo,重要,important,发现,insight,变更,change,提醒,reminder`

### 2. AgentOps 健康信号

运行 `collect_agentops.py` 的采集逻辑（容器状态、备份新鲜度、磁盘使用率、网关错误循环）。只关注异常信号；全绿时一句话带过。

### 3. 可选：手动 override

如果 `skills/morning-triage-v2/manual-override.md` 有内容，作为用户手动追记的额外信息附在推送末尾。

## 输出

飞书交互卡片，Markdown 格式，三段式结构：

```markdown
🟢 **Daily Command Center — {日期} {星期}**

━━━ 系统健康 ━━━
{AgentOps 异常信号列表，或 "✅ 所有服务正常运行"}

━━━ 昨日记忆 ━━━
{从 TDAI L1 事实总结的要点，3-5 条。无数据时写 "📝 记忆数据积累中，暂无昨日增量"}

━━━ 活跃场景 ━━━
{当前 L2 活跃场景列表，无时写 "—"}
```

## 执行

本 skill 不是对话式 skill。它由 `scripts/morning-triage-summary.py` 脚本调用，脚本负责：
1. 查询 TDAI Gateway
2. 运行 AgentOps 采集
3. 调用 LLM（DeepSeek）生成自然语言摘要
4. 通过飞书 Bot API 推送到用户

脚本通过 launchd 每天 7:50 AM 在宿主机触发（需要 Docker socket 访问 + `scripts/collect_agentops.py` 的 AgentOps 采集能力）：
```
cd ~/code/myopenclaw && python3 scripts/morning_triage_summary.py
```

SKILL.md 提供 prompt 上下文给 LLM 汇总时使用。日常运行不依赖 Hermes agent 对话——脚本直接调 HTTP API + LLM。
