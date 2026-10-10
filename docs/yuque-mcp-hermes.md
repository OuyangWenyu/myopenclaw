# 语雀 MCP 接入（Hermes）

> 最后更新：2026-10-09（本机服务端化，issue #79）

本机通过**本机 docker 服务** `yuque-mcp`（`yuque_mcp_server` 的 `RUN_MODE=cloud`/SSE 部署）接入语雀知识库：读取、搜索、备份知识库并查询服务端生成的变更报告。服务端自持语雀只读 token、与消费方同在 `myopenclaw-net`，**不依赖 UniVPN**；校内服务器 `10.48.0.81` 那套继续给团队成员走 UniVPN，两边互不影响。

**当前消费者**：
- **Hermes 侧**：注册在**默认 profile（爱玛士）**生效；yuque-daily-digest cron 同样运行在 `hermes`（默认 profile）容器。⚠️ 2026-10-09 更正：四个 profile 是隔离实例，coder/daoyuan/finance 各有独立 `profiles/<name>/config.yaml`，不含此注册；验证须 `hermes -p <profile> mcp list`（机制详见 [千问办公 AI听记](qwennote-mcp-hermes.md) 的 Profile 隔离一节）。**其余 profile 不需要语雀接入 —— 已确认的边界，保持现状。** skill 的加载与执行源见 [Hermes Skill 机制](hermes-skills.md)（live = `~/.hermes/skills` 原生副本，仓库 `skills/` 不参与加载）。
- **天一（openclaw-tianyi）**：经 `docker/tianyi-bot/openclaw.json.template` 独立注册（SSE + Bearer）；URL/key 来自 `.env.tianyi-bot` 的 `TIANYI_BOT_YUQUE_MCP_URL` / `TIANYI_BOT_MCP_YUQUE_MCP_API_KEY`，compose 同时以无前缀 `MCP_YUQUE_MCP_API_KEY` 注入容器供运行时展开。⚠️ 天一的 key 是**第二份拷贝**（主 `.env` 的 `MCP_YUQUE_MCP_API_KEY`），轮换必须两处同改（守卫 `tests/test_yuque_mcp_local.py::TestLiveEnvSwitched`）。

## 架构边界

- **服务端 = 本仓库 compose 的 `yuque-mcp` 服务**：独立镜像 `myopenclaw/yuque-mcp:latest`，从兄弟仓 `~/code/yuque_mcp_server`（gitcode.com/dlut-water/yuque_mcp_server）构建。上游**无 tag**，按 commit 固定（当前 `3945bce`），守卫 `tests/test_yuque_mcp_local.py::TestSiblingPin` 钉住本地 checkout 的 HEAD —— 升级 = 审查上游 diff → 改 pin + 重建，不存在"悄悄拉到 main"的路径。
- 服务端持有 `YUQUE_TOKEN`（语雀只读 token：`repo:read` + `doc:read`；要编辑者姓名再加 `group:read`），**客户端不需要**它，也不复用语雀侧其它凭据。
- 只挂 `myopenclaw-net`，**不发布宿主端口**；除 Bearer key（服务端 `MCP_API_KEY = .env 的 MCP_YUQUE_MCP_API_KEY`）外不开面。⚠️ key 为空时上游是**无认证直通**（net 上任意容器都能读团队文档）—— 防线有三道：`start.sh` 缺键大声警告、Uptime Kuma 探针**只接受 401**（探得 2xx 即说明 key 未生效，报警）、守卫 `TestLiveEnvSwitched` 卡天一第二份拷贝同值。
- 数据落宿主：`~/.myagentdata/yuque-mcp/change_data`（快照 + `change_summary.db` + `snapshots/` 正文，应用强制 0700/0600）与 `~/.myagentdata/yuque-mcp/backup`（`backup_repo` 工具输出根）。
- 容器内调度（`Asia/Shanghai`）：每天 **06:00 快照**、**07:00 生成变更报告**。时区由 `YUQUE_CHANGE_TIMEZONE` + pip `tzdata` 保证；**compose 刻意不写 `TZ`**（`python:3.11-slim` 无 OS zoneinfo，写了日志时间戳也仍是 UTC，反而误导）。
- 监控知识库（`YUQUE_CHANGE_REPOS_JSON`）：技术交流 `kgo8gd/tnld77`、建设方案 `kgo8gd/xnedwc`、基础学习 `kgo8gd/owoc3p`。缺省时服务端回退到内置两库（**会静默丢掉「建设方案」**）。

## 配置

仓库根 `.env`（gitignored）中：

