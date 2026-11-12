# Kanban（多 profile 协作）

Kanban 是 Hermes 自带的任务看板：**不是独立应用、不需要额外起服务**。它就是 `~/.hermes/kanban.db` 一个 SQLite 文件，四个 profile（爱玛士 `default` / 爱码士 `coder` / 道元 `daoyuan` / `finance`）共用同一块板——这是它们"互相派活"的物理基础。

## 三扇窗，同一个库

| 入口 | 用什么 | 适合 |
|---|---|---|
| 飞书（主入口） | 聊天里发 `/kanban …` | 日常建卡、看板、催卡 |
| 网页 | `http://localhost:9119/kanban`（`hermes-dashboard` 容器，一直在跑） | 看全局、拖拽、读评论线程 |
| CLI | `docker compose exec hermes /opt/hermes/.venv/bin/hermes kanban …` | 排障、脚本、cron |

`kanban` toolset 是**关闭**的（`hermes tools list` 显示 `✗ disabled`）——这不影响使用：`/kanban` 和 CLI 走的是同一套 `kanban_db`，不需要模型有工具。要开（会让爱玛士多出 30+ 工具）：

```bash
docker compose exec hermes /opt/hermes/.venv/bin/hermes tools enable --platform feishu kanban
```

## 概念

| 词 | 含义 |
|---|---|
| **板 board** | 默认一块 `default`。一个项目/工作流一块板，板之间互不干扰 |
| **卡 task** | 一行数据：标题 + body + 状态 + `assignee` + 评论线程 + 若干次 run |
| **状态** | `triage → todo → ready → running → blocked / review → done`（另有 `scheduled` 表示等时间而非等人） |
| **assignee** | **profile 名**（`coder`、`daoyuan`、`finance`），不是模型名。一张卡一次只归一个身份 |
| **dispatcher** | 常驻在网关里的循环，默认 60 秒扫一次：回收超时 claim → `todo` 转 `ready` → 原子抢占 → **spawn 一个 OS 进程** `hermes -p <assignee> … chat -q work kanban task t_x` |
| **workspace** | `scratch`（默认，跑完即删）/ `dir:<路径>` / `worktree[:路径]`（git 分支） |
| **run** | 一次执行尝试。一张卡可以有多轮 run（被打回、换人重做） |
| **交接** | 靠**评论线程**：worker 重新被调度时会把整条评论读进上下文 |

两个容易混的邻近机制：`delegate_task` 是进程内 fork→join（子代理匿名、无记忆），适合"我需要个短答案才能继续"；kanban 是**跨进程、跨 profile、持久化**的队列，适合"这活交给另一个身份慢慢干"。

## 日常：飞书里三行搞定

```
/kanban list                                     # 看板
/kanban show t_7dbb550e                          # 一张卡的 body / 评论 / run
/kanban create 标题 --assignee coder --body "…"   # 建卡（单行 body，别换行）
/kanban runs t_xxx                               # 每次尝试的耗时与结论
/kanban block t_xxx --reason "…"                 # 卡住时人工介入
/kanban unblock t_xxx                            # 放开重跑
```

`/kanban create` 会**自动把当前飞书会话订阅到这张卡**（`notify+wake`）：卡跑完你会在这个会话里收到结果。用 CLI 建的卡**不会**自动订阅，要补：

```bash
hermes kanban notify-subscribe <task_id> --platform feishu --chat-id <oc_…> \
  --notifier-profile default --delivery-mode notify+wake
```

## 四条硬约定

1. **卡里必须写明产物的绝对路径**。默认 `scratch` workspace 跑完就删，worker 的"临时目录"不会留下来。写 `/opt/data/tmp/xxx.md` 这种。
2. **报问题的人和修问题的人不是同一身份**。`default`（爱玛士）握着 server-monitoring / aisecretary 等 MCP，真正干活的 `coder` 有另一套。所以**卡里必须自带证据**（报错原文、容器名、时间戳），worker 无法借用你的上下文。
3. **一个容器一个 dispatcher**。别为了"让 coder 的网关起来"在 `hermes` 容器里 `s6-svc -u /run/service/gateway-coder`——那会和 `hermes-coder` 容器里的同名网关**抢同一个 profile、同一条飞书连接**，出现重复回复和 `gateway_state.json` 双写。
4. **worker 继承的是派发容器的挂载与环境变量**，不是 assignee 的。这就是下一节那条约定存在的原因。

