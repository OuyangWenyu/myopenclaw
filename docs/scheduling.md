# 调度系统

myopenclaw 的定时任务分布在两层：**宿主机 launchd**（数据采集和推送）和 **Docker 容器内调度器**（备份和 Agent 工作流）。本页是全部 14 个定时任务的 single source of truth。

## 总览

按执行时间排序的完整日程表：

| 时间 | 层级 | 调度器 | 任务 | 安装方式 |
|------|------|--------|------|----------|
| 每 60s | 宿主机 | launchd | Healthchecks.io 心跳 ping | `install-healthchecks-ping.sh` |
| 03:00 | 宿主机 | launchd | arXiv 论文抓取 | `install-dailyinfo.sh` |
| 03:30 | 宿主机 | launchd | 资源汇总抓取 | `install-dailyinfo.sh` |
| 03:45 | 宿主机 | launchd | 代码趋势抓取 | `install-dailyinfo.sh` |
| 04:00 | 宿主机 | launchd | 期刊论文抓取 | `install-dailyinfo.sh` |
| 04:30 | 宿主机 | launchd | AI 资讯抓取 | `install-dailyinfo.sh` |
| 05:30 | 宿主机 | launchd | 推送：AI 资讯 + 代码 + 资源 | `install-dailyinfo.sh` |
| 06:00 | 宿主机 | launchd | 推送：期刊论文 | `install-dailyinfo.sh` |
| 07:00 | 宿主机 | launchd | 推送：arXiv 论文 | `install-dailyinfo.sh` |
| 07:45 | 宿主机 | launchd | **AgentOps 健康信号采集** | `install-collect-agentops.sh` |
| 07:50 | Docker | Hermes cron | **Morning Triage v2（晨间三签）** | `start.sh` 自动注册 |
| 每周日 02:00 | Docker | crond (backup-cron) | 快照备份到云盘 | entrypoint 自动 |
| 每周日 08:00 | Docker | cc-connect cron | AI News 周报生成 | entrypoint 自动 |
| 每周日 08:10 | Docker | cc-connect cron | AI News 周报润色 + 飞书推送 | entrypoint 自动 |

## 时序依赖

```
03:00 ─ 04:30  dailyinfo 5 个抓取任务并行
05:30           dailyinfo 推送（AI 资讯 + 代码 + 资源）
06:00           dailyinfo 推送（期刊论文）
07:00           dailyinfo 推送（arXiv 论文）
07:45 ──────── AgentOps 健康采集 ← 必须在 morning-triage 前完成
07:50 ──────── Morning Triage v2 ← 消费 AgentOps 信号 + TDAI 记忆
```

AgentOps 在晨间三签前 5 分钟运行，确保健康信号（容器重启、备份过期、磁盘使用率、网关错误）是最新的。

## 一键安装

```bash
# 安装所有宿主机 launchd 定时任务（幂等，跳过缺失依赖）
./scripts/launchd/install-all-schedulers.sh
```

等价于手动执行：

```bash
./scripts/launchd/install-dailyinfo.sh        # dailyinfo 8 个 launchd 任务
./scripts/launchd/install-healthchecks-ping.sh # Healthchecks.io 心跳
./scripts/launchd/install-collect-agentops.sh  # AgentOps 健康采集
```

Docker 容器内的定时任务（backup、morning-triage、AI News）由 `./scripts/start.sh` 和容器 entrypoint 自动注册，无需手动操作。

## 验证

```bash
# 1. 检查所有 launchd 任务
launchctl list | grep -E 'ai\.(dailyinfo|myopenclaw)'

# 预期输出（10 个任务，ExitCode 0 或 1 均为正常）：
# ai.dailyinfo.run-arxiv      0
# ai.dailyinfo.run-resource   0
# ai.dailyinfo.run-code       0
# ai.dailyinfo.run-papers     0
# ai.dailyinfo.run-ai_news    0
# ai.dailyinfo.push-early     0
# ai.dailyinfo.push-papers    0
# ai.dailyinfo.push-arxiv     0
# ai.myopenclaw.collect-agentops  0
# ai.myopenclaw.healthchecks-ping 0

# 2. 检查 backup-cron
docker compose exec backup-cron crontab -l

# 3. 检查 Hermes cron
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list | grep "Daily Command"

# 4. 检查 cc-connect cron
docker compose exec claude-code bash -c 'echo "cron list" | nc -U /root/.cc-connect/run/api.sock' 2>/dev/null | grep "AI News"
```

## 新机器检查清单

换台机器拉起服务后，逐项确认：

- [ ] `./scripts/launchd/install-all-schedulers.sh` 已执行，无报错
- [ ] `launchctl list | grep ai.` 输出包含全部已安装任务
- [ ] `docker compose ps` 所有服务 Up
- [ ] `docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list` 包含 "Daily Command Center"
- [ ] Healthchecks.io Dashboard 显示 "Last Ping: just now"（等 60s 后刷新）
- [ ] 次日 07:50 检查飞书是否收到晨间三签推送

## 卸载

```bash
# 卸载所有 dailyinfo 任务
./scripts/launchd/uninstall-dailyinfo.sh

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
