# Plan: Hermes 的 MCP 接线改由清单声明

**来源**: `hermes-mcp-ensure-merge.plan.md` 末尾的「做完之后」。三个 Bearer MCP 的写入逻辑已经在 `scripts/ensure_hermes_mcp.py`，本步把「哪个 agent 接哪一项」从 `start.sh` 的四处手写调用挪到清单。
**复杂度**: Medium（渲染器多产出一张表；`start.sh` 两处调用改成读这张表；3 个接线测试要改断言位置）
**分支**: 继续 `refactor/hermes-agent-manifest`（worktree：`.claude/worktrees/hermes-agent-manifest`）
**一个提交**

## 摘要

agent 文件用 `mcp: [server-monitoring]` 这种列表声明要接的项。同一份声明决定两件事：

- 容器环境变量（今天写在 `bundles` 或 agent 自己的 `env` 里）
- `config.yaml` 里的 `mcp_servers` 条目（今天由 `start.sh` 按 profile 各调一次脚本）

宿主的 `python3` 没有 PyYAML，`start.sh` 不能读清单。渲染器额外产出一张提交入库的 TSV，`start.sh` 只读这张表。渲染器仍然是开发期工具，`start.sh` 不调用它。

## 已实测的事实

| 事实 | 依据 |
|---|---|
| 今天一共 4 次 Hermes 侧注册：爱玛士和爱码士的 server-monitoring、道元的 dailyinfo、道元的 paper-queue | `start.sh` 336、345、360、578 行 |
| 前 3 次在一处，paper-queue 单独在后面 | 574 行注释：必须在 token 写入 `.env` 之后、`docker compose up` 之前，否则首跑会因缺 token 把条目删掉 |
| 虾酱的 dailyinfo 是另一次调用，走 `ensure_openclaw_dailyinfo.py` | `start.sh` 370 行。不在本步 |
| 失败时 `start.sh` 把状态改写成字面量 `failed`，然后继续 | 341、350、365、586 行。脚本自己的 `unsupported:*` 不会传到后面 |
| 重启只看三个状态变量：`DEFAULT_MCP_STATUS`、`CODER_MCP_STATUS`、`DAOYUAN_PAPER_QUEUE_STATUS`（外加 paper-queue 的 skill 变化） | 938、939、960 行 |
| `DAOYUAN_DAILYINFO_STATUS` 赋值之后没有人再读 | 全文件只出现在 358–365 行。dailyinfo 注册变了，今天**不会**重启道元 |
| 日报注册用精确字符串 `"${DEFAULT_MCP_STATUS}" != removed*` 和 `"${CODER_MCP_STATUS}" != removed*` | `tests/test_hermes_server_monitoring_wiring.py` 钉着这两句 |
| 三个测试把脚本文件名钉在 `start.sh` 正文里，并要求出现在 `docker compose up` 之前 | `test_hermes_server_monitoring_wiring.py`、`test_dailyinfo_mcp_wiring.py`、`test_hermes_paper_queue_wiring.py` |
| server-monitoring 的两个变量今天是 bundle；dailyinfo 和 paper-queue 的变量写在道元自己的 `env` 里 | `agents/hermes/base.yaml`、`daoyuan.yaml` |
| paper-queue 的 URL 默认值不是空 | `PAPER_QUEUE_MCP_URL=${PAPER_QUEUE_MCP_URL:-http://paper-queue-mcp:8003/mcp}` |
| 配置路径变量名不能从服务名推导 | 默认 profile 是 `HERMES_DEFAULT_CONFIG`（`~/.hermes/config.yaml`），不是 `profiles/default`。finance 没有对应变量 |
| 环境变量顺序不影响配置哈希，值变了会影响 | 上一步已实测 |

## 宁缺毋滥原则

- **不跑** `scripts/start.sh`。验收用静态测试和配置哈希。跑它会改线上 `config.yaml`，并可能重启容器
- **不改**重启判断和日报注册那两段。状态变量名保持 `DEFAULT_MCP_STATUS`、`CODER_MCP_STATUS`、`DAOYUAN_PAPER_QUEUE_STATUS`，dailyinfo 的状态继续不被任何人用来重启
- **不改** paper-queue 的 token / URL / actor 生成，不改 skill 链接，不改 `remove_openclaw_paper_queue.py`
- **不把**虾酱的 `ensure_openclaw_dailyinfo.py` 收进这张表
- **不收**语雀、事务库、Zotero、repo-scanner、qwennote。它们的注册形状不是「这一份 Bearer 块」
- **不把** cron 和 skill 挂载收进 `mcp:`。日报的投递目标、时刻、skill 挂载仍是手写的
- **不改**三个入口脚本和 `ensure_hermes_mcp.py`。`:no-token` 仍由 paper-queue 入口自己加
- 容器环境变量的**值**与今天逐字相同。顺序可以变

