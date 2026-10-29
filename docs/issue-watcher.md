# issue/PR 分配变动 → 飞书群

把 GitHub/GitCode 中“分配给团队成员的 issue/PR 变动”自动推到飞书群（当前为 **预报系统**）。

## 能力边界

| 仓库 | 负责 |
|---|---|
| `git-contribution-stats` | 采集、当前状态、`issue_events`、MCP `get_issue_events`（数据/事件能力） |
| `myopenclaw` | 人员映射、飞书卡片、群投递、游标/去重/重试（渠道能力） |

```text
GitHub/GitCode ─→ issue_watcher（stats，10 min）
                        │  issue_events + immutable snapshot
                        ▼
              MCP get_issue_events（repo-scanner-mcp:8001）
                        │  60s 游标轮询
                        ▼
              issue-notifier（myopenclaw）
                        │  hermes-coder 应用（CODER_FEISHU_*）
                        ▼
                  预报系统 飞书群
```

## 事件与卡片

- 事件：`assigned` / `unassigned` / `review_requested` / `review_request_removed` /
  `opened` / `closed` / `reopened` / `merged`。
- 卡片：平台 + 仓库 + `#编号` + 事件类型 + 标题 + 人员 + 「打开」按钮。
- 分配事件按人拆；状态事件使用事件时的 assignee 快照。
- 通知路径**不经过 LLM**，模板直出。

## 人员映射

真实映射在宿主机：

```
~/.myagentdata/issue-notifier/config.toml
```

```toml
[[people]]
name = "示例成员"
gitcode = "example-gitcode"
github = "example-github"
```

- `platform=github` 比 `github` 字段；`platform=gitcode` 比 `gitcode` 字段。
- 来源：`~/.myagentdata/repo-scanner/repos.sqlite` 的 `author_names` 名册（排除离职/机器人），
  GitHub 别名按同名/同登录查询补充；新成员直接加一段。
- 仓库内只提交 `config/issue-notifier.example.toml`（占位符），真实名册不入公开仓库。
- 可选 `allowed_repos = ["platform/owner/repo", ...]` 控制哪些仓库允许进群（空 = 全部；
  默认全部）。这是群通知的粗粒度白名单，不替代仓库 ACL。

## 环境变量

```bash
# .env
ISSUE_NOTIFIER_FEISHU_MODE=app            # app | webhook
ISSUE_NOTIFIER_FEISHU_CHAT_ID=oc_xxx     # 预报系统群；app 模式必填
# ISSUE_NOTIFIER_FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
CODER_FEISHU_APP_ID=cli_xxx              # 爱码士应用（已在群内）
CODER_FEISHU_APP_SECRET=xxx
ISSUE_NOTIFIER_POLL_SECONDS=60
ISSUE_WATCHER_POLL_SECONDS=600
REPO_SCANNER_WATCH_OWNS_ISSUES=1

# MCP 认证（scripts/ensure_issue_watcher_auth.py 幂等生成/合并；勿手抄）
HERMES_REPO_SCANNER_KEY=xxx               # Hermes 默认 profile 使用
TIANYI_BOT_REPO_SCANNER_KEY=xxx           # 天一使用；.env.tianyi-bot 必须同值
ISSUE_NOTIFIER_MCP_TOKEN=xxx              # notifier 轮询使用
GIT_STATS_API_KEYS={"<key>":"hermes",...} # 服务端 key→用户名映射
```

app 模式只注入 **hermes-coder 应用**（`CODER_FEISHU_*`）；compose 刻意不转发
主飞书应用密钥（notifier 只需要对一个群发消息；专用 bot 可进一步收窄权限）。
webhook 模式只填 `ISSUE_NOTIFIER_FEISHU_WEBHOOK`，消息显示为“自定义机器人”；
只支持未签名 webhook；签名 webhook（timestamp/sign）需要额外实现——当前会重试
最多 24 小时后移入 `dead`，可用 `--requeue-dead` 恢复。
`feishu.py` 同时接受 `{"code":0}` 与自定义机器人历史返回的 `{"StatusCode":0}`。

> **数据边界**：卡片包含私有仓库的 owner/repo、issue 标题、链接和 assignee。
> 群成员应与仓库 ACL 对齐；`state.sqlite` 的 outbox 也会随 data 备份上云。
> 若某些私有仓库不应进群，应在 stats 侧 `configs/repos.toml` 移除或后续加仓库白名单。

## 运行与状态

首次部署请用 `./scripts/start.sh`：它会创建 `~/.myagentdata/issue-notifier/` 并把
`config/issue-notifier.example.toml` 播种为 `config.toml`（否则 notifier 会以
"no people configured" 退出 2，重启循环）。编辑 `config.toml` 后需要
`docker compose restart issue-notifier`（配置只在启动时读取）。

```bash
# 启动/重启（会同时重建 repo-scanner 合并服务）
./scripts/start.sh
docker compose up -d repo-scanner-mcp issue-notifier

# 日志
docker compose logs -f issue-notifier
docker compose logs -f repo-scanner-mcp

# 手动跑一次（基线/排障；daemon 运行时避免并发写 state，先停或优先 --dry-run）
docker compose exec issue-notifier python /app/main.py --once
docker compose exec issue-notifier python /app/main.py --dry-run
docker compose exec issue-notifier python /app/main.py --requeue-dead  # 恢复 dead 卡片
docker compose exec issue-notifier python /app/main.py --purge-dead    # 清理 7 天前的 dead

# 状态
docker compose exec issue-notifier cat /data/status.json
```

