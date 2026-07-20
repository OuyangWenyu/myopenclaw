# TDAI 长期记忆

基于 [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) v0.3.6，让 4 个个人 agent 跨会话、跨 agent 共享长期记忆。**飞书对 CC飞总 说的，Discord 侧爱玛士能召回**，反之亦然。

## 记忆分层 L0→L3

```
L0 Conversation  原始对话         → conversations/<date>.jsonl
  ↑ 抽取
L1 Atom          结构化事实/偏好   → records/<date>.jsonl
  ↑ 聚合
L2 Scenario      按主题的场景块    → scene_blocks/<topic>.md
  ↑ 提炼
L3 Persona       用户画像          → persona.md
```

价值过滤（只抽关键结论）、去重、分层压缩全由 TDAI 管线负责。

## 拓扑：两套物理独立体系

| 体系 | 归属 | 后端 | 接入方式 |
|------|------|------|----------|
| **个人体系** | owen 一人，4 agent 共享 | `~/.myagentdata/tdai-memory/` | 独立 Gateway 容器 `:8420` |
| **虾酱** | OpenClaw Discord，多用户 | `~/.openclaw/memory-tdai/` | OpenClaw 内嵌插件（local 模式） |

两套用不同 SQLite 库文件物理隔离 — 虾酱（多人、零密钥）不掌握个人信息。

## 4 个 agent 的接入方式

| Agent | 容器 | 接入方式 | 写入路径 |
|-------|------|----------|----------|
| Hermes default / 爱玛士 / finance | hermes 三兄弟 | `memory_tencentdb` adapter | plugin 生命周期钩子（自动） |
| CC飞总 | claude-code | MCP server（4 读工具）+ Stop hook | Stop hook 每轮捕获对话 |

- **Hermes adapter**：entrypoint 启动时自动部署 plugin + 注入 `memory.provider`，读 env `MEMORY_TENCENTDB_GATEWAY_HOST/PORT` 连 Gateway。
- **CC飞总 双向**：读走 MCP server（`memory_search` / `conversation_search` / `read_scenario` / `read_core`），写走 `capture-to-gateway.py` Stop hook。hook 异常静默 exit 0，绝不阻塞对话；心跳/失败写 `~/.myagentdata/tdai-memory/capture-hook.log`（RotatingFileHandler 有界）。

## 常用命令

```bash
# 健康检查
curl -s http://localhost:8420/health

# 查记忆（L0 原始对话 / L1 结构化事实）
curl -s -X POST http://localhost:8420/search/conversations \
  -H 'Content-Type: application/json' -d '{"query":"关键词","limit":5}'
curl -s -X POST http://localhost:8420/search/memories \
  -H 'Content-Type: application/json' -d '{"query":"关键词","limit":5}'

# Gateway 日志
docker compose logs -f tdai-memory

# CC飞总 capture 心跳日志
docker compose exec claude-code tail -f /home/node/.myagentdata/tdai-memory/capture-hook.log

# 虾酱 OpenClaw memory plugin
./scripts/setup-openclaw-memory.sh
```

## 重启自动恢复

`docker compose up -d` / `./scripts/start.sh` 后，所有 agent 的记忆能力零手工自动恢复（entrypoint 自动装 plugin、注入 config、注册 hook）。数据落 `~/.myagentdata/tdai-memory/`，纳入 backup 管线（sqlite3 热备）。
