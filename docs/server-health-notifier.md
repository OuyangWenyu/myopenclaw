# 服务健康事件 → 飞书通知

`server-health-notifier` 是 myopenclaw 内的独立 Python 消费者，部署为单独容器。它调用 server-monitoring 已确认的健康事件，不另行采集主机指标、不重新判断健康、不经过 LLM。已在 81 部署并配置飞书应用，完成测试卡片 API 接收验收，已启用正式事件消费。见[部署记录](deployment-20261102-health-notifier.md)。

## 职责与流程

```text
server-monitoring 内部 watcher → get_health_events
    → 健康消费者独立游标 → 范围过滤 → 固定模板卡片
    → SQLite outbox → 飞书应用或未签名 webhook
```

监控端默认每轮完成后等待 300 秒，连续两次同一状态才确认变化；消费者默认每轮完成后等待 60 秒。每页 100 条，每轮最多 5 页（可配置），追平积压无需每页再等一分钟。通知延迟还包含监控采样、防抖、网络与发送时间，不是故障发生后 60 秒内必达。

首次消费先校验响应和 watcher，只建立最新游标、不补发历史。HEALTHY 与非 HEALTHY 之间的事件分别显示异常、恢复；UNKNOWN 明确表示证据不足或陈旧，不等于确认服务已宕机。持续非健康没有重复事件；消费者不再做一遍健康状态机。

## 本地开发和上线边界

- 默认不开启：服务有 `server-health-notifier` Compose profile，普通 `scripts/start.sh` 不会新增这个消费者。
- 同 issue-notifier 共享 `notifier_delivery.py` 的飞书发送实现，两个消费者的配置、游标、outbox 和死信分别存储。
- 当前已完成本地回归、离线镜像构建、真实 MCP 只读联调、观察及正式消费心跳，以及指定群两张测试卡片的 API 接收验收。本页命令不会由文档自动执行。
- 飞书应用/目标群/MCP key 由部署环境提供，不将成员的个人 key 或真实群信息提交到仓库。

## 配置

`.env.example` 有非敏感模板；只需编辑部署环境的 `.env`，不改应用源码：

| 配置项 | 含义 |
|---|---|
| HEALTH_NOTIFIER_MCP_URL | 必填；校园域名入口 `https://<MCP_DOMAIN>:8765/mcp`，可信私网也可用 `http://<MCP_HOST>:8765/mcp` |
| HEALTH_NOTIFIER_MCP_TOKEN | 已在监控服务注册的 Bearer key；不要把 `Bearer ` 前缀写入值 |
| HEALTH_NOTIFIER_POLL_SECONDS | 默认 60，范围 15–3600 |
| HEALTH_NOTIFIER_MAX_PAGES | 每轮最多页数，默认 5，范围 1–20 |
| HEALTH_NOTIFIER_FEISHU_MODE | 默认 app；可选 webhook |
| HEALTH_NOTIFIER_FEISHU_APP_ID / APP_SECRET | app 模式的应用凭据，可显式配置为已有爱码士应用；不会自动读取其它 bot 的凭据 |
| HEALTH_NOTIFIER_FEISHU_CHAT_ID | app 模式目标群，应用需在群内并具备发送权限 |
| HEALTH_NOTIFIER_FEISHU_WEBHOOK | webhook 模式的飞书 HTTPS hook，当前不支持签名 hook |

域名 HTTPS 使用系统信任链，不需要旧私有 CA。私网 URL 仅适合已能访问该网络的部署主机。客户端不读取 URL 内嵌凭据、不跟随重定向，不使用环境代理转发 MCP token。

可选过滤文件放在数据目录的 `config.toml`，模板为 `config/server-health-notifier.example.toml`：

```toml
entity_patterns = ["server:*", "service:*", "gfs:*"]
include_auxiliary = true
```

也可以只选特定实体，例如 `service:forecast-system:*`。配置启动时读取，修改后重启消费者。过滤事件仍会推进游标，后续放宽范围不会补发过去已跳过的事件。

## 分阶段验收

先准备独立数据目录及宿主 UID/GID（以下为 Linux/macOS shell）：

```bash
mkdir -p "$HOME/.myagentdata/server-health-notifier"
chmod 700 "$HOME/.myagentdata/server-health-notifier"
cp config/server-health-notifier.example.toml "$HOME/.myagentdata/server-health-notifier/config.toml"
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"
```

首先只读验证，不需要飞书凭据、不写游标、不发送卡片：

```bash
docker compose --profile server-health-notifier run --rm --build --no-deps   server-health-notifier python /app/health_notifier.py --dry-run
```

无既有游标时 dry-run 只验证并报告静默基线，不打印历史卡片，也不创建状态库。首次实际 `--once` 会写入最新游标，随后 dry-run 可预览新事件。若存在积压，dry-run 只预览一页，不改 daemon 的游标和 status。

确认允许真实消息后，配置测试群并执行一次；此命令会发送新事件或已有 outbox：

```bash
docker compose --profile server-health-notifier run --rm --no-deps   server-health-notifier python /app/health_notifier.py --once
```

实际卡片验收后再启动 daemon：

```bash
docker compose --profile server-health-notifier up -d --build --no-deps server-health-notifier
docker compose logs --tail 50 server-health-notifier
```

手动写入命令（`--once`、`--reset-cursor`、`--requeue-dead`）应在 daemon 停止后执行。同目录单 writer 锁会拒绝并发写入；dry-run 可以并行只读。

