# Linux 服务器部署配置指南

> 服务器：`10.48.0.81` | 部署用户：`gaoyu` | 项目路径：`/home/gaoyu/source_code/myopenclaw`

## 服务架构

```
docker-compose.yml 启动四个服务（myopenclaw-net 桥接网络）：

hermes            :8642   自定义镜像，AI agent gateway
hermes-dashboard  :9119   只读 dashboard，监控 hermes（depends_on hermes）
openclaw-gateway  :18789  OpenClaw gateway（含 /healthz 健康检查）
backup-cron       -       定时快照备份（默认每周日凌晨 2:00）
```

---

## 关键目录

### hermes 容器挂载

| 宿主机路径 | 容器内路径 | 说明 |
|-----------|-----------|------|
| `/home/gaoyu/.hermes` | `/opt/data` | Hermes 主数据目录（home） |
| `/home/gaoyu/.config/gh` | `/opt/gh-config` | gh CLI 认证信息 |
| `/home/gaoyu/.config/opencode` | `/opt/opencode-config` | opencode 配置 |
| `/home/gaoyu/.claude` | `/opt/claude-config` | Claude Code 配置 |
| `./hermes/mcp/` | `/opt/mcp`（只读） | MCP server 脚本 |

### openclaw-gateway 容器挂载

| 宿主机路径 | 容器内路径 | 说明 |
|-----------|-----------|------|
| `/home/gaoyu/.openclaw` | `/home/node/.openclaw` | OpenClaw 数据（含 SQLite DB） |
| `/home/gaoyu/.config/gc` | `/home/node/.config/gc` | GitCode CLI 配置 |

### backup-cron 容器挂载

| 宿主机路径 | 容器内路径 | 说明 |
|-----------|-----------|------|
| `/home/gaoyu/.hermes` | `/root/.hermes`（只读） | Hermes 数据快照源 |
| `/home/gaoyu/.openclaw` | `/root/.openclaw`（只读） | OpenClaw 数据快照源 |
| `/home/gaoyu/.myagentdata` | `/.myagentdata`（只读） | agent 附加数据快照源 |
| `${BACKUP_ROOT}` | `/backup` | 快照写入目标（云盘挂载点） |

**重要**：必须以 `gaoyu` 身份运行 `docker compose`，否则 `${HOME}` 变为 `/root`，导致数据挂载到错误目录。

**容器内文件权限**：hermes 服务进程以 UID `10000` 运行，其创建的文件在宿主机上显示为 UID 10000（非具名用户）；hermes 内的 bash 子进程以 root 运行，其创建的文件在宿主机显示为 root。操作 `~/.hermes/` 下非 gaoyu 所属的文件均需 `sudo`。

---

## 首次部署

```bash
# 以 gaoyu 身份在宿主机执行
cd /home/gaoyu/source_code/myopenclaw

# 1. 创建环境变量文件
cp .env.example .env
nano .env   # 填写 API key（见下方说明）

# 2. 配置云盘备份路径（必须完成，start.sh 依赖 .cloud.conf）
./scripts/setup-cloud.sh

# 3. 构建镜像并启动（首次必须 --build）
./scripts/start.sh --build

# 4. 确认服务状态
docker compose ps
docker compose logs -f hermes
```

`start.sh` 首次运行会自动从模板初始化以下文件（如不存在）：

- `~/.config/opencode/opencode.json`
- `~/.claude/settings.json`
- `~/.hermes/config.yaml`

**注意**：`start.sh` 在 `.cloud.conf` 不存在时会直接退出报错，必须先运行 `setup-cloud.sh`。

---

## API Key 配置

### `.env` 文件

路径：`/home/gaoyu/source_code/myopenclaw/.env`

```bash
# ZAI / 智谱 GLM（同时作为 Claude Code 的 ANTHROPIC_API_KEY 使用）
GLM_API_KEY=xxxxx.xxxxx

# 以下三个 key 被 Hermes 安全黑名单拦截，无法直接传给子进程。
# entrypoint-wrapper.sh 启动时会将其写入 /opt/data/secrets/ 文件，
# opencode.json 通过 {file:} 语法引用。
DEEPSEEK_API_KEY=sk-xxx
OPENROUTER_API_KEY=xxx
OPENAI_API_KEY=sk-xxx

# MCP server 自定义环境变量（非黑名单，可直接透传到容器）
# 在 docker-compose.yml environment: 段添加对应字段后，Python 脚本用 os.environ 读取
# HYDRO_API_BASE_URL=http://10.48.0.81:8000

# GitCode CLI（OpenClaw 容器内 gc 使用）
GITCODE_TOKEN=gitcode_xxxxx
```

