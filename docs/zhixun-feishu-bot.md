# zhixun 独立飞书机器人

该部署只运行一个 OpenClaw 飞书机器人，默认直连**团队服务器上的远程
zhixun-agent 水文 MCP**（Bearer 认证）。它不连接 myopenclaw 的 Hermes、
Claude Code、TDAI Memory、aisecretary、repo-scanner 或现有 OpenClaw 实例。
本地 `zhixun-water-mcp` 容器仅作为 `--with-local-mcp` 调试回退，默认不启动。

## 定位（团队侧）

知汛与天一同类：**团队侧公共 bot**（接入层）。它背后的 **zhixun-agent 统一水文 MCP
就是团队服务本身**（能力层，团队仓库 `gitcode.com/dlut-water/zhixun-agent`）——
与语雀 MCP / git-contribution-stats / 服务监控并列，目标部署位置同为团队服务器。
本文**以本机栈为主、并含「服务器准备」章节**；服务器侧专属的分步部署清单（网络/凭据/验收）
见 [部署到服务器（团队侧）时要看什么](server-deploy.md)，知汛部分待补。

## 服务边界

```text
飞书任意群或私聊
    │ WebSocket
    ▼
openclaw-zhixun
    │ SSE/streamable-http + Bearer
    ▼
远程 zhixun-agent 水文 MCP（团队服务器，新版 zhixun-core）
    │ HTTPS
    ▼
Waterism API
```

默认部署只启动 `openclaw-zhixun`，加入独立的 `zhixun-bot-net`。OpenClaw
数据存放在 `~/.openclaw-zhixun`，不能与现有 `~/.openclaw` 共用。

本地调试栈仍保留 `zhixun-water-mcp` 容器（同一网络内的
`http://zhixun-water-mcp:18201/sse`），但只有 `--with-local-mcp` 才会启动；
它构建的兼容层（站名索引 / `related_page.url` / hydromodel 路由）在远程
服务上是否生效，切换后必须用下面的 `mcp probe` 和真实查询验证。

## 服务器准备

服务器上只需要本仓库：

```bash
mkdir -p /srv/agents
cd /srv/agents
git clone git@github.com:OuyangWenyu/myopenclaw.git
cd myopenclaw
```

`../zhixun-agent` 仅在 `--with-local-mcp` 调试模式下需要（构建本地
`zhixun-water-mcp` 镜像）；默认部署连远程 MCP，不需要克隆它，也不需要
Docker Hub/PyPI 构建依赖。

要求：

- Docker Engine（启用 BuildKit）和 Docker Compose plugin 2.17+
- OpenClaw 镜像版本**必须与主栈同版本**（当前 `2026.9.1`）—— 2.0 的配置 schema 有 breaking change，混版本会让同一份模板在不同栈上有不同解释。三栈一致性由 `tests/test-openclaw-pins.sh` 守卫
- 服务器能够访问飞书、模型 API、远程 water MCP、Waterism API 和容器镜像/插件仓库
- 一个独立的飞书自建应用；远程水 MCP 的 Bearer key

仅本地调试模式需要替换构建源：

```dotenv
ZHIXUN_BOT_PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
ZHIXUN_BOT_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ZHIXUN_BOT_OPENCLAW_IMAGE=docker.m.daocloud.io/openclaw/openclaw:2026.9.1
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

以下变量只影响本地 `--with-local-mcp` 容器；默认部署的站名索引/相关页由
远程服务端负责：

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
ZHIXUN_BOT_FEISHU_APP_ID=cli_xxx
ZHIXUN_BOT_FEISHU_APP_SECRET=xxx
ZHIXUN_BOT_MODEL_API_KEY=xxx
# 远程 water MCP（团队服务器新版 zhixun-agent）
# 当前团队远程端点：http://10.48.0.81:18202/mcp（streamable-http）
ZHIXUN_BOT_WATER_MCP_URL=https://water.example.com/mcp
ZHIXUN_BOT_WATER_MCP_TOKEN=xxx
ZHIXUN_BOT_WATER_MCP_TRANSPORT=streamable-http
```

`ZHIXUN_BOT_WATER_MCP_TRANSPORT` 取值 `streamable-http`（当前远程）或 `sse`
（本地默认回退），按远程端点实际协议填写。`ZHIXUN_BOT_WATER_MCP_TOKEN` 只写入
`.env.zhixun-bot`，由 OpenClaw 在运行时展开到 MCP `headers.authorization`，
渲染产物中不会出现真实值。

`ZHIXUN_AGENT_PATH=../zhixun-agent` 只在 `--with-local-mcp` 调试时使用。

### 模型：DeepSeek V4.1 Flash

将 DeepSeek 平台 API Key 填入 `.env.zhixun-bot` 的
`ZHIXUN_BOT_MODEL_API_KEY`，并配置模型 ID 和 API 地址：

```dotenv
ZHIXUN_BOT_MODEL_API_KEY=你的 DeepSeek API Key
ZHIXUN_BOT_MODEL_ID=deepseek-flash
ZHIXUN_BOT_MODEL_BASE_URL=https://api.deepseek.com
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

默认部署（直连远程 water MCP）：

```bash
./scripts/start-zhixun-bot.sh
```

本地 MCP 调试（构建 `../zhixun-agent` 并启动本机容器）：

```bash
# 先把 ZHIXUN_BOT_WATER_MCP_URL 改成 http://zhixun-water-mcp:18201/sse
./scripts/start-zhixun-bot.sh --with-local-mcp --build
```

脚本只操作 `docker-compose.zhixun-bot.yml` 中的服务，不会启动或重建主
`docker-compose.yml` 中的任何服务。默认只启动 `openclaw-zhixun`；OpenClaw
首次启动时会把官方 `@openclaw/feishu` 插件安装到独立数据目录。

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
  logs -f openclaw-zhixun
```

本地调试栈的 MCP 日志：

```bash
docker compose \
  --env-file .env.zhixun-bot \
  --profile local-water-mcp \
  -f docker-compose.zhixun-bot.yml \
  logs -f zhixun-water-mcp
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

更新（默认部署，远程 MCP 由团队服务器维护）：

```bash
git pull --ff-only
./scripts/start-zhixun-bot.sh
```

本地调试栈（需要更新 `../zhixun-agent` 并重建）：

```bash
git -C ../zhixun-agent pull --ff-only
git pull --ff-only
./scripts/start-zhixun-bot.sh --with-local-mcp --build
```

## 安全说明

- `.env.zhixun-bot` 已被 `.gitignore` 排除，不要提交真实凭据。
- OpenClaw 容器不挂载 Docker socket、宿主机代码目录或现有 Agent 数据。
- MCP 端口和 OpenClaw Gateway 端口均不发布到宿主机。
- 飞书机器人可被加入任意群，并接受所有私聊；群内仍要求 `@机器人`。这会让
  所有可联系或拉入机器人的飞书用户调用 zhixun MCP，请仅向信任的组织成员发布。
- OpenClaw 工具策略只允许 `bundle-mcp`，飞书文档、云盘、知识库、群管理等
  原生工具全部关闭。
- 远程 water MCP 使用 `Authorization: Bearer`。token 只存放在 gitignored 的
  `.env.zhixun-bot`，由 OpenClaw 运行时展开；渲染出的 `openclaw.json` 里只有
  `${ZHIXUN_BOT_WATER_MCP_TOKEN}` 字面量。优先使用 HTTPS；明文 HTTP 只限于
  可信内网。
- 本机 `zhixun-water-mcp` 调试容器没有认证（与远程服务不同），只在
  `--with-local-mcp` 时启用，不要把它暴露到宿主机或跨主机网络。