## 清单怎么写

`base.yaml` 增加目录，agent 文件只点名。目录里的 `on` 是给 `start.sh` 用的壳层名字，因为重启和日报还在按这些变量名读；agent 文件不重复它们。

```yaml
# base.yaml（示意）
mcp:
  server-monitoring:
    script: ensure_hermes_server_monitoring.py
    phase: early
    env:
      SERVER_MONITORING_MCP_URL: "${SERVER_MONITORING_MCP_URL:-}"
      SERVER_MONITORING_MCP_TOKEN: "${SERVER_MONITORING_MCP_TOKEN:-}"
    on:
      hermes:       {config_var: HERMES_DEFAULT_CONFIG, status_var: DEFAULT_MCP_STATUS}
      hermes-coder: {config_var: CODER_CONFIG,          status_var: CODER_MCP_STATUS}
  dailyinfo:
    script: ensure_hermes_dailyinfo.py
    phase: early
    env:
      DAILYINFO_MCP_URL: "${DAILYINFO_MCP_URL:-}"
      DAILYINFO_MCP_KEY: "${DAILYINFO_MCP_KEY:-}"
    on:
      hermes-daoyuan: {config_var: DAOYUAN_CONFIG, status_var: DAOYUAN_DAILYINFO_STATUS}
  paper-queue:
    script: ensure_hermes_paper_queue.py
    phase: late          # token 写入 .env 之后才能跑
    env:
      PAPER_QUEUE_MCP_URL: "${PAPER_QUEUE_MCP_URL:-http://paper-queue-mcp:8003/mcp}"
      PAPER_QUEUE_DAOYUAN_TOKEN: "${PAPER_QUEUE_DAOYUAN_TOKEN:-}"
    on:
      hermes-daoyuan: {config_var: DAOYUAN_CONFIG, status_var: DAOYUAN_PAPER_QUEUE_STATUS}
```

```yaml
# default.yaml / coder.yaml
mcp: [server-monitoring]

# daoyuan.yaml
mcp: [dailyinfo, paper-queue]
```

同时删掉：`bundles` 里的 `server-monitoring`，以及道元 `env` 里的那 4 个键。finance 不写 `mcp`。

规则（违反就报错，不静默）：

- `mcp` 里的名字必须在目录里
- 同一个 agent 把同一项写两次，报错
- 目录的 `on` 和服务实际声明的 `mcp` 必须互相对得上：多一行或少一行都报错
- `mcp` 带进来的变量键若已经在 `base.env`、所选 bundle、推导项或 agent 自己的 `env` 里，报错
- `phase` 只允许 `early` 和 `late`
- `config_var` 只允许 `HERMES_DEFAULT_CONFIG`、`CODER_CONFIG`、`DAOYUAN_CONFIG` 这三个 `start.sh` 里已经赋值的名字

环境变量输出顺序变为：`base.env` → 所选 bundle → **所选 mcp（按 agent 列出的顺序）** → 推导项 → agent 自己的 `env`。

## 对照表

`agents/hermes/mcp.tsv`，渲染器写入，`--check` 一并检查。第一行是注释，说明来源和重新生成的命令。之后每个注册点一行，列用制表符分隔：

```text
phase	service	config_var	script	status_var	server
early	hermes	HERMES_DEFAULT_CONFIG	ensure_hermes_server_monitoring.py	DEFAULT_MCP_STATUS	server-monitoring
early	hermes-coder	CODER_CONFIG	ensure_hermes_server_monitoring.py	CODER_MCP_STATUS	server-monitoring
early	hermes-daoyuan	DAOYUAN_CONFIG	ensure_hermes_dailyinfo.py	DAOYUAN_DAILYINFO_STATUS	dailyinfo
late	hermes-daoyuan	DAOYUAN_CONFIG	ensure_hermes_paper_queue.py	DAOYUAN_PAPER_QUEUE_STATUS	paper-queue
```

行序固定为：`base.order` 里的 agent 顺序，每个 agent 再按它自己的 `mcp` 列表顺序。这四行的顺序因此与今天的调用顺序相同。

## start.sh 怎么改

抽一个函数，调用两次。两次之间留着现有的 token 生成，一行不动。

```bash
# early：今天 server-monitoring 两次 + dailyinfo 那一段的位置
apply_hermes_mcp early
# …… paper-queue 的 token / URL / actor 生成，保持原样 ……
# late：今天 paper-queue 那一次的位置，仍在 docker compose up 之前
apply_hermes_mcp late
```

函数按阶段过滤 TSV。每一行：

