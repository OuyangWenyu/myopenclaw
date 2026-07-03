# Plan: aisecretary 集成到 myopenclaw 统一运维体系

**Source PRD**: `.claude/prds/aisecretary-integration.prd.md`
**Selected Milestone**: 全部 4 个（顺序依赖，需按序执行）
**Complexity**: Small — 改 2 个文件 + 3 个配置 + 验证

## 现状诊断

- aisecretary 当前在独立网络 `aisecretary_default`（172.22.0.0/16），Hermes 在 `myopenclaw_myopenclaw-net`（172.20.0.0/16）— **网络隔离，Hermes 无法访问 aisecretary**

- Hermes 默认配置（`~/.hermes/config.yaml`）已配了 `skills.external_dirs: [~/code/aisecretary/skills]` 和 `mcp_servers` 块，但：
  - `~/code/aisecretary/skills` 在容器内不可达（无 volume mount）→ skill 未加载
  - `mcp_servers` 无 aisecretary 条目 → MCP 连接未建立

- Hermes MCP 连接走 **`~/.hermes/config.yaml` → `mcp_servers`**，与 opencode.json 无关

## Patterns to Mirror
| Category | Source | Pattern |
|---|---|---|
| Compose service 定义 | `docker-compose.yml:1-51`（hermes service） | build context、volumes、networks、healthcheck、resource limits |
| Volume mount（host→容器路径一致） | `~/.hermes:/opt/data` + `~/code/aisecretary/skills:/opt/data/code/aisecretary/skills` | 确保 `~` 展开后的路径在容器内外一致 |
| MCP server 配置（stdio 型） | `~/.hermes/config.yaml:392-400`（codegraph） | `mcp_servers.<name>` 块，含 `enabled` 字段 |
| Uptime Kuma HTTP 监控 | `scripts/setup-uptime-kuma-monitors.py:27-35` | Python dict 定义 url/interval/name |
| 启动前目录确保 | `scripts/start.sh:60-64` | `mkdir -p` 确保 volume mount 源目录存在 |

## Files to Change
| File | Action | Why |
|---|---|---|
| `docker-compose.yml` | UPDATE | 新增 aisecretary service + hermes 容器加 skill volume mount |
| `scripts/start.sh` | UPDATE | 确保 `~/.myagentdata/aisecretary` 目录存在 |
| `~/.hermes/config.yaml` | UPDATE | `mcp_servers` 新增 aisecretary SSE 条目 |
| `~/.hermes/profiles/coder/config.yaml` | UPDATE | （可选）如需 coder 也能用 aisecretary |
| `scripts/setup-uptime-kuma-monitors.py` | UPDATE | 添加 aisecretary HTTP + Docker 监控 |

## Tasks

