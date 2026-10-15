# myopenclaw

个人多 Agent 协作平台，基于 Docker Compose 一键部署。本仓库有**两条线**：个人栈（hermes 全家桶 + 个人服务，面向一人）与**服务器侧团队能力**（[天一](server-deploy.md) + 三项团队服务）。

## 我该从哪开始

| 你是 | 从这里开始 |
|---|---|
| **团队成员**（想用团队能力） | 飞书里直接问 **天一**（零配置）；想接进自己的 agent → [部署到服务器时要看什么](server-deploy.md) |
| **部署服务器**（团队侧） | [部署到服务器时要看什么](server-deploy.md) —— 先搞清"服务器 vs 个人栈"的分界 |
| **部署个人栈**（本仓库主 compose） | [快速开始](setup.md) → [架构](architecture.md) |
| **了解全貌** | [架构](architecture.md) |

## 开始

- [快速开始](setup.md) — 新机器从零到运行
- [架构](architecture.md) — 服务拓扑、数据目录、安全边界
- [可移植性](portability.md) — 换电脑需要准备什么
- [Linux 服务器部署](linux-server-setup.md) — Linux 环境配置指南

## 团队能力（服务器侧）

- [部署到服务器时要看什么](server-deploy.md) — 服务器侧 vs 个人栈的分界（**先看这个**）
- [member-check 自检](member-check.md) — 三项团队能力的连通 + 鉴权自检工具
- [服务器监控 MCP](server-monitoring-mcp.md) — 天一接入团队 server-monitoring（TLS + Bearer 只读）
- [zhixun 知汛助手](zhixun-feishu-bot.md) — 水文智能问答公共 bot（OpenClaw 栈；服务器部署文档待补）

## Agent 与渠道

- [Hermes 渠道](hermes-channels.md) — 飞书/钉钉/Discord 消息平台配置
- [OpenClaw 渠道](openclaw-channels.md) — Discord/飞书渠道配置
- [Hermes Skill 机制](hermes-skills.md) — live 执行源与仓库版本源的关系
- [飞书 CLI](lark-cli.md) — lark-cli 飞书命令行工具

## 能力服务

- [TDAI 长期记忆](tdai-memory.md) — Agent 跨会话记忆系统
- [语雀知识库（本机）](yuque-mcp-hermes.md) — 本机 yuque-mcp 服务（Hermes/天一接入，含每日变更推送）
- [Zotero 文献系统](zotero-cli-cc.md) — Zotero MCP 共享服务 + paper pipeline + 道元文献 Agent
- [论文清单](paper-queue.md) — 论文请求只记不下（独立 MCP 容器）
- [AI 秘书](aisecretary.md) — 事务数据库 MCP 服务
- [dailyinfo 调度](dailyinfo.md) — AI 情报聚合与定时推送
- [研发日报](daily-dev-report.md) — 多仓库每日采集 + 飞书推送
- [千问办公 AI听记](qwennote-mcp-hermes.md) — 爱玛士接入听记 MCP（OAuth，仅默认 profile）

## 运维

- [调度系统](scheduling.md) — 全部定时任务总览与安装
- [备份系统](backup.md) — 快照备份与恢复
- [服务监控](monitoring.md) — Uptime Kuma + Healthchecks.io
- [AgentOps 健康采集](agentops.md) — 系统健康信号自动采集
- [DNS 配置](dns-setup.md) — 中国域名解析

## 工具

- [Google Drive](google-drive-rclone.md) — rclone 论文上传配置
- [通讯录](contacts.md) — cardamum CLI 联系人管理
- [邮件](email.md) — himalaya CLI 邮件客户端
