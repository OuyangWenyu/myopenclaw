# 部署到服务器（团队侧）时要看什么

**一句话**：服务器上部署的是「**天一 + 三项团队服务**」这一条线。**hermes 全家桶、虾酱、TDAI、文献、邮箱等属于个人栈 —— 服务器上不需要、也不要部署。**

## 两条线，别混

| | 服务器侧（团队） | 个人栈（本仓库其余部分） |
|---|---|---|
| 组成 | **天一**（公共 bot）+ 三个团队 MCP 服务 | hermes 四件套、虾酱、TDAI 记忆、Zotero、论文清单、邮箱、通讯录… |
| 面向 | 全团队（飞书里直接用） | 个人（Owen） |
| 运行位置 | 团队服务器 | 本机（本仓库主 compose 一键起） |
| 文档 | 本页 + [member-check](member-check.md) + 各服务自己的仓库文档 | 本仓库其余文档（[快速开始](setup.md)、[架构](architecture.md)…） |

## 服务器上到底部署什么

1. **三项团队服务**（语雀 MCP / git-contribution-stats / 服务监控）
   - 统一 Bearer token 认证；各自独立仓库与部署文档（GitCode `dlut-water` 组织）
   - 数据用内置 SQLite（不引 PostgreSQL）；`.env` 不入库、按需手动同步
2. **天一**（公共 bot）—— `docker/tianyi-bot/`（entrypoint / 配置模板 / 渲染器）
   - ⚠️ **前置**：天一的 compose 声明**外部网络** `myopenclaw_myopenclaw-net`（本机由主栈创建）——
     干净服务器上先 `docker network create myopenclaw_myopenclaw-net`（或随团队服务栈一起建），
     否则 `up -d` 直接失败
   - 它消费上面三项服务；端点不在同网时用环境变量覆盖，**零代码**：
     - `TIANYI_BOT_REPO_SCANNER_MCP_URL`：未设/空 = **回落** `http://repo-scanner-mcp:8001/mcp`
       ——仅"与 repo-scanner 同网"时有意义；异地部署**必须**覆盖
     - `TIANYI_BOT_YUQUE_MCP_URL` / `TIANYI_BOT_SERVER_MONITORING_MCP_URL`：未设 = **能力关闭**
       （渲染时摘除对应条目，不是回落）——与 `.env.tianyi-bot.example` 注释一致
3. **member-check**（可选）—— 成员/验收的连通自检工具，见 [member-check](member-check.md)

## 不需要什么（常见混淆）

- ❌ **hermes 四个容器**（爱玛士 / 爱码士 / 道元 / finance）—— 个人栈，服务器不部署
- ❌ **虾酱 / 知汛** —— 各有自己的运行位置，与此迁移无关
- ❌ **TDAI 记忆 / Zotero / paper-queue / 邮箱 / 通讯录 / Google Drive** —— 个人服务

> 完整的分步部署操作指南将在迁移落地时补齐（2026-10-14：迁移进行中）。
> 迁移期间的历史数据（语雀变更库与快照、仓库统计 SQLite）与**宿主侧采集调度**需随服务一并搬移，别只搬容器。
