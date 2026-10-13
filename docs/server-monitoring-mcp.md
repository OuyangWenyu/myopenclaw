# 服务器监控 MCP（server-monitoring）

天一 bot（研发助手）通过**团队 server-monitoring MCP** 回答服务器、容器与 GFS 数据处理相关的问题：
「weather 这台机器现在怎么样」「rainfall-oa-data 的 GFS 处理到哪一步了」「pm-minio-01 上哪些容器在重启」。

服务端是团队项目（[gitcode.com/dlut-water/server-monitoring](https://gitcode.com/dlut-water/server-monitoring)），
部署在 `10.48.0.81`，**只读**。本仓只做**客户端接入**：一个可选 MCP 注册、内网 CA、守卫与本文档。
不改服务端、不做 81 独立部署（见文末 Phase 2 预案）。

## 拓扑

```
天一容器（openclaw-tianyi）
  │  HTTPS + Bearer token + 内网自签 CA（NODE_EXTRA_CA_CERTS）
  ▼
server-monitoring MCP        weather / 10.48.0.81:8765（Streamable HTTP, stateless, 9 个只读工具）
  │
  ├─ Prometheus              pm-minio-01 / 10.48.0.86:9090
  └─ Docker Metadata Gateway pm-minio-01 / 10.48.0.86:8766（经本机 docker socket，只读容器元数据）

注册实体：服务器 pm-minio-01（10.48.0.86）、weather（10.48.0.81）；项目 rainfall-oa-data。
```

9 个工具：`list_servers` / `get_server_status` / `list_containers` / `get_container` /
`get_container_provenance` / `get_prometheus_status` / `get_project_runtime_status` /
`get_gfs_status` / `get_runtime_failure_evidence`（全部 READ_ONLY）。
天一的使用顺序与证据纪律写在 `openclaw-tianyi/workspace/AGENTS.md`（UNKNOWN ≠ 健康、
「评估完成」≠「已成功处理 cycle」、GFS 主 worker 与 `gfs-visualization` 分开报）。

## 凭据

| 项 | 从哪来（服务器 10.48.0.81） | 放哪 |
|---|---|---|
| Bearer token | `~qupengfei/.config/server-monitoring-mcp/auth_token`（找团队维护者） | **只**进 gitignored 的 `.env.tianyi-bot`（`TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN`）；不进仓库、不进渲染产物、不进聊天记录 |
| 内网 CA 证书（公钥材料） | 同目录 `ca.crt`（签发 MCP 服务器证书的私有 CA） | 随仓库提交：`docker/tianyi-bot/ca/server-monitoring-ca.pem` |

- token 是**全客户端共用的服务端凭据**，轮换要与维护者协调 —— 本仓无法自助轮换（与本机
  yuque-mcp「每客户端一份 key」的模式不同）。
- CA 是公钥材料，提交进私有团队仓没有泄密风险；**私钥绝不进仓库**（`tests/test_server_monitoring_mcp.py`
  里有断言）。服务端的 CA 私钥已在签发后移除，续期需要 CA rollover 规划（见「已知约束」）。
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
