# myopenclaw

用 Docker 运行 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 和 [OpenClaw](https://github.com/openclaw/openclaw)，数据留在本机（`~/.hermes`、`~/.openclaw`、`~/.myagentdata`），配置用 Git 管理，用户数据定期快照备份到云盘。

## 服务说明

| 服务 | 镜像 | 默认端口 | 说明 |
|------|------|----------|------|
| hermes | `nousresearch/hermes-agent:latest` | 8642 | Hermes gateway |
| hermes-dashboard | `nousresearch/hermes-agent:latest` | 9119 | Hermes Web 面板 |
| openclaw-gateway | `ghcr.io/openclaw/openclaw:latest` | 18789 | OpenClaw gateway |
| backup-cron | 自建 alpine 镜像 | — | 定时快照备份（默认每周日凌晨 2:00）|

数据目录映射：

- `~/.hermes` → `/opt/data`（hermes 容器内）
- `~/.openclaw` → `/home/node/.openclaw`（openclaw 容器内）
- `~/.myagentdata` → `/.myagentdata`（backup-cron 容器只读挂载，用于备份）

---

## 首次使用（新机器）

### 1. 前置要求

- Docker Desktop 已安装并运行
- 云盘客户端（Google Drive / OneDrive）已登录并完成本地同步

### 2. 克隆仓库

```bash
git clone https://github.com/OuyangWenyu/myopenclaw.git
cd myopenclaw
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 按需修改端口等配置（通常不用改）
```

### 4. 配置云盘路径

```bash
cp .cloud.conf.example .cloud.conf
# 编辑 .cloud.conf，填写本机云盘实际路径
```

```bash
# 验证云盘目录并初始化备份目录结构
./scripts/setup-cloud.sh
```

### 5. 从云盘快照恢复数据（如有）

```bash
# 恢复全部最新快照（hermes、openclaw、~/.myagentdata）
./scripts/restore.sh all latest

# 也可以只恢复某一个，或指定快照时间戳
./scripts/restore.sh hermes latest
./scripts/restore.sh openclaw 2026-04-23_090000
./scripts/restore.sh data latest
```

### 6. 启动服务

```bash
./scripts/start.sh
```

首次启动或更新镜像时加 `--build`：

```bash
./scripts/start.sh --build
```

---

## 日常操作

### 启动 / 停止

```bash
./scripts/start.sh   # 启动所有服务
./scripts/stop.sh    # 停止所有服务
```

### 查看服务状态

```bash
docker compose ps
docker compose logs -f hermes
docker compose logs -f openclaw-gateway
```

### 手动触发备份

```bash
# 进入 backup-cron 容器执行（推荐）
docker compose exec backup-cron /scripts/backup-all-docker.sh
```

快照保存在：`<云盘路径>/myopenclaw-backups/hermes/`、`.../openclaw/`、`.../data/`

每个快照为独立时间戳目录，同时维护一个 `latest/` 软链接。超过 `BACKUP_KEEP_DAYS`（默认 30 天）的旧快照自动清除。

### 运行 OpenClaw CLI

```bash
docker compose --profile cli run --rm openclaw-cli
```

---

## 目录结构

```ini
myopenclaw/
├── docker-compose.yml          # 服务编排
├── .env.example                # 环境变量模板（端口、cron 等）
├── .cloud.conf.example         # 云盘路径模板（本机路径，不入 git）
├── docker/
│   └── backup-cron/            # 定时备份容器（alpine + rsync + sqlite3）
├── hermes/
│   └── scripts/backup.sh       # hermes 数据选择性快照脚本
├── openclaw/
│   └── scripts/backup.sh       # openclaw 数据选择性快照脚本
└── scripts/
    ├── start.sh                # 启动服务
    ├── stop.sh                 # 停止服务
    ├── setup-cloud.sh          # 初始化云盘备份目录
    ├── backup-data.sh          # ~/.myagentdata 通用快照脚本
    ├── backup-all.sh           # 本机全量备份（不依赖容器）
    ├── backup-all-docker.sh    # 容器内备份（供 cron 调用）
    └── restore.sh              # 从快照恢复数据
```

## 备份内容说明

**Hermes 备份**：`config.yaml`、`SOUL.md`、`memories/`、`skills/`、`hooks/`、`cron/`

**OpenClaw 备份**：`openclaw.json`、`agents/`、`flows/`、`extensions/`、`memory/main.sqlite`（热备份）

**Data 备份**：`~/.myagentdata/` 整目录 rsync 快照。所有数据类应用统一放此目录的子目录下（如 `~/.myagentdata/aisecretary/`），无需额外配置即自动备份。

不备份：大型缓存、临时会话、auth token、日志等。
