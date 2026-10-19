# Hermes 消息平台配置

Hermes 的四个 profile（default、coder、daoyuan、finance）各自可以接入不同的消息平台。平台凭据通过环境变量传入容器，Hermes 启动时自动连接。

## 平台总览

| 平台 | 使用的 profile | 配置方式 | 说明 |
|------|--------------|---------|------|
| 飞书 | 全部（default / coder / daoyuan / finance） | 各自飞书应用凭据 | 主消息平台，走 WebSocket 长连接 |
| Discord | coder（爱码士） | 项目 `.env` + `docker-compose.yml` | 独立 Discord Bot，仅限个人使用 |

**飞书应用隔离**：四个 profile 各用**独立的**飞书应用，互不影响：
- 爱玛士（default）：`FEISHU_APP_ID` — 主飞书 bot
- 爱码士（coder）：`CODER_FEISHU_APP_ID` — 独立飞书 bot（2026-10-13 起；该应用同时是 lark-cli 第二 profile 的「爱码士应用」= `LARK_CLI_IDM_*`）
- 道元（daoyuan）：`DAOYUAN_FEISHU_APP_ID` — 文献学者专用飞书 bot，群内开放访问
- finance：`FINANCE_FEISHU_APP_ID` — 财经助手专用飞书 bot

> **历史（2026-10-12 事故 → 2026-10-13 修复）**：coder 曾与 default **共享**同一个飞书应用 —— 两个容器同时用同一 app 建长连接，DM 被分到其中一条；落到 coder 容器（受限 profile，邮箱是拒绝桩）的轮次读不了/发不出邮件（症状：时好时坏）。10-12 摘除 coder 全部飞书注入止血（副作用：爱码士从飞书消失）；10-13 改为接入它自己的独立应用（事后查明：coder profile 的 `.env` 早在 2026-05 就备好了爱码士应用凭据，只是被 compose 注入的默认 app 遮蔽 ——「共享」是意外的接线事故，不是设计）。**不要恢复共享** —— 一个 app 同一时刻只有一条连接能接到消息，双容器必然互抢。守卫 `tests/test_coder_feishu_app.py`。

各平台凭据独立管理，互不影响。

## 飞书

飞书是 Hermes 的主要消息平台，四个 profile 都支持。配置在 `~/.hermes/.env` 中：

```bash
FEISHU_APP_ID=cli_xxxx
FEISHU_APP_SECRET=xxxxx
FEISHU_DOMAIN=feishu
FEISHU_CONNECTION_MODE=websocket
FEISHU_GROUP_POLICY=open
```

1. 在飞书开发者后台创建应用 → 获取 App ID / App Secret
2. 在「事件与回调」中选择「使用长连接接收事件/回调」
3. 将凭据写入 `~/.hermes/.env`，重启容器

finance profile 使用独立的飞书应用（`FINANCE_FEISHU_APP_ID/SECRET`），在项目 `.env` 中配置，`docker-compose.yml` 中传入。爱码士（coder）同理使用 `CODER_FEISHU_APP_ID/SECRET`（若与已有「爱码士应用」为同一应用，值与 `LARK_CLI_IDM_*` 相同）；该应用需具备**机器人能力** + 「事件与回调」**长连接**（订阅 `im.message.receive_v1`）才能收发消息。

### markdown 渲染：卡片化补丁（Card JSON 2.0）

镜像构建时对 upstream 做文本手术（`docker/hermes/patch-feishu.py`）——**飞书 post 渲染器不支持 GFM 表格**（原样露出生管道符源码）、标题降级为普通文字；卡片（interactive, Card JSON 2.0）能真渲染：

1. **markdown 命中的回复 → 卡片**：标题分级字号、列表项目符号、**表格真渲染**（每组件 ≤4 表、除表头外 ≤5 行数据 + 分页）；纯文本消息保持 text 不变
2. **话题（omt_）回复修复（上游 bug）**：缺回复锚点时 bot 在话题里的回复一律 99992402 发送失败，补上锚点后走 `reply_in_thread` 路径
3. **公式 → 图片**（2026-10-14）：`$...$`（行内）/`$$...$$`（块级）用 matplotlib mathtext 渲染成图嵌卡片 —— 飞书 IM 消息（post 与卡片同）不渲染公式，这是唯一的真渲染路径。行内走 **600px 画布居中渲染**（客户端把窄图放大致模糊+巨大，画布=客户端归一化显示宽才 1:1 清晰）、块级 320px 宽等比（上限 1000px）；公式 hash→image_key 缓存（重启免重复渲染上传）；渲染/上传失败只回退该段源码；货币（"$100 and $200"）不误判