### Hermes 内部凭证库 `auth.json`

路径：`/home/gaoyu/.hermes/auth.json`（**UID 10000 所属，需 sudo**）

存储 Hermes 自身使用的 provider credentials。ZAI/GLM key 在 `credential_pool.zai`。

```bash
# 查看 ZAI 凭证
sudo python3 -c "import json; d=json.load(open('/home/gaoyu/.hermes/auth.json')); print(json.dumps(d['credential_pool'].get('zai'), indent=2))"
```

---

## Hermes 模型配置

文件：`/home/gaoyu/.hermes/config.yaml`（**UID 10000 所属，需 sudo**）

```yaml
model_config:
  default: glm-4.5
  provider: zai
  base_url: https://api.z.ai/api/paas/v4
```

修改后需重启容器才能生效（gateway 进程重新读取配置）：

```bash
docker restart hermes
```

---

## MCP Server 配置

MCP server 脚本放在 `hermes/mcp/<name>/server.py`，容器内挂载为 `/opt/mcp/<name>/server.py`。

### 在 `~/.hermes/config.yaml` 中注册

```yaml
mcp_servers:
  hydro_forecast:
    command: "/usr/local/bin/uv"    # 必须用绝对路径（Hermes 子进程 PATH 不含 /usr/local/bin）
    args:
      - "run"
      - "--quiet"                    # 必须加，否则 uv 安装日志污染 JSON-RPC stdout
      - "--with"
      - "mcp"
      - "--with"
      - "httpx"
      - "/opt/mcp/hydro-forecast/server.py"
    timeout: 60
```

MCP 配置修改后**无需重启容器**，在 Hermes CLI 或 Dashboard 中执行热加载：

```
/reload-mcp
```

验证是否加载成功：启动日志中出现 `hydro_forecast (stdio) — N tool(s)` 表示正常。

### 为 MCP Server 透传环境变量

MCP server 通过 `os.environ` 读取配置（如 `HYDRO_API_BASE_URL`）。透传步骤：

1. 在 `.env` 中添加变量：
   ```bash
   HYDRO_API_BASE_URL=http://10.48.0.81:8000
   ```

2. 在 `docker-compose.yml` 的 `hermes.environment:` 中添加：
   ```yaml
   - HYDRO_API_BASE_URL=${HYDRO_API_BASE_URL:-http://10.48.0.81:8000}
   ```

3. 重启 hermes 容器使环境变量生效：
   ```bash
   docker restart hermes
   ```

### uv 依赖缓存

`uv` 的包缓存目录为 `/opt/data/.cache/uv`（即宿主机 `~/.hermes/.cache/uv`），持久化在 hermes 数据卷中。重建镜像不会丢失已下载的依赖，首次冷启动后后续热加载速度快。

---

## GitCode CLI 配置

**1. 编译 GitCode CLI**

在服务器上从源码编译：

```bash
git clone https://gitcode.com/gitcode-cli/cli.git
cd cli
go env -w GOPROXY=https://goproxy.cn,direct
go build -o gc ./cmd/gc
```

然后放到 OpenClaw 持久化目录：

```bash
mkdir -p ~/.openclaw/bin
mv gc ~/.openclaw/bin/gc
chmod +x ~/.openclaw/bin/gc
```

**2. 容器获得 `gc` 命令**

`~/.openclaw` 已挂载到容器：

```yaml
volumes:
  - ${HOME}/.openclaw:/home/node/.openclaw
```

所以容器内可以访问：

```text
/home/node/.openclaw/bin/gc
```

Compose 又把该目录加入 `PATH`：

```yaml
environment:
  - PATH=/home/node/.openclaw/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

因此 OpenClaw 可以直接执行：

```bash
gc repo list
gc issue create
gc pr diff
```

如果 GitCode 请求来自飞书等 Hermes 渠道，Hermes 容器也要能找到 `gc`。本仓的 `docker-compose.yml` 已额外挂载：

```yaml
volumes:
  - ${HOME}/.openclaw/bin/gc:/usr/local/bin/gc:ro
  - ${HOME}/.config/gc:/root/.config/gc
