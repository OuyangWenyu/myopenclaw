# MyLoop 集成文档

MyLoop 是本项目的 **loop 设计层** — 定义 agent 自主循环的 skill 合同、分类规则、数据模型和输出格式。myopenclaw 是 **执行层** — CC飞总 (Claude Code + cc-connect) 读取设计、执行分类、推送到飞书。

## 架构

```
┌─────────────────────────────────────┐
│ MyLoop（设计层）                      │
│ ~/code/myloop/                      │
│   skills/*/SKILL.md    ← loop 合同   │
│   memory/*/inbox.md    ← 数据账本    │
│   configs/*.toml       ← 项目/预算   │
└──────────┬──────────────────────────┘
           │ symlink（不复制、不分叉）
           ▼
┌─────────────────────────────────────┐
│ myopenclaw（执行层）                  │
│ ~/code/myopenclaw/                  │
│   CC飞总 (claude-code container)     │
│     ~/.claude/skills/morning-triage → myloop/skills/morning-triage
│   scripts/morning-triage-send.py    │
│   launchd @ 7:50 AM                 │
└──────────┬──────────────────────────┘
           │ 飞书 Bot API
           ▼
       📱 飞书消息
```

## Skill 加载机制

容器启动时 `docker/claude-code/entrypoint.sh` 自动执行：

```bash
if [ -d "/home/node/code/myloop/skills" ]; then
    for skill_dir in /home/node/code/myloop/skills/*/; do
        ln -sf "$skill_dir" /home/node/.claude/skills/$(basename "$skill_dir")
    done
fi
```

启动日志确认：
```
📎 myloop skills: knowledge-sync morning-triage paper-ingest session-memory verify-and-ship weekly-digest
```

### 新机器部署

```bash
# 1. 克隆 myloop 到 myopenclaw 同级目录
git clone https://github.com/OuyangWenyu/myloop.git ~/code/myloop

# 2. 启动（entrypoint 自动加载 skills）
cd ~/code/myopenclaw && ./scripts/start.sh

# 3. 安装 morning-triage 定时任务
./scripts/launchd/install-morning-triage.sh
```

无需额外配置。myloop skills 通过 symlink 加载，修改 myloop 后自动生效。

## Morning Triage（晨间三签）

### 数据流

```
四个 ledger (markdown)
  memory/task-ledger/inbox.md      ← 个人任务
  memory/data-ledger/inbox.md      ← 数据/模型信号
  memory/people-ledger/inbox.md    ← 人员状态
  memory/agentops-ledger/inbox.md  ← 基础设施

configs/projects.toml              ← 项目注册表
       │
       ▼
morning-triage-send.py
  1. parse_ledger()     → 解析 markdown item
  2. classify()         → Needs / Today / Watch / Resolved
  3. generate_report()  → Markdown 格式化
  4. send_feishu_message() → 飞书交互卡片
       │
       ▼
   📱 飞书 CC飞总 私聊
```

### 分类规则

对齐 `myloop/skills/morning-triage/SKILL.md` §4：

| 分类 | 触发条件 |
|------|----------|
| ⚡ Needs Human Decision | status=blocked/waiting_feedback, needs_human_decision=yes, 生产故障 |
| 📋 Today Candidates | status=in_progress/ongoing, follow_up_at=today |
| 🔭 Watch | status=watch, 自动恢复但原因未明 |
| ✅ Resolved | status=done |

### 手动操作

```bash
# 触发一次（立即发送飞书）
launchctl start ai.myloop.morning-triage

# 容器内直接运行（调试用）
docker compose exec claude-code python3 /home/node/code/myloop/scripts/morning-triage-send.py

# 查看日志
cat logs/morning-triage.log
```

### 添加 ledger item

编辑对应 `~/code/myloop/memory/<ledger>/inbox.md`，按格式添加：

```markdown
## <简短标题>

- date: YYYY-MM-DD
- source: manual | docker logs | git | ...
- project: <projects.toml 中的 key>
- axis: task | data | org | agentops
- status: new | ongoing | blocked | done | watch
- owner: <负责人>
- evidence: <数据来源引用>
- why_it_matters: <为什么需要关注>
- suggested_next_action: <建议操作>
- needs_human_decision: yes | no
```

下次 triage 运行时自动纳入。

## 添加新 Loop

1. 在 myloop 创建 skill 设计：`myloop/skills/<loop-name>/SKILL.md`
2. 在 myloop 注册到 `configs/loops.toml`
3. 容器重启后自动 symlink 到 CC飞总
4. 在 myopenclaw 创建执行脚本（如需要）
5. 添加 launchd plist 模板 + install 脚本（如需要定时触发）

### 规则

- **设计归 myloop，执行归 myopenclaw**
- Skill 文件永远不复制、不分叉（symlink only）
- 执行脚本依赖容器环境变量（API key、路径），放在 myopenclaw
- 不在 myloop 中引用 myopenclaw 的路径或凭证

## 当前 Loop 状态

| Loop | 设计 | 执行 | 触发 |
|------|------|------|------|
| morning-triage | ✅ | ✅ | launchd 07:50 |
| session-memory | ✅ | ⬜ | 待实现 |
| knowledge-sync | ✅ | ⬜ | 待实现 |
| paper-ingest | ✅ | ⬜ | 待实现 |
| verify-and-ship | ✅ | ⬜ | 待实现 |
| weekly-digest | ✅ | ⬜ | 待实现 |

## 故障排查

### myloop skills 未加载

检查容器日志：
```bash
docker compose logs claude-code | grep "myloop"
```

预期输出 `📎 myloop skills: ...`。如果看到 `ℹ️ myloop 未挂载`，检查 `~/code/myloop` 是否存在且包含 `skills/` 目录。

### morning-triage 未触发

```bash
# 检查 launchd 任务状态
launchctl list | grep morning-triage

# 检查日志
cat ~/code/myopenclaw/logs/morning-triage.log

# 手动触发测试
launchctl start ai.myloop.morning-triage
```

### 飞书未收到消息

1. 检查 `CC_CONNECT_FEISHU_APP_ID` / `CC_CONNECT_FEISHU_APP_SECRET` 在 `.env` 中已配置
2. 检查容器内环境变量：`docker compose exec claude-code env | grep FEISHU`
3. 手动运行脚本查看错误输出：
   ```bash
   docker compose exec claude-code python3 /home/node/code/myloop/scripts/morning-triage-send.py
   ```
