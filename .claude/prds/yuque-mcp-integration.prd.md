# 将语雀 MCP 服务接入 myopenclaw，供 Hermes 使用

**来源**：[GitHub Issue #51](https://github.com/OuyangWenyu/myopenclaw/issues/51)
**状态**：IMPLEMENTED / AUTOMATION VERIFIED — deployment-time manual read-only validation pending
**消费者**：Hermes

## Problem

`yuque_mcp_server` 已能通过 MCP 浏览、搜索、读取和备份语雀知识库，并能采集相邻快照生成净变化摘要，但它仍是独立仓库和独立服务。`myopenclaw` 尚未提供可复现的依赖获取、Docker 编排、凭据注入、数据持久化和 Hermes Skill，因此用户不能像使用“研发日报”一样，在 Hermes 中稳定地调用这些能力。

本功能需要解决的是集成和运维问题，而不是重新实现语雀 API 或 MCP 工具。

## Evidence

- 上游 `yuque_mcp_server` 已注册 6 个 MCP 工具：`list_docs`、`get_doc_content`、`get_repo_toc`、`search_docs`、`backup_repo`、`collect_and_get_change_summary`。
- 上游已提供 Python 3.11 Dockerfile，并能以 FastMCP SSE 模式在 18000 端口运行。
- myopenclaw 的“研发日报”已经验证“兄弟仓库 → Compose 独立服务 → Docker 内网 → Hermes Skill”的集成模式。
- 当前上游分支 `codex/docs-yuque-mcp-deployment-status` 固定为 commit `cc68fd0df172d3b8f24ae325998d56bdfd0e36e6`；该版本包含 Docker 上下文隔离、HTTP timeout/异步隔离、同仓库备份 single-flight、专用非 root 用户、`hmac.compare_digest` 认证、UID/GID 映射、root fail-closed 和 Windows bind mount 宿主机权限模式。

## Users

- **Primary**：myopenclaw 的 Hermes 使用者，通过自然语言访问语雀知识库。
- **Operator**：myopenclaw 运维者，负责配置 Token、启动可选服务、查看日志和管理本地持久化数据。
- **Not users in MVP**：OpenClaw 平台、Claude Code、其他 Agent 平台以及远程语雀 MCP 用户。

## Hypothesis

我们相信，将现有 `yuque_mcp_server` 按“研发日报”的本地托管模式接入 myopenclaw，可以让 Hermes 用户无需理解语雀 API 或手工运行独立服务，就能安全地完成目录浏览、标题搜索、正文读取、全库备份和相邻快照变化查询。

当用户可以从 Hermes 完成这些操作，容器重建后数据仍然存在，并且真实 Token 与正文不进入 Git、镜像层或普通日志时，即认为假设成立。

自动化验收采用与“研发日报”相同的边界：验证 Skill 规则、MCP 认证、Hermes 真实 MCP loader 的 6 工具发现，以及五条确定性 mock 工具旅程。Hermes 的真实模型分析和真实语雀只读调用留作部署后人工验证；未执行前，本产品假设和最终验收不得标记为完全成立。

## Success Metrics

| Metric | Target | How measured |
|---|---|---|
| 工具可用性 | Hermes 可发现并调用 6 个上游工具 | MCP 工具发现测试和代表性调用 |
| 部署可复现性 | 新环境可取得指定上游 commit 并单独构建服务 | 依赖脚本和 Compose 构建测试 |
| 数据持久性 | 快照和 Markdown 备份在容器重建后仍存在 | 重建前后 fixture 数据校验 |
| 凭据隔离 | `YUQUE_TOKEN` 只进入 `yuque-mcp`，不进入 Hermes | Compose 配置检查和容器环境检查 |
| 默认隔离 | 未显式启用时，不构建或启动 `yuque-mcp` | 默认启动回归检查 |
| 本机调试 | 宿主机只能通过 localhost:18000 访问 | 端口绑定静态检查 |

## Scope

### MVP

1. **固定上游依赖**
   - 将 `yuque_mcp_server` 克隆到 `../yuque_mcp_server`。
   - 来源分支为 `codex/docs-yuque-mcp-deployment-status`，固定完整 commit `cc68fd0df172d3b8f24ae325998d56bdfd0e36e6`。
   - 上游合并到 `main` 后改为固定对应合并 commit；有稳定 tag 后可迁移到 tag。

2. **独立 Docker 服务**
   - 在 myopenclaw Compose 中增加可选 `yuque-mcp` 服务，使用兄弟仓库作为 build context。
   - 服务加入 `myopenclaw-net`，Hermes 使用容器服务名访问。
   - 宿主机端口固定为 localhost 绑定：`127.0.0.1:${YUQUE_MCP_PORT:-18000}:18000`。
   - 默认启动流程只构建或启动 Hermes、backup-cron 和该服务。

3. **统一凭据配置**
   - `YUQUE_TOKEN`、`MCP_API_KEY` 和 `YUQUE_MCP_PORT` 使用项目根目录 `.env`。
   - `.env.example` 只记录变量名、说明和安全占位符。
   - `YUQUE_TOKEN` 只注入 `yuque-mcp`；Hermes 只获得连接 MCP 所需的认证信息。
   - 缺少必要凭据时安全失败，不能以无认证 cloud 模式继续运行。

4. **本地持久化**
   - 相邻快照数据保存到 `~/.myagentdata/yuque-mcp/change-data/`。
   - Markdown 备份保存到 `~/.myagentdata/yuque-mcp/backups/`。
   - 两类目录均不得提交到 Git；MVP 默认不进入 myopenclaw 云备份。

5. **Hermes 接入**
   - 为 Hermes 幂等注册本地 `yuque-mcp` SSE 服务，不覆盖已有 MCP 配置。
   - 新增 `yuque-knowledge` Skill，并在 Hermes 重建后自动恢复。
   - 向 Hermes 开放全部 6 个上游工具，包括 `backup_repo`。
   - Skill 明确完整 TOC、100 篇列表限制、最小化正文读取、备份确认、相邻快照语义和错误处理规则。

6. **验证与文档**
   - 覆盖依赖 ref、Compose、localhost 端口、凭据隔离、持久化挂载、网络连通性、MCP 工具发现和 Skill 安装。
   - 测试使用 mock 或 fixture，不读取或提交真实语雀正文。
   - 文档说明配置、启动停止、数据目录、安全边界和故障排查。

### Explicitly not in MVP

- 不新增 `/health` 或其他健康端点。
- 不配置 Uptime Kuma、Docker Compose healthcheck 或 MCP `initialize` 周期探测。
- 不依赖现有远程部署、UniVPN、内部 IP 或公网入口。
- 不让 OpenClaw、Claude Code 或其他 Agent 成为消费者。
- 不在 myopenclaw 中复制语雀 API、MCP tools、备份或快照差异逻辑。
- 不修改语雀文档，不抓取 Cookie、CSRF Token 或浏览器登录态。
- 不实现定时语雀日报推送。
- 不默认把语雀正文、快照或备份同步到云盘。
- 不声称能够恢复服务开始采集前的语雀历史版本或全部编辑参与者。

## Required User Journeys

以下五条旅程的工具选择、参数边界和响应语义由确定性 mock 工具契约自动验证；Hermes 使用真实模型生成最终分析的质量，以及真实语雀只读 API 返回，仅在部署后人工验证。

1. 用户要求“列出团队语雀知识库目录”，Hermes 使用 `get_repo_toc` 返回结构化目录。
2. 用户要求“搜索标题包含某关键词的文档”，Hermes 使用 `search_docs`，并说明其 100 篇列表边界。
3. 用户指定文档后要求总结，Hermes 使用 `get_doc_content`，不先批量读取全部正文。
4. 用户要求查看变化，Hermes 调用 `collect_and_get_change_summary`，首次返回初始化语义，后续只描述相邻完整快照的净变化。
5. 用户明确要求备份知识库，Hermes 调用 `backup_repo`，结果写入持久化备份目录。

## Acceptance Criteria

- [x] 依赖脚本能取得指定上游 commit，并能识别已有仓库是否偏离固定版本。
- [x] `yuque-mcp` 可单独构建、启动和停止，默认启动不强制启用它。
- [x] 宿主机仅通过 `127.0.0.1:${YUQUE_MCP_PORT:-18000}` 访问服务。
- [x] Hermes 通过 Docker 内网连接服务，不依赖 VPN 或远程地址。
- [x] Hermes 真实 MCP loader 能发现全部 6 个工具，五条旅程的确定性工具契约通过。
- [x] `backup_repo` 仅在用户明确要求时调用，备份写入持久化目录。
- [x] 快照和备份在容器重建后仍存在，且默认不进入云备份。
- [x] `YUQUE_TOKEN` 不下发给 Hermes；缺少必要凭据时服务安全失败。
- [x] Token、访问 Key、真实正文、备份、快照和内部地址不出现在 Git diff、镜像层、普通日志或测试 fixture 中。
- [x] 配置和 Skill 安装幂等，不覆盖用户已有 Hermes MCP 配置。
- [x] 不添加任何语雀健康检查机制。
- [x] 现有 Hermes、研发日报、OpenClaw 和其他服务的默认行为不受影响。
- [ ] 部署后使用真实只读 Token 人工验证语雀 API 与 Hermes 模型分析（非自动化验收项）。

## Delivery Milestones

| # | Milestone | Outcome | Status |
|---|---|---|---|
| 1 | 可复现依赖与服务编排 | 固定上游版本，`yuque-mcp` 可选启动 | automation complete |
| 2 | 安全配置与持久化 | 凭据隔离，快照和备份可持久保存 | automation complete |
| 3 | Hermes MCP 与 Skill | Hermes 发现 6 个工具并遵守调用规则 | automation complete |
| 4 | 集成验证与文档 | 代表性流程、重建恢复和安全检查通过 | automation complete; manual read-only validation pending |

## Confirmed Decisions

| Topic | Decision |
|---|---|
| 上游版本 | 来源分支 + 固定完整 commit；后续切换到 main 合并 commit 或稳定 tag |
| 凭据位置 | 项目根目录统一 `.env` |
| 宿主机端口 | localhost:18000，可通过 `YUQUE_MCP_PORT` 覆盖 |
| `backup_repo` | 向 Hermes 开放，但只在用户明确要求时调用 |
| 健康检查 | MVP 不添加任何健康检查机制 |
| 云备份 | 语雀正文、快照和备份默认不进入云备份 |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Hermes 当前版本的 SSE/Header 配置与假设不一致 | Medium | High | 实施首个技术检查验证真实配置格式，再写幂等配置逻辑 |
| 上游移动分支导致构建不可复现 | Medium | High | 新克隆固定完整 commit，已有仓库检查并报告偏离 |
| `backup_repo` 被误触发造成大量 API 调用和磁盘增长 | Medium | Medium | Skill 要求用户明确意图；避免并发与无意义重复调用 |
| 快照或备份泄露敏感正文 | Low | High | 仅本地持久化、禁止 Git/fixture/普通日志、云备份默认关闭 |
| 语雀 API 列表限制导致结果不完整 | High | Medium | 完整枚举使用 TOC；Skill 明示 `list_docs/search_docs` 的 100 篇边界 |
| 无健康检查导致静默故障发现较晚 | Medium | Medium | MVP 接受此限制；通过显式调用错误和集成测试诊断，后续另立需求评估监控 |

## Open Questions

无产品范围待确认问题。SSE、Authorization Header、工具发现和确定性旅程契约均已自动验证；真实语雀 API 与 Hermes 模型分析留作部署后人工只读验证。

---
*Status: IMPLEMENTED / AUTOMATION VERIFIED — deployment-time manual read-only validation pending.*