## 可靠性与异常

- 状态文件位于独立数据目录的 `state.sqlite`，新文件 0600，rollback journal 纳入既有 SQLite 一致性备份流程；不要直接 rsync 正在写入的裸库。
- 整页先校验，卡片入队与游标更新在同一事务中提交。发送成功后记账并删除 outbox；读取上游失败时仍重试已有待发卡片。
- 请求必须包含 `schema_version`、`latest_event_id`、`next_cursor` 和有效 watcher；缺字段、未知 schema、非健康边界事件、乱序、异常游标均报错，不静默从零灌历史。
- `cursor_ahead`、`cursor_expired` 或发现本地游标超前时停止消费新事件，报告历史缺口；已入队卡片继续投递。操作人员核实后，用 `--reset-cursor` 跳到当前最新事件、增加本地 generation；旧 outbox 不清空，事件 ID 重用不会撞掉新事件。此操作跳过缺口，不能恢复已丢失历史。
- 状态库绑定 MCP endpoint 和投递目标；改 URL、群、应用或 webhook 时使用新的独立数据目录，防止旧卡片被意外改投。轮换同一目标的认证 key 不要求重建游标。
- 已建立基线后，即使 watcher 为 PARTIAL/ERROR，也可消费已持久化的合法事件，但 `source_healthy=false`，不会把空事件当成系统正常；首次基线要求 CURRENT 且 fresh。
- 每轮发送预算为 20 秒软预算；单次网络调用有独立超时。失败仅记录类别，不落原始异常、token 或完整响应；持续失败满 24 小时进入 dead，7 天后清理。
- app 模式为同一 outbox 卡片使用稳定 UUID，按飞书接口的约定减少一小时窗口内的重复；超窗重试及 webhook 模式仍可能重复，不承诺端到端 exactly-once。参考[飞书发送消息接口](https://open.feishu.cn/document/server-docs/im-v1/message/create)和[官方 SDK UUID 定义](https://larksuite.github.io/oapi-sdk-java/com/lark/oapi/service/im/v1/model/CreateMessageReqBody.Builder.html)。

## 状态、恢复与测试

`status.json` 包括 `source_healthy`、安全 watcher 摘要、最近成功轮询、游标、最新 ID、积压、待发数量及年龄、死信数和发送失败数。Docker healthcheck 同时检查轮询新鲜度、上游 watcher、发送失败和过久 outbox 积压。

AgentOps 在出现状态文件或设置 `AGENTOPS_HEALTH_NOTIFIER_ENABLED=1` 后检查该消费者；未启用且无状态文件时不报缺失。启用前需要捕获“启动失败/从未有心跳”时，应在运行 AgentOps 的环境显式设为 1。不要只在 notifier 容器内设置。死信会单独进入运维清单。

```bash
# 先停止 daemon，再使用同一数据目录执行恢复命令
docker compose stop server-health-notifier
docker compose --profile server-health-notifier run --rm --no-deps   server-health-notifier python /app/health_notifier.py --reset-cursor
# 或 --requeue-dead；修复投递后将死信重新入队
```

本地无网络回归：

```bash
PYTHONUTF8=1 uv run --with pytest --with pyyaml pytest   tests/test_server_health_notifier.py tests/test_server_health_notifier_wiring.py   tests/test_issue_notifier.py tests/test_issue_notifier_wiring.py tests/test_collect_agentops.py -q
```

现有 issue/PR 通知继续按人员映射工作；健康通知不依赖人员映射。收件人、群权限和实际消息送达需单独验收，不能用单元测试通过替代。

## 2026-11-02 本地验证记录

- Linux（WSL）：上述通知核心、部署接线、既有 issue-notifier、AgentOps 和秘密卫生测试共 152 项通过，包含 POSIX 文件锁和符号链接场景。
- Windows：151 项通过；既有 POSIX 符号链接测试不适用，已在 Linux 覆盖。
- Ruff 检查通过；Compose 使用公开 `.env.example` 并关闭服务 env_file 解析后渲染通过，未读取真实 `.env`。
- MCP 适配器测试使用 mock 会话，飞书发送使用 fake/mock，未安装或升级依赖，未向真实群发送消息。
- 初次本地验证时尚未构建镜像或联调；随后已完成 81 只读观察部署、飞书测试卡片 API 接收验收及正式消费切换，具体身份和边界见部署记录。对应源码与文档随本 PR 交付。

## 尚未配置机器人时的观察模式

容器命令增加 `--observe-only` 可持续进行 MCP 只读轮询，只写状态心跳，`mode=observe-only`、`delivery_enabled=false`。该模式不创建/推进消费游标、不生成 outbox、不打印事件卡片，也不会发送消息；不要求飞书凭据。配置机器人后移除该参数再进行投递验收。观察模式的健康仅表示 MCP 链路可用，不代表飞书投递已就绪。

## 离线镜像构建

81 无法从构建网络访问 PyPI，本次使用 `Dockerfile.offline`。在可联网 Linux 环境下载固定 `mcp==1.28.1` 及其 Python 3.12/Linux wheel，验证文件哈希后放入构建上下文的 `docker/wheels/`，以 `docker/` 为 context、`server-health-notifier/Dockerfile.offline` 为 Dockerfile，在 `--network=none` 下构建。不要把 Windows 专用 wheel、真实配置或 key 放进上下文。wheel 清单和依赖版本随发布材料留存，不改写宿主 Python 环境。
