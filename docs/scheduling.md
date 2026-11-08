# 调度系统

myopenclaw 的定时任务分布在两层：**宿主机 launchd**（健康采集/心跳）和 **Docker 容器内调度器**（备份、Agent 工作流、语雀/研发数据）。本页是 myopenclaw 管理的定时任务的 single source of truth（个人 cron 不在此列）。**dailyinfo 的 8 个每日任务已迁到 dailyinfo 自己的 compose 栈**，见 [dailyinfo 调度](dailyinfo.md)。

## 总览

按执行时间排序的完整日程表：

| 时间 | 层级 | 调度器 | 任务 | 安装方式 |
|------|------|--------|------|----------|
| 每 60s | 宿主机 | launchd | Healthchecks.io 心跳 ping | `install-healthchecks-ping.sh` |
| 06:00 | Docker | yuque-mcp 调度器 | **语雀知识库快照**（变更基线） | 容器内置（`YUQUE_CHANGE_SNAPSHOT_TIME`） |
| 07:00 | Docker | yuque-mcp 调度器 | **语雀变更报告**生成（yuque-daily-digest 的数据源） | 容器内置（`YUQUE_CHANGE_REPORT_TIME`） |
| 07:30 | Docker | Hermes cron | **工作日晨间简报**（事务 + 邮件，周一至周六） | `start.sh` 自动注册 |
| 07:30 | Docker | Hermes cron（**道元 profile**） | **dailyinfo 全量简报镜像**（5 类正文 → 道元飞书私聊；`DAOYUAN_FEISHU_DM_CHAT_ID` 门控，`--no-agent` 不经过 LLM） | `start.sh` 幂等注册（`dailyinfo-digest`） |
| 07:45 | Docker | repo-scanner-mcp 调度器 | **git-contribution-stats 每日全量采集**（容器内 daily sync，唯一写者） | 容器内置（服务强制 `REPO_SCANNER_WATCH_OWNS_ISSUES=1`） |
| 07:45 | 宿主机 | launchd | **AgentOps 健康信号采集** | `install-all-schedulers.sh` |
| 07:50 | Docker | Hermes cron | **Daily Command Center**（TDAI 记忆 + 健康 + 场景） | `start.sh` 自动注册 |
| 07:55 | Docker | Hermes cron | **daily-dev-report**（研发贡献日报） | `start.sh` 自动注册 |
| 08:10 | Docker | Hermes cron | **yuque-daily-digest**（语雀知识库变更日报，需配置 `YUQUE_DAILY_PUSH_REPOS` + `YUQUE_MCP_URL` + `MCP_YUQUE_MCP_API_KEY`） | `start.sh` 自动注册 |
| 08:30 | Docker | **Hermes cron**（爱玛士 / 爱码士） | **服务器日报**（server-monitoring 摘要；爱玛士 → 飞书私聊，爱码士 → 预报系统群；`SERVER_MONITORING_MCP_URL` + `MONITORING_DIGEST_GROUP_CHAT_ID` 门控。⚠️ 域名入口在校园网可达时无需 UniVPN；回退内网直连才需要，部署到内网服务器后无此约束） | `start.sh` 幂等注册（`server-monitoring-digest`） |
| 每天 02:00 | Docker | crond (backup-cron) | 快照备份到云盘（清理过期快照仅在此执行） | entrypoint 自动 |

## 时序依赖

```
07:30           工作日晨间简报（周一至周六）— 事务 + 邮件汇总
07:30 ──────── dailyinfo-digest ← dailyinfo MCP（5 类全量正文 → 道元飞书私聊）
07:45 ──────── repo-scanner-mcp daily sync（容器内）+ AgentOps 采集（宿主）← 并行
07:50 ──────── Daily Command Center ← 读 inbox.md + TDAI 记忆
07:55 ──────── daily-dev-report ← 读 repo-scanner MCP (SQLite)
08:10 ──────── yuque-daily-digest ← 读本机 yuque-mcp 容器（07:00 已生成变更报告）
08:30 ──────── 服务器日报（Hermes 两条 cron：爱玛士私聊 / 爱码士预报系统群）
                 ← 读团队 server-monitoring MCP（校园域名入口或内网直连可达）
```

四个早间推送在 07:30–08:10 窗口内完成（含语雀日报 08:10），数据采集在推送前完成，保证数据新鲜。08:30 的服务器日报由 **Hermes 自己的 cron** 触发（爱玛士默认 profile + 爱码士 coder profile 各一条），不依赖宿主机 launchd；天一（OpenClaw）保留按需查询、不再主动推送。

**AgentOps 数据流**：`collect-agentops (launchd) → inbox.md → Daily Command Center (morning-triage-v2)`。系统健康信号由宿主机采集脚本确定性生成，Hermes skill 直接读取结构化文件，不再依赖 TDAI 记忆搜索。

## 一键安装

