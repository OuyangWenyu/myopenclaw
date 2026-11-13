# Hermes 拓扑：容器 × profile × `.env`

这张机器上跑着 **5 个容器、4 个身份、1 块看板**。三者分别是什么、谁决定什么，是排查问题的第一块地基。

## 一句话

> **profile 是"谁"，容器是"在哪"，gateway 是"活着的那一份"。**
> 身份（配置/记忆/会话）跟着磁盘走；能力（挂载/环境变量）跟着容器走。

## 容器与身份

| 容器 | 启动命令 | 服务的 profile | 飞书 App（来源 env 变量） |
|---|---|---|---|
| `hermes` | `gateway run` | **default（爱玛士，总管）** | `FEISHU_APP_ID` |
| `hermes-coder` | `gateway run --profile coder --replace` | coder（爱码士） | `CODER_FEISHU_APP_ID` |
| `hermes-finance` | `gateway run --profile finance` | finance | `FINANCE_FEISHU_APP_ID` |
| `hermes-daoyuan` | `gateway run --profile daoyuan` | daoyuan（道元） | `DAOYUAN_FEISHU_APP_ID` |
| `hermes-dashboard` | `dashboard --host 0.0.0.0` | **无**（只读网页 UI，不跑 agent） | — |

四个 agent 容器**都挂载同一个宿主目录**：`~/.hermes` → 容器内 `/opt/data`（读写）。

## 三个概念

| | 是什么 | 存在哪 | 换掉它会怎样 |
|---|---|---|---|
| **profile** | 身份：人格、模型、技能、记忆、会话、MCP 声明 | `~/.hermes/`（default）或 `~/.hermes/profiles/<name>/` | 换成另一个人 |
| **容器** | 身体：能看到哪些文件、有哪些环境变量、能连哪些网 | `docker-compose.yml` | 同一个人，能力变了 |
| **gateway** | 某个容器里跑起来的那个身份的常驻进程 | 进程（s6 管） | 这个人下线 |

实测身份隔离是**真的**：default 674 个会话 / coder 577 / finance 134 / daoyuan 1，四本账互不相通。

## 两个正交性（最容易混的地方）

**① 任何容器都能跑任何 profile** —— 四份 `config.yaml` 都在同一个共享目录里：

```bash
# 在爱玛士的容器里跑，跑出来的仍然是"爱码士"（但没有 gitcode 配置、没有 coder 的挂载）
docker compose exec hermes /opt/hermes/.venv/bin/hermes -p coder chat
```

**② 同一个 profile 能被两个容器同时跑，而且不报错** —— 2026-11-11 的真实事故：`hermes` 容器里被手工 `s6-svc -u` 起了一个 `gateway-coder`，和 `hermes-coder` 容器里的同名网关并存 14 小时，两条飞书连接、双写 `profiles/coder/gateway_state.json`。`gateway.lock` 拦不住跨容器。

所以规矩是**一个 profile 一个容器**，靠约定不靠机制。

## 四份 `.env`，谁读谁

| 路径 | 谁读它 | 决定什么 |
|---|---|---|
| `<仓库>/.env` | **`docker compose`** | 插值 compose 里的 `${VAR}` → 变成**容器环境变量** |
| `~/.hermes/.env`（容器内 `/opt/data/.env`） | **Hermes**（default 身份） | default 的环境变量 |
| `~/.hermes/profiles/<name>/.env` | **Hermes**（`-p <name>` 时） | 那个身份的环境变量 |
| compose 的 `environment:` 段 | 容器 PID 1 | 进程环境变量 |

前两份**互不相干**：compose 不知道 `~/.hermes/.env`，Hermes 不知道仓库 `.env`。

### 优先级（`hermes_cli/env_loader.py:363`）

```
$HERMES_HOME/.env   (override=True)   ← 最高，能盖掉容器注入的值
      ↓
容器 / 进程 env（compose 注入）
      ↓
.op.env（override=False）
```

⚠️ **踩坑预警**：因为 `override=True`，改了 compose 里的值但 `~/.hermes/.env` 里有同名键时，**你的修改不会生效，也不会报错**。改完务必用下面的办法确认生效值。

```bash
# 容器注入值 vs 文件值（只输出判定，不回显密钥）
docker compose exec hermes sh -lc 'v=某变量名; \
  a=$(printenv "$v"); b=$(grep -E "^$v=" /opt/data/.env | head -1 | cut -d= -f2- | tr -d "\"\r "); \
  [ -z "$b" ] && echo "文件里没有 → 用容器的" || { [ "$a" = "$b" ] && echo 一致 || echo "⚠ 不一致：文件会覆盖容器"; }'
```

