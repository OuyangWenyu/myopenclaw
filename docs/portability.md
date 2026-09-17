# 可移植性

换台电脑拉起 myopenclaw 需要哪些准备？本页列出所有外部依赖和已知限制。

## 依赖图

```
myopenclaw (本仓库)
│
├── [必须] Docker Desktop
│
├── [可选·硬依赖·build 时需要]
│   ├── ~/code/aisecretary/          ← build context for aisecretary 服务
│   ├── ~/code/git-contribution-stats/ ← build context for repo-scanner-mcp
│   ├── ~/code/mylibrary/            ← 论文流水线代码（缺失时 build 从 GitHub clone）
│   └── ../zhixun-agent/             ← zhixun 栈 MCP 镜像的额外 build context
│
├── [可选·软依赖·运行时 graceful skip]
│   └── ~/code/dailyinfo/            ← 宿主机 launchd 调度（每日论文/资讯抓取推送）
│
├── [配置文件·需要手动创建]
│   ├── .env          (从 .env.example)
│   └── .cloud.conf   (从 .cloud.conf.example)
│
└── [数据·可从云盘恢复]
    ├── ~/.hermes/
    ├── ~/.claude/
    ├── ~/.openclaw/
    ├── ~/.cc-connect/
    └── ~/.myagentdata/
```

## 硬依赖：build 时需要的仓库

这些仓库的 **build context 在 myopenclaw 仓库外**（`docker-compose.yml` / `docker-compose.zhixun-bot.yml` 里写的是 `../xxx`）。`docker compose build` 时需要它们存在于指定路径：

| 仓库 | 期望路径 | 用途 |
|------|----------|------|
| [aisecretary](https://github.com/iHeadWater/aisecretary) | `~/code/aisecretary` | aisecretary MCP 服务镜像构建 |
| [git-contribution-stats](https://gitcode.com/dlut-water/git-contribution-stats) | `~/code/git-contribution-stats` | repo-scanner-mcp 镜像构建 |
| [mylibrary](https://github.com/OuyangWenyu/mylibrary) | `~/code/mylibrary` | hermes / zotero-mcp 镜像里的论文流水线（hydrolitagent + skills）。`start.sh` 先 rsync 进 build context；**本地没有时 Dockerfile 自动从 GitHub clone**，不会 build 失败 |
| [zhixun-agent](https://github.com/OuyangWenyu/zhixun-agent) | `../zhixun-agent` | zhixun 栈 `zhixun-water-mcp` 镜像的 `additional_contexts.zhixun_src`（可用 `ZHIXUN_AGENT_PATH` 覆盖）。只有启动 zhixun 栈时才需要 |

**不需要 build 的情况**（`./scripts/start.sh` 不加 `--build`）：使用已有的 Docker 镜像即可，aisecretary / git-contribution-stats 这两个仓库不需要存在。

## 软依赖：运行时 graceful skip

这些仓库缺失不会导致启动失败，但对应功能不可用：

| 仓库 | 期望路径 | 缺失时的影响 |
|------|----------|-------------|
| [dailyinfo](https://github.com/iHeadWater/dailyinfo) | `~/code/dailyinfo` | AI 情报聚合不可用。launchd 定时任务找不到可执行文件 |

## 一键克隆

```bash
./scripts/clone-deps.sh
```

此脚本克隆 aisecretary / git-contribution-stats / dailyinfo 三个仓库到正确路径。私有仓库需要 `gh auth login` 先。

`mylibrary` 与 `zhixun-agent` **不在**这个脚本里：前者缺失时 build 会自动从 GitHub clone；后者按需手动 clone（见[快速开始](setup.md)第 3 步）。

## 配置文件的机器差异

以下文件每台机器不同，不能直接复制：

| 文件 | 原因 |
|------|------|
| `.env` | API Key 可能不同（虽然可以用同一组 key） |
| `.cloud.conf` | 云盘本机同步路径因用户名和云盘服务而异 |
| `~/.hermes/.env` | 邮箱密码、飞书凭证等 |
| `~/.hermes/SOUL.md` | 人格描述，迁移时通过备份恢复 |
| `~/.hermes/config.yaml` | Hermes 网关配置，迁移时通过备份恢复 |

可以通过云盘备份恢复的数据见 [备份系统](backup.md)。

## macOS 特定

以下功能依赖 macOS launchd，在 Linux 上不可用：

| 功能 | 替代方案 (Linux) |
|------|-----------------|
| dailyinfo 定时调度 | systemd user timer |
| git-contribution-stats 采集 | systemd user timer |
| Healthchecks.io 心跳 | systemd timer 或 cron |
| collect-agentops 采集 | systemd timer 或 cron |

> **注意**：Hermes cron 任务（Daily Command Center、工作日晨间简报、daily-dev-report、yuque-daily-digest）运行在 Docker 容器内，不依赖 macOS launchd，Linux 上可直接使用。

所有 launchd 任务依赖 macOS launchd。Linux 上的替代方案见 [调度系统](scheduling.md) 的 Linux 等价物说明。

## Docker 路径

`/var/run/docker.sock` 在 macOS Docker Desktop 和 Linux 上路径相同，一般不需要修改。部分 Linux 发行版可能使用不同的 Docker socket 路径。

## 不在本仓库管理的内容

以下内容由各自的仓库独立管理，myopenclaw 只负责引用：

- dailyinfo 的 secret、数据源、业务逻辑 → dailyinfo 仓
- git-contribution-stats 的采集逻辑、SQLite schema → git-contribution-stats 仓
- aisecretary 的 MCP tools 实现 → aisecretary 仓
