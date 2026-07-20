# 服务监控体系

双层监控：Uptime Kuma（服务级，Docker 容器）+ Healthchecks.io（主机级，云端死信开关）。

## 架构

```
Layer 1: Uptime Kuma (Docker 容器)
  ├── HTTP 监控: 各服务端点
  ├── Docker 容器监控: 容器运行状态
  └── 告警 → 飞书群机器人 Webhook

Layer 2: Healthchecks.io (云端)
  ├── 宿主机 launchd 每 60s ping
  ├── 整机/Docker 全崩时，Healthchecks.io 检测 silence
  └── 告警 → 邮件
```

两层独立：Layer 1 自身挂了，Layer 2 仍能告警。

## Uptime Kuma

### 容器配置

`docker-compose.yml` 中 `uptime-kuma` 服务：

| 配置项 | 值 |
|--------|-----|
| 镜像 | `louislam/uptime-kuma:latest` |
| 端口 | `${UPTIME_KUMA_PORT:-3001}` |
| 数据卷 | `~/.uptime-kuma:/app/data` |
| Docker socket | `/var/run/docker.sock:ro` (容器状态监控) |
| 资源限制 | 512M 内存 / 0.5 CPU |

### 首次设置

1. 启动服务：
   ```bash
   cd ~/code/myopenclaw && ./scripts/start.sh
   ```

2. 访问 Web UI：
   ```
   http://localhost:3001
   ```

3. 创建管理员账号（用户名 + 密码）。

4. 设置中文（可选）：Settings → Language → 中文。

### 添加监控目标

**自动方式（推荐）**：运行自动发现脚本，从 `docker-compose.yml` 读取所有服务并批量创建：

```bash
bash scripts/setup-uptime-kuma.sh
```

脚本会提示输入 Uptime Kuma 用户名和密码，或通过环境变量传入：
```bash
UPK_USER=owen UPK_PASS=yourpass bash scripts/setup-uptime-kuma.sh
```

幂等运行——已存在的监控会自动跳过。新增服务后重新运行即可。

**手动方式**（备选）：在 Uptime Kuma Web UI 逐一添加。

**HTTP 监控**（类型选择 "HTTP(s)"）：

| 名称 | URL | 间隔 | 重试 | 说明 |
|------|-----|------|------|------|
| Hermes | `http://hermes:8642` | 60s | 3 | Hermes 网关 |
| Hermes Coder | `http://hermes-coder:8642` | 60s | 3 | 爱码士 |
| Hermes Finance | `http://hermes-finance:8642` | 60s | 3 | 财务 agent |
| Hermes Dashboard | `http://hermes-dashboard:9119` | 60s | 3 | 监控面板 |
| OpenClaw Gateway | `http://openclaw-gateway:18789/healthz` | 30s | 3 | 有 /healthz 端点 |
| Claude Code | `http://claude-code:9090` | 60s | 3 | cc-connect 管理界面 |
| aisecretary | `http://aisecretary:8000/health` | 60s | 3 | 事务数据库 MCP 服务 |
| TDAI Memory | `http://tdai-memory:8420/health` | 60s | 3 | Agent 长期记忆 Gateway |
| FreshRSS | `http://dailyinfo_freshrss:80` | 60s | 3 | RSS 聚合 |

> **注意**：URL 使用 Docker 内部 DNS（容器名），因为 Uptime Kuma 和所有服务在同一个 `myopenclaw-net` 网络上。

**Docker 主机配置**（Docker 容器监控的前置条件）：

首次使用 Docker 监控前，需要在 Uptime Kuma 中配置 Docker 连接：
1. Settings → Docker Hosts → Setup Docker Host
2. Socket Type: Unix Socket
3. Socket Path: `/var/run/docker.sock`
4. Name: Mac mini Docker

或者直接插入数据库（自动脚本会尝试自动处理）：
```bash
docker compose exec uptime-kuma sqlite3 /app/data/kuma.db \
  "INSERT INTO docker_host (user_id, docker_daemon, docker_type, name) VALUES (1, '/var/run/docker.sock', 'socket', 'Mac mini Docker');"
```

**Docker 容器监控**（类型选择 "Docker"）：

| 名称 | 容器名 |
|------|--------|
| Docker: hermes | hermes |
| Docker: hermes-coder | hermes-coder |
| Docker: hermes-finance | hermes-finance |
| Docker: hermes-dashboard | hermes-dashboard |
| Docker: claude-code | claude-code |
| Docker: openclaw-gateway | openclaw-gateway |
| Docker: aisecretary | aisecretary |
| Docker: tdai-memory | tdai-memory |
| Docker: dailyinfo_freshrss | dailyinfo_freshrss |
| Docker: backup-cron | backup-cron |

**Ping 监控**（可选，监控宿主机可达性）：

| 名称 | 目标 |
|------|------|
| Host Ping | `host.docker.internal` |

### 配置飞书告警

1. 在飞书创建一个群组（如 "myopenclaw-monitoring"），可以只有你一个人。

2. 群组设置 → 群机器人 → 添加机器人 → 自定义 Webhook。

