# 服务器监控 MCP（server-monitoring）

**爱玛士**（Hermes 默认 profile）与**爱码士**（Hermes coder profile）通过**团队 server-monitoring MCP**
按需回答服务器、容器与 GFS 数据处理相关的问题；每天 08:30 还会分别把服务器日报推到
**飞书私聊**（爱玛士）和**预报系统群**（爱码士）。天一（OpenClaw）保留按需查询，每日推送已关闭。
典型问题：「weather 这台机器现在怎么样」「rainfall-oa-data 的 GFS 处理到哪一步了」
「pm-minio-01 上哪些容器在重启」「zhixun-core 最近一次构建怎么样」。

服务端是团队项目（[gitcode.com/dlut-water/server-monitoring](https://gitcode.com/dlut-water/server-monitoring)），
部署在团队服务器，**只读**。本仓只做**客户端接入**：一个可选 MCP 注册、TLS 信任配置、守卫与本文档。
不改服务端、不做 81 独立部署（见文末 Phase 2 预案）。

**两个客户端入口**（2026-10-30 起）：

| 入口 | URL | TLS | 前置条件 |
|---|---|---|---|
| **校园域名（推荐）** | `https://<MCP_DOMAIN>:8765/mcp` | BunkerWeb 终结，公网通配符证书（TrustAsia） | 校园网可达即可；**无需 UniVPN / 内网 CA** |
| 内网直连 | `https://<MCP_HOST>:8765/mcp` | 内网自签 CA | 需 UniVPN；容器经 `NODE_EXTRA_CA_CERTS` 加载仓库 CA |

域名入口的 Host 白名单问题见服务端 issue
[#5](https://gitcode.com/dlut-water/server-monitoring/issues/5)（`MCP_ALLOWED_HOSTS`
需显式包含 `<MCP_DOMAIN>:8765`；客户端无法靠 headers 绕过——`Host`
是 Fetch 规范的 forbidden header）。

> 公开仓库不写真实域名/入口 IP：本文用 `<MCP_DOMAIN>` / `<入口 IP>` 占位，
> 真实值以本地 gitignored `.env.tianyi-bot` 或维护者提供为准。

## 拓扑

```
Hermes（爱玛士 / 爱码士） / 天一容器（openclaw-tianyi）
  │  HTTPS + Bearer token
  │  入口一：<MCP_DOMAIN>:8765（公网证书，系统信任库）
  │  入口二：<MCP_HOST>:8765（内网自签 CA，NODE_EXTRA_CA_CERTS；需 UniVPN）
  ▼
BunkerWeb（仅域名入口）      TLS 终结 → http://<MCP_HOST>:8765
  │
  ▼
server-monitoring MCP        weather / <MCP_HOST>:8765（Streamable HTTP, stateless, 13 个只读工具：9 运行 + 3 交付/CI + get_health_events）
  │
  ├─ Prometheus              pm-minio-01 / <MONITOR_HOST>:9090
  └─ Docker Metadata Gateway pm-minio-01 / <MONITOR_HOST>:8766（经本机 docker socket，只读容器元数据）

注册实体：服务器 pm-minio-01（<MONITOR_HOST>）、weather（<MCP_HOST>）；
项目 rainfall-oa-data（仅运行监控）、zhixun-core 与 realtime-forecast（生产 + staging，含 Jenkins 交付映射）。
```

13 个工具（9 运行 + 3 交付/CI + `get_health_events`，全部 READ_ONLY）：
`list_servers` / `get_server_status` / `list_containers` / `get_container` /
`get_container_provenance` / `get_prometheus_status` / `get_project_runtime_status` /
`get_gfs_status` / `get_runtime_failure_evidence`；
交付类（2026-10-13 服务端发布 Jenkins 证据链后纳入客户端策略）：`list_projects` /
`get_project_overview` / `get_project_build`（构建号、JUnit 测试与 revision 证据；先总览、后按构建号追问）；
健康事件（2026-11-05 实测服务端已上线）：`get_health_events`（健康边界事件游标；Hermes 日报暂未消费）。
Hermes skill（`skills/server-monitoring-digest/SKILL.md`）与天一 `openclaw-tianyi/workspace/AGENTS.md`
都写着同样的证据纪律（UNKNOWN ≠ 健康、
「评估完成」≠「已成功处理 cycle」、GFS 主 worker 与 `gfs-visualization` 分开报、
**staging ≠ 生产验收**、构建绿 ≠ 已部署/已验收）。
已知缺口（服务端现状，如实报告即可）：`weather` 无 Docker 详情后端 —— 查该服务器容器返回
`DOCKER_UNAVAILABLE`，这是能力缺失，不是服务故障。

## 凭据

| 项 | 从哪来（服务器 <MCP_HOST>） | 放哪 |
|---|---|---|
| Bearer token | 服务器维护者私有目录 `.config/server-monitoring-mcp/auth_token`（找团队维护者要） | **只**进 gitignored 的 `.env`（Hermes：`SERVER_MONITORING_MCP_TOKEN`）和 `.env.tianyi-bot`（天一：`TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN`）；不进仓库、不进渲染产物、不进聊天记录 |
| 内网 CA 证书（公钥材料） | 同目录 `ca.crt`（签发内网 IP 入口证书的私有 CA） | 随仓库提交：`docker/tianyi-bot/ca/server-monitoring-ca.pem`；**仅内网直连需要**，域名入口用系统信任库 |

> 当前生产入口是**校园域名**（`https://<MCP_DOMAIN>:8765/mcp`，公网证书），Hermes 与天一
> 都不需要自定义 CA。下面这份 CA 只用于直连内网 IP 入口的备选路径。

- token 是**全客户端共用的服务端凭据**，轮换要与维护者协调 —— 本仓无法自助轮换（与本机
  yuque-mcp「每客户端一份 key」的模式不同）。
- CA 是公钥材料，提交进**公开仓库**也没有泄密风险（公钥只能验证签名、不能制造签名）；
  **私钥绝不进仓库**（`tests/test_server_monitoring_mcp.py` 里有断言）。服务端的 CA 私钥
  已在签发后移除，续期需要 CA rollover 规划（见「已知约束」）。
- 域名入口的证书是公网 TrustAsia 证书，Node 默认信任库直接认可；容器仍会加载仓库内 CA
  （`NODE_EXTRA_CA_CERTS` 是**追加**语义，不影响公网证书校验，也保留了直连回退能力）。
- 核对内网直连的仓库 CA 与线上一致：

```bash
openssl s_client -connect <MCP_HOST>:8765 \
  -CAfile docker/tianyi-bot/ca/server-monitoring-ca.pem </dev/null 2>/dev/null \
  | grep -E "subject=|Verify return code"
# 期望：subject=CN = <MCP_HOST>；Verify return code: 0 (ok)
```

## 启用

### Hermes（爱玛士 / 爱码士，当前主用）

```bash
# 1. 根 .env（两个 profile 共用；真实地址/token 只放 .env，已 gitignore）
SERVER_MONITORING_MCP_URL=https://<MCP_DOMAIN>:8765/mcp
SERVER_MONITORING_MCP_TOKEN=<找维护者要>
MONITORING_DIGEST_GROUP_CHAT_ID=oc_xxx   # 爱码士推送的预报系统群；留空 = 不注册群日报

# 2. 启动：start.sh 在 up -d 前幂等写入两个 profile 的 MCP 条目，
#    并注册两条 08:30 北京（30 0 * * * UTC）的 server-monitoring-digest cron
./scripts/start.sh

# 3. 两个 profile 各自验证（最终判据）
docker compose exec -T hermes /opt/hermes/.venv/bin/hermes mcp test server-monitoring
docker compose exec -T hermes-coder /opt/hermes/.venv/bin/hermes -p coder mcp test server-monitoring

# 4. 手动触发日报（会真实投递：爱玛士私聊 / 爱码士预报系统群）
#    job id 用 hermes cron list / hermes -p coder cron list 查
docker compose exec -T hermes /opt/hermes/.venv/bin/hermes cron run <default-job-id>
docker compose exec -T hermes-coder /opt/hermes/.venv/bin/hermes -p coder cron run <coder-job-id>
```

### 天一（可选，仅按需查询；日报默认 off）

```bash
# 1. .env.tianyi-bot 配 URL + token（日报保持 TIANYI_BOT_MONITORING_DIGEST_CRON=off）
#    推荐校园域名入口（公网证书，无需 VPN）：
TIANYI_BOT_SERVER_MONITORING_MCP_URL=https://<MCP_DOMAIN>:8765/mcp
#    内网直连回退（需 UniVPN，容器自动加载仓库 CA）：
# TIANYI_BOT_SERVER_MONITORING_MCP_URL=https://<MCP_HOST>:8765/mcp
TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN=<找维护者要>

# 2. 启动（预检会拦「URL 非空而 token 空」）
./scripts/start-tianyi-bot.sh

# 3. 网关侧 MCP 连接
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml \
  exec openclaw-tianyi node /app/openclaw.mjs mcp probe server-monitoring --json
```

只读检查器（宿主机，token 只回显长度）：
`uv run scripts/check_server_monitoring_mcp.py --json`。
TLS 判定为**双模式**：系统信任库能验通 = 公网证书（域名入口）；否则叠加仓库 CA = 内网自签
（直连入口）；两者都不通才红。`server_monitoring_summary.py` 使用同一个 TLS 上下文。

飞书真实提问（爱玛士/爱码士私聊或群里）：
「weather 这台机器现在怎么样」「rainfall-oa-data 的 GFS 处理到哪了」；反向用例：问未注册服务器名，
必须如实说不知道，不得答成健康。

## 开关与回退

- **开关就是 URL**：清空 `TIANYI_BOT_SERVER_MONITORING_MCP_URL` → `./scripts/start-tianyi-bot.sh`（force-recreate）
  → 渲染时移除 `server-monitoring` 条目，能力完全关闭。关闭态**没有任何日志噪音**：
  `NODE_EXTRA_CA_CERTS` 为空串时 node 静默忽略（实测，有守卫钉着）。
- **三道 fail-loud**（都是刻意设计，静默降级比报错难查得多）：
  1. `render-config.mjs`：URL 非空而 token 空 → 抛错，容器起不来；
  2. `entrypoint.sh`：URL 非空而容器内 CA 缺失 → 中文报错 + exit 1
     （node 对缺失的 `NODE_EXTRA_CA_CERTS` 文件也是静默的，所以这层显式检查是唯一防线。
     域名入口本身不需要内网 CA，但 CA 随仓库提供、直连回退还要用，因此不做「按入口豁免」）；
  3. `start-tianyi-bot.sh`：同样预检 —— `compose up` 对 crash-loop 返回 0，这层最早发现。
- 监控不可用**不影响天一其它能力**：工具报错时如实回复「连不上/未知」，repo-scanner、语雀照常。

## 验证清单（改动后按序跑）

```bash
uv run --with pytest --with pyyaml pytest tests/ -q          # 含 test_server_monitoring_mcp.py
for t in tests/test-*.sh; do bash "$t"; done                 # 含 rendered-config（需要本机 node + docker + 目标镜像）
# 开关两态（⚠️ 别贴完整 config 输出 —— 会渲染 token）：
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml config --quiet
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml config | grep NODE_EXTRA_CA_CERTS
#   关闭态应得到空串；启用态应得到 /opt/tianyi-bot/ca/server-monitoring-ca.pem
uv run scripts/check_server_monitoring_mcp.py --json
```

## 总结获取（摘要脚本）

`scripts/server_monitoring_summary.py` —— 一次收集 **5 次固定调用 + 每服务器/每项目各 1 次**
（当前注册表 2 服务器 + 11 项目 = 18 次：每台服务器状态+告警（含盘高水位/负载）、Prometheus、
GFS（含 stage 失败 / cycle 年龄 / UNKNOWN 原因）、失败证据、各项目 overview），**模板直出**
人读摘要；`--json` 输出结构化结果（供下游自取——本仓不做推送、不经过 LLM：数字与状态全部
来自工具返回，服务端原则"事实由程序确定"）。

**消费分工（2026-11-05 定）**：

- **爱玛士（Hermes 默认 profile）**：按需查询 + 每日 08:30 服务器日报 → 飞书私聊；
- **爱码士（Hermes coder profile）**：按需查询 + 每日 08:30 服务器日报 → **预报系统群**
  （用它自己的飞书应用；应用必须在群内）；
- **天一（OpenClaw）**：保留按需查询（`TIANYI_BOT_SERVER_MONITORING_MCP_URL` 非空），
  **每日推送已关闭**——`.env.tianyi-bot` 的 `TIANYI_BOT_MONITORING_DIGEST_CRON=off`，
  entrypoint 启动时会回收既有的私聊/群聊 automation。天一的推送代码仍保留，需要时可重新打开；
- 道元 / finance 不接入。

Hermes 两个 profile 的 MCP 条目由 `scripts/ensure_hermes_server_monitoring.py` 幂等写入
（profile 不继承彼此的 `mcp_servers`），配置里只写 `${SERVER_MONITORING_MCP_URL}` /
`${SERVER_MONITORING_MCP_TOKEN}` 占位符；真实值只放 `.env`（gitignored），由 compose 注入
`hermes` / `hermes-coder`。URL 为空 = 自动移除条目。改 `.env` 后跑 `./scripts/start.sh`：
它在 `up -d` 前重写受管条目，并在容器未被重建时重启对应 profile。

**日报 skill / cron**：`skills/server-monitoring-digest/SKILL.md`（repo 只读挂载到
`/opt/hermes-skills/server-monitoring-digest`，两个 profile 各一条 symlink）。`start.sh`
幂等注册两条 cron（`30 0 * * *` UTC = 08:30 北京）：

| profile | 投递 | 配置 |
|---|---|---|
| 爱玛士（hermes） | 飞书私聊（`DELIVER` = `LARK_USER_OPEN_ID` / `FEISHU_HOME_CHANNEL`） | `SERVER_MONITORING_MCP_URL` + `SERVER_MONITORING_MCP_TOKEN` |
| 爱码士（hermes-coder） | 预报系统群 | `MONITORING_DIGEST_GROUP_CHAT_ID`（`oc_` 开头；爱码士应用必须在群内） |

两条 job 各自跑独立 agent 回合，内容相近但不保证逐字一致；各有自己的
`delivery_outcome`。`MONITORING_DIGEST_GROUP_CHAT_ID` 留空 = 不注册群日报（私聊日报不受影响）。

**天一旧推送（保留但默认关闭）**：entrypoint 仍支持私聊 `tianyi-monitoring-daily` +
群聊 `tianyi-monitoring-daily-group`；`TIANYI_BOT_MONITORING_DIGEST_CRON=off` 时按
declaration-key 主动回收。若重新打开：私聊填 `TIANYI_BOT_OWNER_OPEN_ID`（**应用维度**
open_id，填错症状 `99992361 "open_id cross app"`），群聊填
`TIANYI_BOT_MONITORING_DIGEST_CHAT_ID`。⚠️ 域名入口在校园网可达时无需 UniVPN；回退内网
直连才需要。入口不可达时如实报"取数失败"——这也是监控信号；部署到内网服务器后无此约束。

本脚本（`server_monitoring_summary.py`）的定位是**验收证据 / 入口可达时的
随手查 / 部署参考实现**——与推送自动化是两条独立路径，互不依赖。

`--json` 顶层键（下游消费契约）：`generated_at` / `overall{headline, alerts_firing,
unknown_projects, servers_total, projects_total}` / `servers[]` / `prometheus` / `gfs` /
`failure_evidence` / `projects[]` / `completeness{ok, failed[{key,error}]}` / `note`。
键的含义随本节描述；**新增字段可以，改名/删减视为破坏性变更**。

```bash
uv run scripts/server_monitoring_summary.py            # 人读摘要
uv run scripts/server_monitoring_summary.py --json     # 结构化（含 completeness.failed 缺口清单）
```

行为约定（有守卫钉住，`tests/test_server_monitoring_summary.py`）：
- 单工具失败**不中止**：记入"取数缺口"照常出摘要（退出码 1）；
- 全部失败**明确报"无法获取"**，绝不拿空数据冒充"一切正常"；
- UNKNOWN 单列点名，不吞进"正常"；退出码 0=全部成功 / 1=有缺口 / 2=配置错误。

## probe / 连接失败对照表

| 症状 | 含义 | 处置 |
|---|---|---|
| 401 Unauthorized | token 不对，或 Authorization header 没送达 | 先核 `.env.tianyi-bot`；token 确定正确仍 401 → 见下条「header 通道」 |
| TLS 校验失败 | 证书链不被系统信任库 / 仓库 CA 认可 | 域名入口查公网证书与系统时间；内网直连核仓库 CA 与 `compose config` 的 `NODE_EXTRA_CA_CERTS` |
| ENOTFOUND / 连接超时 | 容器到入口网络不通 | 域名入口查校园网/DNS（`<入口 IP>`）；内网直连查 UniVPN 与 `<MCP_HOST>` |
| redirect | URL 少了 `/mcp` | URL 必须带 `/mcp` |
| 421 Misdirected / Invalid Host header | 服务端 `MCP_ALLOWED_HOSTS` 未包含该 Host | 域名入口需维护者把 `<MCP_DOMAIN>:8765` 加入白名单（issue #5）；客户端无法用 headers 绕过（`Host` 是 forbidden header） |

## 已知约束

- **streamable-http + 自定义 header 是本仓第一次用**（先例：yuque-mcp 是 sse+header、
  repo-scanner 是 streamable-http 无 header）。最终判据是运行时 `mcp probe`。若 probe 报 401
  而检查器用同一 token 过了 `/health/ready`，说明 OpenClaw 未在 streamable-http 上发送自定义
  header —— 退路是加一个注入 `Authorization` 的 shim 容器（**本期未建**，出现再说）。
- token 全客户端共用：轮换 = 所有客户端同改。
- 客户端策略 = **12 个必需工具**（9 运行 + 3 交付，2026-10-13 服务端正式发布交付层后纳入）；
  2026-11-05 实测服务端已多出 `get_health_events`（共 13 个），检查器对额外工具只提示；
  日报暂未消费健康事件（原服务端 health-events 提案已上线，后续可做跃迁通知）。
  服务端统一汇报计划中的 `get_progress_report` / `get_report_item` / `get_evidence`
  （按时间范围汇总、事项统计与持久化追问）**尚未上线**——上线并以真实数据验收后再评估
  是否纳入，本仓不预接入。
- 证书有效期：内网自签叶子证书到 **2027-10-30**（直连入口）；域名入口是 BunkerWeb 的
  公网通配符证书，由团队维护、自动续期。检查器会报所连入口的叶子证书剩余天数。
  CA 私钥签发后已移除 ⇒ 内网直连续期 = CA rollover + 换服务端证书 + 同步本仓
  `ca/server-monitoring-ca.pem`。
- TLS 信任是**进程级**的：`NODE_EXTRA_CA_CERTS` 对整个 gateway 的出站 TLS 生效，且是
  **追加**语义（不替换系统信任库）。仅在启用时生效（URL 门控）；域名入口走系统信任库，
  内网直连才依赖仓库 CA —— 这是接受的取舍。Hermes 走校园域名（公网证书），Python MCP
  客户端按系统信任链校验，不需要自定义 CA；若改用内网 IP 入口，需另行给 Hermes 配置 CA
  （当前未接）。
- 域名入口依赖服务端 `MCP_ALLOWED_HOSTS` 包含 `<MCP_DOMAIN>:8765`（issue #5）。
  这是服务端部署项，客户端改配置无法绕过；入口新增/换域名必须同步白名单。
- 服务端 readiness 与 metadata 路由要求授权；`/health/live` 公开。日志不含凭据与工具响应。

## Phase 2：81 独立部署预案（本期不做）

最终目标是把天一（或一个独立监控 bot）部署在 81，就近消费监控。与本机部署的差异清单：

- **repo-scanner-mcp 依赖**：天一现在复用主栈的 `repo-scanner-mcp`（共享 `myopenclaw_myopenclaw-net`）；
  81 上没有主栈。三条路线：① 81 也起主栈（重）；② 天一在 81 上不带 repo-scanner（砍掉研发日报问答）；
  ③ 参考 `docker-compose.zhixun-bot.yml` 的**自包含栈**模式（独立网络 + 自带的全部依赖）。
- **网络形态**：天一现在挂外部网络 `myopenclaw_myopenclaw-net`；81 上需要等价网络或改自包含。
- **未决问题**：81 到飞书/模型 API 的出口；OpenClaw 镜像在 81 的获取渠道（当前走 daocloud 镜像源）；
  容器资源配额（81 是生产监控机，需与维护者商量）；81 上 token 的落盘位置（gitignored `.env` 的等价物）。

## 验收记录

Mac 主栈验收完成后在此追加带日期的记录（先例：`docs/zhixun-mcp-acceptance-20260928.md`）。

### 2026-10-30 切换到校园域名入口（对应 server-monitoring issue #5）

| 项 | 结果 |
|---|---|
| 客户端配置 | 已改为 `https://<MCP_DOMAIN>:8765/mcp`（公网证书） |
| 检查器 TLS 双模式 | ✅ 纯函数单测通过：系统信任库（公网证书）/ 仓库 CA（内网自签）双模式；421 提示指向服务端 `MCP_ALLOWED_HOSTS` |
| 渲染/守卫 | ✅ URL 门控、token 运行时展开、CA 追加语义不变 |
| 端点可达性（恢复后复测） | ✅ 维护者恢复入口后：`GET /health/live` → HTTP 200；TCP 8765 可连；证书为公网 **TrustAsia** 证书（有效期至 2026-12-29，剩 60 天） |
| 检查器全量 | ✅ **全部检查项通过（9/9）、exit 0**：token 形状 / health / TLS 公网证书 / 证书有效期 / 401 反证 / 正确 token / **12/12 工具** / `list_servers` 真实数据 / `get_server_status(weather)` 真实数据 |
| 总结获取器 | ✅ **20/20 调用全部成功**、exit 0（2 服务器 + Prometheus 7/7 + GFS + 13 项目） |
| 每日 08:30 日报 | ✅ 手动触发 `cron run` → 生成并**投递飞书成功**（`delivered=true`）；明天 08:30 起按计划自动执行 |

恢复前的排查记录（保留作证据）：10:00 前从校园有线客户端与容器内均无法建立 TCP
（`<MCP_DOMAIN>` → `<入口 IP>`，443/8765 超时；DNS 经阿里/Cloudflare DoH 一致）。
已在 issue #5 留言请维护者确认白名单与入口状态；维护者恢复后 10:14 复测全绿。

### 2026-10-14 Mac 主栈（天一 × server-monitoring，对应 issue #3 验收清单）

| 项 | 结果 |
|---|---|
| compose 两态 config | ✅ 关闭 = 空串 / 启用 = `/opt/tianyi-bot/ca/server-monitoring-ca.pem` |
| 只读冒烟检查器（宿主机） | ✅ 10/10、exit 0（TLS/401 双反证、12 工具、真实数据、证书剩 381 天） |
| **`mcp probe server-monitoring`** | ✅ **12 工具、diagnostics 空** —— streamable-http + 自定义 header 首例实测成立，**无需 shim 退路** |
| agent 能力（openclaw agent 回合） | ✅ 6 次调用 0 失败，按 AGENTS.md 纪律先总览后下钻；回答兑现 UNKNOWN≠健康、主/可视化分开、盘告警聚合 |
| 反向用例（未注册服务器名） | ✅ 如实答"不在已注册服务器里……UNKNOWN 不等于健康，不会用其他机器数据替代" |
| 回退演练 | ✅ 关闭：条目消失、probe 报 "No MCP server named"、repo-scanner 不受影响；恢复：12 工具、飞书 ws 重连 |
| 飞书真实提问（DM） | ✅ 2026-10-14 用户真实客户端提问 → 天一以 interactive 卡片回复：服务器状态表 + forecast-system 容器下钻 + GFS 三件套（主/可视化/数据库边界）+ zhixun-core CI #38（477/477）与生产 CD receipt 分开报，并主动指出 **"CI 最新构建 ≠ 当前已部署版本"**（SHA 不一致）——服务端验收表中最刁钻的口径题被正确兑现 |
| 部署期修复（意外收获） | 首启竞态：某 root 上下文进程抢建 root:700 的 `/home/node/.cache` → node gateway "SQLite read-only worker Unsafe fallback" crash-loop（重启复用容器可写层，永不恢复）。entrypoint 预建 node 属主 `.cache` + 守卫测试 `test_precreates_node_owned_cache_dir`；受控实验（快照镜像 A/B）验证 |
