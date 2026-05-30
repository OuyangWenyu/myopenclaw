# Hermes 消息平台配置

Hermes 的三个 profile（default、coder、finance）各自可以接入不同的消息平台。平台凭据通过环境变量传入容器，Hermes 启动时自动连接。

## 平台总览

| 平台 | 使用的 profile | 配置方式 | 说明 |
|------|--------------|---------|------|
| 飞书 | 全部（default / coder / finance） | `~/.hermes/.env` | 主消息平台，走 WebSocket 长连接 |
| Discord | coder（爱码士） | 项目 `.env` + `docker-compose.yml` | 独立 Discord Bot，仅限个人使用 |

各平台凭据独立管理，互不影响。

## 飞书

飞书是 Hermes 的主要消息平台，三个 profile 都支持。配置在 `~/.hermes/.env` 中：

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

finance profile 使用独立的飞书应用（`FINANCE_FEISHU_APP_ID/SECRET`），在项目 `.env` 中配置，`docker-compose.yml` 中传入。

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

首次启动时 `start.sh` 自动创建 `~/.hermes/profiles/coder/config.yaml`，默认模型 `deepseek-v4-pro`（DeepSeek API），备用模型 `glm-5.1`（z.ai）。