## 角色归角色，机制归机制

**`default`（爱玛士）是总管**：它决定做什么、把活拆成卡、指派给谁、卡住时定夺、最后验收。这个权威跟"哪个进程跑派发循环"无关 —— 谁建卡、谁指派、谁看板，谁就是总管。

**派发执行器（工头）是另一回事**：它是个 60 秒的机械循环，只做四件事 —— 回收超时 claim、把 `todo` 抬成 `ready`、原子抢占、spawn 进程。它不做任何判断。

两者必须分开看，因为它们受不同的物理约束：

| | 总管（`default` / 爱玛士） | 派发执行器 |
|---|---|---|
| 干什么 | 拆活、建卡、指派、验收、答疑 | 扫板、抢占、spawn worker |
| 在哪 | `hermes` 容器（爱玛士自己的网关） | 跟"工人要在哪个车间干活"走 |
| 受什么约束 | 无 | worker 是它的子进程，**继承它的挂载与环境变量** |

### 为什么工头不在总管容器里

因为 worker 是 dispatcher 的 `subprocess`，只可能在 dispatcher 所在的容器里跑。两个容器的能力差集决定了工头该放哪：

| 能力 | `hermes`（总管） | `hermes-coder` |
|---|---|---|
| 真邮箱配置 / token | ✅ 有 | ❌ 空卷遮蔽 |
| OneDrive token（`/opt/onedrive`） | ✅ 有 | ❌ 无 |
| gitcode 配置（`/opt/gitcode-config`） | ❌ 无（token 仍在 env） | ✅ 有 |
| gh / opencode / lark / Zotero / myagentdata / aisecretary skills | ✅ | ✅ |

把工头放在总管容器里，`coder` 的 worker 就会读到爱玛士的邮箱凭据和 OneDrive token —— 那会破掉仓库刻意维持的"邮箱仅爱玛士可用"隔离（`docs/email.md`）。放在 `hermes-coder`，worker 拿得到干活要的工具、拿不到不该拿的钥匙（代价只是 gitcode 配置目录；`GITCODE_TOKEN` 在两边 env 里都有）。

所以 `docker-compose.yml` 里除 `hermes-coder` 外的三个容器都设了：

```yaml
- HERMES_KANBAN_DISPATCH_IN_GATEWAY=0
```

**这不是让 coder 当老板**，是让"工头"待在正确的车间。爱玛士照旧建卡派活 —— 它建完卡，最长 60 秒后由 `hermes-coder` 里的循环接手。

> 另一条实测记录：改之前是 `hermes-finance` 抢到了派发锁。finance/daoyuan **没有** `SERVER_MONITORING_MCP_URL` 等凭据（`tests/test_hermes_server_monitoring_wiring.py` 刻意钉着"其它 profile 不该收到它"），于是给 `coder` 的卡会在 finance 容器里跑、MCP 展开成空 URL —— 这就是"同一张卡时好时坏"的来源。

守卫：`tests/test_hermes_kanban_wiring.py`（断言恰好一个容器开着派发，且是 `hermes-coder`）。

### 当前只有一个工人身份

工人继承的是 `hermes-coder` 容器，所以**今天只有 `coder` 适合当 assignee**。给 `daoyuan` 派活会跑在 coder 容器里，缺 `PAPER_QUEUE_DAOYUAN_TOKEN` / `DAILYINFO_MCP_KEY` 等 env（zotero 这种无 token 的网络 MCP 反而可用）；给 `finance` 同理。

> **2026-11-12 决定（业主）：暂时保持"单工人 = coder"，不扩。**
> 理由是当前 95% 值得排队的活都是 coder 的，为此付出隔离代价不划算。
> **这不是待修的缺陷，是明确的选择** —— 除非出现"必须由 daoyuan/finance 身份做、且超过 10 分钟、要留痕"的活（累计 ≥3 次），否则不要动它。
> 真要扩有三条路：① 凭据下沉到 `~/.hermes/profiles/<p>/.env`（`override=True`，单派发器即可派多身份；代价是凭据落在共享卷上）；② 把四个 gateway 收回一个容器（最接近官方，但容器级隔离全没了）；③ 新建只装工人凭据的容器（保留 boss/worker 隔离，工人间凭据互通）。

