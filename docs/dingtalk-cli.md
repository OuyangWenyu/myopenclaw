# 钉钉 CLI (dws)

Hermes 容器内装了钉钉官方 [Workspace CLI](https://github.com/open-dingtalk/dingtalk-workspace-cli)（命令 `dws`），
可用终端操作钉钉 17 个产品域：AI 表格、日历、通讯录、群聊与机器人、待办、OA 审批、
考勤、日志（日报周报）、钉钉文档、钉钉云盘、AI 听记、邮箱、在线电子表格、知识库、DING 等。

**只有默认 profile（爱玛士 / `hermes` 容器）有这个能力**（2026-11-16 用户明确要求）。
其余三个 profile（爱码士 / 道元 / finance）的 compose 定义里既没有 token 目录也没有
`DWS_CONFIG_DIR`，`dws` 调不通。

## 安装方式

镜像构建时装（`docker/hermes/Dockerfile`）：

```dockerfile
npm install -g dingtalk-workspace-cli@1.0.26
```

npm 包内**自带五平台预编译 Go 二进制**（`assets/dws-linux-amd64.tar.gz` 等），
postinstall 只做解压，构建期不需要额外下载。构建时把 `HOME` 指到 `/tmp`，避免
postinstall 把官方 skill 复制进 `/root/.hermes/skills` 这类构建期垃圾目录。

版本 pin 在 Dockerfile；守卫 `tests/test_dws_cli_wiring.py` 还会校验 pin **满足**
skill 自己声明的 `cli_version` 下限（低了等于 skill 的 `--help` 断言失效）。

## 隔离设计（为什么 token 不在 /opt/data 里）

四个 hermes 容器共享 `/opt/data`（= 宿主机 `~/.hermes`），所以钉钉 token **不能**
落在 `$HOME/.dws` —— 那等于把个人钉钉账号交给 coder / 道元 / finance。
所以走 OneDrive / 邮箱同一套模式：

| 项 | 默认 profile | 其余 profile |
|---|---|---|
| 宿主机 token 目录 `~/.dws-cli` | 挂到 `/opt/dws-config` | 不挂 |
| `DWS_CONFIG_DIR`（app.json / identity / logs / cache） | `/opt/dws-config` | 不设 |
| `DWS_KEYCHAIN_DIR`（**真正的 token** 落点） | `/opt/dws-config/keychain` | 不设 |
| skill（`skills/dws`） | start.sh 链进 `~/.hermes/skills/dws` | 不链 |

⚠️ **两个环境变量缺一不可**（2026-11-16 实测踩到）：`dws` 把凭据写进
`$DWS_KEYCHAIN_DIR/dws-cli/`（`auth-token.enc` + `dek`），默认路径是
`$HOME/.local/share/dws-cli` —— 容器里就是 `/root`，**重建即丢**；`DWS_CONFIG_DIR`
只管 `app.json` / `identity.json` / `logs/` / 缓存。只设前者的症状是「登录成功，
`docker compose up -d` 之后又变未登录」。两个都指到挂载卷后，容器重建、镜像升级
都不会掉登录态。

声明位置：`agents/hermes/base.yaml` 的 `mounts`（两个挂载点定义）、
`agents/hermes/default.yaml`（只有它列进 `mounts:` 并设两个环境变量）。
改完必须重渲染：`uv run --with pyyaml scripts/render_agents.py`。

## 首次授权（一次性，设备流）

`dws` 用的是**用户身份 OAuth**，不是 bot 身份 —— 不能像 lark-cli 那样靠
`.env` 里的 AppKey 免登录。容器内跑设备流，浏览器/手机授权一次即可：

```bash
docker compose exec -u hermes -it hermes dws auth login --device
# 屏幕会给 user_code + 短链接，授权完成后：
docker compose exec hermes dws auth status
```

- `access token` 2 小时、`refresh token` 30 天，**用一次即自动轮转**；日常无需重复登录。
- token 落在 `DWS_KEYCHAIN_DIR`，即宿主机 `~/.dws-cli/keychain/dws-cli/`
  （`auth-token.enc` + `dek`，都是 600）；`app.json` / `identity.json` / `logs/`
  在 `~/.dws-cli/` 下。容器重建/换镜像不影响登录态。
- `~/.dws-cli` **不在云备份范围**（`hermes/scripts/backup.sh` 只备 `~/.hermes`）——
  刻意的：个人钉钉 token 不上云盘。换机 / 丢目录 → 重跑一次设备流即可。
- 设备码有效期约 10 分钟、整个流程最长 15 分钟；`Ctrl-C` 或超时都会让码作废，
  重新跑一次命令拿新码。CLI 在码过期后会自动申请新码并继续轮询（日志里
  `Authorization code expired, restarting device authorization flow...`）。
- 报 `AUTH_TOKEN_EXPIRED` / `USER_TOKEN_ILLEGAL` → 同上重新登录；彻底坏掉用
  `dws auth reset` 后重登。

## 验证

```bash
# 二进制在不在（所有 profile 都有）
docker compose exec hermes dws --version
# 认证状态（只有默认 profile 应为 authenticated）
docker compose exec hermes dws auth status
# token 是否真的落在挂载卷上（不在 = 容器重建会掉登录）
ls -l ~/.dws-cli/keychain/dws-cli/
# skill 有没有被 Hermes 加载
docker compose exec hermes /opt/hermes/.venv/bin/hermes skills list | grep dws
# 只读冒烟：本人信息 / 待办 / 群搜索
docker compose exec hermes dws contact user get-self --format json
docker compose exec hermes dws todo task list --format json
docker compose exec hermes dws chat search --query "创新" --format json
```

## 使用方式

飞书里直接让爱玛士去办（skill 已加载，模型会自己挑产品命令），或手工：

```bash
docker compose exec hermes dws calendar event list --start 1750000000000 --end 1750600000000
docker compose exec hermes dws todo task create --title "本周组会材料" \
  --executors <userId> --due 2026-11-20T18:00:00+08:00
docker compose exec hermes dws chat message send-by-bot --robot-code <code> \
  --group <openConversationId> --title "组会" --text "今晚 8 点"
```

注意 `todo task create` 的 `--executors`（通讯录 userId）和 `chat message send-by-bot`
的 `--robot-code` 都是必填，userId 用 `dws contact user search --query 姓名` 查。

参数的**事实源**是 `dws <cmd> --help` 和 `dws schema <path>`，不是 skill 里的 `references/`；
两者冲突以 `--help` 为准（上游会按服务发现动态改字段）。写/删操作先用 `--dry-run`，
危险操作（删表/删记录/撤回/拒绝审批等）必须先跟用户确认再加 `--yes`。

## 已知噪音与坑

- `WARN plugin: stdio initialize failed plugin=conference server=conference-local`
  —— 会议能力要本机钉钉桌面客户端（≥ 8.3.20）在线，容器里必然没有。**只影响会议，
  其余产品照常**，可以忽略。
- `0.x` 的 cron / 定时任务里没有 TTY：`dws auth login --device` 需要人工授权，**不要**
  把登录塞进 cron。
- 容器重建**不会**丢 token（`~/.dws-cli` 在宿主机），但与 lark-cli 的 user token 不同
  —— 那个存容器内、重建即丢。

## 升级

1. 改 Dockerfile 的 `dingtalk-workspace-cli@<新版本>`；
2. 用同一版本的官方 skill 覆盖仓库副本：
   ```bash
   rm -rf skills/dws && cp -R "$(npm root -g)/dingtalk-workspace-cli/share/skills/dws" skills/dws
   ```
3. `docker compose build hermes && docker compose up -d hermes`；
4. `uv run --with pytest --with pyyaml pytest tests/test_dws_cli_wiring.py -q` 应全绿。
