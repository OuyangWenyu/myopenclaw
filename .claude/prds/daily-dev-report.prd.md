# 每日研发贡献报告

## Problem
当前 repo-triage 推送以仓库为视角，逐仓库列出 commits/PRs 标题和作者，信息碎片化。作为团队管理者，无法在 1 分钟内掌握"昨天谁做了什么、整体产出如何、核心主题是什么"。需要每天手动翻 GitHub/GitCode 补充信息，效率低且容易遗漏。

## Evidence
- repo-triage 日推已验证数据管道可用（27 repos, 349 commits/day），但推送内容停留在"逐仓列举"层次
- 用户已有月度绩效报告模板（团队级统计 + 核心战役总结 + 个人小结），期望日报达到类似深度
- 用户需向研发管理层或研发人员转发贡献摘要，日报是第一读者消费物

## Users
- **Primary**: OuyangWenyu（团队管理者）— 每天早晨查看昨日研发全景
- **Secondary**: 研发管理层 / 研发人员 — 接收转发（手动，非自动推）

## Hypothesis
We believe **每日研发贡献报告（人视角 + 代码量统计 + 主题归纳 + 个人小结）** will **让团队管理者在 1 分钟内掌握昨日研发全貌** for **OuyangWenyu**.
We'll know we're right when **用户不再需要手动翻 GitHub/GitCode 来了解团队昨日产出**。

## Success Metrics
| Metric | Target | How measured |
|---|---|---|
| 信息完整度 | 日报覆盖所有跟踪仓库的昨日活动 | 推送后核对 SQLite 数据 |
| 阅读效率 | 1 分钟内掌握全貌 | 用户反馈 |
| 推送可靠性 | 每天 07:55 准时推送 | Hermes cron status |

## Scope

### ✅ Done — git-contribution-stats（已实现）
1. commits 表 `additions INTEGER`, `deletions INTEGER` — 已加列 + schema 自动迁移
2. GitHub commit detail API 采集 — `_fetch_commit_detail()` 取 stats，失败返回 None（不覆盖已有数据）
3. 日报生成器 `core/report.py` — `DailyReport` dataclass: overall stats + per-person breakdown
4. MCP server `mcp_server/server.py` — SSE transport (port 8001)，3 个 tools: `get_daily_report` / `query_commits` / `query_authors`
5. Docker `docker/mcp-server/Dockerfile` — `python:3.12-slim` + `mcp==1.28.1`，默认 `--transport sse --port 8001`

### 📋 TODO — myopenclaw（本仓库）
6. **docker-compose**: 新增 `repo-scanner-mcp` service
   - image: 从 git-contribution-stats 构建或拉取
   - port: 8001（仅内网，不暴露宿主机）
   - volume: `~/.myagentdata/repo-scanner:/data:ro`
   - env: `REPO_SCANNER_DB_PATH=/data/repos.sqlite`
   - 参考 aisecretary 的集成模式
7. **Hermes MCP 注册**: `~/.hermes/config.yaml` 加 `mcp_servers.repo-scanner` + `platform_toolsets.cli` 加 `mcp-repo-scanner`
8. **Hermes skill**: `skills/daily-dev-report/SKILL.md`
   - 调 MCP `get_daily_report`（昨日日期）
   - DeepSeek 润色：主题聚类 + 核心战役总结 + 个人工作小结
   - 推送：`send_card.py` → Hermes 飞书私聊 (`LARK_USER_OPEN_ID`)
9. **Hermes cron**: 每天 07:55 北京 (UTC 23:55) 触发

### Out of scope
- 月度汇总报告（日报数据已存储，月度报告在后续 PRD 中单独设计）
- 多用户推送（仅推送庄赖宏本人）
- Web UI / 可视化面板（先做数据，后做界面）

## Delivery Milestones

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | commits 表加列 + 增强采集 | GitHub/GitCode commit 采集带回代码行数 | ✅ complete | — |
| 2 | 日报生成器 `core/report.py` | 确定性聚合，输出结构化日报 | ✅ complete | — |
| 3 | MCP server 容器化 (SSE, port 8001) | `repo-scanner-mcp` container 可被 Hermes 调用 | ✅ complete | — |
| 4 | myopenclaw: docker-compose + Hermes MCP 注册 + skill + cron | Hermes 每天 07:55 推送日报到飞书私聊 | in-progress | `.claude/plans/daily-dev-report.plan.md` |

## Open Questions
- [ ] GitHub commit detail API 需逐 commit 调用 — 对于活跃仓库，~100 commits/day × 多仓库，API rate limit 是否够？需实测
- [ ] 个人小结的 LLM prompt 需要迭代调优 — 初始版用 LLM 直接生成，后续根据用户反馈调整
- [ ] email_name_mapping.csv 是否覆盖所有 contributor？需补充映射

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GitHub API rate limit 不够（逐 commit 调 detail） | Medium | High | 先用 list commits API (含 stats)，不够再降级 |
| LLM 生成质量不稳定 | Medium | Medium | 确定性模板 fallback；prompt 迭代 |
| 27 repos 采集时间过长超时 | Low | Medium | 已有 ThreadPoolExecutor(5)，按需调大并发 |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
