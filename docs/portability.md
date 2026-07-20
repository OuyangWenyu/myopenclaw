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
│   └── ~/code/git-contribution-stats/ ← build context for repo-scanner-mcp
│
├── [可选·软依赖·运行时 graceful skip]
│   ├── ~/code/myloop/               ← skills symlink 注入 CC飞总
│   └── ~/code/dailyinfo/            ← launchd 调度 + ai-news-weekly-polish skill
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

这两个仓库的 **build context 在 myopenclaw 仓库外**（`docker-compose.yml` 中 `context: ../xxx`）。`docker compose build` 时需要它们存在于同级目录：

| 仓库 | 期望路径 | 用途 |
|------|----------|------|
| [aisecretary](https://github.com/OuyangWenyu/aisecretary) | `~/code/aisecretary` | aisecretary MCP 服务镜像构建 |
| [git-contribution-stats](https://github.com/OuyangWenyu/git-contribution-stats) | `~/code/git-contribution-stats` | repo-scanner-mcp 镜像构建 |

**不需要 build 的情况**（`./scripts/start.sh` 不加 `--build`）：使用已有的 Docker 镜像即可，这两个仓库不需要存在。

## 软依赖：运行时 graceful skip

这些仓库缺失不会导致启动失败，但对应功能不可用：

| 仓库 | 期望路径 | 缺失时的影响 |
|------|----------|-------------|
| [myloop](https://github.com/OuyangWenyu/myloop) | `~/code/myloop` | 晨间三签、session-memory 等 loop 功能不可用。启动日志显示 `myloop 未挂载，跳过 skill 安装` |
| [dailyinfo](https://github.com/iHeadWater/dailyinfo) | `~/code/dailyinfo` | AI 情报聚合不可用。launchd 定时任务找不到可执行文件 |

## 一键克隆

```bash
./scripts/clone-deps.sh
```

此脚本克隆所有依赖仓库到正确路径。私有仓库需要 `gh auth login` 先。

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
| morning-triage 定时触发 | systemd user timer 或 cron |
| Healthchecks.io 心跳 | systemd timer 或 cron |
| collect-agentops 采集 | systemd timer 或 cron |

所有 launchd plist 模板在 `scripts/launchd/`，对应的 systemd 等价物尚未提供。

## Docker 路径

`/var/run/docker.sock` 在 macOS Docker Desktop 和 Linux 上路径相同，一般不需要修改。部分 Linux 发行版可能使用不同的 Docker socket 路径。

## 不在本仓库管理的内容

以下内容由各自的仓库独立管理，myopenclaw 只负责引用：

- dailyinfo 的 secret、数据源、业务逻辑 → dailyinfo 仓
- myloop 的 skill 设计、分类规则、ledger → myloop 仓
- git-contribution-stats 的采集逻辑、SQLite schema → git-contribution-stats 仓
- aisecretary 的 MCP tools 实现 → aisecretary 仓
