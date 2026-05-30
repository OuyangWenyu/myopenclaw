# OpenClaw 渠道配置（Discord / 飞书 / 钉钉）

OpenClaw 的渠道需要在 `~/.openclaw/openclaw.json` 中手动配置。`openclaw.json.example` 不含渠道配置，因为每个用户的 bot 凭证不同。

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

通过 `dingtalk-connector` 扩展接入，配置在 `~/.openclaw/openclaw.json` 的 `extensions` 下。

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

deepseek-v4-flash（主）→ kimi-k2.5（备份）。可在 `~/.openclaw/openclaw.json` 的 `agents.defaults.model` 中修改。
