# myopenclaw

个人多 Agent 协作平台 —— Docker Compose 一键部署，整合 Hermes、Claude Code、OpenClaw 三个 AI Agent 框架，配合长期记忆、飞书/Discord 桥接、晨间三签、论文管线、水文智能问答等能力。数据留在本机，配置用 Git 管理，定期快照备份到云盘。

## 能力地图

| 能力 | 实现方式 | 依赖仓库 |
|------|----------|----------|
| 多 Agent 协作 | Hermes ×4（爱玛士 / 爱码士 / 道元 / finance）+ Claude Code + OpenClaw ×3（虾酱 / 知汛 / 天一） | — |
| 跨 Agent 长期记忆 | TDAI Memory L0→L3 分层管线，3 agent + CC飞总 共享（道元独立） | — |
| Zotero 文献系统 | zotero-mcp 12 tools（mylibrary 提供）+ paper pipeline（爱码士写入，道元只读） | [mylibrary](https://github.com/OuyangWenyu/mylibrary) |
| 飞书直连 | cc-connect（CC飞总）+ lark-cli（Hermes CLI）+ 道元 bot + zhixun 知汛 bot + tianyi 天一 bot | — |
| Discord 桥接 | Hermes coder（爱码士）+ OpenClaw 虾酱 | — |
| 晨间三签 | Hermes cron skill → TDAI + AgentOps 信号 → 飞书推送 | — |
| AI 情报聚合 | dailyinfo 多源抓取 + AI 摘要 → 飞书 / Discord 推送 | [dailyinfo](https://github.com/iHeadWater/dailyinfo) |
| 研发日报 | repo-scanner MCP 采集 27 仓库 → Hermes skill → 飞书推送；天一 bot 复用同源读能力 | [git-contribution-stats](https://gitcode.com/dlut-water/git-contribution-stats) |
| 水文智能问答 | zhixun 知汛助手 — OpenClaw + zhixun-water-mcp → 飞书 bot（独立栈） | [zhixun-agent](https://github.com/OuyangWenyu/zhixun-agent) |
| 论文管线 | paper-fetch 下载 → Google Drive 上传 → Zotero 入库 | — |
| 事务追踪 | aisecretary MCP 服务 → SQLite 持久化 | [aisecretary](https://github.com/iHeadWater/aisecretary) |
| 邮件 | himalaya CLI 邮件客户端（IMAP/SMTP 多账户；Outlook 走 ortie OAuth；仅爱玛士可用，其余 agent 拒绝访问） | — |
| 联系人 | cardamum CLI 联系人管理（vdir 后端，vCard） | — |
| Google Drive | rclone 直连云端上传论文 PDF | — |
| 语雀知识库 | Hermes + 天一 远程 MCP（读取/搜索/备份/变更报告） | [yuque_mcp_server](https://gitcode.com/dlut-water/yuque_mcp_server) |
| 云端备份 | 定时 rsync + sqlite3 热备 → 云盘（Google Drive / OneDrive） | — |
| 服务监控 | Uptime Kuma 面板 + Healthchecks.io 死士开关 + AgentOps 健康采集 | — |

## 仓库配合

```
dailyinfo/                 ← AI 情报聚合（RSS + AI 摘要）
aisecretary/               ← 事务数据库 MCP
git-contribution-stats/    ← 多仓库 Git 贡献统计
zhixun-agent/              ← 水文 MCP（zhixun 知汛 bot 构建依赖）
mylibrary/                 ← Zotero 文献工具（zotero-mcp 源码）
    │
    │  全部通过 Docker volume、build context 或 MCP 接入
    ▼
myopenclaw/  (本仓库)     ← Docker 编排 + 执行层 + 兼容层
    │
    ├─ hermes        (4 个 profile: default / coder / daoyuan / finance)
    ├─ claude-code   (CC飞总，飞书直连)
    ├─ openclaw      (虾酱，Discord 网关)
    ├─ openclaw-zhixun (知汛助手，飞书水文 bot，独立栈)
    ├─ openclaw-tianyi (天一，飞书研发 bot，独立栈共享主栈网络)
    ├─ zotero-mcp    (文献查询 MCP，mylibrary 源码)
    ├─ repo-scanner-mcp (研发日报 MCP)
    ├─ aisecretary   (事务数据库 MCP)
    ├─ tdai-memory   (Agent 长期记忆)
    ├─ freshrss      (RSS 聚合，dailyinfo 数据源)
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

如需启动 zhixun 飞书机器人（知汛助手）：

```bash
git clone https://github.com/OuyangWenyu/zhixun-agent.git ../zhixun-agent
cp .env.zhixun-bot.example .env.zhixun-bot  # 编辑填入飞书 App ID/Secret + DeepSeek Key
./scripts/start-zhixun-bot.sh --build
```

如需启动 tianyi 飞书机器人（天一·研发助手，前提：主栈已启动，共享 repo-scanner-mcp）：

```bash
cp .env.tianyi-bot.example .env.tianyi-bot  # 编辑填入飞书 App ID/Secret + 模型 key + GitHub/GitCode token
./scripts/start-tianyi-bot.sh --build
```

## 服务一览

| 服务 | 端口 | Compose 文件 | 说明 |
|------|------|-------------|------|
| hermes | 8642 | 主栈 | Hermes gateway — 爱玛士（默认 profile，飞书 bot） |
| hermes-coder | 8643 | 主栈 | Hermes coder — 爱码士（Discord bot + Zotero 写入 + 论文管线） |
| hermes-finance | 8644 | 主栈 | Hermes finance — 金融 agent（飞书 bot） |
| hermes-daoyuan | 8645 | 主栈 | 道元 — 文献学者 agent（飞书 bot，Zotero 只读） |
| hermes-dashboard | 9119 | 主栈 | Hermes Web 面板（只读） |
| claude-code | 9090 | 主栈 | Claude Code + cc-connect（CC飞总，飞书直连） |
| openclaw-gateway | 18789 | 主栈 | OpenClaw 网关（虾酱 Discord bot） |
| zotero-mcp | 8002 | 主栈 | Zotero 文献 MCP（12 tools，mylibrary 提供） |
| tdai-memory | 8420 | 主栈 | Agent 长期记忆 Gateway（L0→L3） |
| aisecretary | 8000 | 主栈 | 事务数据库 MCP 服务 |
| repo-scanner-mcp | 8001 | 主栈 | 研发日报 MCP 数据服务 |
| freshrss | 8081 | 主栈 | RSS 聚合（dailyinfo 数据源） |
| uptime-kuma | 3001 | 主栈 | 服务监控面板 |
| backup-cron | — | 主栈 | 定时快照备份 |
| zhixun-water-mcp | 18201 | zhixun 栈 | 水文 MCP（43 tools），兼容 zhixun-core v2 |
| openclaw-zhixun | 18791 | zhixun 栈 | 知汛助手 — OpenClaw 飞书 bot（独立网络 + 数据目录） |
| openclaw-tianyi | 18792 | tianyi 栈 | 天一研发助手 — OpenClaw 飞书 bot（共享主栈网络，复用 repo-scanner-mcp，gh/gc 写 GitHub/GitCode issue） |

外部接入服务：

| 服务 | 接入方式 | 说明 |
|------|----------|------|
| yuque-mcp | 远程 SSE（Hermes 经 `scripts/bootstrap_hermes.sh` 注册；天一经 `docker/tianyi-bot/openclaw.json.template` 注册） | 语雀知识库 MCP，服务端部署在服务器，通过 Bearer key 访问，skill 由 `skills/yuque-knowledge/` 挂载；每日变更日报由 `skills/yuque-daily-digest/` + Hermes cron 推送飞书私聊 |

## 目录结构

```
myopenclaw/
├── docker-compose.yml                # 主栈服务编排
├── docker-compose.zhixun-bot.yml     # zhixun 独立栈
├── docker-compose.tianyi-bot.yml     # tianyi 独立栈（共享主栈网络）
├── .env.example                      # 主栈环境变量模板
├── .env.zhixun-bot.example           # zhixun bot 环境变量模板
├── .env.tianyi-bot.example           # tianyi bot 环境变量模板
├── .cloud.conf.example               # 云盘路径模板
├── docs/                             # 文档（→ GitHub Pages）
├── docker/                           # 自定义镜像
│   ├── hermes/                       #   Hermes（cardamum + himalaya + ortie + lark-cli + rclone + mylibrary）
│   ├── claude-code/                  #   Claude Code + cc-connect
│   ├── backup-cron/                  #   定时备份
│   ├── tdai-memory/                  #   Agent 长期记忆
│   ├── repo-scanner-mcp/             #   研发日报 MCP
│   ├── zotero-mcp/                   #   Zotero 文献 MCP（mylibrary 源码）
│   ├── zhixun-bot/                   #   zhixun 水文 MCP + 兼容层
│   └── tianyi-bot/                   #   tianyi bot entrypoint + 配置模板 + 渲染脚本
├── openclaw-zhixun/workspace/        # zhixun bot agent 策略文件（AGENTS.md, SOUL.md）
├── openclaw-tianyi/workspace/        # tianyi bot agent 策略文件（AGENTS.md, SOUL.md）
├── hermes/                           # Hermes 配置模板 + 备份脚本
├── claude/                           # Claude Code / cc-connect 配置模板 + 备份脚本
├── openclaw/                         # OpenClaw 配置模板 + 备份脚本
├── scripts/                          # 运维脚本（启动/停止/备份/恢复/调度/监控/zhixun bot）
├── skills/                           # 执行层 skill（morning-triage-v2 等）
└── tests/                            # 集成测试
```

## 文档

完整文档 → **[ouyangwenyu.github.io/myopenclaw](https://ouyangwenyu.github.io/myopenclaw)**

- [快速开始](https://ouyangwenyu.github.io/myopenclaw/setup/) — 新机器从零到运行
- [架构](https://ouyangwenyu.github.io/myopenclaw/architecture/) — 服务拓扑、数据目录、安全边界
- [服务](https://ouyangwenyu.github.io/myopenclaw/hermes-channels/) — Hermes / OpenClaw 渠道、邮件、联系人、飞书 CLI
- [集成](https://ouyangwenyu.github.io/myopenclaw/dailyinfo/) — dailyinfo 调度、研发日报、zhixun 飞书机器人
- [运维](https://ouyangwenyu.github.io/myopenclaw/scheduling/) — 调度、备份、监控、AgentOps、DNS
- [可移植性](https://ouyangwenyu.github.io/myopenclaw/portability/) — 换电脑需要准备什么
- [备份系统](https://ouyangwenyu.github.io/myopenclaw/backup/) — 备份内容、恢复流程
- [语雀知识库接入](https://ouyangwenyu.github.io/myopenclaw/yuque-mcp-hermes/) — Hermes 接入远程语雀 MCP 服务（含每日变更推送 yuque-daily-digest）

本地预览文档：

```bash
uv sync --group docs
uv run mkdocs serve
```