```env
# 客户端 URL（hermes bootstrap 与天一使用；容器名直连，不经宿主）
YUQUE_MCP_URL=http://yuque-mcp:18001/sse
# 服务端与客户端共享的访问 key —— 单一来源：compose 同时喂两边（不一致 ⇒ 每次 401）
MCP_YUQUE_MCP_API_KEY=xxxxx
# 语雀只读 token（服务端消费；语雀后台创建：repo:read + doc:read）
YUQUE_TOKEN=your_yuque_token_here
# 监控知识库（JSON 数组，name/namespace；见架构边界一节）
YUQUE_CHANGE_REPOS_JSON=[{"name":"技术交流","namespace":"kgo8gd/tnld77"},{"name":"建设方案","namespace":"kgo8gd/xnedwc"},{"name":"基础学习","namespace":"kgo8gd/owoc3p"}]
```

`.env.tianyi-bot` 中：`TIANYI_BOT_YUQUE_MCP_URL=http://yuque-mcp:18001/sse`，`TIANYI_BOT_MCP_YUQUE_MCP_API_KEY` 与上面 key **同值**。

## 注册 MCP 服务

```bash
./scripts/bootstrap_hermes.sh --no-skill
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--url URL` / `--api-key KEY` | 覆盖 `.env` 中的 URL/key |
| `--dry-run` | 只打印将要做的改动，不写文件 |
| `--disable` | 移除已注册的 `yuque-mcp` 配置和 `.env` key |
| `--force` | 替换/删除同名但非本脚本管理的条目 |
| `--no-skill` | 不安装 skill（本机 skill 已由 docker-compose 挂载，用这个） |

脚本只改 Hermes 侧文件（`~/.hermes/config.yaml` + `~/.hermes/.env`），不部署服务端、不打印 key。

## 生效与验证

改完 `.env` 后**重建**（`restart` 不会重读环境变量）：

```bash
docker compose up -d hermes
```

验证：

```bash
# 服务端活着 + key 已配置：不带 token 探针应得 401
docker compose exec -T uptime-kuma curl -s -o /dev/null -w '%{http_code}\n' http://yuque-mcp:18001/sse

# MCP 连接（工具列表含 get_change_summary）
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp test yuque-mcp

# 调度在跑（应看到 yuque_change_summary_scheduler_enabling repo_count=3）
docker compose logs yuque-mcp | grep yuque_change_summary

# 天一侧
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml \
  exec --user node openclaw-tianyi node /app/openclaw.mjs mcp probe yuque-mcp --json
```

Uptime Kuma 监控项「Yuque MCP」（HTTP，**只接受 401**：探得 2xx 即说明 key 未生效的无鉴权直通，会报警）与「Docker: yuque-mcp」由 `scripts/setup-uptime-kuma.sh` 幂等注册。

## 冷启动与基线

- **首次部署**（当天 06:00 之后）：容器启动即补当天基线快照（catch-up），随后跑当天 report cycle → `get_change_summary` 返回 `initialized`（「已建立基线，明日起推送日报」）。
- **次日起**：06:00 快照 → 07:00 报告 → 08:10 日报推送。上一版数据在校内服务器上；本次切换**不拷贝**（已拍板），基线在本机重建。
- 重启/重建容器不会重复建基线（同一天去重）。
- 本机休眠/关机错过 06:00/07:00：唤醒后调度器当轮补齐快照与报告，日报次日恢复；当日 08:10 若在睡眠中则错过该天。

## 故障排查

| 现象 | 处理 |
|------|------|
| 探针/工具全 401 | key 不一致：核对 `.env` 的 `MCP_YUQUE_MCP_API_KEY`（服务端与客户端同源）；天一另核对 `.env.tianyi-bot` 的第二份拷贝 |
| 探针变成挂住不返回 | 服务端 `MCP_API_KEY` 为空 ⇒ 无认证长流（上游行为）。确认 compose 环境变量注入正常 |
| `get_change_summary` 返回 `initialized` | 正常首日状态（只有基线快照），次日 07:00 后才有真报告 |
| 返回 `not_available` | 本机服务端尚未完成调度生成（如当天 07:00 前查询）；看 `docker compose logs yuque-mcp` 的 scheduler 日志 |
| 快照/日报断档 | 机器休眠或容器停过；唤醒/重启后自动补。连续断档查 `sync_issue`/`error` 日志 |
| `YUQUE_TOKEN 未设置` 警告 | 语雀后台创建只读 token 写入 `.env` 后重跑 `./scripts/start.sh` |
| skill 未生效 | 先 `hermes skills list` 看真实加载面与 source（**挂载存在 ≠ 被加载**）；同名撞名会让 cron 静默跳过（run 输出顶部有 `Skill(s) not found and skipped`）——机制与排查见 [Hermes Skill 机制](hermes-skills.md) |

