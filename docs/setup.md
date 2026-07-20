# 快速开始

从零到运行 myopenclaw 的完整流程。

## 前置要求

- Docker Desktop 已安装并运行
- 云盘客户端（Google Drive / OneDrive）已登录并完成本地同步（可选，用于备份）

## 1. 克隆仓库

```bash
git clone https://github.com/OuyangWenyu/myopenclaw.git
cd myopenclaw
```

## 2. 配置环境变量

```bash
cp .env.example .env
```

`.env` 中需要填写的项目：

| 变量 | 必填？ | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | **推荐** | DeepSeek API Key，Claude Code 和 OpenClaw 默认模型使用 |
| `GLM_API_KEY` | 可选 | 智谱 API Key，Hermes 备用模型 |
| `CC_CONNECT_FEISHU_APP_ID` / `SECRET` | 可选 | cc-connect 飞书应用凭证 |
| `GH_TOKEN` | 可选 | GitHub PAT，gh CLI 认证用 |
| `OPENCODE_API_KEY` | 可选 | opencode 专用 Key |
| `MOONSHOT_API_KEY` | 可选 | Moonshot API Key，OpenClaw 备份模型 |
| `UNPAYWALL_EMAIL` | 可选 | Unpaywall 联系邮箱 |
| `LARK_CLI_APP_ID` / `SECRET` | 可选 | lark-cli 主应用 |
| `LARK_CLI_IDM_APP_ID` / `SECRET` | 可选 | lark-cli 第二 profile |
| `DISCORD_BOT_TOKEN` | 可选 | Hermes coder Discord Bot Token |
| `UPK_USER` / `UPK_PASS` | 可选 | Uptime Kuma 管理员账号 |

### 配置云盘路径

```bash
cp .cloud.conf.example .cloud.conf
# 编辑 .cloud.conf，填写本机云盘实际路径
./scripts/setup-cloud.sh
```

## 3. 克隆依赖仓库（可选）

部分能力需要额外的仓库。跳过不影响核心服务运行：

```bash
./scripts/clone-deps.sh
```

依赖关系详见 [可移植性](portability.md)。

## 4. 启动服务

```bash
./scripts/start.sh
```

首次启动或镜像变更后加 `--build`：

```bash
./scripts/start.sh --build
```

### 首次启动自动完成的操作

| 自动操作 | 目标位置 |
|----------|----------|
| 创建 opencode 配置 | `~/.config/opencode/opencode.json` |
| 创建 Claude Code 配置 | `~/.claude/settings.json` |
| 创建 cc-connect 配置 | `~/.cc-connect/config.toml` |
| 创建 OpenClaw 配置 | `~/.openclaw/openclaw.json` |
| 安装 paper-fetch skill | `~/.openclaw/skills/paper-fetch` |
| 物化黑名单 API Key | 容器内 `/opt/data/secrets/` |
| 生成 himalaya 邮件配置 | `~/.hermes/.config/himalaya/config.toml` |
| 生成 cardamum 联系人配置 | `~/.hermes/home/.config/cardamum/config.toml` |
| 映射 DEEPSEEK_API_KEY → ANTHROPIC_API_KEY | claude-code 容器环境变量 |
| 初始化 lark-cli 配置 | `~/.lark-cli/` |

## 5. 从云盘恢复数据（可选）

新机器首次部署可跳过。从旧机器迁移时：

```bash
./scripts/restore.sh all latest
./scripts/restore.sh hermes latest
./scripts/restore.sh claude 2026-04-23_090000
```

如果恢复了 `~/.openclaw/openclaw.json` 或 `~/.cc-connect/config.toml`，start.sh 不会覆盖它们。

## 6. 配置渠道

按需配置：

- **cc-connect 飞书**：在飞书开发者后台创建应用 → 开启机器人 → 填入 `.env` 中 `CC_CONNECT_FEISHU_*` 凭证
- **OpenClaw 渠道**：详见 [OpenClaw 渠道](openclaw-channels.md)
- **Hermes 渠道**：详见 [Hermes 渠道](hermes-channels.md)
- **lark-cli 授权**：详见 [飞书 CLI](lark-cli.md)

## 日常命令

```bash
./scripts/start.sh                        # 启动所有服务
./scripts/stop.sh                         # 停止所有服务
docker compose ps                         # 查看服务状态
docker compose logs -f hermes             # 查看 Hermes 日志
docker compose logs -f claude-code        # 查看 Claude Code 日志
docker compose exec backup-cron /scripts/backup-all-docker.sh  # 手动备份
```
