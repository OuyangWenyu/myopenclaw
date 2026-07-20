# 飞书 CLI (lark-cli)

Hermes 容器内已安装 [lark-cli](https://github.com/larksuite/cli)（飞书官方 CLI），可通过终端操作飞书：消息、日历、文档、多维表格等 17 个业务域、200+ 命令。

## 前置条件

在 `.env` 中配置 `LARK_CLI_APP_ID` / `LARK_CLI_APP_SECRET`（及可选的 `LARK_CLI_IDM_APP_ID` / `LARK_CLI_IDM_APP_SECRET`）。

首次启动时 entrypoint 自动初始化 lark-cli 配置。

## OAuth 授权

OAuth 授权需手动完成：

```bash
# 查看当前配置
docker compose exec hermes lark-cli config show

# 授权主应用（Hermes）
docker compose exec hermes lark-cli auth login --recommend
# 按提示在浏览器中打开验证链接，登录飞书并授权

# 授权第二应用（爱码士，如已配置）
docker compose exec hermes lark-cli auth login --recommend --profile idm

# 验证授权状态
docker compose exec hermes lark-cli auth status
docker compose exec hermes lark-cli auth status --profile idm
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
