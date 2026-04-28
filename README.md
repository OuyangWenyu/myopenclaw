# myopenclaw

用 Docker 运行 [Hermes Agent](https://github.com/NousResearch/hermes-agent)（含 [opencode](https://opencode.ai) + [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) + [GitHub CLI](https://cli.github.com)）和 [OpenClaw](https://github.com/openclaw/openclaw)，数据留在本机（`~/.hermes`、`~/.openclaw`、`~/.myagentdata`、`~/.claude`），配置用 Git 管理，用户数据定期快照备份到云盘。

## 服务说明

| 服务 | 镜像 | 默认端口 | 说明 |
|------|------|----------|------|
| hermes | 自建镜像（基于 `nousresearch/hermes-agent:latest`，含 opencode + Claude Code + gh） | 8642 | Hermes gateway |
| hermes-dashboard | `nousresearch/hermes-agent:latest` | 9119 | Hermes Web 面板 |
| openclaw-gateway | `ghcr.io/openclaw/openclaw:latest` | 18789 | OpenClaw gateway |
| backup-cron | 自建 alpine 镜像 | — | 定时快照备份（默认每周日凌晨 2:00）|

数据目录映射：

- `~/.hermes` → `/opt/data`（hermes 容器内）
- `~/.openclaw` → `/home/node/.openclaw`（openclaw 容器内）
- `~/.myagentdata` → `/.myagentdata`（backup-cron 容器只读挂载，用于备份）
- `~/.config/gh` → `/opt/gh-config`（hermes 容器内，gh 认证和配置，宿主机持久化）
- `~/.config/opencode` → `/opt/opencode-config`（hermes 容器内，opencode 配置，宿主机持久化）
- `~/.claude` → `/opt/claude-config`（hermes 容器内，Claude Code 配置和凭证，宿主机持久化）
- `~/.hermes/secrets/` → `/opt/data/secrets/`（hermes 容器内，LLM 密钥文件，opencode.json 用 `{file:}` 引用）

---

## 首次使用（新机器）

### 1. 前置要求

- Docker Desktop 已安装并运行
- 云盘客户端（Google Drive / OneDrive）已登录并完成本地同步

### 2. 克隆仓库

```bash
git clone https://github.com/OuyangWenyu/myopenclaw.git
cd myopenclaw
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 按需修改端口等配置（通常不用改）
# 如需 gh 认证，取消注释 GH_TOKEN 并填入 GitHub PAT（容器内自动映射为 GITHUB_TOKEN）
# 如需 opencode，取消注释 OPENCODE_API_KEY 等密钥
```

### 4. 配置云盘路径

```bash
cp .cloud.conf.example .cloud.conf
# 编辑 .cloud.conf，填写本机云盘实际路径
```

```bash
# 验证云盘目录并初始化备份目录结构
./scripts/setup-cloud.sh
```

### 5. 从云盘快照恢复数据（如有）

```bash
# 恢复全部最新快照（hermes、openclaw、~/.myagentdata）
./scripts/restore.sh all latest

# 也可以只恢复某一个，或指定快照时间戳
./scripts/restore.sh hermes latest
./scripts/restore.sh openclaw 2026-04-23_090000
./scripts/restore.sh data latest
```

### 6. 启动服务

```bash
./scripts/start.sh
```

首次启动或更新镜像时加 `--build`（Hermes 自定义镜像变更后也需重新构建）：

```bash
./scripts/start.sh --build
```

### 7. gh 认证（可选）

Hermes 容器内已安装 GitHub CLI，需认证后方可使用。二选一：

- **方式 A**：在 `.env` 中设置 `GH_TOKEN=github_pat_xxxx`（推荐）。容器内自动映射为 `GITHUB_TOKEN`，绕过 Hermes 安全黑名单传递给 bash 子进程。
- **方式 B**：进入容器交互式登录
  ```bash
  docker compose exec hermes gh auth login
  ```
  认证状态保存在宿主机 `~/.config/gh/hosts.yml`，容器重建不丢失。

### 8. opencode 配置（可选）

opencode 默认使用 [OpenCode Zen](https://opencode.ai/zen) 的 GLM 5.1 模型。首次启动时会自动创建 `~/.config/opencode/opencode.json`（从 `hermes/config/opencode.json.example` 复制）。

**密钥配置**：所有 API Key 统一在 `.env` 中配置。不在黑名单中的密钥（GH_TOKEN → GITHUB_TOKEN、OPENCODE_API_KEY、GLM_API_KEY、ANTHROPIC_API_KEY）通过 `env_passthrough` 直接传递给 bash 子进程。

被 Hermes 黑名单拦截的密钥（DEEPSEEK、OPENROUTER、OPENAI）通过 `.env` 传入容器后，由 entrypoint 脚本自动写入 `/opt/data/secrets/` 文件，opencode.json 通过 `{file:路径}` 引用。无需手动创建 secrets 文件：

```bash
# .env 中直接填写即可
DEEPSEEK_API_KEY=xxxxx
OPENROUTER_API_KEY=xxxxx
OPENAI_API_KEY=sk-...
```

### 9. Claude Code 配置

Hermes 容器内已安装 [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview)，默认配置为使用智谱 GLM 模型。

**密钥配置**：Claude Code 使用 `ANTHROPIC_API_KEY`，由 entrypoint 脚本自动从 `GLM_API_KEY` 映射。在 `.env` 中设置 `GLM_API_KEY` 即可：

```bash
# .env 中填写智谱 API Key
GLM_API_KEY=xxxxx.xxxxx
```

**模型配置**：首次启动时，`start.sh` 会自动创建 `~/.claude/settings.json`（从 `hermes/config/claude-settings.json.example` 复制），内容如下：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-4.7",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-4.7",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1"
  }
}
```

如需切换回真实 Anthropic API：清空 `GLM_API_KEY`，在 `.env` 中设置 `ANTHROPIC_API_KEY=sk-ant-...`，并修改 `~/.claude/settings.json` 移除 `ANTHROPIC_BASE_URL`。

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
docker compose logs -f openclaw-gateway
```

