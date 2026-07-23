# Spec: 将语雀 MCP 服务接入 myopenclaw，供 Hermes 使用

**Issue**：[GitHub #51](https://github.com/OuyangWenyu/myopenclaw/issues/51)
**PRD**：[`.claude/prds/yuque-mcp-integration.prd.md`](../prds/yuque-mcp-integration.prd.md)
**状态**：IMPLEMENTED / AUTOMATION VERIFIED — deployment-time manual read-only validation pending
**实现仓库**：`myopenclaw`，以及已获授权的 `yuque_mcp_server` Required 安全修复

## Objective

将固定版本的 `yuque_mcp_server` 作为可选、独立 Docker 服务接入 myopenclaw，使 Hermes 能经 `myopenclaw-net` 和 Bearer 认证调用上游已有的 6 个 MCP 工具，同时满足：

- 默认启动只包含 Hermes、backup-cron 和语雀服务；
- `YUQUE_TOKEN` 只进入 `yuque-mcp`；
- 快照与 Markdown 备份保存到宿主机持久化目录；
- 宿主机端口只绑定 localhost；
- Hermes 配置与 Skill 安装幂等；
- MVP 不增加任何健康检查机制。

## Upstream Baseline

| Item | Contract |
|---|---|
| Repository | `https://gitcode.com/dlut-water/yuque_mcp_server.git` |
| Source branch | `codex/docs-yuque-mcp-deployment-status` |
| Pinned commit | `cc68fd0df172d3b8f24ae325998d56bdfd0e36e6` |
| Runtime | Python 3.11, `mcp[cli]>=1.26.0`, FastMCP |
| Container port | 18000 |
| Transport | `RUN_MODE=cloud` → SSE |
| Authentication | `Authorization: Bearer <MCP_API_KEY>` |
| Change data | `YUQUE_CHANGE_DATA_DIR`，默认保留 30 天 |
| Backup path | 上游固定写入 `/app/yuque/backup` |

上游工具契约：

1. `list_docs(repo_namespace)`
2. `get_doc_content(repo_namespace, slug)`
3. `get_repo_toc(repo_namespace)`
4. `search_docs(repo_namespace, query)`
5. `backup_repo(repo_namespace, repo_display_name)`
6. `collect_and_get_change_summary(repo_namespace)`

`list_docs` 和 `search_docs` 使用语雀文档列表接口，可能只覆盖前 100 篇；完整枚举必须使用 `get_repo_toc`。变化摘要只表示相邻完整快照之间的净变化。

> 该固定 commit 已包含 Required 修复：同步 HTTP 请求增加明确 timeout 并移出 async event loop，`backup_repo` 增加同仓库 single-flight，以及 Docker 构建上下文隔离。

## Architecture

```text
project .env
  ├─ YUQUE_TOKEN ────────────────> yuque-mcp only
  └─ MCP_API_KEY ──┬─────────────> yuque-mcp auth middleware
                   └─ mapped as connection credential for Hermes

../yuque_mcp_server @ pinned commit
        │ Docker build
        ▼
yuque-mcp:18000 ── myopenclaw-net ──> Hermes + yuque-knowledge Skill
        │
        ├─ /data/change-data ──> ~/.myagentdata/yuque-mcp/change-data
        └─ /app/yuque/backup ──> ~/.myagentdata/yuque-mcp/backups
```

外部访问仅允许：

```text
http://127.0.0.1:${YUQUE_MCP_PORT:-18000}
```

禁止绑定 `0.0.0.0`。远程语雀 MCP、VPN 和内部 IP 不进入运行链路。

## Configuration Contract

`.env.example` 新增：

```dotenv
# Yuque MCP（可选；启用 yuque profile 时必填）
# YUQUE_TOKEN=replace_with_read_only_yuque_token
# MCP_API_KEY=replace_with_random_local_mcp_key
YUQUE_MCP_PORT=18000
YUQUE_CHANGE_RETENTION_DAYS=30
YUQUE_MCP_UID=10001
YUQUE_MCP_GID=10001
```

规则：

- `.env.example` 不得包含可用凭据。
- `YUQUE_TOKEN` 只注入 `yuque-mcp`，不得出现在 Hermes、Claude Code、OpenClaw 或其他服务的环境中。
- `MCP_API_KEY` 注入 `yuque-mcp`；Hermes 只获得连接该 MCP 所需的等价凭据，不获得 `YUQUE_TOKEN`。
- cloud/SSE 模式下 `YUQUE_TOKEN` 或 `MCP_API_KEY` 为空时，容器启动命令必须在运行 Python 服务前退出非零。
- 错误消息只能指出缺少变量名，不得输出变量值。
- 镜像默认用户为专用非 root UID/GID 10001；代码和 `.venv` 保持 root-owned，只有 `/data/change-data` 与 `/app/yuque/backup` 可写。
- Compose 使用 `${YUQUE_MCP_UID:-10001}:${YUQUE_MCP_GID:-10001}`。Windows Docker Desktop 保持默认值；`scripts/start.sh` 在非 Windows POSIX 主机导出当前 `id -u`/`id -g`，不得通过 `chmod 777`、宿主机 `sudo`/`chown` 或改用 named volume 绕过 bind mount 所有权。
- 非 Windows POSIX 主机当前 UID 为 0 时，`start.sh` 必须在任何 Compose 调用前失败；Compose 命令本身也必须在凭据检查前拒绝 UID 0，防止直接设置 `YUQUE_MCP_UID=0` 绕过启动脚本。

安全失败的 Compose 命令应遵循以下形式，实际 YAML 需使用 `$$` 避免 Compose 在宿主机提前展开：

```yaml
command:
  - /bin/sh
  - -ec
  - |
    test "$$(id -u)" -ne 0 || { echo "refusing to run yuque-mcp as root" >&2; exit 1; }
    test -n "$${YUQUE_TOKEN:-}" || { echo "YUQUE_TOKEN is required" >&2; exit 1; }
    test -n "$${MCP_API_KEY:-}" || { echo "MCP_API_KEY is required" >&2; exit 1; }
    export HOME="/tmp/yuque-home-$$(id -u)"
    mkdir -p "$${HOME}"
    exec uv run python yuque/server.py
```

## Dependency Pinning Contract

`scripts/clone-deps.sh` 增加独立常量：

```bash
YUQUE_MCP_REPO_URL="https://gitcode.com/dlut-water/yuque_mcp_server.git"
YUQUE_MCP_SOURCE_REF="codex/docs-yuque-mcp-deployment-status"
YUQUE_MCP_PINNED_COMMIT="cc68fd0df172d3b8f24ae325998d56bdfd0e36e6"
```

行为：

- 目标目录为 `${CODE_DIR}/yuque_mcp_server`，默认对应 `../yuque_mcp_server`。
- 新克隆先取得来源 ref，再 checkout 固定 commit。
- 固定 commit 必须能被 `git cat-file -e <commit>^{commit}` 验证。
- 已有仓库不得自动 reset、clean 或覆盖本地改动。
- 已有仓库 HEAD 与固定 commit 不一致时，脚本报告当前 SHA、目标 SHA 和人工切换命令，并返回清晰状态。
- 后续切换到 `main` 或 tag 时，必须在同一次变更中更新来源 ref、固定 commit、PRD/Spec 和测试期望。

## Compose Service Contract

`docker-compose.yml` 新增可选服务，目标结构：

```yaml
yuque-mcp:
  profiles: ["yuque"]
  build:
    context: ../yuque_mcp_server
    dockerfile: Dockerfile
  image: myopenclaw/yuque-mcp:latest
  container_name: yuque-mcp
  restart: unless-stopped
  user: "${YUQUE_MCP_UID:-10001}:${YUQUE_MCP_GID:-10001}"
  ports:
    - "127.0.0.1:${YUQUE_MCP_PORT:-18000}:18000"
  environment:
    - RUN_MODE=cloud
    - PORT=18000
    - YUQUE_TOKEN=${YUQUE_TOKEN:-}
    - MCP_API_KEY=${MCP_API_KEY:-}
    - YUQUE_CHANGE_DATA_DIR=/data/change-data
    - YUQUE_CHANGE_RETENTION_DAYS=${YUQUE_CHANGE_RETENTION_DAYS:-30}
  volumes:
    - ${HOME}/.myagentdata/yuque-mcp/change-data:/data/change-data
    - ${HOME}/.myagentdata/yuque-mcp/backups:/app/yuque/backup
  networks:
    - myopenclaw-net
  deploy:
    resources:
      limits:
        memory: 512M
        cpus: "0.5"
```

约束：

- 服务不得定义 `healthcheck`。
- `scripts/setup-uptime-kuma.sh` 不得增加 `yuque-mcp`。
- `scripts/start.sh` 默认应显式启用 `yuque` profile，并且只启动 Hermes、backup-cron 和语雀服务。
- `scripts/start.sh` 仅在显式启用语雀功能时创建两个持久化目录；不得把它们加入云备份清单。
- Compose 不挂载上游仓库的真实 `.env`、`backup/` 或其他工作区数据。

## Hermes Compatibility Gate

当前仓库没有版本化的 Hermes MCP 配置模板，运行时配置保存在 `~/.hermes/config.yaml`。已对 Hermes Agent v0.16.0 完成一次无真实语雀数据的兼容性检查，实际生效的最小配置为：

```yaml
mcp_servers:
  yuque-mcp:
    url: http://yuque-mcp:18000/sse
    transport: sse
    headers:
      Authorization: "Bearer ${MCP_YUQUE_MCP_API_KEY}"
```

`MCP_YUQUE_MCP_API_KEY` 由 Hermes 从 `~/.hermes/.env` 插值，其值与项目根 `.env` 中仅注入服务端的 `MCP_API_KEY` 等价。未显式指定 `transport: sse` 时，Hermes 会按 Streamable HTTP 连接并失败。

1. 确认 Hermes 支持的远程 MCP transport 名称和 SSE URL 形式（例如 `/sse`）。
2. 确认 Authorization Header 的配置字段和环境变量/文件引用能力。
3. 使用占位 `YUQUE_TOKEN` 启动服务，只执行 MCP initialize 与 tools/list，不调用语雀 API。
4. 记录实际生效的最小配置片段，并据此更新本 Spec；未通过时不得继续实现配置写入。

实测中错误 Bearer 凭据连接失败，正确凭据完成 initialize/tools/list 并精确发现 6 个工具；测试未调用语雀 API，日志未包含测试凭据或占位 Token。该检查只能确认连接和工具发现，不能被表述为真实语雀集成通过。

## Hermes Provisioning Contract

兼容性检查通过后，自动配置必须满足：

- 仅在用户显式启用语雀功能时注册 `yuque-mcp`。
- 服务 URL 固定为 Docker DNS SSE endpoint `http://yuque-mcp:18000/sse`，并显式设置 `transport: sse`；不得使用 localhost、宿主机 IP 或远程地址。
- Authorization 使用 `Bearer ${MCP_YUQUE_MCP_API_KEY}`；启动逻辑将项目根 `.env` 的 `MCP_API_KEY` 等值、安全地写入 Hermes `~/.hermes/.env`，不把 `YUQUE_TOKEN` 写入 Hermes 配置或环境。
- 重复启动不产生重复 server、重复 toolset 或重复 Skill。
- 保留用户已有 MCP server 和 toolset；不得整体覆盖 `config.yaml`。
- 配置失败时保留原文件，并输出不含秘密的可执行诊断。
- Hermes 重建后，持久化配置和 Skill 仍可恢复。

若 Hermes 不支持安全的 Header 配置，本功能阻断，不得通过关闭上游认证来绕过。

## Skill Contract

新增 `skills/yuque-knowledge/SKILL.md`，通过只读 volume 挂载并由 Hermes entrypoint 幂等链接到 `/opt/data/skills/yuque-knowledge`。

Skill 必须规定：

- 目录或完整枚举优先使用 `get_repo_toc`。
- `search_docs` 只按标题搜索，且受文档列表 100 篇限制。
- 调 `get_doc_content` 前先由目录、标题或 slug 缩小范围。
- `backup_repo` 只在用户明确表达备份意图时调用；不得把“总结”“搜索”解释为备份。
- 不并发触发同一知识库的备份，不主动循环重复备份。
- 首次变化采集返回初始化状态；后续结果只描述相邻完整快照的净变化。
- 不把最后编辑者推断为全部编辑参与者，不声称恢复完整版本历史。
- 区分认证缺失、API 认证失败、MCP 不可达、存储失败、无基线和部分成功。
- 不回显 Token、绝对宿主机路径或完整敏感正文到普通日志。

Skill 不实现 HTTP 请求、备份、快照或 diff 逻辑，只负责工具选择、参数收敛、语义约束和面向用户的结果表达。

## Commands

以下命令是实现后的目标操作方式：

```bash
# 克隆并核对依赖
./scripts/clone-deps.sh

# 仅构建语雀服务
docker compose --profile yuque build yuque-mcp

# 启动语雀服务和消费者 Hermes
docker compose --profile yuque up -d yuque-mcp hermes

# 查看运行状态和日志（不构成健康检查）
docker compose ps yuque-mcp hermes
docker compose logs --tail=200 yuque-mcp

# 停止语雀服务
docker compose stop yuque-mcp

# 静态与 mock 集成验证
bash scripts/test-yuque-mcp-integration.sh
```

Windows 上执行 Bash 脚本时必须使用用户实际的 Git Bash，不把 WSL `bash.exe` 的结果当作 Git Bash 验证。

## Project Structure

```text
myopenclaw/
├─ .claude/prds/yuque-mcp-integration.prd.md    # 产品需求
├─ .claude/specs/yuque-mcp-integration.spec.md  # 技术契约
├─ .env.example                                  # 变量名与安全占位符
├─ docker-compose.yml                            # 可选 yuque-mcp 服务与 Hermes 挂载
├─ docker/hermes/entrypoint-wrapper.sh           # Skill/配置幂等恢复入口
├─ scripts/clone-deps.sh                         # 上游 ref/commit 获取与核对
├─ scripts/start.sh                              # 显式启用时的目录/配置准备
├─ scripts/test-yuque-mcp-integration.sh         # 静态、mock 和可选容器测试
├─ skills/yuque-knowledge/SKILL.md               # Hermes 工具使用规则
└─ docs/yuque-mcp.md                             # 运维与故障排查

../yuque_mcp_server/                             # 独立上游仓库，不复制到本仓库
```

## Code Style

- Shell 使用现有 `#!/usr/bin/env bash`、`set -euo pipefail` 和大写只读配置变量风格。
- Compose 服务名、镜像名和 Skill 名统一使用 kebab-case：`yuque-mcp`、`yuque-knowledge`。
- 环境变量使用大写 snake case；路径必须通过引号保护。
- 配置更新必须结构化、幂等，不能用可能覆盖相邻用户配置的宽泛文本替换。
- 日志只说明动作与变量名，不输出凭据或正文。

示例：

```bash
ensure_directory() {
  local target_dir="$1"
  mkdir -p "${target_dir}"
  printf '   ✅ 已准备目录: %s\n' "${target_dir}"
}
```

## Testing Strategy

### Static contract tests

`scripts/test-yuque-mcp-integration.sh` 至少验证：

- clone 脚本包含正确 URL、来源 ref 和完整固定 commit；
- Compose service 使用 profile `yuque`；
- build context 和 Dockerfile 正确；
- 端口以 `127.0.0.1` 绑定；
- `YUQUE_TOKEN` 只出现在 `yuque-mcp` 环境块；
- 两个持久化目录挂载正确；
- service 不含 `healthcheck`，Uptime Kuma 不含 `yuque-mcp`；
- Skill mount、链接逻辑和六个工具名存在；
- `.env.example` 有变量名但没有真实 secret-like 值。

### Container contract tests

在 Docker 可用时，以占位 Token 和随机测试 MCP key：

1. `docker compose config` 成功，且默认 profile 不包含运行中的 `yuque-mcp`。
2. 单独构建并启动 `yuque-mcp`。
3. 缺少 `YUQUE_TOKEN` 或 `MCP_API_KEY` 时容器退出非零。
4. 无 Authorization、错误 Authorization 返回 401。
5. 正确 Authorization 能完成 initialize 和 tools/list，并返回精确的 6 个工具名。
6. Hermes 容器能通过 `yuque-mcp:18000` 建立 MCP 连接。
7. 容器重建前后 fixture 快照与备份文件仍存在。

这些测试不得调用真实语雀 API。真实 Token 验证必须由用户单独授权，并与 mock/contract 测试结果分开报告。

### Hermes journey validation boundary

自动化验证采用与研发日报相同的边界：验证 Skill 契约、MCP 认证、Hermes 真实 MCP loader 的连接与 6 工具发现，以及使用 mock 工具返回的五条确定性 prompt/行为契约。Hermes 的自然语言分析与最终回答依赖真实推理提供商，不搭建 mock inference server；部署后由用户使用真实只读 Token 人工验证，未执行前不得标记为自动化通过或真实集成通过。

### Regression checks

- 现有默认 Compose 配置仍可解析。
- 默认启动不要求 `YUQUE_TOKEN` 或 `MCP_API_KEY`。
- 现有 Hermes Skill 链接、研发日报和其他服务配置不被覆盖。
- `git diff --check` 通过。
- diff 中不包含 Token、真实正文、内部地址或备份文件。

## Error Contract

面向用户或测试输出至少区分：

| Class | Expected behavior |
|---|---|
| 缺少本地配置 | 启动失败并指出缺少的变量名 |
| MCP 未授权 | 返回 401，不泄露 key |
| 语雀 Token 无效/权限不足 | 返回上游认证类错误，不伪装为空结果 |
| 语雀 API 不可达/限流 | 返回可重试错误或部分成功状态 |
| 首次变化采集 | 返回 initialized 语义，不生成虚假变化 |
| 快照存储失败 | 返回稳定的存储错误，不泄露宿主机路径 |
| 备份部分失败 | 返回成功/失败计数，保留已完成文件 |
| MCP 服务不可达 | Hermes 明确报告连接失败，不回退到远程服务 |

## Boundaries

### Always

- 固定并验证上游完整 commit。
- 仅在显式启用时启动语雀 profile。
- 保留用户已有配置和未提交改动。
- 使用 mock/fixture 完成自动化测试。
- 分开报告静态检查、容器契约测试和真实语雀验证。

### Ask first

- 修改 `yuque_mcp_server` 上游代码或切换固定 commit。
- 运行会读取真实语雀正文的测试。
- 将语雀数据加入云备份。
- 将消费者扩展到 Hermes 之外。
- 新增健康检查、定时任务或公网访问。

### Never

- 提交 `.env`、真实 Token、正文、快照或备份。
- 把 `YUQUE_TOKEN` 下发给 Hermes。
- 关闭 MCP 认证来绕过 Hermes Header 兼容问题。
- 自动 reset/clean 已有上游仓库。
- 将 localhost 端口改为 `0.0.0.0`。
- 把相邻快照摘要描述成完整版本历史或完整审计记录。

## Success Criteria

- [x] PRD 的自动化 Acceptance Criteria 有对应测试，人工项明确保持未执行。
- [x] Hermes compatibility gate 已用当前镜像实际验证并回写准确配置格式。
- [x] 固定 commit 已更新到包含 Required 修复的真实上游 SHA，偏离仓库不会被自动覆盖。
- [x] `yuque-mcp` 是可选 profile，默认流程保持不变。
- [x] localhost 端口、凭据隔离和安全失败行为通过测试。
- [x] Hermes 精确发现 6 个工具，`yuque-knowledge` Skill 可重建恢复。
- [x] 快照和备份持久化通过容器重建测试。
- [x] 没有添加健康端点、Uptime Kuma monitor 或 Compose healthcheck。
- [x] 自动化测试不读取真实语雀正文，diff 不包含敏感数据。
- [x] 运维文档包含配置、启动停止、验证边界和故障排查。
- [ ] 部署后真实只读语雀 API 与 Hermes 模型分析人工验证。

## Open Questions

无产品决策或实施阻断项待确认。Hermes v0.16.0 的 SSE URL、`transport: sse`、Authorization Header 和环境变量插值格式已通过 Compatibility Gate 实测并回写本 Spec。

---
*Status: IMPLEMENTED / AUTOMATION VERIFIED — real Hermes analysis and real Yuque API access remain deployment-time manual read-only checks.*
