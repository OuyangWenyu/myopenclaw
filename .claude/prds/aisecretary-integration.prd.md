# aisecretary 集成到 myopenclaw 统一运维体系

## Problem
aisecretary 已重构为 Docker + MCP 服务（FastAPI + SQLite，端口 8000），但目前独立运行在 `~/code/aisecretary/docker-compose.yml`，未接入 myopenclaw 的统一启动和监控体系。运维者经常在服务挂了或忘启动后，直到用到飞书事务管理功能时才发现不可用——无告警、无自动拉起、无健康可见性。myopenclaw 的 Hermes 也未加载 aisecretary 的 MCP tools，飞书上无法通过 Hermes 管理事务数据库。

## Evidence
- 运维者亲述：曾多次发生 aisecretary 未启动或服务挂了，直到使用时才被动发现
- 当前 myopenclaw 启动脚本（`scripts/start.sh`）完全不感知 aisecretary 的存在
- Uptime Kuma（`http://localhost:3001`）未配置 aisecretary 的 HTTP 或 Docker 监控
- Hermes skills 目录（`~/.hermes/skills/`）中无 `transaction_manager` skill
- Hermes MCP 配置（`~/.config/opencode/opencode.json`）未注册 aisecretary 的 MCP 端点

## Users
- **Primary**: 运维者（owen），通过飞书 + Hermes 使用 aisecretary 事务管理功能，需要服务可靠、有监控、自动拉起
- **Not for**: 其他不依赖 aisecretary 的飞书用户；aisecretary 本身的开发者（不修改其代码）

## Hypothesis
我们相信 **将 aisecretary 纳入 myopenclaw 统一 compose 启动 + skill 重载 + Uptime Kuma 监控** 能够为 **运维者** 解决「服务挂了不知道、忘启动导致飞书功能不可用」的问题。
我们怎么知道做对了？当以下条件全部满足：
- Uptime Kuma 能监控到 aisecretary 的 HTTP health 端点和 Docker 容器状态
- Hermes 能通过 MCP 读取 aisecretary 数据库数据（list_transactions / summarize_transactions 返回正确结果）
- `./scripts/start.sh` 一次拉起包含 aisecretary 在内的全部服务
- 飞书上通过 Hermes 能查询事务数据库内容

## Success Metrics
| Metric | Target | How measured |
|---|---|---|
| aisecretary 被 Uptime Kuma 监控 | HTTP + Docker 双监控均上线 | Uptime Kuma Dashboard 可见绿色状态 |
| Hermes MCP 连通性 | 6 个 MCP tools 均可被 Hermes 发现并调用 | `list_transactions` 和 `summarize_transactions` 只读测试通过 |
| 启动链路完整性 | `./scripts/start.sh` 一次性拉起全部服务 | `docker compose ps` 显示 aisecretary 为 running |
| 数据库安全 | 测试全程零写入 | 测试前后 transactions 表行数不变 |

## Scope

**MVP** — 以下 4 项全部完成：
1. **Skill 重载**：将 aisecretary 的 `transaction_manager` skill 加载到 Hermes（symlink，不复制）
2. **Compose 集成**：aisecretary 服务加入 myopenclaw 的 `docker-compose.yml`，`./scripts/start.sh` 一键拉起
3. **Uptime Kuma 监控**：HTTP (`/health`) + Docker 容器状态双监控
4. **只读测试**：验证 Hermes → MCP → SQLite 读链路正常，确认数据库数据未被修改

**Out of scope**
- 修改 aisecretary 源代码 — aisecretary 是独立项目，本 PRD 只做集成
- 写入/修改/删除 aisecretary 数据库数据 — 红线，测试只读
- 为 aisecretary 新增功能 — 不改变现有 6 个 MCP tools 的行为
- 改变 aisecretary 的部署方式 — 保持 Docker + MCP SSE 架构不变
- aisecretary CLI 功能测试 — 只测 Hermes → MCP 通路

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->
<!-- Status: pending | in-progress | complete -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | 服务集成 | `./scripts/start.sh` 拉起全部服务含 aisecretary | complete | [plan](../plans/aisecretary-integration.plan.md) |
| 2 | Skill 重载 + MCP | Hermes 发现 aisecretary 6 个 MCP tools，transaction_manager skill 可用 | complete | [plan](../plans/aisecretary-integration.plan.md) |
| 3 | 监控上线 | Uptime Kuma 脚本已更新（待运行），HTTP + Docker 双监控 | complete | [plan](../plans/aisecretary-integration.plan.md) |
| 4 | 只读验证 | `list_transactions` 189 条，`summarize_transactions` 数据一致，数据库零写入 | complete | [plan](../plans/aisecretary-integration.plan.md) |

## Open Questions
- [ ] aisecretary 是否应该在 myopenclaw docker-compose.yml 中定义（build context 指向 `../aisecretary`），还是保留独立 compose 仅加入共享网络？— **待 `/plan` 决策**
- [ ] Hermes 的 MCP 连接是通过 opencode.json 配置还是通过 Hermes 自身的 MCP 配置机制？— **待 `/plan` 确认 opencode MCP 配置方式**
- [ ] `scripts/setup-uptime-kuma-monitors.py` 是否需要更新以自动发现 aisecretary，还是手动添加？— **待 `/plan` 决策**

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 测试时误写数据库 | Low | High | 所有测试工具调用只用 `list_transactions` 和 `summarize_transactions`（纯读）；测试前后记录行数对比 |
| aisecretary 和 myopenclaw 网络不互通 | Low | High | 确保 aisecretary 加入 `myopenclaw-net` 共享网络 |
| opencode MCP 配置格式和 aisecretary 的 SSE transport 不兼容 | Medium | Medium | aisecretary 使用 `/mcp` SSE 端点，需确认 opencode 支持的 MCP transport 类型 |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
