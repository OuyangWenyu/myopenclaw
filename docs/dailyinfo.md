# dailyinfo（独立栈 + MCP 消费）

> 最后更新：2026-11-08（调度与 FreshRSS 迁到 dailyinfo 独立 compose 栈）
>
> 托管模式的取舍见 [依赖服务容器化](dependency-services.md)。

[dailyinfo](https://github.com/iHeadWater/dailyinfo) 现在是**自包含服务栈**：采集、生成、Discord 推送、RSS 数据源和只读 MCP 都在 dailyinfo 仓自己的 compose 里，由 dailyinfo 容器内的 scheduler 负责每日执行。**myopenclaw 不再托管 dailyinfo 的定时任务，也不再运行 FreshRSS**；本仓只做两件事：

1. 通过 MCP 读取 dailyinfo 简报（道元、虾酱）
2. 用 `backup-data.sh` 备份 `~/.myagentdata/dailyinfo`（数据由 dailyinfo 写入）

## dailyinfo 栈的形态

```text
dailyinfo compose（~/code/dailyinfo，docker compose up -d）
├─ freshrss        RSS 采集，127.0.0.1:8081，内部服务
├─ scheduler       常驻；supercronic 读 deploy/crontab.full
├─ collector       手工排障（profiles: [jobs]）
└─ dailyinfo-mcp   只读 MCP，127.0.0.1:8768
```

调度表（`Asia/Shanghai`，scheduler 容器内）：

| 时间 | Job |
|---|---|
| 03:00 | arxiv |
| 03:30 | resource |
| 03:45 | code |
| 04:00 | papers |
| 04:30 | ai_news |
| 05:30 | push_early（ai_news / code / resource） |
| 06:00 | push_papers |
| 07:00 | push_arxiv |

- 数据根：`~/.myagentdata/dailyinfo`（`users/owen/` 是历史 FreshRSS 库，切换后原样沿用）
- 调度实现、补跑、健康检查、单写者锁：见 dailyinfo 的 `docs/production.md`
- 切换手册：dailyinfo 的 `docs/migration-selfhosted-scheduler-20261107.md`

## 本机启动

`./scripts/start.sh` 会在拉起主栈之前**顺手拉起 dailyinfo 独立栈**（等价于 `cd ~/code/dailyinfo && docker compose up -d`）；dailyinfo 缺失或启动失败只警告，不阻断主栈。dailyinfo 部署在远程（如 81）时关掉：

```env
DAILYINFO_LOCAL_STACK=0
```

`./scripts/stop.sh` 只停 myopenclaw，**不会停 dailyinfo**（独立栈）；需要时手动：

```bash
cd ~/code/dailyinfo && docker compose stop
```

## myopenclaw 侧配置

仓库根 `.env`（gitignored）：

```env
# dailyinfo 只读 MCP（本地走宿主 loopback；远程 81 则填 http://<MCP_HOST>:8768/mcp）
DAILYINFO_MCP_URL=http://host.docker.internal:8768/mcp
DAILYINFO_MCP_KEY=<dailyinfo keys.json 里的个人 key>
```

`./scripts/start.sh` 会幂等写入两个客户端条目（占位符形式，真实值在容器环境展开）：

| Agent | 配置文件 | 注册脚本 |
|---|---|---|
| 道元（Hermes daoyuan profile） | `~/.hermes/profiles/daoyuan/config.yaml` | `scripts/ensure_hermes_dailyinfo.py` |
| 虾酱（OpenClaw 主网关） | `~/.openclaw/openclaw.json` | `scripts/ensure_openclaw_dailyinfo.py` |

两者都遵循 **URL 非空 = 开关**：`.env` 里留空则移除受管条目。

## 道元私聊全量镜像

除了让 agent 按需查询，道元还会每天 **07:30（北京）** 把当天全部 5 类简报的正文**原样**推到「道元 bot 与用户的飞书私聊」。这是一条 **Hermes no-agent cron**（`dailyinfo-digest`），脚本 stdout 由 Hermes 直接投递，**不经过 LLM**：

```text
Hermes cron（30 23 * * * UTC）
  → ~/.hermes/profiles/daoyuan/scripts/dailyinfo_digest.py
  → dailyinfo MCP: list_briefings + get_briefing（跟随 next_cursor 取全文）
  → stdout（5 类正文）
  → Hermes 飞书适配器按 8000 字符自动分片
  → 道元私聊
```

配置（仓库根 `.env`，gitignored）：

```env
DAOYUAN_FEISHU_DM_CHAT_ID=oc_...   # 用 hermes -p daoyuan send --list feishu 查看
```

- 为空 = 不注册 job（已有 job 不会自动删除）；格式必须是 `oc_` + 32 位十六进制
- 数据可见性跟随 dailyinfo 的 `DAILYINFO_MCP_VISIBILITY`（当前 `generated`）：
  **不依赖 Discord 推送成功**
- body 会做**飞书友好清洗**（只影响镜像，不改 dailyinfo 原文）：`   > 摘要` 转成
  `**摘要：** 摘要`（飞书卡片不渲染 blockquote），`&lt;i&gt;` / `<em>` / `<sub>` 等
  HTML 解码后去标签（否则会显示成源码）
- 占位/无更新正文不投递；缺失的分类显示 `📭 … 今日暂无简报`
- `STALE_CURSOR` / `CONTENT_CHANGED`（读取期间内容变化）会退避重读 2 次（1s、2s），
  仍失败才降级；错误码取 MCP 包装文本（`Error executing tool …: CODE`）的末尾，
  不整串比较
- 单类拉取失败显示 `⚠️ …`，其余类别照常投递；整体 MCP 不可达 → 非 0 退出，
  Hermes 把失败告警投到同一私聊
- 已知误报：`CONTENT_CHANGED` 偶发而内容其实没变（2026-11-10 papers 一次：读取窗口内
  宿主全树无任何写入，重读即恢复）。根因未证实，不要据此推断数据损坏
  - **2026-11-15 定位并修复**：`dailyinfo-mcp` 的 `ReadOnlyQueries._signature()` 把
    `st_ino` 算进签名，而 Docker Desktop 的 virtiofs 绑定挂载在容器内 `stat` 同一文件
    会返回**不同 inode**（宿主机侧稳定，容器侧不稳定）⇒ 两次签名必然不同，
    `CONTENT_CHANGED` 稳定复现，重试无效。日志证据：容器内连续三次 `os.stat` 同一
    `/data/publications/...json` 得到三个不同 `st_ino`。已在 dailyinfo 侧去掉
    `st_ino`（保留 mtime+size）并重建 `dailyinfo-mcp` 镜像，当天道元 5 类正文已补发。
    本仓自检脚本会提示该根因，但**不会**自动改 dailyinfo 代码；若它再次出现，说明
    镜像回退到了旧代码。
- 脚本每次 `./scripts/start.sh` 覆盖安装（仓库是唯一版本源，不要手改 profile 里的副本）
- `cron.wrap_response: false` 由 `scripts/ensure_hermes_cron_config.py` 幂等写入，
  否则消息会带 `Cronjob Response: …` 头尾样板

手工补推/预览：

```bash
# 只打印，不投递
docker compose exec -T hermes-daoyuan /opt/hermes/.venv/bin/python \
  /opt/data/profiles/daoyuan/scripts/dailyinfo_digest.py --date 2026-11-08 | head -40

# 真实投递一次（会发到私聊）
docker compose exec -T hermes-daoyuan /opt/hermes/.venv/bin/hermes -p daoyuan \
  cron run dailyinfo-digest
docker compose exec -T hermes-daoyuan /opt/hermes/.venv/bin/hermes -p daoyuan \
  cron runs dailyinfo-digest --limit 3

# 关闭 / 删除
docker compose exec -T hermes-daoyuan /opt/hermes/.venv/bin/hermes -p daoyuan \
  cron remove dailyinfo-digest
```

## 每日推送自检（07:45）

三路推送的完成时间：Discord 05:30–07:00、道元 07:30、网页 07:40。**07:45** 由宿主
launchd 跑一次确定性自检，失败的通道做**一次幂等补救**并复检；同一时刻 DSH 还会跑一条
agent 定时任务做根因诊断（见下）。

```text
launchd ai.myopenclaw.dailyinfo-push-check（07:45）
  → python3 scripts/check_dailyinfo_push.py --fix
      Discord: deliveries/discord/<cat>-<date>.json 必须 success → 否则重跑 scheduler job
      网页:    deliveries/web/<cat>-<date>.json（等 07:40 的 publish 跑完）→ 否则 launchctl start
      道元:    daoyuan cron executions.db 当天最后一条必须 completed → 否则 hermes cron run
  → logs/dailyinfo-push-check.log（人读）+ logs/dailyinfo-push-check.json（状态）
```

- **预期分类**由当天的 canonical publication 决定（`publications/briefings/<Y>/<M>/<D>/<cat>/briefing.json`）；
  某类当天没有内容（例如 arxiv 为空）就**不检查**，不会误报
- **修复动作**：Discord → `python -m scripts.scheduler run-job push_early|push_papers|push_arxiv`
  （同时把 scheduler 的失败状态记成成功）；网页 → `launchctl start ai.dailyinfo.web-publish`；
  道元 → `hermes -p daoyuan cron run <job_id>`
- **不重试已知确定性失败**：自检会先在容器里无投递地跑一遍 digest 采集并展开
  `ExceptionGroup`，拿到真实错误码；`CONTENT_CHANGED` 这类重试无意义的错误只报告根因，
  不再触发一次会往飞书私聊发失败告警的重跑
- 退出码：`0` 三路都成功 / `1` 仍有失败 / `2` 配置错误；`--json` 供机器消费，
  `--no-wait` 跳过等待进行中的 web/digest
- 安装 / 手动触发：

```bash
./scripts/launchd/install-dailyinfo-push-check.sh   # 已含在 install-all-schedulers.sh 里
launchctl start ai.myopenclaw.dailyinfo-push-check
python3 scripts/check_dailyinfo_push.py             # 只检查
python3 scripts/check_dailyinfo_push.py --fix       # 检查 + 补救 + 复检
tail -f logs/dailyinfo-push-check.log
```

**DSH agent 任务**（同上 07:45，跨天连续会话）：读 `--json` 结果；三路都成功就只回一行
结论；有失败则按根因排查（容器网络 / MCP 错误码 / 发布进程 / Hermes 投递），能修则修、
修不了给出结论与下一步。它和 launchd 共用 `logs/.dailyinfo-push-check.lock`，后来者等
前者跑完再复检，不会重复投递。

## 验证

```bash
# dailyinfo 栈本身
cd ~/code/dailyinfo
docker compose ps
docker compose exec scheduler python -m scripts.scheduler status
docker compose exec scheduler python -m scripts.scheduler healthcheck

# 道元
docker compose -f ~/code/myopenclaw/docker-compose.yml exec hermes-daoyuan \
  /opt/hermes/.venv/bin/hermes -p daoyuan mcp test dailyinfo

# 虾酱
docker compose -f ~/code/myopenclaw/docker-compose.yml exec openclaw-gateway \
  node /app/openclaw.mjs mcp probe dailyinfo --json

# MCP 活着 + key 已配置：不带 token 应得 401
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8768/mcp
```

## 数据与备份

- `~/.myagentdata/dailyinfo/freshrss/data/users/owen/db.sqlite`：**历史 FreshRSS 数据**，dailyinfo 的 `dailyinfo-freshrss` 与 scheduler 通过同一 bind mount 读写（不是拷贝）
- `publications/`、`deliveries/`：canonical 简报与投递记录，dailyinfo-mcp 只读挂载
- `state/scheduler_state.json`：调度状态（原子写入）
- myopenclaw 的 `scripts/backup-data.sh` 继续覆盖整个 `~/.myagentdata`，其中 `dailyinfo/freshrss/data/users/*/db.sqlite` 在 `HOT_DBS_RO` 热备清单里

## 故障排查

| 现象 | 处理 |
|---|---|
| 当天没有简报 | `docker compose exec scheduler python -m scripts.scheduler status` 看哪个 job 未 success；`logs` 看 verify-env/catch-up |
| Discord 没收到 | 看 scheduler 日志里的 `discord: delivered=...`；`DISCORD_CHANNEL_*` 在 dailyinfo `.env` |
| MCP 401 | key 不对/未传；核对 `.env` 的 `DAILYINFO_MCP_KEY` 与 dailyinfo `keys.json` |
| MCP 超时 | `dailyinfo-mcp` 没起，或 `DAILYINFO_MCP_ALLOWED_HOSTS` 没包含 `host.docker.internal:8768` |
| FreshRSS 库异常 | 先停 dailyinfo 栈；本仓备份在 `~/.myagentdata/dailyinfo`，另有切换前安全副本 |

## 不在本仓维护

dailyinfo 的 secret、数据源、抓取/摘要/推送逻辑、调度代码全部由 dailyinfo 仓自己管理。本仓只维护 MCP 客户端注册、备份覆盖和文档入口。