```bash
# 安装所有宿主机 launchd 定时任务（幂等，跳过缺失依赖）
./scripts/launchd/install-all-schedulers.sh
```

等价于手动执行：

```bash
./scripts/launchd/install-healthchecks-ping.sh                    # Healthchecks.io 心跳
./scripts/launchd/install-collect-agentops.sh                     # AgentOps 健康采集
```

> Git 数据采集的宿主 launchd 任务已于 **2026-11-03 退役**：它与容器 `repo-scanner-mcp`
> 的 07:45 daily sync 双写同一 SQLite（跨 virtiofs），会损坏数据库
> （`database disk image is malformed`）。每日全量采集现在只由容器负责。

Docker 容器内的定时任务（Hermes cron 默认 profile / coder / daoyuan + backup）由 `./scripts/start.sh` 和容器 entrypoint 自动注册，无需手动操作；yuque-mcp 容器的快照/报告调度为镜像内置（服务端启动即生效，无注册步骤）。其中 yuque-daily-digest 需在 `.env` **同时**配置 `YUQUE_DAILY_PUSH_REPOS`、`YUQUE_MCP_URL`、`MCP_YUQUE_MCP_API_KEY` 三者后才会注册（缺任一项静默跳过，只留一行提示）；dailyinfo-digest 需要 `DAOYUAN_FEISHU_DM_CHAT_ID` + `DAILYINFO_MCP_URL` + `DAILYINFO_MCP_KEY`。

> backup-cron 的保留策略（删除超过 `BACKUP_KEEP_DAYS` 的快照）**只在 02:00 这个定时任务里执行**；容器启动时的初始备份带 `BACKUP_SKIP_PRUNE=1`，重启不会删任何快照。详见 [备份系统](backup.md)。

## 验证

```bash
# 1. 检查所有 launchd 任务
launchctl list | grep -E 'ai\.(myopenclaw|git-contribution-stats)'

# 预期输出（launchctl 输出是三列 PID/退出码/Label，第 2 列是上次退出码，
# 0 或 1 均为正常）：
# -	0	ai.myopenclaw.collect-agentops
# -	0	ai.myopenclaw.healthchecks-ping

# ⚠️ 直接 grep 'ai\.' 会带出 ai.hermes.gateway
#（宿主机上的 Hermes 网关常驻服务，不由本仓安装，不是定时任务）。
# ai.git-contribution-stats.collect 已于 2026-11-03 退役，不应再出现。

# 2. 检查 backup-cron
docker compose exec backup-cron crontab -l

# 3. 检查 Hermes cron（默认 profile / coder / daoyuan）
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list
docker compose exec hermes-daoyuan /opt/hermes/.venv/bin/hermes -p daoyuan cron list
```

## 新机器检查清单

换台机器拉起服务后，逐项确认：

- [ ] `./scripts/launchd/install-all-schedulers.sh` 已执行，无报错
- [ ] `launchctl list | grep ai.` 输出包含全部已安装任务
- [ ] `docker compose ps` 所有服务 Up
- [ ] `docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list` 包含 "Daily Command Center"
- [ ] 若 `.env` 里 `YUQUE_DAILY_PUSH_REPOS` + `YUQUE_MCP_URL` + `MCP_YUQUE_MCP_API_KEY` 三者都已配置，cron list 还应包含 "yuque-daily-digest"
- [ ] 若 `.env` 里 `DAOYUAN_FEISHU_DM_CHAT_ID` + `DAILYINFO_MCP_URL` + `DAILYINFO_MCP_KEY` 已配置，`hermes -p daoyuan cron list` 应包含 "dailyinfo-digest"（no-agent）
- [ ] Healthchecks.io Dashboard 显示 "Last Ping: just now"（等 60s 后刷新）
- [ ] 次日 07:50–08:10 检查飞书是否收到晨间四签推送（含 08:10 语雀日报）

## 卸载

```bash
# 卸载单个任务
launchctl unload -w ~/Library/LaunchAgents/<label>.plist
```

## 故障排查

### 某些 launchd 任务未运行

```bash
# 查看上次运行日志
tail -50 logs/collect-agentops.log
tail -50 logs/healthchecks-ping.log

# 手动触发
launchctl start ai.myopenclaw.collect-agentops
```

### morning-triage 未触发

```bash
# 查看 Hermes cron 列表
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list

# 查看 agent 日志
docker compose exec hermes tail -100 /opt/data/logs/agent.log | grep "Daily Command"
```

### dailyinfo 推送未收到

参见 [dailyinfo 调度](dailyinfo.md) 的故障排查部分。

## macOS 特定

所有 launchd 任务依赖 macOS launchd。Linux 上的替代方案：

| launchd | Linux 等价物 |
|---------|-------------|
| `~/Library/LaunchAgents/*.plist` | systemd user timer (`~/.config/systemd/user/`) |
| `launchctl load -w` | `systemctl --user enable --now` |

systemd 等价物尚未提供（详见 [可移植性](portability.md)）。