`status.json` 字段：`last_poll_epoch` / `last_success_epoch` / `events_seen` /
`events_queued` / `events_sent` / `send_failed` / `cursor` / `pending` / `dead` /
`watcher` / `last_error`。
容器 healthcheck 要求 `last_success_epoch` 在 5 分钟内、`send_failed`/`dead` 为 0。
`watcher.error_repos` 只记录/告警，不判 unhealthy（私有仓库 401/404 是预期噪音）。
`--dry-run` 在首次运行（无 cursor）时只建基线、不打印事件；要看事件先跑一次
`--once` 建立 cursor 后再 `--dry-run`。

## 游标、去重与重试

- 本地 `~/.myagentdata/issue-notifier/state.sqlite`（rollback journal，随 data 备份）。
- `kv.cursor`：最后扫描的 `event_id`；首次运行跳到 `latest_event_id`（不补发历史）。
  `latest_event_id` 缺失时**直接报错**（不静默从 0 开始灌历史）。
- **cursor > latest_event_id**（stats DB 被恢复/重建）→ 记 ERROR 并重置到 latest，
  避免永远空轮询、通知静默停摆。
- `sent(event_key)` + `outbox(event_id)` + `dead(event_id)`：同一事件不会重复发送；
  发送失败留在 outbox，下一轮（60s）重试；**持续失败 24 小时**才移入 `dead`
  （短时故障/机器人被移出群再拉回不会永久丢通知）。
- 恢复：`docker compose exec issue-notifier python /app/main.py --requeue-dead`
  把 dead 卡片移回 outbox（attempts 归零）；dead 默认保留 7 天，之后可用
  `--purge-dead` 清理（card_json 含私有标题；state 随 data 备份，stats DB 本身
  也含这些标题，属既定备份边界）。
- 每次 poll 的发送有 20s 预算，Feishu 黑洞不会把轮询拖死；单张失败不阻塞同批其他卡片。
- `pending`/`dead` 计数来自 `COUNT(*)`。
- Feishu/MCP 异常文本在落日志/`last_error` 前会脱敏（`access_token=`、`Bearer`、
  webhook 路径、`oc_/ou_` id）；Feishu HTTP body 不再回显。
- MCP 调用有 30s 超时；服务重启/网络抖动不会把 notifier 卡死。
- `httpx`/`mcp` 客户端日志级别降到 WARNING（避免每 60s 6 行噪音）。

> **MCP 认证**：`repo-scanner-mcp` 启用 per-user Bearer（`GIT_STATS_API_KEYS`，
> `key → 用户名`）。三个消费者各持一把 key：Hermes 默认 profile 的
> `mcp_servers.repo-scanner.headers`、天一模板的 `headers`、notifier 的
> `ISSUE_NOTIFIER_MCP_TOKEN`。`scripts/ensure_issue_watcher_auth.py` 幂等生成/合并
> 根 `.env` 里的三把 key，`scripts/ensure_hermes_repo_scanner_auth.py` 幂等补
> Hermes header；`./scripts/start.sh` 启动前自动运行两者。`/health` 不鉴权
> （Docker/Uptime Kuma 探针），其余路径无 key 一律 401。
>
> **轮换**：删除/修改根 `.env` 里的 key 后重跑 `./scripts/start.sh`（会重建
> repo-scanner/notifier/Hermes 容器使新值生效）。天一那份在 `.env.tianyi-bot`
> 的 `TIANYI_BOT_REPO_SCANNER_KEY`，必须与根 `.env` 同值——两份不一致时天一
> 会 401，且不会自动同步。

## 故障排查

| 症状 | 检查 |
|---|---|
| `internal_error` / 无事件 | `repo-scanner-mcp` 是否 healthy；`/data/repos.readonly.sqlite` 是否存在（快照） |
| 401/403 | 群/应用权限；`CODER_FEISHU_*` 是否在群内；token 是否过期 |
| 卡片没到群 | `ISSUE_NOTIFIER_FEISHU_CHAT_ID` 是否正确；`status.send_failed` / `dead`；notifier 日志 |
| 一直不触发 | `config.toml` 映射是否匹配平台用户名；事件是否属于该仓库/人；`cursor > latest_event_id` 是否被重置 |
| 事件为空 | stats 侧 baseline 是否完成；`repo_watch_state.baseline_done=1`；watcher 是否 401/403/404（`last_error`） |
| 私有仓库标题出现在群里 | 检查群成员是否与仓库 ACL 一致；必要时从 `configs/repos.toml` 移除该仓库 |

## 过渡与退役

watcher 接管 issue/PR 当前状态后，宿主的每日 `git-contribution-stats` launchd
任务应设置 `REPO_SCANNER_WATCH_OWNS_ISSUES=1`（只采 commits），避免两个写者。
合并服务稳定一周后，可卸载宿主 launchd（合并服务已在每日 07:45 跑全量采集）。

## 测试

```bash
uv run --with pytest pytest tests/test_issue_notifier.py -q
uv run --with pytest --with pyyaml pytest tests/test_issue_notifier_wiring.py -q
```