## 每日变更推送（yuque-daily-digest）

```
本机 yuque-mcp 容器 06:00 快照 → 07:00 生成变更报告 → Hermes cron 08:10（北京）触发 yuque-daily-digest skill
  → agent 逐库调用 get_change_summary（纯只读）→ 重点变更文档 get_doc_content 做中文摘要
  → cron --deliver 自动推送飞书私聊
```

行为约定：有变更 → 变更清单 + 摘要日报；全部无变更 → 静默；`not_available` / `initialized` / 401 / 连接失败 → 推送简短警告（不静默）。

### 启用

1. 完成上面的 MCP 注册（`YUQUE_MCP_URL` + `MCP_YUQUE_MCP_API_KEY`，key 必须在仓库根 `.env` —— `bootstrap_hermes.sh` 写入 `~/.hermes/.env` 的那份不参与 Hermes MCP 客户端 `${VAR}` 展开）
2. `.env` 填 `YUQUE_DAILY_PUSH_REPOS=技术交流,建设方案,基础学习`（显示名，逗号分隔；三库需与 `YUQUE_CHANGE_REPOS_JSON` 一致）
3. `./scripts/start.sh` —— 检测到 `YUQUE_DAILY_PUSH_REPOS` / `YUQUE_MCP_URL` / `MCP_YUQUE_MCP_API_KEY` 三个变量即自动注册 cron（`10 0 * * *` UTC = 08:10 北京）；任一缺失则警告并跳过。已注册后改知识库列表，重跑 `start.sh` 自动更新 job prompt

### 验证

```bash
# cron job 已注册（每日 8:10 北京）
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list | grep yuque-daily-digest

# 手动触发：会**真实投递**（2026-10-10 实测，direct run 的 delivery_outcome=delivered）
#   ⚠️ 同一 job 有 in-flight run 时会被拒（"Job is already being fired"），先等它收尾
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron run <job_id>

# skill 静态断言（skill 自包含，无外部脚本）
python3 skills/yuque-daily-digest/test-skill.py
bash skills/yuque-daily-digest/test-cron-config.sh
```

### 说明

- 仅推送**飞书私聊**（复用 `LARK_USER_OPEN_ID` / `FEISHU_HOME_CHANNEL`）；群推送不在本期范围（见 issue #60 三Agent群推送定调）
- 摘要由 Hermes agent 主模型生成，skill 不指定模型
- **skill 执行源 = 原生副本** `~/.hermes/skills/productivity/yuque-daily-digest/`（v1.2.0，agent 维护的作战手册式 SKILL.md + `scripts/daily_digest.py` 直连 SSE 的 fallback，其 HOST 已指向逻辑地址 `yuque-mcp`）。仓库 `skills/yuque-daily-digest`（自包含、无脚本）经 `./skills/yuque-daily-digest` → `/opt/hermes-skills/...` 只读挂载提供，**当前不参与 Hermes 的 skill 加载**——"改仓库版 ≠ 改线上行为"，详见 [Hermes Skill 机制](hermes-skills.md)。cron 注册在 `hermes` 容器
- 天一复用此能力延后至 issue #60 统一处理

## 备份

- `change_summary.db`（回滚模式活库）：进 `scripts/backup-data.sh` 的 `HOT_DBS_RO` 热备清单（`-readonly` `.backup` 到 `.tmp`、成功才 `mv`），不裸 rsync。
- `snapshots/`（文档正文原文）与 `backup/`（Markdown）：随 `~/.myagentdata` 数据备份照常上云（2026-10-09 拍板接受：团队文档正文进个人云盘；如需停止，backup-data.sh 加 `--exclude="yuque-mcp/"` 一行即可 —— 注意这只挡**未来**快照，云上既有快照要等留存到期（`BACKUP_KEEP_DAYS`）才消失）。
- `backup_repo` 工具输出落宿主卷 `~/.myagentdata/yuque-mcp/backup`，不落兄弟仓工作区；上游「team 备份纳入 git 推送」的路径在本机部署下不存在（兄弟仓 `.dockerignore` 亦已排除 `yuque/backup`、`yuque/change_data`，双保险）。

## 已知边界

- 校内服务器旧 key 仍在生效（不归本仓管理）——本机切换只影响本机消费者。
- 本机服务是单实例：Hermes 默认 profile 与天一共享同一份快照/报告与同一个 `get_change_summary` 读取面。
