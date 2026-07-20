# AgentOps 健康采集

> 本文是 AgentOps 健康采集的详细说明。全部 14 个定时任务的总览见 [调度系统](scheduling.md)。

每天 07:45 自动采集 5 种系统健康信号，写入 ledger 供晨间三签（morning-triage-v2）消费。

## 采集的信号

| 信号 | 检测方式 | 阈值（可配置） |
|------|----------|---------------|
| 容器近期重启 | `docker compose ps --format json` 解析运行时间 | < 2h 内重启 |
| 备份过期 | 检查 `latest/` 符号链接的修改时间 | > 24h 未更新 |
| 磁盘使用率 | `df -P /System/Volumes/Data` | > 85% |
| 网关错误循环 | `scripts/check-gateway-errors.sh --json` | 检测到重复错误 |
| 容器健康状态 | `docker compose ps` JSON 中的 `(unhealthy)` 状态 | 任何 unhealthy |

## 调度

通过宿主机 launchd 每天 07:45（Asia/Shanghai）自动触发，早于晨间三签（07:50）：

```bash
# 安装定时任务
./scripts/launchd/install-collect-agentops.sh

# 手动触发
launchctl start ai.myopenclaw.collect-agentops

# 查看日志
tail -f logs/collect-agentops.log
```

## 输出

采集结果写入 `~/.myagentdata/agentops/inbox.md`。自动采集项（`source: auto`）每次覆盖刷新，手动添加的条目保留不变。

```markdown
## 容器 hermes 近期重启
- date: 2026-07-20
- source: auto | docker compose ps
- status: watch
- owner: owen
- evidence: 容器 hermes 运行时间: Up 30 minutes（< 2h 阈值）
- why_it_matters: 容器 hermes 在最近 2 小时内重启过，可能发生过崩溃或被手动重启
- suggested_next_action: 检查 docker compose logs hermes --tail 50 确认重启原因
- needs_human_decision: no
```

### 在晨间三签中的使用

morning-triage-v2 通过关键词搜索 TDAI 记忆来消费 AgentOps 信号。当采集到异常信号时，晨间三签报告会包含系统健康小结。

## 配置

通过环境变量覆盖默认阈值：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENTOPS_RESTART_THRESHOLD` | `2` | 容器重启检测窗口（小时） |
| `AGENTOPS_BACKUP_STALE_HOURS` | `24` | 备份过期阈值（小时） |
| `AGENTOPS_DISK_THRESHOLD` | `85` | 磁盘使用率告警阈值（百分比） |
| `AGENTOPS_DATA_DIR` | `~/.myagentdata/agentops` | 输出目录 |
| `AGENTOPS_BACKUP_ROOT` | `~/Google Drive/.../myopenclaw-backups` | 备份目录路径 |

## 手动运行

```bash
# 采集并写入 ledger
python3 scripts/collect_agentops.py

# 预览模式（不写入）
python3 scripts/collect_agentops.py --dry-run
```

## 故障排查

### 采集未触发

```bash
# 检查 launchd 任务
launchctl list | grep collect-agentops

# 查看上次运行日志
tail -50 logs/collect-agentops.log
```

### 输出文件为空

采集脚本在系统正常时不会写入内容（无异常 = 无条目）。手动运行 `--dry-run` 查看检测结果。
