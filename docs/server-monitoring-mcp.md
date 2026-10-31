# 服务器监控 MCP（server-monitoring）

天一 bot（研发助手）通过**团队 server-monitoring MCP** 回答服务器、容器与 GFS 数据处理相关的问题：
「weather 这台机器现在怎么样」「rainfall-oa-data 的 GFS 处理到哪一步了」「pm-minio-01 上哪些容器在重启」
「zhixun-core 最近一次构建怎么样」。爱码士（Hermes coder）自 2026-10-31 起也可按需查询同一服务
（见下文「消费分工」）。

服务端是团队项目（[gitcode.com/dlut-water/server-monitoring](https://gitcode.com/dlut-water/server-monitoring)），
部署在 `10.48.0.81`，**只读**。本仓只做**客户端接入**：一个可选 MCP 注册、内网 CA、守卫与本文档。
不改服务端、不做 81 独立部署（见文末 Phase 2 预案）。

## 拓扑

```
天一容器（openclaw-tianyi）
  │  HTTPS + Bearer token + 内网自签 CA（NODE_EXTRA_CA_CERTS）
  ▼
server-monitoring MCP        weather / 10.48.0.81:8765（Streamable HTTP, stateless, 12 个只读工具：9 运行 + 3 交付）
  │
  ├─ Prometheus              pm-minio-01 / 10.48.0.86:9090
  └─ Docker Metadata Gateway pm-minio-01 / 10.48.0.86:8766（经本机 docker socket，只读容器元数据）

注册实体：服务器 pm-minio-01（10.48.0.86）、weather（10.48.0.81）；
项目 rainfall-oa-data（仅运行监控）、zhixun-core 与 realtime-forecast（生产 + staging，含 Jenkins 交付映射）。
```

12 个工具（9 运行 + 3 交付，全部 READ_ONLY）：
`list_servers` / `get_server_status` / `list_containers` / `get_container` /
`get_container_provenance` / `get_prometheus_status` / `get_project_runtime_status` /
`get_gfs_status` / `get_runtime_failure_evidence`；
交付类（2026-10-13 服务端发布 Jenkins 证据链后纳入客户端策略）：`list_projects` /
`get_project_overview` / `get_project_build`（构建号、JUnit 测试与 revision 证据；先总览、后按构建号追问）。
天一的使用顺序与证据纪律写在 `openclaw-tianyi/workspace/AGENTS.md`（UNKNOWN ≠ 健康、
「评估完成」≠「已成功处理 cycle」、GFS 主 worker 与 `gfs-visualization` 分开报、
**staging ≠ 生产验收**、构建绿 ≠ 已部署/已验收）。
已知缺口（服务端现状，如实报告即可）：`weather` 无 Docker 详情后端 —— 查该服务器容器返回
`DOCKER_UNAVAILABLE`，这是能力缺失，不是服务故障。

## 凭据

| 项 | 从哪来（服务器 10.48.0.81） | 放哪 |
|---|---|---|
| Bearer token | 服务器维护者私有目录 `.config/server-monitoring-mcp/auth_token`（找团队维护者要） | **只**进 gitignored 的 `.env.tianyi-bot`（`TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN`）；不进仓库、不进渲染产物、不进聊天记录 |
| 内网 CA 证书（公钥材料） | 同目录 `ca.crt`（签发 MCP 服务器证书的私有 CA） | 随仓库提交：`docker/tianyi-bot/ca/server-monitoring-ca.pem` |

- token 是**全客户端共用的服务端凭据**，轮换要与维护者协调 —— 本仓无法自助轮换（与本机
  yuque-mcp「每客户端一份 key」的模式不同）。
- CA 是公钥材料，提交进**公开仓库**也没有泄密风险（公钥只能验证签名、不能制造签名）；
  **私钥绝不进仓库**（`tests/test_server_monitoring_mcp.py` 里有断言）。服务端的 CA 私钥
  已在签发后移除，续期需要 CA rollover 规划（见「已知约束」）。
- 核对仓库里的 CA 与线上一致：

```bash
openssl s_client -connect 10.48.0.81:8765 \
  -CAfile docker/tianyi-bot/ca/server-monitoring-ca.pem </dev/null 2>/dev/null \
  | grep -E "subject=|Verify return code"
# 期望：subject=CN = 10.48.0.81；Verify return code: 0 (ok)
```

## 启用

```bash
# 1. .env.tianyi-bot 加两行（或从注释块取消注释）
TIANYI_BOT_SERVER_MONITORING_MCP_URL=https://10.48.0.81:8765/mcp
TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN=<找维护者要>

# 2. 启动（预检会拦「URL 非空而 token 空」）
./scripts/start-tianyi-bot.sh

# 3. 冒烟：只读检查器（token 只回显长度，从不打印）
uv run scripts/check_server_monitoring_mcp.py --json

# 4. 网关侧 MCP 连接（最终判据）
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml \
  exec openclaw-tianyi node /app/openclaw.mjs mcp probe server-monitoring --json

# 5. 飞书群里 @天一 真实提问：
#    「weather 这台机器现在怎么样」「rainfall-oa-data 的 GFS 处理到哪了」
#    反向用例：问一个未注册的服务器名 —— 必须如实说不知道，不得答成健康
```

## 开关与回退

- **开关就是 URL**：清空 `TIANYI_BOT_SERVER_MONITORING_MCP_URL` → `./scripts/start-tianyi-bot.sh`（force-recreate）
  → 渲染时移除 `server-monitoring` 条目，能力完全关闭。关闭态**没有任何日志噪音**：
  `NODE_EXTRA_CA_CERTS` 为空串时 node 静默忽略（实测，有守卫钉着）。
- **三道 fail-loud**（都是刻意设计，静默降级比报错难查得多）：
  1. `render-config.mjs`：URL 非空而 token 空 → 抛错，容器起不来；
  2. `entrypoint.sh`：URL 非空而容器内 CA 缺失 → 中文报错 + exit 1
     （node 对缺失的 `NODE_EXTRA_CA_CERTS` 文件也是静默的，所以这层显式检查是唯一防线）；
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

**消费分工（2026-10-31 起）**：

- **天一（OpenClaw）**：每日服务器日报 + 飞书群问答；
- **爱码士（Hermes coder profile）**：按需查询（`hermes -p coder mcp test server-monitoring`），不承担每日推送；
- **Hermes 默认 profile / 道元 / finance 不接入**——server-monitoring 只给天一和爱码士。

爱码士的 MCP 条目由 `scripts/ensure_hermes_server_monitoring.py` 幂等写入 coder profile（profile 不继承默认
`mcp_servers`），配置里只写 `${SERVER_MONITORING_MCP_URL}` / `${SERVER_MONITORING_MCP_TOKEN}` 占位符；
真实值只放 `.env`（gitignored），由 compose 注入 `hermes-coder`。URL 为空 = 自动移除条目。
改 `.env` 后跑 `./scripts/start.sh`：它在 `up -d` 前重写受管条目，并在容器未被重建时重启
`hermes-coder`（MCP 配置不热加载）。

```bash
# 验证（容器内）
docker compose exec -T hermes-coder /opt/hermes/.venv/bin/hermes -p coder mcp list
docker compose exec -T hermes-coder /opt/hermes/.venv/bin/hermes -p coder mcp test server-monitoring
```

> 服务健康「跃迁通知」（健康→不健康 / 不健康→健康各一次、持续不健康不重复）已作为
> 服务端能力提案给 server-monitoring（[issue #7](https://gitcode.com/dlut-water/server-monitoring/issues/7)）：
> 服务端产 `health_events` + `get_health_events` 游标，客户端只做游标消费与飞书投递。
> 在服务端接口落地前，爱码士只做按需查询，不在客户端重复实现状态机。

**每日推送已声明式内置**（2026-10-14）：entrypoint 在网关上电后**幂等声明**一条
automation（`declaration-key = tianyi-monitoring-daily`，同 key 重加 = 原地更新、
ID 不变——改本仓后重启自动更新）。门控与排期：
- `TIANYI_BOT_SERVER_MONITORING_MCP_URL` 非空 = 声明开启（清空即自动消失——**OFF 态
  会在启动时按 declaration-key 主动回收既有 automation**；框架自身不会回收 service
  声明的 job，2026-10-14 复核实测，经 `cron-job-id.mjs` 提取 id 后 `cron rm`）；
- 推给 `TIANYI_BOT_OWNER_OPEN_ID`（接收人 open_id）；缺它只警告不拦启动；
  ⚠️ **open_id 是应用维度的**：必须填天一应用视角下该用户的 id。填错应用视角
  （如 lark-cli/爱码士视角）的症状是投递 400 `99992361 "open_id cross app"`
  （2026-10-14 实测踩过；取法见 `.env.tianyi-bot.example` 注释）；
- 排期默认 `30 8 * * *`（Asia/Shanghai），`TIANYI_BOT_MONITORING_DIGEST_CRON` 可改，
  设 `off` 显式关闭；
- ⚠️ 本机（Mac）推送时需 UniVPN 在线（断线时会如实报"取数失败"——这也是监控信号）；
  部署到内网服务器后无此约束。

**拉起即有**：本地 `./scripts/start-tianyi-bot.sh` 或将来服务器上直接拉起，automation
自动就位。本脚本（`server_monitoring_summary.py`）的定位是**验收证据 / 连着 UniVPN
的电脑上的随手查 / 部署参考实现**——与推送自动化是两条独立路径，互不依赖。

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
| TLS 校验失败 | CA 不对 / 容器内 `NODE_EXTRA_CA_CERTS` 未生效 | 核对仓库 CA 与 `compose config` 里 `NODE_EXTRA_CA_CERTS` 的值 |
| ENOTFOUND / 连接超时 | 容器到 `10.48.0.81` 网络不通 | 检查网络与出口 |
| redirect | URL 少了 `/mcp` | URL 必须 `https://10.48.0.81:8765/mcp` |
| 421 Misdirected | Host 白名单拒绝 | URL 必须是**裸 IP:port** —— 服务端 `MCP_ALLOWED_HOSTS` 钉死，不能走 DNS 别名/反代 |

## 已知约束

- **streamable-http + 自定义 header 是本仓第一次用**（先例：yuque-mcp 是 sse+header、
  repo-scanner 是 streamable-http 无 header）。最终判据是运行时 `mcp probe`。若 probe 报 401
  而检查器用同一 token 过了 `/health/ready`，说明 OpenClaw 未在 streamable-http 上发送自定义
  header —— 退路是加一个注入 `Authorization` 的 shim 容器（**本期未建**，出现再说）。
- token 全客户端共用：轮换 = 所有客户端同改。
- 客户端策略当前 = **12 工具**（9 运行 + 3 交付，2026-10-13 服务端正式发布交付层后纳入）。
  服务端统一汇报计划中的 `get_progress_report` / `get_report_item` / `get_evidence`
  （按时间范围汇总、事项统计与持久化追问）**尚未上线**——上线并以真实数据验收后再评估
  是否纳入，本仓不预接入。
- 证书有效期：MCP 叶子证书到 **2027-10-30**；CA 私钥签发后已移除 ⇒ 续期 = CA rollover +
  换服务端证书 + 同步本仓 `ca/server-monitoring-ca.pem`（检查器会报剩余天数）。
- TLS 信任是**进程级**的：`NODE_EXTRA_CA_CERTS` 对整个 gateway 的出站 TLS 生效。仅在启用时
  生效（URL 门控），且信任对象是团队自己的内网 CA —— 这是接受的取舍。
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