## 能力矩阵（2026-11-12 实测）

| 项 | `hermes` | `hermes-coder` | `hermes-finance` | `hermes-daoyuan` |
|---|---|---|---|---|
| 真邮箱配置 / ortie token | ✅ | ❌ 空卷遮蔽 | ❌ | ❌ |
| OneDrive（`/opt/onedrive`） | ✅ | ❌ | ❌ | ❌ |
| `GITCODE_TOKEN` | ✅ | ✅ | ❌ | ❌ |
| gitcode 配置目录 | ❌ | ✅ | ❌ | ❌ |
| `SERVER_MONITORING_MCP_URL` | ✅ | ✅ | ❌ | ❌ |
| `ZOTERO_API_KEY`（写） | ✅ | ✅ | ✅ | ❌ |
| `PAPER_QUEUE_DAOYUAN_TOKEN` | ❌ | ❌ | ❌ | ✅ |
| `DAILYINFO_MCP_KEY` | ❌ | ❌ | ❌ | ✅ |
| `GH_TOKEN` | ✅ | ✅ | ✅ | ✅ |
| zotero-mcp / paper-queue-mcp（网络服务） | ✅ | ✅ | ✅ | ✅ |

**没有任何一个容器是超集。** 这就是"派发落在哪个容器"会决定任务能不能跑通的原因。

## 看板与这三层的关系

- 板是**一个文件**（`~/.hermes/kanban.db`），四个 profile 共读共写 —— 这是唯一的交接介质
- `assignee` 只是卡上的一个字符串，**被指的 agent 全程不知情**
- 全机**只有一个** dispatcher（60s 扫板），当前住在 `hermes-coder` 容器的网关进程里
- worker 是该 dispatcher `fork` 出来的**临时进程**：`hermes -p <assignee> chat -q …`，用 assignee 的身份、住 dispatcher 的容器，干完就死
- **worker 不启动容器，也不启动 gateway**（容器数恒为 5，gateway 数恒为 4）

详见 [docs/kanban.md](kanban.md)。

## 网关隔离：一个容器一个网关

基础镜像的 s6 会在**每个**容器里、按共享卷上的 `gateway_state.json`，给四个 profile 都建
`gateway-<profile>` 服务。所以"一个容器只跑自己的 agent"**不是结构保证**，是
`entrypoint-wrapper.sh` 里的后台清扫维持出来的：等四个服务目录出现 → 非本 profile 的
`s6-svc -dk`（kill + disable；刻意不碰共享的 state 文件，那属于别的容器的活网关）→ 每 20s
重扫一轮（防 hydra 复活）→ 终检，最后对本 profile 补一次 `s6-svc -u`。

为什么这么在意：**飞书 app 凭据看容器 env，不看 profile**。容器里那个 `gateway-finance`
游荡，会拿**本容器**的 `FEISHU_APP_ID` 去连飞书 —— 于是两个网关抢同一个 app 的长连接。

### 2026-11-12 的两种现场

| 症状 | 真因 | 处置 |
|---|---|---|
| 某个 bot **完全没反应，且日志一条错误都没有**（该 profile 的 `gateway.log` 冻结、无 inbound 行） | 同一 app 上曾有两条 WS：游荡被 kill 后飞书仍把事件投给已死的连接，活着的网关收不到 | 重启该 profile 的网关（`s6-svc -k`）即恢复 |
| 自家网关 `exit 78`，日志 `Another local Hermes gateway is already using this Feishu app_id (PID …)` | 游荡先抢到了本容器的 app_id，正牌启动即退 | 先 `-dk` 清掉外部，再 `-u` 拉起自己 |

触发条件很隐蔽：清扫原来只等 **90s**，而 stage2 的 `chown -R /opt/data/home` 在 virtiofs 上
能跑 **10 分钟** —— 清扫等到超时、一轮没扫，却仍打印「✅ 隔离完成」。修完是 **900s** +
等不到就告警 + 末尾兜底拉起 own（守卫 `tests/test_entrypoint_gateway_isolation.py`）。
重建容器时若看到启动"Up 了但网关几分钟不起"，先看 `[stage2] Fixing ownership` 是否还在跑，
不要急着加服务。

### 处置口诀

