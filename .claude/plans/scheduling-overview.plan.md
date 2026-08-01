# Plan: 调度系统总览 + 一键安装

**Complexity**: Small
**Type**: Docs + one new script

## 问题

当前文档中宿主机 launchd 定时任务散落在 3 个页面（dailyinfo.md、agentops.md、monitoring.md），Docker 容器内的 cron 任务（backup、morning-triage、cc-connect）没有统一可见的地方。换台机器后，"装哪些定时任务才能复现功能" 没有明确答案。

## 现状摸底

### 宿主机 launchd（10 个）

| 时间 | Label | 命令 | 安装脚本 |
|------|-------|------|----------|
| 03:00 | ai.dailyinfo.run-arxiv | `uv run dailyinfo run -p 3` | install-dailyinfo.sh |
| 03:30 | ai.dailyinfo.run-resource | `uv run dailyinfo run -p 5` | install-dailyinfo.sh |
| 03:45 | ai.dailyinfo.run-code | `uv run dailyinfo run -p 4` | install-dailyinfo.sh |
| 04:00 | ai.dailyinfo.run-papers | `uv run dailyinfo run -p 1` | install-dailyinfo.sh |
| 04:30 | ai.dailyinfo.run-ai_news | `uv run dailyinfo run -p 2` | install-dailyinfo.sh |
| 05:30 | ai.dailyinfo.push-early | `uv run dailyinfo push --categories ai_news,code,resource` | install-dailyinfo.sh |
| 06:00 | ai.dailyinfo.push-papers | `uv run dailyinfo push --categories papers` | install-dailyinfo.sh |
| 07:00 | ai.dailyinfo.push-arxiv | `uv run dailyinfo push --categories arxiv` | install-dailyinfo.sh |
| 07:45 | ai.myopenclaw.collect-agentops | `python3 scripts/collect_agentops.py` | install-collect-agentops.sh |
| 每 60s | ai.myopenclaw.healthchecks-ping | `scripts/healthchecks-ping.sh` | install-healthchecks-ping.sh |

### Docker 容器内 cron（4 个）

| 时间 | 容器 | 调度器 | 任务 | 注册方式 |
|------|------|--------|------|----------|
| 每周日 02:00 | backup-cron | crond | `backup-all-docker.sh` | entrypoint 自动 |
| 每天 07:50 | hermes | Hermes cron | morning-triage-v2 | start.sh 自动注册 |
| 每周日 08:00 | claude-code | cc-connect cron | AI News 周报生成 | entrypoint 自动 |
| 每周日 08:10 | claude-code | cc-connect cron | AI News 周报润色 | entrypoint 自动 |

### 关键时序依赖

```
03:00  dailyinfo run (5 个抓取任务)
05:30  dailyinfo push (早间推送)
06:00  dailyinfo push (论文推送)
07:00  dailyinfo push (arXiv 推送)
07:45  collect-agentops (健康信号采集) ← 必须在 morning-triage 前完成
07:50  morning-triage-v2 (晨间三签，消费 AgentOps 信号)
```

## 变更范围

### 1. 新建 `docs/scheduling.md` — 调度系统总览

统一页面，作为所有定时任务的 single source of truth：
- **总览表**：全部 14 个定时任务，分「宿主机 launchd」和「Docker 容器内」两组
- **时间线**：按时间排序的完整日程表，标注时序依赖
- **安装命令**：3 个 install 脚本 + 一键安装
- **验证方法**：如何确认所有调度已生效
- **新机器检查清单**：装完系统后要确认哪些调度在跑

### 2. 新建 `scripts/launchd/install-all-schedulers.sh`

一键安装所有宿主机 launchd 任务，幂等、跳过缺失依赖：

```bash
./scripts/launchd/install-all-schedulers.sh
```

行为：
- 调用 `install-dailyinfo.sh`（若 `~/code/dailyinfo` 存在）
- 调用 `install-healthchecks-ping.sh`（检查 `.env` 中 `HEALTHCHECKS_PING_URL`）
- 调用 `install-collect-agentops.sh`
- 每个步骤独立，失败不阻塞后续
- 结束时打印状态总览

### 3. 更新现有文档

| 文件 | 变更 |
|------|------|
| `mkdocs.yml` | nav 新增「调度系统」条目（运维分组） |
| `docs/index.md` | 新增调度系统链接 |
| `docs/setup.md` | 启动后增加「安装定时任务」步骤 |
| `docs/portability.md` | launchd 替代方案处链接到 scheduling.md |
| `docs/dailyinfo.md` | 添加「详见调度总览」交叉引用 |
| `docs/agentops.md` | 添加「详见调度总览」交叉引用 |
| `docs/monitoring.md` | healthchecks-ping 处链接到 scheduling.md |

## 不在本次范围

- 不创建 Linux systemd 等价物（另开 issue）
- 不改动 Docker 容器内的 cron 注册逻辑
- dailyinfo 本身不是 myopenclaw 维护的，保持现状

## 验收

- [ ] `docs/scheduling.md` 覆盖全部 14 个定时任务
- [ ] `scripts/launchd/install-all-schedulers.sh` 幂等可运行
- [ ] `mkdocs build --strict` 通过
- [ ] 新机器按 scheduling.md 操作可复现全部调度
