# 飞书 CLI (lark-cli)

Hermes 容器内已安装 [lark-cli](https://github.com/larksuite/cli)（飞书官方 CLI），可通过终端操作飞书：消息、日历、文档、多维表格等 23 个业务域、200+ 命令。

## 前置条件

在 `.env` 中配置 `LARK_CLI_APP_ID` / `LARK_CLI_APP_SECRET`（及可选的 `LARK_CLI_IDM_APP_ID` / `LARK_CLI_IDM_APP_SECRET`）。

首次启动时 entrypoint 自动初始化 lark-cli 配置。

## 授权状态

entrypoint 首次启动时已用 `LARK_CLI_APP_ID/SECRET`（以及可选的 `LARK_CLI_IDM_APP_ID/SECRET`）自动初始化两个 profile 并按 **bot 身份**绑定，日常调用走 bot，不需要人工授权：

```bash
# 查看当前配置
docker compose exec hermes lark-cli config show
docker compose exec hermes lark-cli config show --profile idm

# 验证授权状态（bot 身份应为 ready）
docker compose exec hermes lark-cli auth status
docker compose exec hermes lark-cli auth status --profile idm
```

默认 profile 当前处于 **bot-only 严格模式**（`lark-cli config strict-mode` 输出 `bot`），**user 身份的 OAuth 授权会被策略拒绝**：

```
$ docker compose exec hermes lark-cli auth login --recommend
{"ok":false,"error":{"type":"validation","subtype":"failed_precondition",
 "message":"strict mode is \"bot\", only bot-identity commands are available", ...}}
```

因此 `auth status` 里 user 身份恒为 `missing`（`defaultAs: bot`）——这是当前策略下的预期状态，不是配置故障。若确实需要用户身份，先切策略再授权（`strict-mode` 是安全策略，切换前请确认）：

```bash
docker compose exec hermes lark-cli config strict-mode user   # 或 --reset 清除 profile 覆盖
docker compose exec hermes lark-cli auth login --help         # 查看该模式下可用的授权参数
```

## 使用示例

```bash
# 列出群聊
docker compose exec hermes lark-cli im +chat-list --format pretty

# 发送消息
docker compose exec hermes lark-cli im +messages-send --chat-id oc_xxx --text "Hello"

# 查看日历
docker compose exec hermes lark-cli calendar +agenda
```

用 `--profile idm` 切换到爱码士应用，不加则使用默认 Hermes 应用。

lark-cli 支持三种命令层级：快捷命令（`+` 前缀）、API 命令、原始 API 调用，详见 `lark-cli --help`。