1. 配置文件不存在就跳过，状态变量保持调用前设好的空字符串（与今天「文件不存在则保持 `""`」一致）
2. 执行表里的脚本，参数仍是 `"<配置文件>" --env-file "${REPO_ROOT}/.env"`，stdout 和 stderr 一起接住
3. 退出码为 0：状态变量等于脚本打印的状态。若状态以 `:no-token` 结尾，照现在的句子警告（`PAPER_QUEUE_DAOYUAN_TOKEN 为空，道元 paper-queue 已停用`）
4. 退出码非 0：先把脚本输出打进警告，再把状态变量写成 `failed`

不在这个函数里做重启。`:no-token` 只有 paper-queue 入口会产生，所以那句警告不会套到另外两项上。

## Files to Change

| File | Action | Why |
|---|---|---|
| `agents/hermes/base.yaml` | UPDATE | MCP 目录；去掉 `server-monitoring` bundle |
| `agents/hermes/default.yaml` | UPDATE | `mcp: [server-monitoring]`，不再点那个 bundle |
| `agents/hermes/coder.yaml` | UPDATE | 同上 |
| `agents/hermes/daoyuan.yaml` | UPDATE | `mcp: [dailyinfo, paper-queue]`，删掉手写的 4 个变量 |
| `agents/hermes/mcp.tsv` | CREATE | 渲染产物，`start.sh` 的读取对象 |
| `scripts/render_agents.py` | UPDATE | 把 mcp 展开进环境变量，并写出 / 核对 TSV |
| `scripts/start.sh` | UPDATE | 四处调用换成 `apply_hermes_mcp` 的两次调用 |
| `tests/test_render_agents.py` | UPDATE | 目录校验、行序、变量值、`--check` 覆盖 TSV |
| `tests/test_hermes_server_monitoring_wiring.py` | UPDATE | 「脚本名在 start.sh 里」改为「在 TSV 里，且 early 调用在 up 之前」。重启和日报那两条断言不动 |
| `tests/test_dailyinfo_mcp_wiring.py` | UPDATE | Hermes 侧脚本名改去 TSV 找。`ensure_openclaw_dailyinfo.py` 仍须在 `start.sh` |
| `tests/test_hermes_paper_queue_wiring.py` | UPDATE | late 调用必须在 `secrets.token_hex` 之后、`docker compose up` 之前 |
| `docs/hermes-topology.md` | UPDATE | 补一句：`mcp` 列表同时决定环境变量和这张表 |

`docker-compose.yml` 会随渲染改掉环境变量的文本顺序。不手改。

## Tasks

### 0. 记下基线

四个 hermes 服务的 `docker compose config --hash`（按主目录解析，与上一步相同的命令）。另外记下这三组变量在渲染结果里的完整 `KEY=value`。

### 1. 清单和渲染器

按上面的形状改清单。渲染器在写 compose 的同时写 `mcp.tsv`。`--check` 任一产物漂移都退出 1，清单自身不合法退出 2。

### 2. start.sh

只替换四处注册。用 `git diff -U3 -- scripts/start.sh` 确认 token 生成、重启、日报、虾酱 dailyinfo、skill 链接这些行不在 diff 里。

### 3. 测试

新测试覆盖：未知 mcp 名、`on` 与声明不一致、变量键冲突、TSV 恰好是上面四行且顺序不变、paper-queue 的 URL 默认值逐字还在。三个旧接线测试按上一节改断言，其余断言不动。

### 4. 验收

```bash
MAIN=/Users/owen/code/myopenclaw
S='hermes,hermes-coder,hermes-finance,hermes-daoyuan'
uv run --with pyyaml scripts/render_agents.py
uv run --with pyyaml scripts/render_agents.py --check
diff <(docker compose -f $MAIN/docker-compose.yml --project-directory $MAIN -p myopenclaw config --hash $S | sort) \
     <(docker compose -f docker-compose.yml       --project-directory $MAIN -p myopenclaw config --hash $S | sort)
docker compose -f docker-compose.yml --project-directory $MAIN -p myopenclaw --dry-run up -d hermes hermes-coder hermes-finance hermes-daoyuan
uv run --with pytest --with pyyaml --with pylatexenc pytest tests/ -q
```

预期：哈希相同，试运行四行 `Running`，全量通过且 0 跳过。不执行 `start.sh`。

## 回滚

单个提交 `git revert`。TSV 只在有人运行 `start.sh` 时被读取；revert 之后下一次启动回到四处手写调用。

## 做完之后

加一个 Hermes agent 并让它使用已有的这三项之一，改的是清单而不是 `start.sh`。再往后才考虑把日报注册也收进声明：那一步要动投递目标和时刻，比本步大，而且会碰到现在钉死的 `removed*` 判断。
