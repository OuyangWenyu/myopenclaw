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
│   爱玛士 (hermes container)          │
│     Hermes cron @ 7:50 AM           │
│     skill: morning-triage-v2        │
│   skills/morning-triage-v2/         │
│     SKILL.md  — 数据采集 + 分析规则   │
│     tools/send_card.py — 飞书推送   │
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
```

Morning triage 由 Hermes 内置 cron 自动触发，无需额外安装 launchd 任务。

无需额外配置。myloop skills 通过 symlink 加载，修改 myloop 后自动生效。

## Morning Triage（晨间简报）

### 数据流

```
TDAI Memory Gateway (tdai-memory:8420)
  /search/memories      ← L1 结构化事实
  /recall               ← L2 场景上下文
       │
       ▼
Hermes cron skill: morning-triage-v2
  1. 查询 TDAI Gateway（多关键词搜索）
  2. LLM 深度分析（DeepSeek，过滤论文元数据/第三方信息）
  3. 生成 Markdown 报告（系统健康 + 昨日记忆 + 活跃场景）
  4. send_card.py → 飞书交互卡片
       │
       ▼
   📱 飞书 爱玛士 私聊
```

### 手动操作

```bash
# 查看 cron 状态
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list | grep "Daily Command"

# 手动触发
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron run <job_id>
```

## 添加新 Loop

1. 在 myloop 创建 skill 设计：`myloop/skills/<loop-name>/SKILL.md`
2. 在 myloop 注册到 `configs/loops.toml`
3. 容器重启后自动 symlink 到 CC飞总
4. 在 myopenclaw 创建执行脚本或 Hermes skill（如需要）
5. 如需定时触发，使用 Hermes 内置 cron 而非宿主机 launchd

### 规则

- **设计归 myloop，执行归 myopenclaw**
- Skill 文件永远不复制、不分叉（symlink only）
- 定时任务优先使用 Hermes 内置 cron，避免宿主机 launchd 依赖
- 执行层依赖容器环境变量（API key、路径），放在 myopenclaw
- 不在 myloop 中引用 myopenclaw 的路径或凭证

## 当前 Loop 状态

| Loop | 设计 | 执行 | 触发 |
|------|------|------|------|
| morning-triage | ✅ | ✅ | Hermes cron 07:50 (morning-triage-v2) |
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
# 检查 Hermes cron 状态
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list | grep "Daily Command"

# 查看 Hermes agent 日志
docker compose exec hermes tail -50 /opt/data/logs/agent.log | grep "Daily Command"

# 手动触发测试
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron run <job_id>
```

### 飞书未收到消息

1. 检查 `LARK_CLI_APP_ID` / `LARK_CLI_APP_SECRET` 在 `.env` 中已配置
2. 检查容器内环境变量：`docker compose exec hermes env | grep LARK`
3. 手动触发 cron 查看 agent 日志：
   ```bash
   docker compose exec hermes /opt/hermes/.venv/bin/hermes cron run <job_id>
   docker compose logs hermes --tail 30
   ```
