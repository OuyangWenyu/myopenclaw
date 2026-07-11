# Agent Memory — 为 myopenclaw 各 agent 引入 TencentDB 长期记忆层

## Problem

myopenclaw 的 4 个个人 agent（Hermes default、爱玛士、finance、CC飞总）之间没有共享记忆。owen 在 Discord 跟爱玛士聊过的 issue 背景，切到飞书跟 CC飞总聊实现时，必须重新解释一遍。每个 agent 的每次新会话都从零开始，上下文窗口关闭后所有对话记忆消失。同时，公共 Discord bot 虾酱面向多用户，也无法保留跨会话的用户上下文。

**代价**：重复沟通、agent 无法积累对 owen 偏好和决策的理解、长任务超过上下文窗口后丢失进展。

## Evidence

- **用户直接反馈**："我跟爱码士聊的 issue 啥的背景信息，跟 CC飞总聊实现的时候还得跟它重复一遍"
- **可观测行为**：owen 需要频繁手动管理 session 上下文，在不同 agent/平台间复制粘贴背景信息
- **设计层驱动**：MyLoop 项目已完成 L0→L3 记忆分层设计（`skills/session-memory/SKILL.md`），定义了"记忆该怎么组织"，执行层（本项目）负责"怎么跑起来"

## Users

- **Primary**: owen — 通过 4 个 agent（Hermes default / 爱玛士 coder / finance / CC飞总）与系统交互，期望跨 agent 共享背景
- **Secondary**: Discord 虾酱用户 — 后续接入（本 PRD 不覆盖虾酱侧落地细节）
- **Not for**: 无。这不是面向外部用户的产品功能，是基础设施改进

## Hypothesis

We believe **为 4 个个人 agent 引入共享长期记忆层（TencentDB Agent Memory v0.3.6 Gateway + adapter/MCP server）** will **消除跨 agent 的重复沟通成本** for **owen 的个人 agent 体系**。
We'll know we're right when **飞书侧 CC飞总 讨论过的决策/偏好，Discord 侧爱玛士能在不重复解释的情况下直接引用**。

## Success Metrics

| Metric | Target | How measured |
|--------|--------|--------------|
| 跨 agent 信息召回 | ≥4/5 事实可召回 | 人工抽检：记录 5 条事实 → 48h 后另一 agent 查询 |
| 写入延迟 | 不影响对话体验 | Gateway `/health` < 200ms，写入 API < 2s p95 |
| 零破坏 | 现有 agent 功能无回归 | `docker compose ps` 全 healthy，Hermes/CC 正常对话 |

## Scope

### MVP — 首发只接 CC飞总（最小风险验证回路）

1. Mac mini 上 `docker run` 单独起 tdai-memory Gateway 容器（v0.3.6）
2. Bash + Python SDK 冒烟测试（`search_atomic` 一次调用）
3. 编写薄 MCP server（`memory_search` / `conversation_search` / `read_scenario` / `read_core`）
4. 注册到 CC飞总的 `~/.claude/settings.json` 的 `mcpServers`
5. 固化为 `docker-compose.yml` 的 `tdai-memory` service + 纳入 backup 管线

MVP 不包含 Hermes adapter、不包含虾酱插件、不包含 offload 短期压缩。

### Out of scope

- ❌ **不替换验证回路** — TencentDB Agent Memory 是抽取管线，无 Generator≠Judge，不承担 verify 职责
- ❌ **不修改虾酱与个人体系的隔离边界** — 两套物理独立 SQLite 库的边界不可变
- ❌ **不改造 Hermes 现有内置 `memory` provider** — 两套独立运行，Hermes 内置 memory 继续工作
- ❌ **虾酱侧落地** — 本 PRD 只锁定架构约束，虾酱侧 OpenClaw 插件安装不在本次范围内
- ❌ **offload 短期压缩** — 首发关闭，待长期记忆链路稳定后单独评估

## Architecture

### 核心决策：两套物理独立的记忆体系

不是"一个库分逻辑域"，而是**两个独立 SQLite 库，靠文件路径物理隔离**——OpenClaw 面向多人且持零密钥，不应掌握个人信息。

| 体系 | 归属 | 后端库 | 接入方式 | session_id |
|------|------|--------|----------|------------|
| **虾酱** | OpenClaw Discord，多用户，完全独立 | `~/.openclaw/memory-tdai/` | OpenClaw 内嵌插件（local 模式） | `discord_<user>_<channel>` |
| **个人体系** | owen 一人，4 agent 共享 | `~/.myagentdata/memory-tdai/` | 独立 Gateway 容器 `:8420`，`service_id=personal` | 见下表 |

### 个人体系：4 agent 共享 L1/L2/L3，各自 L0 分开

| Agent | 容器/端口 | session_id | 接入方式 |
|-------|----------|-----------|----------|
| Hermes default | hermes (8642) | `personal_hermes` | `memory_tencentdb_v2` adapter |
| 爱玛士 (coder) | hermes-coder (8643) | `personal_aimashi` | 同上（三 profile 共用一次安装） |
| finance | hermes-finance (8644) | `personal_finance` | 同上 |
| CC飞总 | claude-code (cc-connect, 9090) | `personal_ccfeizong` | MCP server（CC 无插件机制） |

**关键效果**：飞书对 CC飞总说的，Discord 侧爱玛士能召回；finance 财务信息全共享。各 agent L0 逐字对话流分开，不混一锅。

### 拓扑图

