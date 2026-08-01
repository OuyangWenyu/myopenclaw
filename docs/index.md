# myopenclaw

个人多 Agent 协作平台，基于 Docker Compose 一键部署。

## 这是什么

myopenclaw 用 Docker 运行三个 AI Agent 框架 — [Hermes Agent](https://github.com/NousResearch/hermes-agent)、[Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview)、[OpenClaw](https://github.com/openclaw/openclaw) — 并通过长期记忆、飞书/Discord 桥接、定时任务等将它们整合成一个协作系统。

## 核心能力

- **多 Agent 协作**：Hermes ×4（默认/爱码士/finance/道元）+ Claude Code + OpenClaw，各自负责不同领域
- **跨 Agent 长期记忆**：TDAI Memory L0→L3 分层管线，飞书说的 Discord 能召回
- **飞书 + Discord 双通道**：cc-connect（飞书长连接）+ OpenClaw（Discord bot）
- **自动化工作流**：晨间三签、AI 情报聚合、研发日报、论文管线
- **数据安全**：数据全在本机，配置 Git 管理，定时快照备份到云盘

## 快速导航

- [快速开始](setup.md) — 新机器从零到运行
- [架构](architecture.md) — 服务拓扑、数据目录、安全边界
- [可移植性](portability.md) — 换电脑需要准备什么
- [Hermes 渠道](hermes-channels.md) — 飞书/钉钉/Discord 消息平台配置
- [OpenClaw 渠道](openclaw-channels.md) — Discord/飞书渠道配置
- [TDAI 长期记忆](tdai-memory.md) — Agent 跨会话记忆系统
- [调度系统](scheduling.md) — 全部 14 个定时任务总览与安装
- [备份系统](backup.md) — 快照备份与恢复
- [服务监控](monitoring.md) — Uptime Kuma + Healthchecks.io
- [AgentOps 健康采集](agentops.md) — 系统健康信号自动采集
- [Zotero 文献系统](zotero-cli-cc.md) — Zotero MCP 共享服务 + paper pipeline + 道元文献 Agent
- [AI 秘书](aisecretary.md) — 事务数据库 MCP 服务
- [研发日报](daily-dev-report.md) — 27 仓库每日采集 + 飞书推送
- [Google Drive](google-drive-rclone.md) — rclone 论文上传配置
- [通讯录](contacts.md) — cardamum CLI 联系人管理
- [邮件](email.md) — himalaya CLI 邮件客户端
- [DNS 配置](dns-setup.md) — 中国域名解析
- [Linux 服务器部署](linux-server-setup.md) — Linux 环境配置指南