environment:
  - GITCODE_TOKEN=${GITCODE_TOKEN:-}
```

这样 Hermes 自己的 terminal/shell 也能直接运行 `gc`。

**3. 配置认证**

通过环境变量传入 GitCode Token：

```yaml
environment:
  - GITCODE_TOKEN=${GITCODE_TOKEN:-}
```

实际 token 写在项目 `.env`：

```env
GITCODE_TOKEN=你的GitCode访问令牌
```

同时挂载 `gc` 配置目录：

```yaml
volumes:
  - ${HOME}/.config/gc:/home/node/.config/gc
```

更新 `.env` 或 `docker-compose.yml` 后，需要重新创建容器让环境变量生效：

```bash
docker compose up -d openclaw-gateway
```

验证：

```bash
docker compose exec openclaw-gateway gc auth status
```

**4. 告诉机器人怎么使用**

在 OpenClaw 工作区指令中添加 GitCode 规则：

```text
~/.openclaw/workspace/AGENTS.md
```

例如：

```md
当用户提到 GitCode、仓库、Issue、PR、项目列表时，不要凭记忆回答，必须先调用 `gc` 查询实时结果，再根据结果回复。

查询全部仓库时运行：

`gc repo list --limit 100 --json`

创建 Issue 时运行：

`gc issue create -R <owner>/<repo> --title "<标题>" --body "<正文>"`

审查 PR 时运行：

`gc pr view <number> -R <owner>/<repo>`
`gc pr diff <number> -R <owner>/<repo>`
```

**完整调用链**

```text
飞书用户 @机器人
→ openclaw-gateway 收到消息
→ Agent 读取 AGENTS.md
→ Agent 判断需要操作 GitCode
→ 通过 exec/shell 执行 gc
→ gc 使用 GITCODE_TOKEN 调用 GitCode API
→ Agent整理结果并回复飞书
```

---

## 日常运维

```bash
# 以 gaoyu 身份，在项目目录执行

# 启动 / 重启
./scripts/start.sh
docker restart hermes

# 查看日志
docker compose logs -f hermes
docker compose logs -f openclaw-gateway

# 停止所有服务
./scripts/stop.sh

# 修改 Hermes 配置（UID 10000 所属，需 sudo）
sudo nano /home/gaoyu/.hermes/config.yaml
sudo nano /home/gaoyu/.hermes/auth.json

# 手动备份
docker compose exec backup-cron /scripts/backup-all-docker.sh

# 从快照恢复
./scripts/restore.sh all latest
./scripts/restore.sh hermes 2026-04-23_090000
```

### 代码更新 / 镜像重建

```bash
cd /home/gaoyu/source_code/myopenclaw

# 拉取最新代码
git pull

# 重新构建镜像并重启（Dockerfile 有变更时必须 --build）
./scripts/stop.sh
./scripts/start.sh --build

# 仅 MCP server 脚本变更（server.py 修改），无需重建镜像，热加载即可：
# 在 Hermes CLI / Dashboard 执行 /reload-mcp
```

---

## 已知问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `command: uv` 报 not found | Hermes 子进程 PATH 不含 `/usr/local/bin` | 改为 `command: /usr/local/bin/uv` |
| 数据挂载到 `/root/.hermes` | 以 root 身份执行了 `docker compose` | 改为 `gaoyu` 用户执行 |
| `config.yaml` / `auth.json` 无法直接编辑 | 文件 owner 是 UID 10000 或 root（容器进程创建） | 用 `sudo` 操作，或写 Python 脚本 scp 到服务器执行 |
| DeepSeek v4 多轮对话 400 错误 | 推理模型返回 `reasoning_content`，Hermes 重发时 API 报错 | 使用 ZAI GLM 等标准模型，避免 DeepSeek 推理模型 |
| `/model` 命令更改不持久 | 仅改当前 CLI session，不影响 gateway | 必须修改 `config.yaml` + `docker restart hermes` |
| `start.sh` 报 `.cloud.conf` 不存在 | 跳过了 `setup-cloud.sh` 步骤 | 先执行 `./scripts/setup-cloud.sh` 配置云盘路径 |