3. 设置机器人名称（如 "Uptime Kuma Alert"），复制 Webhook URL：
   ```
   https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

4. 在 Uptime Kuma Web UI → Settings → Notifications → Add Notification：
   - Type: 选择 "Feishu (飞书)"
   - Name: "飞书告警"
   - Webhook URL: 粘贴上面复制的 URL
   - 点击 "Test" 验证

5. 返回监控目标列表，为每个监控目标关联此通知渠道（Edit → Notifications → 勾选 "飞书告警"）。

### 告警调优建议

首次配置后，建议在 Settings → Notifications → "飞书告警" 中设置：

- **Resend Notification every**: 30 分钟（避免重复告警刷屏）
- **Max retries**: 3（临时故障不告警，连续失败才告警）

### 升级 Uptime Kuma

```bash
docker compose pull uptime-kuma
docker compose up -d uptime-kuma
```

升级前建议备份数据：
```bash
cp -r ~/.uptime-kuma ~/.uptime-kuma.bak
```

## Healthchecks.io

Healthchecks.io 是 Uptime Kuma 的**独立保险**。当整台 Mac mini 死机或 Docker 全部崩掉时，Uptime Kuma 自身也挂了，无法告警。Healthchecks.io 运行在云端，检测宿主机的心跳 silence。

> Healthchecks.io 心跳是全部 14 个定时任务之一，详见 [调度系统](scheduling.md)。

### 首次设置

1. 注册 https://healthchecks.io（免费版：20 个 Check）。

2. 创建 Check：
   - Name: "Mac mini heartbeat"
   - Period: 1 minute
   - Grace: 5 minutes（连丢 5 次 ping 才告警，避免网络抖动误报）
   - Schedule: Simple

3. 复制 Ping URL：
   ```
   https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

4. 填入 `.env`：
   ```bash
   HEALTHCHECKS_PING_URL=https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

5. 安装宿主机定时 ping 任务：
   ```bash
   cd ~/code/myopenclaw
   ./scripts/launchd/install-healthchecks-ping.sh
   ```

6. 验证：
   ```bash
   launchctl list | grep healthchecks
   tail -f logs/healthchecks-ping.log
   ```

   去 healthchecks.io Dashboard 应该能看到 "Last Ping: just now"。

### 配置邮件告警

在 healthchecks.io → Integrations → Email，添加你的邮箱。Check 超过 Grace 时间未收到 ping 时会发邮件。

### Ping 数据

每次 ping 附带系统信息（hostname、uptime、disk、load），在 healthchecks.io Dashboard 点击 Check → "Ping Body" 可查看。异常时可用于事后排查。

### 卸载

```bash
launchctl unload -w ~/Library/LaunchAgents/ai.myopenclaw.healthchecks-ping.plist
rm ~/Library/LaunchAgents/ai.myopenclaw.healthchecks-ping.plist
```

## 日常操作

```bash
# 查看监控面板
open http://localhost:3001

# 查看 Healthchecks 心跳日志
tail -f ~/code/myopenclaw/logs/healthchecks-ping.log

# 手动触发心跳
launchctl start ai.myopenclaw.healthchecks-ping

# 检查心跳任务状态
launchctl list | grep healthchecks
```

## 数据备份

Uptime Kuma 所有数据存储在 `~/.uptime-kuma/`（SQLite + 配置）。当前不纳入自动备份，因为：

- 监控配置可在 Uptime Kuma 中手动重建（工作量 < 10 分钟）
- SQLite 文件小（< 50MB），手动备份即可

如需备份：
```bash
cp -r ~/.uptime-kuma ~/.uptime-kuma.backup.$(date +%Y%m%d)
```

## 故障排查

### Uptime Kuma 无法启动

```bash
docker compose logs uptime-kuma
docker compose ps uptime-kuma
```

常见原因：端口 3001 被占用。改 `.env` 中 `UPTIME_KUMA_PORT` 为其他端口。

### 飞书收不到告警

1. 在 Uptime Kuma → Settings → Notifications → 点击 "Test" 测试 Webhook
2. 确认 Webhook URL 正确（飞书群 → 设置 → 群机器人 → 复制 Webhook 地址）
3. 确认机器人未从群中移除
4. 检查 DNS（容器内 `curl https://open.feishu.cn` 看能否解析）

### Healthchecks.io 停止报告

1. 检查心跳日志：
   ```bash
   tail -20 ~/code/myopenclaw/logs/healthchecks-ping.log
   ```
2. 检查 launchd 任务是否运行：
   ```bash
   launchctl list | grep healthchecks
   ```
3. 手动触发看报什么错：
   ```bash
   launchctl start ai.myopenclaw.healthchecks-ping
   ```
4. 确认 `.env` 中 `HEALTHCHECKS_PING_URL` 正确配置

### 监控到问题后的处理

1. 收到告警 → 检查 `docker compose ps` 确认哪些服务挂了
2. 查看对应容器日志：`docker compose logs <service>`
3. 尝试重启：`docker compose restart <service>`
4. 如果重启无效 → 检查 `.env` 配置、磁盘空间、内存

## Gateway 错误循环检测

OpenClaw 配置兼容性问题可能导致日志刷屏（历史事故：3 个月 762MB）。提供检测脚本：

```bash
# 人类可读
./scripts/check-gateway-errors.sh

# JSON 输出（适合 cron/AgentOps）
./scripts/check-gateway-errors.sh --json
```

此检测已纳入 [AgentOps 健康采集](agentops.md)，每天自动运行。如果检测到错误循环，晨间三签报告会包含告警。