### 手动触发备份

```bash
# 进入 backup-cron 容器执行（推荐）
docker compose exec backup-cron /scripts/backup-all-docker.sh
```

快照保存在：`<云盘路径>/myopenclaw-backups/hermes/`、`.../openclaw/`、`.../data/`

每个快照为独立时间戳目录，同时维护一个 `latest/` 软链接。超过 `BACKUP_KEEP_DAYS`（默认 30 天）的旧快照自动清除。

### 运行 OpenClaw CLI

```bash
docker compose --profile cli run --rm openclaw-cli
```

---

## 目录结构

```ini
myopenclaw/
├── docker-compose.yml          # 服务编排
├── .env.example                # 环境变量模板（端口、cron 等）
├── .cloud.conf.example         # 云盘路径模板（本机路径，不入 git）
├── docker/
│   ├── backup-cron/            # 定时备份容器（alpine + rsync + sqlite3）
│   └── hermes/                 # 自定义 Hermes 镜像（opencode + Claude Code + gh CLI）
├── hermes/
│   ├── config/opencode.json.example       # opencode 配置模板（首次启动自动复制到 ~/.config/opencode/）
│   ├── config/claude-settings.json.example # Claude Code 配置模板（首次启动自动复制到 ~/.claude/）
│   └── scripts/backup.sh       # hermes 数据选择性快照脚本
├── openclaw/
│   └── scripts/backup.sh       # openclaw 数据选择性快照脚本
└── scripts/
    ├── start.sh                # 启动服务
    ├── stop.sh                 # 停止服务
    ├── setup-cloud.sh          # 初始化云盘备份目录
    ├── backup-data.sh          # ~/.myagentdata 通用快照脚本
    ├── backup-all.sh           # 本机全量备份（不依赖容器）
    ├── backup-all-docker.sh    # 容器内备份（供 cron 调用）
    └── restore.sh              # 从快照恢复数据
```

## 备份内容说明

**Hermes 备份**：`config.yaml`、`SOUL.md`、`memories/`、`skills/`、`hooks/`、`cron/`

**OpenClaw 备份**：`openclaw.json`、`agents/`、`flows/`、`extensions/`、`memory/main.sqlite`（热备份）

**Data 备份**：`~/.myagentdata/` 整目录 rsync 快照。所有数据类应用统一放此目录的子目录下（如 `~/.myagentdata/aisecretary/`、`~/.myagentdata/dailyinfo/`），无需额外配置即自动备份。

不备份：大型缓存、临时会话、auth token、日志等（`~/.config/gh`、`~/.config/opencode` 和 `~/.hermes/secrets/` 中的敏感内容不备份，需重新配置）。

---

## 安全边界

Hermes 和 OpenClaw 在本项目中承担不同角色，密钥隔离策略也不同：

**Hermes = 个人助手**。所有涉及个人身份和密钥的能力（GitHub 操作、LLM API Key、opencode 编码代理）都装在 Hermes 里。Hermes 的 `~/.hermes/secrets/` 存放个人密钥，`env_passthrough` 精确控制哪些变量能被 agent bash 子进程看到，Hermes 自身的 `redact_secrets` 机制在工具输出中自动脱敏。Hermes 适合单人使用，不暴露给多人环境。

**OpenClaw = 协作网关**。OpenClaw 不持有个人密钥，可以安全地开放到多人场景（团队共享、群组 bot 等）。它的配置和 agent 定义与个人身份无关，适合做工作流编排和多人任务分发。

简言之：**需要你的 key 的 → Hermes；可以给别人用的 → OpenClaw**。

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
