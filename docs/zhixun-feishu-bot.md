# zhixun 独立飞书机器人

该部署只运行一个 OpenClaw 飞书机器人和 zhixun 的统一水文 MCP。它不连接
myopenclaw 的 Hermes、Claude Code、TDAI Memory、aisecretary、repo-scanner
或现有 OpenClaw 实例。

## 服务边界

```text
飞书指定群
    │ WebSocket
    ▼
openclaw-zhixun
    │ SSE: http://zhixun-water-mcp:18201/sse
    ▼
zhixun-water-mcp
    │ HTTPS
    ▼
Waterism API
```

两个容器只加入独立的 `zhixun-bot-net`。OpenClaw 数据存放在
`~/.openclaw-zhixun`，不能与现有 `~/.openclaw` 共用。

## 服务器准备

服务器上将两个仓库放在同一个父目录：

```bash
mkdir -p /srv/agents
cd /srv/agents
git clone git@github.com:CylenLC/myopenclaw.git
git clone <zhixun-agent-repository-url> zhixun-agent
cd myopenclaw
git switch codex/zhixun-feishu-bot
```

要求：

- Docker Engine 和 Docker Compose plugin
- OpenClaw 镜像版本不低于 `2026.5.29`
- 服务器能够访问飞书、模型 API、Waterism API 和容器镜像/插件仓库
- 一个独立的飞书自建应用
- 飞书目标群的 `chat_id`，格式为 `oc_...`

飞书应用需要启用机器人能力，并通过 WebSocket 订阅
`im.message.receive_v1`。将应用发布后，把机器人加入目标群。

## 配置

```bash
cp .env.zhixun-bot.example .env.zhixun-bot
chmod 600 .env.zhixun-bot
vi .env.zhixun-bot
```

必须填写：

```dotenv
ZHIXUN_AGENT_PATH=../zhixun-agent
ZHIXUN_BOT_FEISHU_APP_ID=cli_xxx
ZHIXUN_BOT_FEISHU_APP_SECRET=xxx
ZHIXUN_BOT_FEISHU_GROUP_ID=oc_xxx
ZHIXUN_BOT_MODEL_API_KEY=xxx
```

生产服务器建议设置绝对数据路径：

```dotenv
ZHIXUN_BOT_DATA_DIR=/srv/myopenclaw-data/openclaw-zhixun
```

默认只开放查询类工具。需要允许会商、调度和条目写操作时，显式设置：

```dotenv
ZHIXUN_BOT_ENABLE_WRITE_TOOLS=true
```

即使开放写工具，工作区规则仍要求机器人在执行创建、更新、删除或调度操作前
进行确认。

## 启动

首次部署或 zhixun 代码更新后：

```bash
./scripts/start-zhixun-bot.sh --build
```

普通重启：

```bash
./scripts/start-zhixun-bot.sh
```

脚本只操作 `docker-compose.zhixun-bot.yml` 中的两个独立服务，不会启动或
重建主 `docker-compose.yml` 中的任何服务。OpenClaw 首次启动时会把官方
`@openclaw/feishu` 插件安装到独立数据目录。

## 验证

```bash
docker compose \
  --env-file .env.zhixun-bot \
  -f docker-compose.zhixun-bot.yml \
  ps
```

验证 MCP 工具发现：

```bash
docker compose \
  --env-file .env.zhixun-bot \
  -f docker-compose.zhixun-bot.yml \
  exec openclaw-zhixun \
  node /app/openclaw.mjs mcp probe water_unified --json
```

查看日志：

```bash
docker compose \
  --env-file .env.zhixun-bot \
  -f docker-compose.zhixun-bot.yml \
  logs -f openclaw-zhixun zhixun-water-mcp
```

在目标群中 `@机器人` 并发送：

```text
列出当前可查询的流域和站点类型。
```

机器人不会响应私聊，也不会响应未配置的群。

## 停止与更新

停止：

```bash
docker compose \
  --env-file .env.zhixun-bot \
  -f docker-compose.zhixun-bot.yml \
  down
```

更新：

```bash
git -C ../zhixun-agent pull --ff-only
git pull --ff-only
./scripts/start-zhixun-bot.sh --build
```

## 安全说明

- `.env.zhixun-bot` 已被 `.gitignore` 排除，不要提交真实凭据。
- OpenClaw 容器不挂载 Docker socket、宿主机代码目录或现有 Agent 数据。
- MCP 端口和 OpenClaw Gateway 端口均不发布到宿主机。
- 飞书仅允许配置的一个群，并要求 `@机器人`。
- OpenClaw 工具策略只允许 `bundle-mcp`，飞书文档、云盘、知识库、群管理等
  原生工具全部关闭。
- 如果服务器需要跨主机访问 MCP，应增加 TLS 和认证；当前配置只支持同一
  Docker 网络内访问。
