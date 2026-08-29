# 架构

myopenclaw 由主栈 14 个 Docker 服务 + zhixun 独立栈 2 个服务 + tianyi 独立栈 1 个服务组成（不含 profile-gated 的 openclaw-cli）。主栈运行在共享的 `myopenclaw-net` 桥接网络上，zhixun 栈运行在独立的 `zhixun-bot-net` 上；tianyi 栈为独立 Compose 但**共享主栈 `myopenclaw-net`**（复用 repo-scanner-mcp）。

## 服务拓扑

### 核心服务

| 服务 | 镜像 | 端口 | 说明 |
|------|------|------|------|
| hermes | 自建（基于 `nousresearch/hermes-agent:latest`） | 8642 | Hermes gateway，默认 profile（爱玛士，`mimo-v2.5` 多模态） |
| hermes-coder | 同 hermes 镜像 | 8643 | 爱码士，coder profile，飞书+Discord 双通道（`mimo-v2.5-pro`） |
| hermes-daoyuan | 同 hermes 镜像 | 8645 | 道元·文献学者，daoyuan profile，飞书群开放访问 |
| hermes-finance | 同 hermes 镜像 | 8644 | 财经助手，finance profile |
| hermes-dashboard | `nousresearch/hermes-agent:latest` | 9119 | Hermes Web 面板（只读） |
| claude-code | 自建（基于 `ubuntu:24.04`） | 9090 | Claude Code + cc-connect 飞书直连（默认 `deepseek-v4-flash`，Opus 层 `deepseek-v4-pro`） |
| openclaw-gateway | `ghcr.io/openclaw/openclaw:latest` | 18789 | OpenClaw gateway，虾酱 Discord bot（`mimo-v2.5` + TTS 语音回复） |

### 数据与支撑服务

| 服务 | 端口 | 说明 |
|------|------|------|
| zotero-mcp | 8002 | Zotero 文献 MCP 服务，12 个 tools（mylibrary 提供） |
| tdai-memory | 8420 | Agent 长期记忆 Gateway，L0→L3 分层管线 |
| aisecretary | 8000 | 事务数据库 MCP 服务，7 个 tools，SQLite 持久化 |
| repo-scanner-mcp | 8001 | 研发日报 MCP 数据服务，来自 git-contribution-stats |
| freshrss | 8081 | RSS 聚合，dailyinfo 数据源 |
| uptime-kuma | 3001 | 服务监控面板，HTTP + Docker 容器状态 |
| backup-cron | — | 定时快照备份 |

### zhixun 独立栈（`docker-compose.zhixun-bot.yml`，`zhixun-bot-net` 网络）

| 服务 | 端口 | 说明 |
|------|------|------|
| zhixun-water-mcp | 18201 | 水文 MCP 服务（43 tools），包装 zhixun-agent 源码 + v2 兼容层 |
| openclaw-zhixun | 18791 | OpenClaw gateway（知汛助手），飞书 bot，仅 MCP 工具无代码执行 |

### tianyi 独立栈（`docker-compose.tianyi-bot.yml`，共享 `myopenclaw-net`）

| 服务 | 端口 | 说明 |
|------|------|------|
| openclaw-tianyi | 18792 | OpenClaw gateway（天一·研发助手），飞书 bot（`deepseek-v4-flash`），terminal + MCP profile：读走共享 repo-scanner-mcp，写走 `gh`/`gc` CLI 创建 GitHub/GitCode issue，另接远程语雀 MCP |

## 数据目录映射

所有持久化数据在宿主机，通过 Docker volume 挂载：

| 宿主机路径 | 容器内路径 | 容器 | 说明 |
|------------|-----------|------|------|
| `~/.hermes` | `/opt/data` | hermes 四兄弟 | Hermes 全部数据 |
| `~/.claude` | `/opt/claude-config` | claude-code | Claude Code 配置和凭证 |
| `~/.cc-connect` | `/opt/cc-config` | claude-code | cc-connect 配置 |
| `~/.openclaw` | `/home/node/.openclaw` | openclaw | OpenClaw 配置和 memory |
| `~/.myagentdata/tdai-memory` | `/opt/data/tdai-memory` | tdai-memory | L0→L3 记忆数据 |
| `~/.myagentdata/aisecretary` | `/data` | aisecretary | 事务 SQLite |
| `~/.myagentdata/repo-scanner` | `/data` | repo-scanner-mcp | 研发日报 SQLite（只读） |
| `~/.myagentdata/dailyinfo` | — | freshrss | RSS 数据 |
| `~/.config/gh` | `/opt/gh-config` | hermes, claude-code | GitHub CLI 认证 |
| `~/.config/opencode` | `/opt/opencode-config` | hermes | opencode 配置 |
| `~/.lark-cli` | `/opt/lark-config` | hermes | lark-cli 配置 |
| `~/.uptime-kuma` | `/app/data` | uptime-kuma | 监控 SQLite + 配置 |
| `~/.openclaw-zhixun` | `/home/node/.openclaw` | openclaw-zhixun | zhixun bot 配置和插件 |
| `~/.openclaw-zhixun-mcp` | `/var/lib/zhixun-water-mcp` | zhixun-water-mcp | 水文站点名称索引缓存 |
| `~/code` + `~/Code` | `/home/node/code` + `/home/node/Code` | claude-code | 代码仓库 |

## 安全边界

三个框架在本项目中承担不同角色，密钥隔离策略不同：

- **Hermes = 个人助手**。持有 GitHub、飞书、邮箱等个人身份和密钥。`env_passthrough` 精确控制哪些变量能被 agent bash 子进程看到，`redact_secrets` 机制自动脱敏。适合单人使用，不暴露给多人环境。

- **Claude Code = 编码 Agent**。通过 cc-connect 直连飞书，专注于代码任务。持有 `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY`，独立于 Hermes。

- **OpenClaw 虾酱 = 协作网关**。不持有个人密钥，可安全开放到多人场景。配置与个人身份无关，适合工作流编排。

- **OpenClaw 知汛 = 独立水文 bot**。完全隔离的 Compose 栈，独立网络、独立飞书应用凭据、独立模型 API Key。不接入主栈的 Hermes、长期记忆或任何其他共享服务。仅开放 MCP 查询工具，写工具默认关闭。

**简言之：需要你的 key 的 → Hermes / Claude Code；可以给别人用的 → OpenClaw 虾酱；查水文数据的 → OpenClaw 知汛。**

## 密钥传递机制

被 Hermes 黑名单拦截的密钥（DEEPSEEK、OPENROUTER、OPENAI）通过特殊管道传递：

1. `.env` 变量 → `docker-compose.yml` env
2. 容器 entrypoint 脚本写入 `/opt/data/secrets/` 文件
3. opencode.json 通过 `{file:路径}` 引用

其他密钥（GH_TOKEN、OPENCODE_API_KEY、LARK_CLI_*）直接通过 env 传递。

## 网络

主栈所有服务在 `myopenclaw-net` 桥接网络上，通过 Docker DNS（容器名）互相访问。zhixun 栈使用独立的 `zhixun-bot-net` 桥接网络，与主栈物理隔离。部分服务需要访问外部 Chinese 域名时，可能需要配置 DNS —— 详见 [DNS 配置](dns-setup.md)。

## 容器内路径注意事项

不同容器的 HOME 不同：

| 容器 | HOME |
|------|------|
| hermes | `/opt/data`（实际 `/root`） |
| claude-code | `/home/node` |
| openclaw | `/home/node` |
| backup-cron | `/root` |

备份脚本中引用的路径需要对应各容器的 HOME。
