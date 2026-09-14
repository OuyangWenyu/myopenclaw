# OpenClaw 渠道配置（Discord / 飞书 / 钉钉）

OpenClaw 的渠道配置在 `~/.openclaw/openclaw.json` 的 `channels` 段。`openclaw.json.example` 里的渠道段默认 `enabled: false`、凭证为 `__OPENCLAW_*__` 占位符 —— 由 `start.sh` 首次启动时从 `.env` 注入实际值并启用。

实际支持的渠道取决于已安装的扩展（extensions）。当前已安装 `dingtalk-connector` 插件支持钉钉，Discord / 飞书通过内置渠道配置。

## Discord Bot

1. 在 [Discord Developer Portal](https://discord.com/developers/applications) 创建 Bot，获取 Token
2. 编辑 `~/.openclaw/openclaw.json`，在 `channels` 下添加：

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_DISCORD_BOT_TOKEN",
      "dmPolicy": "allowlist",
      "groupPolicy": "open",
      "allowFrom": ["YOUR_DISCORD_USER_ID"],
      "streaming": { "mode": "partial" }
    }
  }
}
```

## 飞书 Bot

1. 在飞书开发者后台创建应用，获取 App ID 和 App Secret
2. 在「事件与回调」→「订阅方式」中选择「使用长连接接收事件/回调」
3. 编辑 `~/.openclaw/openclaw.json`，在 `channels` 下添加：

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "YOUR_FEISHU_APP_ID",
      "appSecret": "YOUR_FEISHU_APP_SECRET",
      "domain": "feishu",
      "connectionMode": "websocket",
      "dmPolicy": "open",
      "groupPolicy": "allowlist",
      "groupAllowFrom": ["YOUR_FEISHU_GROUP_ID"],
      "allowFrom": ["*"]
    }
  }
}
```

## 钉钉

通过 `dingtalk-connector` 插件接入。配置在 `~/.openclaw/openclaw.json` 的 `channels.dingtalk-connector`，凭据放在其 `accounts.__default__`（`clientId` / `clientSecret`，由 `start.sh` 从 `.env` 注入）。

> 注意区分：`extensions/` 是**数据目录下的插件安装目录**（见 `docs/backup.md`），不是配置键 —— 配置里没有 `extensions` 这一段。

配置完成后重启 OpenClaw：

```bash
docker compose restart openclaw-gateway
```

验证：

```bash
docker compose logs --tail=20 openclaw-gateway
# 看到 [discord] starting / [feishu] WebSocket client started 即成功
```

## 默认模型

deepseek-flash（主）→ kimi-k2.5（备份）。可在 `~/.openclaw/openclaw.json` 的 `agents.defaults.model` 中修改。
