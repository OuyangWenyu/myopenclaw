# 部署到服务器（团队侧）时要看什么

**一句话**：服务器上部署的是「**公共 bot（天一先，知汛随后）+ 团队服务（语雀 MCP / 仓库统计 / 服务监控 / 水文 MCP）**」这一条线。**hermes 全家桶、虾酱、TDAI、文献、邮箱等属于个人栈 —— 服务器上不需要、也不要部署。**

## 两条线，别混

| | 服务器侧（团队） | 个人栈（本仓库其余部分） |
|---|---|---|
| 组成 | **天一 / 知汛**（两个公共 bot，均 OpenClaw 栈）+ 团队 MCP 服务（语雀 / 仓库统计 / 服务监控 / 水文 MCP） | hermes 四件套、虾酱、TDAI 记忆、Zotero、论文清单、邮箱、通讯录… |
| 面向 | 全团队（飞书里直接用） | 个人（Owen） |
| 运行位置 | 团队服务器 | 本机（本仓库主 compose 一键起） |
| 文档 | 本页 + [member-check](member-check.md) + 各服务自己的仓库文档 | 本仓库其余文档（[快速开始](setup.md)、[架构](architecture.md)…） |

## 服务器上到底部署什么

分两层看：**能力层 = 团队服务**（数据与能力的家），**接入层 = 公共 bot**（飞书入口）——
bot 是能力层的客户端，别把『天一接了哪三个』当成团队服务的全集。

**能力层——团队服务**（均独立仓库，GitCode `dlut-water` 组织）

1. **语雀 MCP / git-contribution-stats / 服务监控** —— **成员可直连的三项**
   （统一 Bearer token 认证、数据按迁移定案用内置 SQLite 不引 PostgreSQL、`.env` 不入库按需手动同步；
   [member-check](member-check.md) 检查的就是这三项）
2. **水文 MCP（zhixun-agent）** —— 知汛背后的水文能力，**同为团队服务**（与上三项并列，不是 bot 的附属品）
   - 认证：远程服务使用 `Authorization: Bearer`（`ZHIXUN_BOT_WATER_MCP_TOKEN`），
     知汛 bot 默认直连远程端点（`ZHIXUN_BOT_WATER_MCP_URL`），不再依赖本机 MCP 容器；
     优先 HTTPS，明文 HTTP 仅限可信内网；站名索引 JSON 落盘（非 SQLite）
   - 本地 `zhixun-water-mcp` 容器保留为 `--with-local-mcp` 调试回退（无认证），
     默认不启动；镜像构建逻辑在 `docker/zhixun-bot/Dockerfile.mcp`
   - 暂不进成员直连清单——水文走知汛 bot

**接入层——公共 bot**（均 OpenClaw 栈，不是 Hermes）

1. **天一**（公共 bot）—— `docker/tianyi-bot/`（entrypoint / 配置模板 / 渲染器）
   - ⚠️ **前置**：天一的 compose 声明**外部网络** `myopenclaw_myopenclaw-net`（本机由主栈创建）——
     干净服务器上先 `docker network create myopenclaw_myopenclaw-net`（或随团队服务栈一起建），
     否则 `up -d` 直接失败
   - 它消费成员直连三项；端点不在同网时用环境变量覆盖，**零代码**：
     - `TIANYI_BOT_REPO_SCANNER_MCP_URL`：未设/空 = **回落** `http://repo-scanner-mcp:8001/mcp`
       ——仅"与 repo-scanner 同网"时有意义；异地部署**必须**覆盖
     - `TIANYI_BOT_YUQUE_MCP_URL` / `TIANYI_BOT_SERVER_MONITORING_MCP_URL`：未设 = **能力关闭**
       （渲染时摘除对应条目，不是回落）——与 `.env.tianyi-bot.example` 注释一致
2. **知汛**（公共 bot，水文智能问答）—— 消费水文 MCP（zhixun-agent）；同属团队侧、**同样要部署到服务器**
   - 与天一一样是 **OpenClaw 栈**（不是 Hermes）；独立 compose 栈（`docker-compose.zhixun-bot.yml`）+ 水文 MCP 容器（zhixun-water-mcp）
   - ⏳ 服务器侧分步部署清单待补（现有文档以本机栈为主、含「服务器准备」章节：[zhixun 知汛助手](zhixun-feishu-bot.md)）

**附带（可选）**：member-check —— 成员/验收的连通自检工具，见 [member-check](member-check.md)

## 不需要什么（常见混淆）

- ❌ **hermes 四个容器**（爱玛士 / 爱码士 / 道元 / finance）—— 个人栈，服务器不部署
- ❌ **虾酱** —— 个人栈的 Discord 网关，与此迁移无关（⚠️ 知汛不在此列：它是团队侧公共 bot，见上）
- ❌ **TDAI 记忆 / Zotero / paper-queue / 邮箱 / 通讯录 / Google Drive** —— 个人服务

> 完整的分步部署操作指南将在迁移落地时补齐（2026-10-14：迁移进行中）。
> 迁移期间的历史数据（语雀变更库与快照、仓库统计 SQLite）与**宿主侧采集调度**需随服务一并搬移，别只搬容器。
> server-monitoring 的存储现状（可选 psycopg 依赖）与「内置 SQLite」定案存在差距，随迁移核对。
