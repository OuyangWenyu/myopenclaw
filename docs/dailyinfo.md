# dailyinfo 调度

[dailyinfo](https://github.com/iHeadWater/dailyinfo) 是独立的 AI for Science 情报聚合仓，提供幂等 CLI。myopenclaw 通过宿主机 launchd 托管其定时调度。数据落在 `~/.myagentdata/dailyinfo/`，由 `backup-cron` 自动备份。

## 前置条件

dailyinfo 需要 FreshRSS 容器常驻。myopenclaw 的 `freshrss` 服务已包含。首次部署或停机后，启动 dailyinfo：

```bash
cd ~/code/dailyinfo && uv run dailyinfo start
```

## 调度表

| 时间（Asia/Shanghai） | LaunchAgent Label | 命令 |
|---|---|---|
| 03:00 | `ai.dailyinfo.run-arxiv` | `uv run dailyinfo run -p 3`（arXiv 论文） |
| 03:30 | `ai.dailyinfo.run-resource` | `uv run dailyinfo run -p 5`（资源汇总） |
| 03:45 | `ai.dailyinfo.run-code` | `uv run dailyinfo run -p 4`（代码趋势） |
| 04:00 | `ai.dailyinfo.run-papers` | `uv run dailyinfo run -p 1`（期刊论文） |
| 04:30 | `ai.dailyinfo.run-ai_news` | `uv run dailyinfo run -p 2`（AI 资讯） |
| 05:30 | `ai.dailyinfo.push-early` | `uv run dailyinfo push --categories ai_news,code,resource` |
| 06:00 | `ai.dailyinfo.push-papers` | `uv run dailyinfo push --categories papers` |
| 07:00 | `ai.dailyinfo.push-arxiv` | `uv run dailyinfo push --categories arxiv` |

## 安装 / 卸载

```bash
./scripts/launchd/install-dailyinfo.sh     # 安装
./scripts/launchd/uninstall-dailyinfo.sh   # 卸载
```

若 dailyinfo 放在非默认路径，用环境变量覆盖：

```bash
DAILYINFO_DIR=/path/to/dailyinfo ./scripts/launchd/install-dailyinfo.sh
```

## 失败排查

1. 看日志：
   ```bash
   tail -n 200 ~/code/dailyinfo/logs/dailyinfo-*.log
   ```
2. 看 launchd 退出码（非 0/1 才需要关注）：
   ```bash
   launchctl list | grep ai.dailyinfo
   ```
3. 进 dailyinfo 目录跑状态检查：
   ```bash
   cd ~/code/dailyinfo && uv run dailyinfo status
   ```

## 告警策略

- `run` / `push` 返回退出码 0（至少成功处理 1 份）和退出码 1（当天已全部处理或无新内容）**都是正常状态**，不要作为失败告警
- 真正需要关注的是「连续若干天日志不再更新」「退出码 ≥ 2」「进程崩溃」

## 不在本仓维护的内容

dailyinfo 的 secret、数据源配置、抓取/AI 摘要/推送业务逻辑全部由 dailyinfo 仓自己管理。本仓只负责定时触发和备份覆盖。