```bash
S6=/package/admin/s6-2.15.0.0/command/s6-svc

# 1) 看谁在跑（每容器应恰好 1 个 up，其余 down）
docker compose exec -T <容器> sh -lc 'for d in /run/service/gateway-*; do printf "%-16s %s\n" "$(basename $d)" "$(/package/admin/s6-2.15.0.0/command/s6-svstat $d)"; done'
# 2) 非本 profile 的一律 kill + disable
docker compose exec -T <容器> sh -lc "$S6 -dk /run/service/gateway-<其它>"
# 3) 自家的拉起来（若 exit 78，先确认外部已清干净）
docker compose exec -T <容器> sh -lc "$S6 -u /run/service/gateway-<own>"
```

重建容器前记得备份 lark-cli 的 user token（`/root/.local/share/lark-cli`，存容器内 keychain，
不在挂载卷上）：`docker compose cp <容器>:/root/.local/share/lark-cli /tmp/lark-cli-<容器>`，
重建后拷回并 `chown root:root` + `chmod 700`。

## 排查命令

```bash
# 谁是 dispatcher（每个容器只会打一条，只有一条是 holding）
docker compose exec -T hermes         sh -lc 'grep -i "kanban dispatcher" /opt/data/logs/gateway.log | tail -1'
docker compose exec -T hermes-coder   sh -lc 'grep -i "kanban dispatcher" /opt/data/profiles/coder/logs/gateway.log | tail -1'
docker compose exec -T hermes-finance sh -lc 'grep -i "kanban dispatcher" /opt/data/profiles/finance/logs/gateway.log | tail -1'
docker compose exec -T hermes-daoyuan sh -lc 'grep -i "kanban dispatcher" /opt/data/profiles/daoyuan/logs/gateway.log | tail -1'

# 容器数 / 进程树
docker compose ps --format '{{.Name}}' | grep -c hermes
docker compose exec -T hermes-coder sh -lc 'ps -eo pid,ppid,args | grep hermes | grep -v grep'

# 每个 profile 有没有自己的 .env（决定"钥匙挂身份"还是"挂容器"）
ls ~/.hermes/.env ~/.hermes/profiles/*/.env 2>/dev/null
```

注意：`/opt/data` 是共享卷，`/opt/data/*/logs/gateway.log` 这种通配会**读到别人的日志**。查某个 profile 必须指到它自己的路径。

## 服务定义从哪来

四个 hermes 服务的 compose 段不是手写的。来源是 `agents/hermes/`：

- `base.yaml`：四个服务相同的字段、环境变量、挂载目录，以及被多个 agent 共用的变量组
- `<agent>.yaml`：端口、`profile`、飞书应用前缀、要启用的变量组、`mcp` 列表、自己独有的环境变量、挂载。`HERMES_PROFILE` 和 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 由前两项推导，不手写
- `mcp` 列表同时决定容器环境变量，和 `start.sh` 读的 `agents/hermes/mcp.tsv`。paper-queue 在表里是 `late`：token 写入 `.env` 之后才注册
- 环境变量的输出顺序由渲染器固定：公共变量、所选变量组、所选 MCP、推导出的变量、agent 自己的变量。这个顺序不进入配置哈希
- 挂载按清单顺序输出，顺序变了会重建容器

改完清单后重新生成，不要改 `docker-compose.yml` 里 `>>> GENERATED` 和 `<<< GENERATED` 之间的内容，也不要手改 `mcp.tsv`：

```bash
uv run --with pyyaml scripts/render_agents.py          # 写回 compose 和 mcp.tsv
uv run --with pyyaml scripts/render_agents.py --check  # 只检查有没有人改过生成结果
```

加一个 hermes agent：新增一个清单文件，把它的名字加进 `base.yaml` 的 `order`，再跑上面的命令。挂载顺序会进入 Docker 的配置哈希，顺序变了就会重建容器。

`hermes-dashboard` 不在这套清单里，仍然手写。

## 已知的历史包袱

- `~/.hermes/profiles/default/` 只有一个空的 `pairing/` —— **废弃空壳**。root profile 的 home 是 `~/.hermes` 本身，Hermes 启动时会 warn 并跳过这个目录
- 2026-11-11 清理过一个手工拉起的 `gateway-coder` 残留（双写 `gateway_state.json`）。别再在 `hermes` 容器里用 `s6-svc -u /run/service/gateway-<profile>` 去"补一个网关" —— 想补之前先读上一节的「网关隔离」
- `CLAUDE.md` 曾说 coder 的 Zotero 查询走共享 zotero-mcp，实际 `hermes -p coder mcp list` 只有 `server-monitoring`（coder 是拿 `ZOTERO_API_KEY` 走 API/pipeline，不是 MCP）
