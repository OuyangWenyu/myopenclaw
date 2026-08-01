# myopenclaw

个人多 Agent 协作平台 —— Docker Compose 一键部署，整合 Hermes、Claude Code、OpenClaw 三个 AI Agent 框架，配合长期记忆、飞书/Discord 桥接、每日三签、论文管线等能力。数据留在本机，配置用 Git 管理，定期快照备份到云盘。

## 能力地图

| 能力 | 实现方式 | 依赖仓库 |
|------|----------|----------|
| 多 Agent 协作 | Hermes ×3（默认/爱码士/finance）+ Claude Code + OpenClaw | — |
| 跨 Agent 长期记忆 | TDAI Memory L0→L3 分层管线，4 agent 双向共享 | — |
| 飞书直连 | cc-connect（Claude Code）+ lark-cli（Hermes） | — |
| Discord 桥接 | Hermes coder（爱码士）+ OpenClaw（虾酱） | — |
| 晨间三签 | Hermes cron skill → TDAI + AgentOps 信号 → 飞书推送 | — |
| AI 情报聚合 | dailyinfo 多源抓取 + AI 摘要 → 飞书推送 | [dailyinfo](https://github.com/iHeadWater/dailyinfo) |
| 研发日报 | repo-scanner MCP 采集 27 仓库 → Hermes skill → 飞书推送 | [git-contribution-stats](https://gitcode.com/dlut-water/git-contribution-stats) |
| 论文管线 | paper-fetch 下载 → Google Drive 上传 → Zotero 入库 | — |
| 事务追踪 | aisecretary MCP 服务 → SQLite 持久化 | [aisecretary](https://github.com/iHeadWater/aisecretary) |
| 服务健康监控 | AgentOps 采集（容器/备份/磁盘/网关信号）→ 晨间三签输入 | — |
| 云端备份 | 定时 rsync + sqlite3 热备 → 云盘（Google Drive / OneDrive） | — |
| 服务监控 | Uptime Kuma 面板 + Healthchecks.io 死士开关 | — |

## 仓库配合

```
dailyinfo/                 ← AI 情报聚合（RSS + AI 摘要）
aisecretary/               ← 事务数据库 MCP
git-contribution-stats/    ← 多仓库 Git 贡献统计
    │
    │  全部通过 Docker volume 或 build context 挂入
    ▼
myopenclaw/  (本仓库)     ← Docker 编排 + 执行层
    │
    ├─ hermes        (3 个 profile: default / coder / finance)
    ├─ claude-code   (CC飞总，飞书直连)
    ├─ openclaw      (虾酱，Discord 网关)
    ├─ tdai-memory   (Agent 长期记忆)
    ├─ backup-cron   (定时备份)
    └─ uptime-kuma   (服务监控)
```

- **所有数据落 `~/.myagentdata/`** — 备份管线自动覆盖
- **每个仓库独立克隆** — 放在 `~/code/<repo>`，Docker 通过 volume mount 访问

## 快速开始

```bash
# 1. 克隆本仓库
git clone https://github.com/OuyangWenyu/myopenclaw.git
cd myopenclaw

# 2. 配置环境变量
cp .env.example .env          # 编辑 .env，至少填入 DEEPSEEK_API_KEY
cp .cloud.conf.example .cloud.conf

# 3. （可选）克隆依赖仓库
./scripts/clone-deps.sh       # 克隆 dailyinfo、aisecretary、git-contribution-stats

# 4. 启动所有服务
./scripts/start.sh
```

首次启动自动完成：配置模板创建、API Key 物化、skill 安装。详见 [快速开始指南](https://ouyangwenyu.github.io/myopenclaw/setup/)。

镜像变更后需重新构建：

```bash
./scripts/start.sh --build
```

## 服务一览

| 服务 | 端口 | 说明 |
|------|------|------|
| hermes | 8642 | Hermes gateway（默认 profile） |
| hermes-coder | 8643 | Hermes coder profile（爱码士，Discord 接入） |
| hermes-finance | 8644 | Hermes finance profile |
| hermes-dashboard | 9119 | Hermes Web 面板（只读） |
| claude-code | 9090 | Claude Code + cc-connect 飞书直连 |
| openclaw-gateway | 18789 | OpenClaw gateway（虾酱 Discord bot） |
| tdai-memory | 8420 | Agent 长期记忆 Gateway（L0→L3） |
| aisecretary | 8000 | 事务数据库 MCP 服务 |
| repo-scanner-mcp | 8001 | 研发日报 MCP 数据服务 |
| freshrss | 8081 | RSS 聚合（dailyinfo 数据源） |
| uptime-kuma | 3001 | 服务监控面板 |
| backup-cron | — | 定时快照备份 |

## 目录结构

```
myopenclaw/
├── docker-compose.yml          # 服务编排
├── .env.example                # 环境变量模板
├── .cloud.conf.example         # 云盘路径模板
├── docs/                       # 文档（→ GitHub Pages）
├── docker/                     # 自定义镜像（hermes, claude-code, backup-cron, tdai-memory, repo-scanner-mcp）
├── hermes/                     # Hermes 配置模板 + 备份脚本
├── claude/                     # Claude Code / cc-connect 配置模板 + 备份脚本
├── openclaw/                   # OpenClaw 配置模板 + 备份脚本
├── scripts/                    # 运维脚本（启动/停止/备份/恢复/调度/监控）
├── skills/                     # 执行层 skill
└── tests/                      # 集成测试
```

## 文档

### GitCode CLI（可选）

如需让 OpenClaw 或 Hermes 通过 `gc` 操作 GitCode，先编译并安装 CLI：

```bash
git clone https://gitcode.com/gitcode-cli/cli.git
cd cli
go env -w GOPROXY=https://goproxy.cn,direct
go build -o gc ./cmd/gc
mkdir -p ~/.openclaw/bin
mv gc ~/.openclaw/bin/gc
chmod +x ~/.openclaw/bin/gc
```

在项目 `.env` 中配置 `GITCODE_TOKEN`，并重新创建相关容器：

```bash
docker compose up -d --force-recreate hermes openclaw-gateway openclaw-cli
docker compose exec openclaw-gateway gc auth status
```

配置目录为 `~/.config/gc`，CLI 文件为 `~/.openclaw/bin/gc`，两者都会持久化在宿主机。

完整文档 → **[ouyangwenyu.github.io/myopenclaw](https://ouyangwenyu.github.io/myopenclaw)**

- [快速开始](https://ouyangwenyu.github.io/myopenclaw/setup/) — 新机器从零到运行
- [架构](https://ouyangwenyu.github.io/myopenclaw/architecture/) — 服务拓扑、数据目录、安全边界
- [可移植性](https://ouyangwenyu.github.io/myopenclaw/portability/) — 换电脑需要准备什么
- [备份系统](https://ouyangwenyu.github.io/myopenclaw/backup/) — 备份内容、恢复流程

本地预览文档：

```bash
uv sync --group docs
uv run mkdocs serve
```
