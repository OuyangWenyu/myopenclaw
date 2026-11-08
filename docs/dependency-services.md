# 依赖服务容器化

> 最后更新：2026-11-08（aisecretary / dailyinfo 的两种托管方式）

本仓依赖若干兄弟仓库提供的服务。按「谁负责拉起」分成两种模式，新增依赖服务时先选模式再写 compose。

## 模式一：主 compose 内构建（aisecretary 等）

服务直接写在 `docker-compose.yml` 里，构建上下文指向兄弟仓，镜像归 myopenclaw 命名：

```yaml
aisecretary:
  build:
    context: ../aisecretary
    dockerfile: Dockerfile
  image: myopenclaw/aisecretary:latest
  container_name: aisecretary
  restart: unless-stopped
  ports:
    - "8000:8000"
  volumes:
    - ${HOME}/.myagentdata/aisecretary:/data
  networks:
    - myopenclaw-net
```

- `./scripts/start.sh` 的 `docker compose up -d` 会一并拉起
- `./scripts/stop.sh` 会一并停止
- `./scripts/clone-deps.sh` 负责克隆兄弟仓（build context 缺失时构建失败）
- 数据落在 `~/.myagentdata/<service>/`，由 `backup-data.sh` 覆盖
- 适用：服务生命周期与主栈一致、没有独立部署需求

当前使用该模式的服务：`aisecretary`、`repo-scanner-mcp`、`yuque-mcp`、`zotero-mcp`、`paper-queue-mcp`。

## 模式二：独立 compose 栈（dailyinfo）

服务有自己的 `compose.yml`、`.env`、secrets 和数据根，在兄弟仓里独立部署；myopenclaw 只负责**顺手拉起**和 **MCP 消费**：

```text
dailyinfo compose（~/code/dailyinfo/compose.yml）
├─ freshrss        RSS 采集，127.0.0.1:8081
├─ scheduler       supercronic：5 个 run + 3 个 Discord push
├─ collector       手工排障（profiles: [jobs]）
└─ dailyinfo-mcp   只读 MCP，127.0.0.1:8768
```

myopenclaw 侧只做三件事：

1. **启动**：`scripts/start.sh` 在拉起主栈前执行
   ```bash
   (cd ~/code/dailyinfo && docker compose up -d)
   ```
   由 `.env` 的 `DAILYINFO_LOCAL_STACK` 控制：`1`（默认）= 本机存在就拉起；`0` = 跳过（dailyinfo 部署在远程 / 81）。
   失败只警告，**不阻断主栈**。
2. **不停止**：`scripts/stop.sh` 只停 myopenclaw；dailyinfo 是独立栈，需要时手动 `cd ~/code/dailyinfo && docker compose stop`。
3. **MCP 客户端**：道元（Hermes daoyuan）与虾酱（OpenClaw 主网关）通过 `DAILYINFO_MCP_URL` / `DAILYINFO_MCP_KEY` 读取；注册脚本为 `scripts/ensure_hermes_dailyinfo.py` / `scripts/ensure_openclaw_dailyinfo.py`，由 `start.sh` 幂等调用。

数据根仍是 `~/.myagentdata/dailyinfo`（沿用历史 `users/owen/db.sqlite`），`backup-data.sh` 继续覆盖（`HOT_DBS_RO` 里的 freshrss 通配仍有效）。

### 为什么 dailyinfo 不并进主 compose

- dailyinfo 要能**独立部署到 81**，它自己的 compose 是那份部署的唯一来源；并进主 compose 会出现两套定义，容易漂移
- `DISCORD_BOT_TOKEN` 撞名：myopenclaw 里是爱码士的 bot，dailyinfo 里是推送 bot，值不同
- scheduler 的 UID 33、`read_only`、`cap_drop`、healthcheck、profile 等约束需要逐条重对齐
- 独立栈的 stop/restart 边界更清晰：停 agent 不会误停采集/推送

## 镜像管理（后续）

当前镜像来源：

| 服务 | 镜像 | 构建位置 |
|---|---|---|
| aisecretary | `myopenclaw/aisecretary:latest` | myopenclaw compose `build.context: ../aisecretary` |
| dailyinfo producer | `dailyinfo-producer:<tag>` | dailyinfo `Dockerfile.producer` |
| dailyinfo MCP | `dailyinfo-mcp:<tag>` | dailyinfo `Dockerfile.mcp` |

dailyinfo 已预留两个镜像变量（`dailyinfo/.env`）：

```env
DAILYINFO_IMAGE=dailyinfo-producer:dev
DAILYINFO_MCP_IMAGE=dailyinfo-mcp:local
```

后续统一管理的路径（尚未定案）：

1. 把 dailyinfo 镜像打成 `myopenclaw/dailyinfo-producer:latest` / `myopenclaw/dailyinfo-mcp:latest`，与 aisecretary 命名一致
2. 推送到 GHCR / 私有 registry，dailyinfo `.env` 改成 `ghcr.io/...`；`start.sh` 在 `up -d` 前加 `docker compose pull`
3. 需要考虑的问题：用户/权限（谁能拉镜像、谁能写 dailyinfo 数据）、服务数量多、版本回滚、密钥分发

在这些定案前，保持「兄弟仓 build context + 本地 tag」即可，不引入 registry 依赖。

## 新增依赖服务检查清单

- [ ] 选模式：生命周期跟主栈一致 → 模式一；要独立部署/独立 stop → 模式二
- [ ] 数据目录落在 `~/.myagentdata/<service>/`
- [ ] 活 SQLite 进 `backup-data.sh` 的热备清单（WAL vs 回滚模式）
- [ ] MCP 服务：客户端注册脚本 + `${VAR}` 占位符 + `start.sh` 调用 + 守卫测试
- [ ] 监控：Uptime Kuma HTTP/Docker 监控项写进 `setup-uptime-kuma.sh`
- [ ] 文档：本页 + 对应服务页 + `mkdocs.yml` nav
