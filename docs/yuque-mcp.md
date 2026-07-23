# 语雀 MCP（Hermes）

myopenclaw 将兄弟仓库 `yuque_mcp_server` 作为可选 Docker 服务运行，仅由 Hermes 消费。MVP 不添加健康端点、Compose healthcheck、Uptime Kuma 监控或远程部署。

## 前提与依赖

- Docker Desktop 已运行。
- Windows 下使用 Git Bash 执行 `.sh` 脚本。
- 上游仓库位于 `../yuque_mcp_server`，固定 commit 为 `cc68fd0df172d3b8f24ae325998d56bdfd0e36e6`；该版本包含 Docker 上下文隔离、HTTP timeout/异步隔离、同仓库备份 single-flight、专用 non-root 用户、`hmac.compare_digest` 认证、UID/GID 映射、root fail-closed 和 Windows bind mount 宿主机权限模式。

执行 `./scripts/clone-deps.sh` 可克隆或核对依赖。已有仓库若偏离固定 commit，脚本只报告当前和目标 SHA，不会 reset、clean 或覆盖本地改动。

上述 Required 安全修复已形成上游真实 commit；依赖脚本和自动化测试均固定到该完整 SHA。

## 配置

在项目根目录 `.env` 中配置：

```dotenv
YUQUE_TOKEN=<只读语雀 Token>
MCP_API_KEY=<随机本地访问 Key>
YUQUE_MCP_PORT=18000
YUQUE_CHANGE_RETENTION_DAYS=30
YUQUE_CHANGE_DATA_PERMISSION_MODE=strict
YUQUE_MCP_UID=10001
YUQUE_MCP_GID=10001
```

`YUQUE_TOKEN` 只注入 `yuque-mcp`。Hermes 仅获得等价的本地 MCP 访问凭据，不获得语雀 Token。不要提交 `.env` 或在日志中输出这些值。

`YUQUE_CHANGE_DATA_PERMISSION_MODE` 默认使用 `strict`，要求容器能够施加 POSIX 权限。通过 Git Bash 执行 `start.sh` 时，Windows Docker Desktop 会自动改为 `host`，由 Windows ACL 管理 bind mount 权限；Linux/macOS 继续保持严格模式。

镜像默认以专用用户 `yuque`（UID/GID 10001）运行。Windows Docker Desktop 保持上述默认值；Linux/macOS 通过 `start.sh` 启动时会自动将 UID/GID 映射为当前用户，从而安全写入 bind mount，不需要 `sudo`、宿主机 `chown` 或放宽目录权限。直接使用 Compose 的 POSIX 用户应先执行 `export YUQUE_MCP_UID="$(id -u)" YUQUE_MCP_GID="$(id -g)"`。

Linux/macOS 不得以 root 执行 `start.sh`；脚本会在调用 Compose 前明确拒绝。即使绕过脚本并把 Compose UID/GID 设为 0，容器命令也会在读取凭据前退出。

## 选择性启动

推荐使用 Git Bash：

```bash
# 只构建并启动 Hermes、backup-cron 和语雀服务
./scripts/start.sh --build

# 不重建镜像
./scripts/start.sh
```

`--yuque` 参数为兼容旧命令而保留，但已不再需要。

直接使用 Compose 时必须显式写服务名：

```bash
YUQUE_MCP_ENABLED=true docker compose --profile yuque build hermes yuque-mcp
YUQUE_MCP_ENABLED=true docker compose --profile yuque up -d hermes backup-cron yuque-mcp
docker compose ps hermes backup-cron yuque-mcp
docker compose logs --tail=200 yuque-mcp
docker compose stop yuque-mcp
```

禁止使用无服务名的 `docker compose up`，否则会启动本项目其他非 profile 服务。宿主机只可通过 `http://127.0.0.1:${YUQUE_MCP_PORT:-18000}` 访问；Hermes 使用 Docker 内网地址 `http://yuque-mcp:18000/sse`。

## 数据目录

- 变化快照：`~/.myagentdata/yuque-mcp/change-data/`
- Markdown 备份：`~/.myagentdata/yuque-mcp/backups/`

目录在容器重建后保留，默认不加入 myopenclaw 云备份。不得把快照、正文或备份加入 Git。删除容器不会删除这些宿主机目录；只有确认不再需要数据后才能单独删除。

## Hermes 工具边界

Hermes 的 `yuque-knowledge` Skill 使用 6 个上游工具：`list_docs`、`get_doc_content`、`get_repo_toc`、`search_docs`、`backup_repo`、`collect_and_get_change_summary`。

Hermes 为 `yuque-mcp` 单独配置 900 秒工具调用超时，以支持大型知识库备份；该设置不影响其他 Hermes 工具。

- 完整目录使用 `get_repo_toc`。
- `list_docs` 和按标题工作的 `search_docs` 可能只覆盖前 100 篇。
- 读取正文前先缩小到指定文档。
- `backup_repo` 只在用户明确要求备份时调用。
- 变化摘要只代表相邻完整快照的净变化，不是完整版本历史。

## 验证与排障

```bash
./scripts/test-yuque-mcp-integration.sh
docker compose ps hermes backup-cron yuque-mcp
docker compose exec -T hermes /opt/hermes/.venv/bin/hermes mcp test yuque-mcp
```

容器为 `Up` 只证明进程运行；`hermes mcp test` 能发现 6 个工具才证明 MCP 连接可用；只有使用真实只读 Token 调用工具，才能证明真实语雀 API 可用。自动化契约测试不读取真实语雀正文。

自动化测试采用研发日报相同边界：检查 Skill、认证、工具发现、Hermes 真实 MCP loader 和五条确定性 mock 旅程。Hermes 的真实模型分析不使用 mock inference server，部署后再以只读方式人工验证；未执行时必须明确标为未验证。

常见问题：

- 提示 `YUQUE_TOKEN is required` 或 `MCP_API_KEY is required`：补齐项目根 `.env` 后重建服务。
- 返回 401：确认服务端 `MCP_API_KEY` 与 Hermes 的连接凭据一致，勿在终端打印值。
- Linux/macOS 写入数据目录失败：使用 `./scripts/start.sh` 让脚本自动映射当前 UID/GID；直接使用 Compose 时显式导出 `YUQUE_MCP_UID`/`YUQUE_MCP_GID`，不要使用 `sudo`、宿主机 `chown` 或 `chmod 777`。
- Hermes 未发现工具：确认使用了 `transport: sse` 和 `/sse` URL，然后重启 `hermes`。
- 上游仓库版本偏离：先检查本地改动，再按依赖脚本给出的命令人工切换；不要直接 reset/clean。

MVP 没有健康检查机制。故障判断依赖容器状态、日志与显式 MCP 工具发现测试。
