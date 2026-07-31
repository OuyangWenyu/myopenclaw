# zhixun 独立飞书机器人

该部署只运行一个 OpenClaw 飞书机器人和 zhixun 的统一水文 MCP。它不连接
myopenclaw 的 Hermes、Claude Code、TDAI Memory、aisecretary、repo-scanner
或现有 OpenClaw 实例。

## 服务边界

```text
飞书任意群或私聊
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
git clone git@gitcode.com:dlut-water/zhixun-agent.git
cd myopenclaw
git switch feat/zhixun-feishu-bot
```

要求：

- Docker Engine（启用 BuildKit）和 Docker Compose plugin 2.17+
- OpenClaw 镜像版本不低于 `2026.5.29`
- 服务器能够访问飞书、模型 API、Waterism API 和容器镜像/插件仓库
- 一个独立的飞书自建应用

默认构建会访问 Docker Hub 的 `python:3.12-slim` 和 PyPI。网络受限时，在
`.env.zhixun-bot` 中替换：

```dotenv
ZHIXUN_BOT_PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
ZHIXUN_BOT_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ZHIXUN_BOT_OPENCLAW_IMAGE=docker.m.daocloud.io/openclaw/openclaw:2026.7.1
```

zhixun-agent 使用纯 MCP 目录结构，必须包含：

```text
mcp_servers/water/mcp_server_unified.py
```

MCP 镜像由 myopenclaw 中的 `docker/zhixun-bot/Dockerfile.mcp` 构建，通过
Compose 的附加构建上下文读取 zhixun-agent 源码，不依赖 zhixun-agent
仓库中的 `docker/` 目录或 Dockerfile。

镜像启动时会加载 myopenclaw 提供的 v2 兼容层，将
`/api/v2/reservoirs` 返回的 `_embedded.reservoirs[].data` 转成 MCP
需要的扁平记录，并按照接口限制使用每页 100 条。这样可以直接按水库名称
查询，不需要修改 zhixun-core 或 zhixun-agent。

新版 Core 没有河道站和雨量站的全局列表搜索接口。兼容层会在首次按这两类
站点名称查询时，遍历现有 `/api/v2/basins/{basin_id}/stations` 资源，建立
并持久化名称索引。名称唯一时自动解析成站码；同名站点不会静默选取，而是
返回候选站码与所属流域供用户确认。默认缓存 24 小时：

```dotenv
ZHIXUN_CORE_BASE_URL=https://ws.waterism.tech:8090/api/v2
ZHIXUN_BOT_MCP_DATA_DIR=/srv/myopenclaw-data/zhixun-water-mcp
ZHIXUN_MCP_STATION_INDEX_TTL_SECONDS=86400
ZHIXUN_MCP_STATION_INDEX_WORKERS=12
```

兼容层还会把 URL 工具与水文数据查询组合起来。查询水库、河道站、雨量站或流域
时，相应数据工具会直接返回经过 MCP URL 工具验证的 `related_page.url`，并要求
模型把它作为回复的最后一行；用户不需要在问题中另外要求“给出链接”。泛流域
概况或流域测站查询会返回 `related_pages`，按“雨情监测、风险研判”的顺序附两个
链接；有明确业务意图的流域查询只附最匹配的一个链接。

当前自动覆盖：

- 唯一命中的水库名称搜索、水库档案、库容曲线和 GeoJSON：水库详情页；
- 水库告警及时序/最新数据：对应告警页或监测页；
- 河道站详情、告警及时序/最新数据：河道监测页；
- 河道历史对比：河道历史对比页，并保留年份、日期范围和指标；
- 雨量站详情、单站旬月统计、时序和最新数据：雨量站分析页，并尽量保留
  查询时段。
- 流域概况或测站清单：雨情监测页和风险研判页；流域雨情、统计、预报、等雨量线
  或风险研判：分别对应监测、统计、预报、等雨量线或风险研判页。

`AGENTS.md` 和 `SOUL.md` 是这个专用机器人的受管策略文件。容器每次启动都会
用镜像中的版本同步到独立工作区，因此升级后不需要手工复制规则文件。

飞书应用需要启用机器人能力，并通过 WebSocket 订阅
`im.message.receive_v1`。将应用发布后，可将机器人加入任意目标群。

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
ZHIXUN_BOT_MODEL_API_KEY=xxx
```

### 模型：DeepSeek V4 Pro

机器人默认使用 DeepSeek V4 Pro。将 DeepSeek 平台 API Key 填入
`ZHIXUN_BOT_MODEL_API_KEY`，并保持以下配置；不要使用智谱或其他厂商的 API Key：

```dotenv
ZHIXUN_BOT_MODEL_ID=deepseek-v4-pro
ZHIXUN_BOT_MODEL_BASE_URL=https://api.deepseek.com
```

运行时模型提供商 ID 固定为 `deepseek`，因此飞书流式卡片中的 Provider 会显示
`deepseek`，不会再显示历史遗留的 `zhipu`。

切换模型后无需重建 MCP 镜像，重新生成机器人配置即可：

```bash
./scripts/start-zhixun-bot.sh
```

可通过下列命令确认运行时配置已经生效（不会输出 API Key）：

```bash
docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml \
  exec openclaw-zhixun \
  sh -c 'grep -E "primary|baseUrl|deepseek-v4-pro" /home/node/.openclaw/openclaw.json'
```

默认使用普通文本回复，不展示流式卡片底部的 `Agent`、`Model`、`Provider`
运行元信息。如需逐字流式卡片，可设置：

```dotenv
ZHIXUN_BOT_FEISHU_STREAMING=true
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

水文模型有两个容易混淆的入口：

- `get_basin_hydromodel`：只读查询流域已有模型，回答“某流域有哪些模型”；
- `hydromodel_list`：简报写入流程的辅助工具，为后续 `item_add` 获取
  `model_param_id`。

只读模式会隐藏 `hydromodel_list`。开启写工具后它才可见，但机器人规则仍要求
普通模型查询使用 `get_basin_hydromodel`。MCP 兼容层还修复了新版接口
`_embedded.hydromodels` 的解析，并在水库详情没有单独 `basin_id` 时使用水库站码
作为流域编码。

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

在任意已加入机器人的群中 `@机器人` 并发送：

```text
列出当前可查询的流域和站点类型。
```

机器人会响应任意私聊，也会响应任意已加入机器人的群；为避免群内每条消息都
触发，群聊仍必须 `@机器人`。

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
- 飞书机器人可被加入任意群，并接受所有私聊；群内仍要求 `@机器人`。这会让
  所有可联系或拉入机器人的飞书用户调用 zhixun MCP，请仅向信任的组织成员发布。
- OpenClaw 工具策略只允许 `bundle-mcp`，飞书文档、云盘、知识库、群管理等
  原生工具全部关闭。
- 如果服务器需要跨主机访问 MCP，应增加 TLS 和认证；当前配置只支持同一
  Docker 网络内访问。
