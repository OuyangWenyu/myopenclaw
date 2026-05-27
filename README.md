# myopenclaw

用 Docker 运行 [Hermes Agent](https://github.com/NousResearch/hermes-agent)（含 [opencode](https://opencode.ai) + [GitHub CLI](https://cli.github.com)）、[Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview)（含 [cc-connect](https://github.com/chenhg5/cc-connect) 飞书直连）和 [OpenClaw](https://github.com/openclaw/openclaw)，数据留在本机（`~/.hermes`、`~/.openclaw`、`~/.myagentdata`、`~/.claude`、`~/.cc-connect`），配置用 Git 管理，用户数据定期快照备份到云盘。

## 服务说明

| 服务 | 镜像 | 默认端口 | 说明 |
|------|------|----------|------|
| hermes | 自建镜像（基于 `nousresearch/hermes-agent:latest`，含 opencode + gh + lark-cli） | 8642 | Hermes gateway |
| hermes-coder | 同 hermes 镜像 | 8643 | Hermes coder profile |
| hermes-finance | 同 hermes 镜像 | 8644 | Hermes finance profile |
| hermes-dashboard | `nousresearch/hermes-agent:latest` | 9119 | Hermes Web 面板 |
| claude-code | 自建镜像（基于 `node:22-slim`，含 Claude Code + cc-connect + gh） | 9090 | Claude Code + 飞书直连 |
| openclaw-gateway | `ghcr.io/openclaw/openclaw:latest` | 18789 | OpenClaw gateway |
| backup-cron | 自建 alpine 镜像 | — | 定时快照备份（默认每周日凌晨 2:00）|

数据目录映射：

- `~/.hermes` → `/opt/data`（hermes 容器内）
- `~/.claude` → `/opt/claude-config`（claude-code 容器内，Claude Code 配置和凭证）
- `~/.cc-connect` → `/opt/cc-config`（claude-code 容器内，cc-connect 配置）
- `~/.openclaw` → `/home/node/.openclaw`（openclaw 容器内）
- `~/.myagentdata` → `/.myagentdata`（backup-cron 容器只读挂载，用于备份）
- `~/.config/gh` → `/opt/gh-config`（hermes 和 claude-code 容器内，gh 认证和配置，宿主机持久化）
- `~/.config/opencode` → `/opt/opencode-config`（hermes 容器内，opencode 配置，宿主机持久化）
- `~/.hermes/secrets/` → `/opt/data/secrets/`（hermes 容器内，LLM 密钥文件，opencode.json 用 `{file:}` 引用）
- `~/code` + `~/Code` → `/home/claude/code` + `/home/claude/Code`（claude-code 容器内，代码仓库）

---

## 首次使用（新机器）

### 前置要求

- Docker Desktop 已安装并运行
- 云盘客户端（Google Drive / OneDrive）已登录并完成本地同步

### 配置流程总览

| 步骤 | 操作 | 自动/手动 |
|------|------|-----------|
| 1 | 克隆仓库 + 填写 `.env` + `.cloud.conf` | **手动** |
| 2 | （可选）配置中国域名 DNS 解析 `./scripts/setup-dns.sh` | **自动**：详见 [DNS 配置文档](docs/dns-setup.md) |
| 3 | （可选）从云盘快照恢复历史数据 | 手动 |
| 4 | 启动服务 `./scripts/start.sh` | **自动**：创建配置文件、安装 skill、拉起容器 |
| 5 | （必须）填写 `.env` 中的 API Key | **手动** |
| 6 | （按需）配置 cc-connect 飞书 bot | **手动**：见下方说明 |
| 7 | （按需）配置 OpenClaw 渠道（Discord / 飞书） | **手动** |
| 8 | （按需）配置 lark-cli 授权（飞书 CLI） | **手动**：见下方说明 |
| 9 | （按需）配置 Hermes 个性（邮箱、SOUL.md、config.yaml） | **手动** |

> 步骤 4 会自动完成：opencode.json、Claude Code settings.json、cc-connect config.toml、openclaw.json 从模板创建，paper-fetch skill 自动安装，API Key 物化到 secrets 文件。步骤 5-8 的内容因人而异，无法自动化。

---

### 1. 克隆仓库 + 基础配置

```bash
git clone https://github.com/OuyangWenyu/myopenclaw.git
cd myopenclaw
```

**配置环境变量**（必须）：

```bash
cp .env.example .env
```

`.env` 中需要填写的项目（详见文件内注释）：

| 变量 | 必填？ | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | 推荐 | DeepSeek API Key，OpenClaw 默认模型（deepseek-v4-flash）使用 |
| `GLM_API_KEY` | 推荐 | 智谱 API Key，Claude Code 的 `ANTHROPIC_API_KEY` |
| `CC_CONNECT_FEISHU_APP_ID` / `SECRET` | 可选 | cc-connect 飞书应用凭证（独立于 Hermes 的飞书 bot） |
| `GH_TOKEN` | 可选 | GitHub PAT，gh CLI 认证用 |
| `OPENCODE_API_KEY` | 可选 | opencode 专用 Key（不填则走默认 GLM） |
| `MOONSHOT_API_KEY` | 可选 | Moonshot API Key，OpenClaw 备份模型（kimi-k2.5）使用 |
| `UNPAYWALL_EMAIL` | 可选 | Unpaywall 联系邮箱，提高 paper-fetch 论文下载命中率 |
| `LARK_CLI_APP_ID` / `SECRET` | 可选 | lark-cli 主应用（Hermes 机器人），不填则绑定 Hermes 内置飞书应用 |
| `LARK_CLI_IDM_APP_ID` / `SECRET` | 可选 | lark-cli 第二个 profile（爱码士应用） |

被 Hermes 黑名单拦截的密钥（DEEPSEEK、OPENROUTER、OPENAI）通过 `.env` 传入容器后，由 entrypoint 脚本自动写入 `/opt/data/secrets/` 文件，opencode.json 通过 `{file:路径}` 引用，无需手动创建。

**配置云盘路径**（必须）：

```bash
cp .cloud.conf.example .cloud.conf
# 编辑 .cloud.conf，填写本机云盘实际路径
./scripts/setup-cloud.sh   # 验证并初始化备份目录
```

### 2. 从云盘快照恢复数据（可选）

新机器首次部署可跳过。从旧机器迁移时执行：

```bash
# 恢复全部最新快照（hermes、claude、openclaw、~/.myagentdata）
./scripts/restore.sh all latest

# 也可以只恢复某一个，或指定快照时间戳
./scripts/restore.sh hermes latest
./scripts/restore.sh claude 2026-04-23_090000
./scripts/restore.sh openclaw latest
```

> 如果恢复了 `~/.openclaw/openclaw.json` 或 `~/.cc-connect/config.toml`，步骤 3 不会覆盖它（start.sh 只在文件不存在时从模板创建）。

### 3. 启动服务

```bash
./scripts/start.sh
```

首次启动或更新镜像时加 `--build`（Hermes / Claude Code 自定义镜像变更后也需重新构建）：

```bash
./scripts/start.sh --build
```

首次启动会自动完成：

| 自动操作 | 目标位置 |
|----------|----------|
| 创建 opencode 配置 | `~/.config/opencode/opencode.json`（从 `hermes/config/opencode.json.example`） |
| 创建 Claude Code 配置 | `~/.claude/settings.json`（从 `claude/config/settings.json.example`） |
| 创建 cc-connect 配置 | `~/.cc-connect/config.toml`（从 `claude/config/cc-connect.toml.example`） |
| 创建 OpenClaw 配置 | `~/.openclaw/openclaw.json`（从 `openclaw/config/openclaw.json.example`） |
| 安装 paper-fetch skill | `~/.openclaw/skills/paper-fetch`（自动 git clone） |
| 物化被黑名单拦截的 API Key | 容器内 `/opt/data/secrets/`（deepseek、openrouter、openai） |
| 生成 himalaya 邮件配置 | `~/.hermes/.config/himalaya/config.toml`（从 `~/.hermes/.env` 中 `EMAIL_*` 变量） |
| 映射 GLM_API_KEY → ANTHROPIC_API_KEY | claude-code 容器内环境变量，供 Claude Code 使用 |
| 初始化 lark-cli 配置 | `~/.lark-cli/`（从 `.env` 读取凭证，支持多 profile） |

### 4. 填写 API Key（必须）

在 `.env` 中填入你的 API Key 后，重启服务使其生效：

```bash
# 最小必填：让 OpenClaw 和 Claude Code 能调用 LLM
DEEPSEEK_API_KEY=sk-...
GLM_API_KEY=xxxxx.xxxxx

# 重启让新 Key 生效
docker compose up -d
```

### 5. 配置 cc-connect 飞书 bot（按需）

cc-connect 通过飞书 WebSocket 长连接桥接 Claude Code，无需公网 IP。

1. 在 [飞书开发者后台](https://open.feishu.cn) 创建应用
2. 开启「机器人」能力
3. 添加 `im.message.receive_v1` 事件，选择「WebSocket 长连接」模式
4. 在 `.env` 中填入凭证：
   ```
   CC_CONNECT_FEISHU_APP_ID=cli_xxxx
   CC_CONNECT_FEISHU_APP_SECRET=xxxxx
   ```
5. 重启 claude-code 服务：`docker compose restart claude-code`

也可通过 cc-connect Web 管理界面配置：`open http://localhost:9090`

验证连接：`docker compose logs --tail=20 claude-code`，看到飞书 WebSocket 连接成功即 OK。

### 6. 配置 OpenClaw 渠道（按需）

OpenClaw 的渠道（Discord、飞书等）需要在 `~/.openclaw/openclaw.json` 中手动配置。`openclaw.json.example` 不含渠道配置，因为每个用户的 bot 凭证不同。

**配置 Discord Bot**：

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

**配置飞书 Bot**：

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

配置完成后重启 OpenClaw：

```bash
docker compose restart openclaw-gateway
```

验证渠道是否连上：

```bash
docker compose logs --tail=20 openclaw-gateway
# 看到 [discord] starting / [feishu] WebSocket client started 即成功
```

**OpenClaw 默认模型**：deepseek-v4-flash（主）→ kimi-k2.5（备份）。可在 `~/.openclaw/openclaw.json` 的 `agents.defaults.model` 中修改。

### 7. 配置 Hermes 个性（按需）

Hermes 的核心配置文件在 `~/.hermes/` 下，不在本仓库中管理（通过备份恢复）：

| 文件 | 说明 | 如何获取 |
|------|------|----------|
| `config.yaml` | Hermes 网关和 agent 配置 | 手动编写，或从云盘备份恢复 |
| `SOUL.md` | Hermes 人格/提示词 | 手动编写，或从云盘备份恢复 |

如果是从云盘恢复的用户，步骤 2 已恢复这些文件。新用户需要自行创建或从 OpenClaw Web 面板（`http://localhost:18789`）引导配置。

**配置邮件工具**（可选）：Hermes **不把 email 当消息平台**（不会自动回复邮件），而是通过 [himalaya](https://github.com/pimalaya/himalaya) CLI 工具手动查/发邮件。himalaya 已预装在 Hermes 镜像中（v1.2.0）。

首次启动时 entrypoint 会自动从 `~/.hermes/.env` 解析 `EMAIL_*` 变量并生成 `~/.hermes/.config/himalaya/config.toml`。以 QQ 邮箱为例：

```
# 这些变量在 ~/.hermes/.env 中保持注释状态（避免 Hermes 启用 email 消息平台）
# entrypoint 能解析注释行来生成 himalaya 配置
EMAIL_ADDRESS=你的QQ号@qq.com
EMAIL_PASSWORD=授权码
EMAIL_IMAP_HOST=imap.qq.com
EMAIL_IMAP_PORT=993
EMAIL_SMTP_HOST=smtp.qq.com
EMAIL_SMTP_PORT=587
```

> **注意**：① 不要用个人主力邮箱，建一个新邮箱或用小号。② SMTP 端口必须用 **587**（STARTTLS），不能用 465。③ `EMAIL_*` 变量请保持注释状态 — 取消注释会导致 Hermes 把 email 当作消息平台，任何人发邮件都会自动回复。④ QQ 邮箱需要开启 IMAP/SMTP 服务并生成授权码。

使用方式：直接跟 Hermes 说「查收件箱」「搜来自 xxx 的邮件」「给 xxx 发封邮件」。

验证 himalaya 配置：`docker compose exec hermes himalaya envelope list --page-size 5`

**gh CLI 认证**（可选）：Hermes 容器内已安装 GitHub CLI。二选一：

- **方式 A**：在 `.env` 中设置 `GH_TOKEN=github_pat_xxxx`（推荐）。容器内自动映射为 `GITHUB_TOKEN`。
- **方式 B**：进入容器交互式登录
  ```bash
  docker compose exec hermes gh auth login
  ```
  认证状态保存在宿主机 `~/.config/gh/hosts.yml`，容器重建不丢失。

**opencode 配置**：首次启动自动创建 `~/.config/opencode/opencode.json`，默认使用 [OpenCode Zen](https://opencode.ai/zen) 的 GLM 5.1。API Key 通过 `.env` 传入，无需手动配置。

**Claude Code 配置**：首次启动自动创建 `~/.claude/settings.json`，默认使用智谱 GLM 模型。如需切换回真实 Anthropic API：清空 `GLM_API_KEY`，在 `.env` 中设置 `ANTHROPIC_API_KEY=sk-ant-...`，并修改 `~/.claude/settings.json` 移除 `ANTHROPIC_BASE_URL`。

### 8. 配置 lark-cli 授权（按需）

Hermes 容器内已安装 [lark-cli](https://github.com/larksuite/cli)（飞书官方 CLI），可通过终端操作飞书（消息、日历、文档、多维表格等 17 个业务域、200+ 命令）。

**前置条件**：在 `.env` 中配置了 `LARK_CLI_APP_ID` / `LARK_CLI_APP_SECRET`（及可选的 `LARK_CLI_IDM_APP_ID` / `LARK_CLI_IDM_APP_SECRET`）。首次启动时 entrypoint 会自动初始化 lark-cli 配置，但 OAuth 授权需手动完成：

```bash
# 1. 查看当前配置
docker compose exec hermes lark-cli config show

# 2. 授权主应用（Hermes）
docker compose exec hermes lark-cli auth login --recommend
# 按提示在浏览器中打开验证链接，登录飞书并授权

# 3. 授权第二应用（爱码士，如已配置）
docker compose exec hermes lark-cli auth login --recommend --profile idm

# 4. 验证授权状态
docker compose exec hermes lark-cli auth status
docker compose exec hermes lark-cli auth status --profile idm
```

授权成功后即可使用，例如：

```bash
# 列出群聊
docker compose exec hermes lark-cli im +chat-list --format pretty

# 发送消息
docker compose exec hermes lark-cli im +messages-send --chat-id oc_xxx --text "Hello"

# 查看日历
docker compose exec hermes lark-cli calendar +agenda
```

用 `--profile idm` 切换到爱码士应用，不加则使用默认 Hermes 应用。lark-cli 支持三种命令层级：快捷命令（`+` 前缀）、API 命令、原始 API 调用，详见 `lark-cli --help`。

---

## 日常操作

### 启动 / 停止

```bash
./scripts/start.sh   # 启动所有服务
./scripts/stop.sh    # 停止所有服务
```

### 查看服务状态

```bash
docker compose ps
docker compose logs -f hermes
docker compose logs -f claude-code
docker compose logs -f openclaw-gateway
```

### 手动触发备份

```bash
# 进入 backup-cron 容器执行（推荐）
docker compose exec backup-cron /scripts/backup-all-docker.sh
```

快照保存在：`<云盘路径>/myopenclaw-backups/hermes/`、`.../claude/`、`.../openclaw/`、`.../data/`

每个快照为独立时间戳目录，同时维护一个 `latest/` 软链接。超过 `BACKUP_KEEP_DAYS`（默认 30 天）的旧快照自动清除。

### 运行 OpenClaw CLI

```bash
docker compose --profile cli run --rm openclaw-cli
```

### cc-connect 管理

```bash
# Web 管理界面
open http://localhost:9090

# 查看日志
docker compose logs -f claude-code
```

---

## 目录结构

```ini
myopenclaw/
├── docker-compose.yml          # 服务编排
├── .env.example                # 环境变量模板（API Key、端口、cron 等）
├── .cloud.conf.example         # 云盘路径模板（本机路径，不入 git）
├── docker/
│   ├── backup-cron/            # 定时备份容器（alpine + rsync + sqlite3）
│   ├── hermes/                 # 自定义 Hermes 镜像（opencode + gh CLI + lark-cli）
│   └── claude-code/            # 自定义 Claude Code 镜像（Claude Code + cc-connect + gh）
├── hermes/
│   ├── config/opencode.json.example       # opencode 配置模板（首次启动自动复制到 ~/.config/opencode/）
│   └── scripts/backup.sh       # hermes 数据选择性快照脚本
├── claude/
│   ├── config/settings.json.example       # Claude Code 配置模板（首次启动自动复制到 ~/.claude/）
│   ├── config/cc-connect.toml.example     # cc-connect 配置模板（首次启动自动复制到 ~/.cc-connect/）
│   └── scripts/backup.sh       # claude + cc-connect 数据选择性快照脚本
├── openclaw/
│   ├── config/openclaw.json.example       # OpenClaw 配置模板（首次启动自动复制到 ~/.openclaw/）
│   └── scripts/backup.sh       # openclaw 数据选择性快照脚本
└── scripts/
    ├── start.sh                # 启动服务（含自动配置 + skill 安装）
    ├── stop.sh                 # 停止服务
    ├── setup-cloud.sh          # 初始化云盘备份目录
    ├── backup-data.sh          # ~/.myagentdata 通用快照脚本
    ├── backup-all.sh           # 本机全量备份（不依赖容器）
    ├── backup-all-docker.sh    # 容器内备份（供 cron 调用）
    └── restore.sh              # 从快照恢复数据
```

## 备份内容说明

**Hermes 备份**：`config.yaml`、`SOUL.md`、`memories/`、`skills/`、`hooks/`、`cron/`

**Claude Code 备份**：`settings.json`、`projects/`、`skills/`、`plans/`、`tasks/`、cc-connect `config.toml`

**OpenClaw 备份**：`openclaw.json`、`agents/`、`flows/`、`extensions/`、`memory/main.sqlite`（热备份）

**Data 备份**：`~/.myagentdata/` 整目录 rsync 快照。所有数据类应用统一放此目录的子目录下（如 `~/.myagentdata/aisecretary/`、`~/.myagentdata/dailyinfo/`），无需额外配置即自动备份。

不备份：大型缓存、临时会话、auth token、日志等（`~/.config/gh`、`~/.config/opencode` 和 `~/.hermes/secrets/` 中的敏感内容不备份，需重新配置）。

---

## 安全边界

Hermes、Claude Code 和 OpenClaw 在本项目中承担不同角色，密钥隔离策略也不同：

**Hermes = 个人助手**。所有涉及个人身份和密钥的能力（GitHub 操作、LLM API Key、opencode 编码代理）都装在 Hermes 里。Hermes 的 `~/.hermes/secrets/` 存放个人密钥，`env_passthrough` 精确控制哪些变量能被 agent bash 子进程看到，Hermes 自身的 `redact_secrets` 机制在工具输出中自动脱敏。Hermes 适合单人使用，不暴露给多人环境。

**Claude Code = 编码 Agent**。通过 cc-connect 直连飞书，专注于代码任务。持有 `GLM_API_KEY` / `ANTHROPIC_API_KEY`，独立于 Hermes 运行。

**OpenClaw = 协作网关**。OpenClaw 不持有个人密钥，可以安全地开放到多人场景（团队共享、群组 bot 等）。它的配置和 agent 定义与个人身份无关，适合做工作流编排和多人任务分发。

简言之：**需要你的 key 的 → Hermes / Claude Code；可以给别人用的 → OpenClaw**。

---

## dailyinfo 调度

[dailyinfo](https://github.com/iHeadWater/dailyinfo) 是独立的 AI for Science 情报聚合仓（本机路径固定为 `/Users/owen/code/dailyinfo`，与 myopenclaw 是兄弟目录）。dailyinfo 自身只提供幂等 CLI，调度由本仓通过宿主机 launchd 托管。数据落在 `~/.myagentdata/dailyinfo/`，已被 `backup-cron` 自动覆盖，无需额外挂载。

### 前置

dailyinfo `run` 依赖 FreshRSS 容器常驻（由 dailyinfo 自己的 `docker-compose.yml` 管理，容器名 `dailyinfo_freshrss`，端口 `8081`）。**myopenclaw 的定时任务不会自动拉起它**。首次部署或停机后，去 dailyinfo 仓启一次即可：

```bash
cd /Users/owen/code/dailyinfo && uv run dailyinfo start
```

FreshRSS 配了 `restart: unless-stopped`，起一次之后宿主机重启也会自己恢复。容器挂了再手动 `dailyinfo start` 即可。

### 调度表

| 时间（Asia/Shanghai） | LaunchAgent Label | 命令 |
|---|---|---|
| 06:00 | `ai.dailyinfo.run-p1` | `uv run dailyinfo run -p 1`（RSS papers / AI news） |
| 06:15 | `ai.dailyinfo.run-p2` | `uv run dailyinfo run -p 2`（code trending） |
| 06:30 | `ai.dailyinfo.run-p3` | `uv run dailyinfo run -p 3`（university news） |
| 07:00 | `ai.dailyinfo.push`   | `uv run dailyinfo push` |

日志统一落在 `/Users/owen/code/dailyinfo/logs/dailyinfo-<cmd>.log`。

### 安装 / 卸载

```bash
# 首次部署或新机器
./scripts/launchd/install-dailyinfo.sh

# 卸载（停止所有 dailyinfo 定时任务）
./scripts/launchd/uninstall-dailyinfo.sh
```

install 脚本会自动解析 dailyinfo 仓路径（默认 `../dailyinfo`）和 `uv` 二进制路径，把 4 个 plist 模板渲染写到 `~/Library/LaunchAgents/` 并 `launchctl load -w`。若 dailyinfo 放在别处，用环境变量覆盖：

```bash
DAILYINFO_DIR=/path/to/dailyinfo ./scripts/launchd/install-dailyinfo.sh
```

### 失败排查

失败优先级从低到高：

1. 看日志（最常用）：
   ```bash
   tail -n 200 /Users/owen/code/dailyinfo/logs/dailyinfo-*.log
   ```
2. 看 launchd 上次退出码（非 0/1 才需要关注）：
   ```bash
   launchctl list | grep ai.dailyinfo
   ```
3. 进 dailyinfo 目录跑状态检查：
   ```bash
   cd /Users/owen/code/dailyinfo && uv run dailyinfo status
   ```

### 告警策略

- `run` / `push` 返回 **退出码 0**（至少成功处理 1 份）和 **退出码 1**（当天已全部处理或无新内容）**都是正常状态**，不要作为失败告警。
- 真正需要关注的是「连续若干天日志不再更新」「退出码 ≥ 2」「进程崩溃」这种硬故障。

### 不在本仓维护的内容

dailyinfo 的 secret（`OPENROUTER_API_KEY` / `DISCORD_BOT_TOKEN` / `DISCORD_CHANNEL_*`）、数据源配置（`config/sources.json`）、抓取 / AI 摘要 / 推送业务逻辑全部由 dailyinfo 仓自己管理，本仓只负责定时触发和备份覆盖。