### 兜底

`hermes-coder` 停机时**不会有**自动派发。手动推一轮（注意：从哪个容器跑，worker 就落在哪个容器）：

```bash
docker compose exec hermes-coder /opt/hermes/.venv/bin/hermes kanban dispatch
```

> ⚠️ 在 `hermes-coder` 容器里用 CLI 建卡时，可能会打一句 `No gateway is running — the task will sit in 'ready'`。**这是误报**：该探测按 `HERMES_HOME`（这里是 `/opt/data` 根 profile）找 `gateway_state.json`，而 coder 的网关写在 `profiles/coder/` 下。卡的创建与派发都正常（实测 60s 内被 `pid 558` 认领）。

## 排障

| 症状 | 先看什么 |
|---|---|
| 卡在 `ready` 一直不动 | 谁在派发：`grep -i "dispatcher" ~/.hermes/profiles/*/logs/gateway.log` 里的 `holding singleton dispatcher lock` / `will NOT dispatch`；再看 `docker compose ps hermes-coder` |
| 卡 `blocked` 且带 `Auto-blocked` | 连续 2 次 spawn 失败（`failure_limit=2`）触发熔断。看 `hermes kanban log <id>` 找真正的报错，修完 `unblock` |
| 卡 `done` 但产物不见了 | 用了 `scratch` workspace。产物必须写在 db 外面（`/opt/data/…`） |
| worker 说某个 MCP 连不上 | worker 落在了没有该凭据的容器。确认派发者是 `hermes-coder` |
| 飞书里同一个 bot 回两遍 | 有重复网关：`docker compose exec hermes /command/s6-svstat /run/service/gateway-coder` 应显示 `down` |
| 想看 worker 干了什么 | `hermes kanban log <id>`（工具流）/ `hermes kanban show <id>`（评论与 run）/ `~/.hermes/kanban/logs/<id>.log` |

常用巡检：

```bash
docker compose exec hermes-coder /opt/hermes/.venv/bin/hermes kanban stats      # 各状态计数 + 最老 ready 年龄
docker compose exec hermes-coder /opt/hermes/.venv/bin/hermes kanban list
docker compose exec hermes-coder /opt/hermes/.venv/bin/hermes kanban diagnostics
```

## 网页看板

`http://localhost:9119/kanban`，用户名 `admin`，密码是 `~/.hermes/config.yaml` 里 `dashboard.basic_auth` 配的那个。忘了密码就重算一个 hash 填回去：

```bash
docker compose exec hermes /opt/hermes/.venv/bin/python -c \
  "import sys; sys.path.insert(0,'/opt/hermes'); from plugins.dashboard_auth.basic import hash_password; print(hash_password('新密码'))"
# 把输出的 scrypt$… 写进 config.yaml 的 dashboard.basic_auth.password_hash，重启 hermes-dashboard
```

## 进阶（用熟之后）

- **review 流**：`request-review` 把卡挪到 `review`，reviewer（另一个 profile）可以 `request-changes` 打回原实现者，或直接完工。默认 `kanban.review_dispatch: true`。
- **拆解**：`--triage` 建卡停在 `triage`，由 specifier 补全规格后升 `todo`；`hermes kanban decompose` 把一张卡拆成依赖图。
- **并行编排**：`hermes kanban swarm` 生成"并行 worker → verifier → synthesizer"的图。
- **可见性**：`hermes kanban watch` 实时跟事件流；`hermes kanban tail <id>` 跟单卡。

## 不在本文范围

- 事件（GitHub webhook / server-monitoring 告警 / cron）自动转成卡——需要先有一张"授权判定表"（哪些信号该开卡、哪些只播报、哪些丢弃），属于下一阶段。
- A2A（跨进程/跨机器的 Agent-to-Agent 协议）：同机多 profile 不需要它，kanban 更省事。