- 开关：`FEISHU_MARKDOWN_CARD` / `FEISHU_LATEX_IMAGE`（compose 透传四个 hermes 服务；默认 on）—— 填 `0/off/false/no` 单容器回退（公式开关依赖卡片开关）；`.env` 改后 `up -d` 重建容器生效
- 降级链：卡片被拒 → post 重试 → 纯文本（永不丢消息；降级打 `[Feishu] Card payload rejected` warning 日志）
- 已知边界：Card 2.0 需飞书客户端 ≥ 7.20（旧客户端显示升级提示）；>4 个表格的消息保持 post；公式变静态图（不可选中复制，缓存文件在 `~/.myagentdata` 之外的数据卷 `~/.hermes/.cache/feishu-latex.json`）
- 判据：`bash tests/test-feishu-card-patch.sh` 全绿（25 项）；上游 merge 卡片 PR 后应删补丁并重验
- 回滚：环境开关（最快）或 `docker tag myopenclaw/hermes:pre-latex myopenclaw/hermes:latest` + `up -d`（另有 `pre-card` = 完全原始 / `cards-only` = 仅卡片版）

## Discord（爱码士 coder）

hermes-coder 通过 Discord Bot 接入，与 OpenClaw（虾酱）的 Discord Bot 是**独立的两套**。

### 访问控制

`DISCORD_ALLOWED_USERS` 限定 Discord 用户 ID，只有指定的用户能调用。`DISCORD_REQUIRE_MENTION=true` 确保仅在 @bot 时才响应。

### 配置步骤

1. 在 [Discord Developer Portal](https://discord.com/developers/applications) 创建新的 Application
2. 左侧 **Bot** → **Add Bot** → **Reset Token** 获取 token
3. 在 **Privileged Gateway Intents** 区域开启 Message Content Intent、Server Members Intent
4. 左侧 **OAuth2** → **URL Generator**：
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`、`Read Message History`、`Attach Files`、`Use Slash Commands`
   - 用生成的 URL 把 Bot 邀请到目标服务器
5. 在项目 `.env` 中设置 `DISCORD_BOT_TOKEN=<token>`
6. `docker compose up -d hermes-coder`

环境变量（`docker-compose.yml` 中 `hermes-coder` 的 environment 段）：

| 变量 | 说明 |
|------|------|
| `DISCORD_BOT_TOKEN` | Bot Token，写在 `.env` 中 |
| `DISCORD_ALLOWED_USERS` | 逗号分隔的 Discord 用户 ID |
| `DISCORD_REQUIRE_MENTION` | `true` 时只有 @bot 才响应 |

### 网络注意事项

Discord 网关 (`gateway.discord.gg`) 在国内可能间歇性 DNS 解析失败。若日志中出现 `Temporary failure in name resolution` 或 `PrivilegedIntentsRequired`，重启容器即可（`docker compose restart hermes-coder`）。若频繁失败，需检查 DNS 或代理设置。

## coder profile 模型

首次启动时 `start.sh` 自动创建 `~/.hermes/profiles/coder/config.yaml`，默认模型 `deepseek-flash`（DeepSeek API，`DEEPSEEK_API_KEY`），备用模型 `glm-5.3-flash`（z.ai，**国内端点 `open.bigmodel.cn`**）。2026-10-15 起三个有兜底的 profile（爱玛士/爱码士/道元）统一该兜底，两个配套要点（守卫 `tests/test_hermes_fallback_glm.py`）：glm-5.3-flash **强制思考**，必须配 `agent.reasoning_overrides: {glm-5.3-flash: low}`（否则全局 thinking=off 被智谱 400；low 也是国内端点词表 `low/high/max` 里最省的档）；zai 凭据的探测缓存会漂到国外 `api.z.ai`，端点必须钉 `open.bigmodel.cn`。

**既有 profile 的模型迁移**：`start.sh` 每次启动都会调用 `scripts/ensure_hermes_model.py`，幂等地把历史遗留的模型名迁移到 canonical 的 `deepseek-flash` —— 只改已知历史取值（`mimo-v2.5` / `mimo-v2.5-pro`、以及非 canonical 的 `deepseek-v4-*`），**不覆盖**操作者手选的其他模型（与 `ensure_hermes_web_search.py` 同一原则）。脚本用文本手术改写，不依赖宿主机 PyYAML、不破坏注释与 key 顺序。

> ⚠️ **底座版本敏感性**：旧底座的 `hermes_cli/model_normalize.py` 会把没有版本段的 `deepseek-flash` 改写掉（详见 `CLAUDE.md` 的 Key Design Decisions）。改完配置后跑 `bash tests/test-hermes-normalize.sh` 验证，回退到旧底座它会立刻变红。

## 网络搜索（web_search）

四个 Hermes profile 共用同一镜像。`web_search` 默认走 **ddgs**（DuckDuckGo 抓取，免 API key）：

1. 镜像安装 `ddgs` 包（`docker/hermes/Dockerfile`）
2. `start.sh` 调用 `scripts/ensure_hermes_web_search.py`，幂等写入 `~/.hermes/config.yaml`（该脚本用文本手术改写，不依赖宿主机 PyYAML，也不会动你的注释和 key 顺序）：

```yaml
web:
  search_backend: "ddgs"
```

已有 `search_backend` 不会被覆盖（可手动改成 `brave_free`）。Dockerfile 变更后需要重建镜像：

```bash
docker compose build hermes
docker compose up -d hermes hermes-coder hermes-finance hermes-daoyuan
```

DuckDuckGo 从国内网络的可达性依赖宿主机 VPN（与 Discord 同通道）。provider 自带 30s 超时，失败时工具返回错误文本，agent 不会挂死。