### Task 1: Compose 集成 — 让 aisecretary 进入 myopenclaw 网络
- **背景**: 当前 aisecretary 独立运行在自己的 `aisecretary_default` 网络，Hermes 无法访问
- **Action**:
  1. 在 `docker-compose.yml` 新增 aisecretary service：
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
       environment:
         - DATABASE_PATH=/data/transactions.sqlite
       volumes:
         - ${HOME}/.myagentdata/aisecretary:/data
       networks:
         - myopenclaw-net
       deploy:
         resources:
           limits:
             memory: 256M
             cpus: "0.5"
     ```
  2. 在 `scripts/start.sh` 的目录确保区块添加：
     ```bash
     mkdir -p "${HOME}/.myagentdata/aisecretary"
     ```
  3. 停止独立 aisecretary：`cd ~/code/aisecretary && docker compose down`
  4. 从 myopenclaw 重新拉起：`docker compose up -d --build aisecretary`
- **Mirror**: hermes service（compose 结构）+ aisecretary Dockerfile（ENV/EXPOSE 8000）
- **Validate**: `docker inspect aisecretary | jq '.[0].NetworkSettings.Networks | keys'` 显示 `myopenclaw_myopenclaw-net`

### Task 2: Skill 路径可达 — volume mount 让 Hermes 能看到 skill
- **背景**: `~/.hermes/config.yaml` 已配 `external_dirs: [~/code/aisecretary/skills]`，但容器内 `~/code/aisecretary/skills` = `/opt/data/code/aisecretary/skills` 不存在
- **Action**:
  1. 在 hermes、hermes-coder、hermes-finance 的 volumes 中各加一行：
     ```yaml
     - ${HOME}/code/aisecretary/skills:/opt/data/code/aisecretary/skills:ro
     ```
  2. 确保宿主机路径存在（`~/code/aisecretary/skills/` 已存在，无需创建）
  3. 重启容器使 mount 生效
- **Mirror**: 现有 `~/.hermes:/opt/data` volume mount 模式 — host 路径 → 容器内相同展开路径
- **Validate**: `docker compose exec hermes ls /opt/data/code/aisecretary/skills/transaction_manager/SKILL.md` 文件存在可读

### Task 3: MCP 连接 — Hermes 的 `mcp_servers` 配置
- **背景**: Hermes 通过 `mcp_servers` 块（非 opencode.json）连接 MCP 服务器。当前只有 stdio 型的 codegraph。aisecretary 是 SSE 型，格式需要确认
- **Action**:
  1. 在 `~/.hermes/config.yaml` 的 `mcp_servers` 块中添加 aisecretary 条目。
     格式需在实现时确认，预期类似于：
     ```yaml
     mcp_servers:
       aisecretary:
         enabled: true
         url: http://aisecretary:8000/mcp
         transport: sse
         timeout: 120
     ```
  2. 若 Hermes SSE transport 字段名不同（如 `type: sse` / `transport: sse` / `connection_type`），以实际报错或文档为准调整
  3. 重启 hermes 使配置生效
- **Mirror**: 现有 `codegraph` 条目（`enabled` + 连接参数模式）
- **Risk**: SSE transport 的 YAML 字段名未确认 — 实现时若不确定，先在容器内 `hermes gateway run --help` 查看 MCP 相关选项，或用 `hermes config` 子命令探索
- **Validate**: Hermes 重启后能列出 6 个 aisecretary MCP tools（可在飞书让 Hermes 列一下，或查 Hermes 日志）

### Task 4: Uptime Kuma 监控
- **Action**: 在 `scripts/setup-uptime-kuma-monitors.py` 的 `HTTP_SERVICE_MAP` 中添加：
  ```python
  "aisecretary": {
      "url": "http://aisecretary:8000/health",
      "interval": 60,
      "name": "aisecretary",
  },
  ```
  并确保 Docker 容器监控列表也包含 `aisecretary`
- **Mirror**: 现有 `openclaw-gateway` / `hermes-dashboard` 的 HTTP 监控条目
- **Validate**: 运行脚本后在 Dashboard 看到 aisecretary 双监控绿色

### Task 5: 只读验证
- **Action**:
  1. 记录测试前行数：
     ```bash
     docker compose exec aisecretary sqlite3 /data/transactions.sqlite "SELECT COUNT(*) FROM transactions;"
     ```
  2. 通过飞书让 Hermes 执行"列出当前事务"（触发 `list_transactions`）和"汇总事务"（触发 `summarize_transactions`）
  3. 验证前后 `COUNT(*)` 一致
  4. 绝对不调用 create/update/delete
- **Validate**: 前后行数相等，`summarize_transactions` 的 total 与 COUNT(*) 一致

## Validation

```bash
# 1. Network 正确
docker inspect aisecretary | jq '.[0].NetworkSettings.Networks | keys' | grep myopenclaw

# 2. Health check
curl -s http://localhost:8000/health | jq '.status' | grep ok

# 3. Skill 路径可访问
docker compose exec hermes ls /opt/data/code/aisecretary/skills/transaction_manager/SKILL.md

# 4. MCP server 可达（从 hermes 容器内）
docker compose exec hermes curl -s http://aisecretary:8000/health

# 5. 数据库只读验证
BEFORE=$(docker compose exec aisecretary sqlite3 /data/transactions.sqlite "SELECT COUNT(*) FROM transactions;")
# ... 飞书上通过 Hermes 执行 list_transactions + summarize_transactions ...
AFTER=$(docker compose exec aisecretary sqlite3 /data/transactions.sqlite "SELECT COUNT(*) FROM transactions;")
test "$BEFORE" = "$AFTER" && echo "✅ 数据库未被修改" || echo "❌ 行数不一致"

# 6. Uptime Kuma 监控上线
curl -s http://localhost:3001/api/status | jq '.monitors[] | select(.name | contains("aisecretary"))'
```

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Hermes MCP SSE transport 字段名不明确 | Medium | Medium | 实现时在容器内查 Hermes 文档/help，或用 `hermes config` 探索 |
| 独立 aisecretary compose 端口冲突 | Low | Medium | Task 1 先停旧容器再拉新 |
| Skill external_dirs 路径展开在容器内仍有问题 | Low | Low | 已验证：容器 home=`/opt/data`，volume mount 到 `/opt/data/code/aisecretary/skills` 正好对应 `~/code/aisecretary/skills` |
| 测试时误调写入 tool | Low | High | 飞书对话中只用"列出"和"汇总"触发词；测试前后对比行数 |

## Acceptance
- [ ] `./scripts/start.sh` 一次性拉起全部服务含 aisecretary
- [ ] aisecretary 在 `myopenclaw_myopenclaw-net` 上，Hermes 可 ping 通 `http://aisecretary:8000`
- [ ] `docker compose exec hermes ls /opt/data/code/aisecretary/skills/transaction_manager/SKILL.md` 成功
- [ ] `mcp_servers` 包含 aisecretary 条目
- [ ] Uptime Kuma Dashboard 可见 aisecretary HTTP + Docker 双监控
- [ ] 飞书上 Hermes 能列出事务和汇总事务
- [ ] 测试前后数据库行数一致（红线）
