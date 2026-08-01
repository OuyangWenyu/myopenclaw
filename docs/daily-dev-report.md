# 研发日报

基于 git-contribution-stats 的每日研发贡献报告，通过 repo-scanner MCP → Hermes skill → 飞书推送。

## 架构

```
git-contribution-stats/ (独立仓库)
  ├── scripts/collect.py        ← 每日采集 27 仓库 GitHub + GitCode 提交
  ├── core/report.py            ← 日报数据查询
  └── docker/mcp-server/        ← MCP server (build context for repo-scanner-mcp)
        │
        ▼
repo-scanner-mcp (容器, port 8001)
  3 个 MCP tools: get_daily_report, query_commits, query_authors
        │
        ▼
Hermes daily-dev-report skill
  → DeepSeek LLM 润色
  → 飞书私聊推送
```

## 调度

| 时间 | 任务 | 方式 |
|------|------|------|
| 07:45 | git-contribution-stats 采集 | launchd |
| 07:55 | Hermes cron 推送日报 | 容器内 Hermes cron |

## 常用命令

```bash
# 查看日报数据
docker compose exec repo-scanner-mcp python3 -c "from core.report import daily_report_as_dict; print(daily_report_as_dict())"

# 查看 MCP 连接
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp list | grep repo-scanner

# 查看 Hermes cron 状态
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list | grep daily-dev

# 手动推送测试
cat /tmp/report.txt | docker compose exec -T hermes python3 /opt/hermes-skills/daily-dev-report/tools/send_card.py
```

## 数据来源

git-contribution-stats 采集 27 个仓库（GitHub + GitCode），数据存储在 `~/.myagentdata/repo-scanner/repos.sqlite`（只读挂载到 repo-scanner-mcp 容器）。