```
虾酱 (OpenClaw)                  个人体系 (4 agent 共享)
──────────────────              ────────────────────────────────
OpenClaw 内嵌插件 local           独立 Gateway 容器 tdai-memory:8420
~/.openclaw/memory-tdai/          ~/.myagentdata/memory-tdai/
多用户，物理隔离                   service_id = personal
                                 ├ Hermes default   (adapter)
                                 ├ 爱玛士 coder      (adapter)
                                 ├ finance          (adapter)
                                 └ CC飞总           (MCP server)
```

### 记忆分层 L0→L3（MyLoop 设计约束）

```
L0 Conversation  原始对话/工具输出   → 按 session_id 分 agent 存储
  ↑ 抽取
L1 Atom          结构化事实/偏好/指令  → 四大账本 item（date/source/evidence）
  ↑ 聚合
L2 Scenario      按主题归档的场景块    → 跨 agent 共享的场景归档
  ↑ 提炼
L3 Persona       画像 / Golden Rules  → 跨 agent 共享的用户画像
```

**下钻契约**：L2/L3 结论必须携带可追溯引用，沿 `L2 结论 → L1 item.evidence → L0 原文` 能找回证据。压缩可折叠、可展开，不可不可逆。

### 三种接入方式

1. **OpenClaw 插件**（虾酱，本次不落地）：`openclaw plugins install` + `openclaw.json` 开 `enabled`，local 模式进程内本地 SQLite
2. **Hermes adapter**（default/爱玛士/finance，MVP 后接入）：hermes 镜像装 `memory_tencentdb_v2`，`config.yaml` 配 `provider: memory_tencentdb_v2`，env 指向 `http://tdai-memory:8420`。三 profile 共用一次安装
3. **MCP server**（CC飞总，MVP 首发）：自写薄 MCP server 调 Python SDK（`AsyncMemoryClient`），暴露 4 个工具注册进 `~/.claude/settings.json`

## Configuration Decisions

以下决策已在 issue #33 讨论中锁定，PRD 记录为约束：

| 决策 | 结论 | 锁定理 | 由 |
|------|------|--------|-----|
| 两套物理隔离 | 不同 SQLite 文件，路径隔离，非权限约束 | 不可变 ||
| 财务数据共享 | finance 的 L1/L2/L3 与其他 3 agent 全共享 | 已确认 ||
| Gateway 版本 | pin `v0.3.6`（2026-05-28 最新 release） | 首发 | 等稳定后再评估升级 |
| Embedding | 首发用默认 **local sqlite-vec**（零配置），不配外部 embedding | 首发 | 召回不足时加一行 `embedding.*` 配置切 OpenAI 兼容 API |
| offload 短期压缩 | 首发**关闭** | 首发 | 先验证长期记忆链路；短期压缩独立评估 |
| Backup | `sqlite3 .backup` 热备，复用 OpenClaw 现有模式 | 首发 ||
| Bearer 鉴权 | 首发**不开** | 首发 | Docker 内网 `myopenclaw-net` 已物理隔离；暴露外网时再加 |
| 首发 agent | **CC飞总**（MCP server 冒烟成本最低） | MVP ||
| 密钥域 | Gateway 为第四个独立密钥域（`TDAI_LLM_*` 系列 env var） | 不可变 | 复用 DeepSeek API key |

## Delivery Milestones

| # | Milestone | Outcome | Status | Plan |
|---|-----------|---------|--------|------|
| 1 | tdai-memory 容器冒烟 | Gateway :8420 运行，`/health` 返回 200 | in-progress | [harmonic-nibbling-wave](../../.claude/plans/harmonic-nibbling-wave.md) |
| 2 | CC飞总 MCP server | CC飞总可通过 `memory_search` 工具召回记忆 | pending | — |
| 3 | docker-compose 固化 + backup | `tdai-memory` service 纳入 docker-compose.yml + backup 管线 | pending | — |
| 4 | Hermes 三 profile adapter | default / coder / finance 均接入 Gateway | pending | — |
| 5 | 虾酱 OpenClaw 插件 | 虾酱 Discord bot 独立接入 local 模式（独立 DB 文件） | pending | — |

## Open Questions

- [ ] **CC飞总 MCP server 放在哪个 repo？** — myopenclaw（执行层）还是 myloop（设计层）？建议 myopenclaw，因为依赖容器环境
- [ ] **Gateway LLM key 复用 DeepSeek 还是独立申请？** — 技术上可复用 `DEEPSEEK_API_KEY`，独立申请更符合密钥隔离哲学
- [ ] **召回质量的抽检频率？** — 建议首次接入后 1 周、1 月各做一次人工抽检
- [ ] **虾酱侧隐私声明** — 需在 Discord 频道/system prompt 声明"对话会被记录"，时机待定

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Gateway v0.3.6 有未发现 bug | Medium | 记忆写入丢失 | 首发只接 CC飞总一个 agent，观察 1 周再扩展 |
| sqlite-vec 默认召回质量不足 | Medium | 记忆召不回，功能形同虚设 | `embedding.*` 配置是热切换的，一行配置加云端 API 即可对标 |
| LLM 抽取成本超预期 | Low | L1→L3 抽取消耗 DeepSeek token | `pipeline.everyNConversations` 控制频率；首发只接一个 agent 观察 |
| MCP server 成为单点 | Low | CC飞总调用失败 | MCP server 调的是容器内 SDK，Gateway 挂了 MCP 工具返回 error，不影响 CC飞总正常对话 |
| 虾酱记忆误存个人信息 | Low | 跨用户隐私泄漏 | 虾酱用独立 DB 文件 + 独立 session_id 粒度，物理碰不到个人体系 |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
