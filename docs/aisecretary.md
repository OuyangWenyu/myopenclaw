# 事务追踪 (aisecretary)

aisecretary 是事务数据库 MCP 服务，提供 7 个 tools，SQLite 持久化存储。

## 服务信息

| 项目 | 值 |
|------|-----|
| 端口 | 8000 |
| Build context | `../aisecretary` |
| 数据 | `~/.myagentdata/aisecretary/transactions.sqlite` |
| 资源限制 | 256M / 0.5 CPU |

## 常用命令

```bash
# 健康检查
curl -s http://localhost:8000/health

# 查看事务数量
docker compose exec aisecretary python3 -c "
import sqlite3; conn=sqlite3.connect('/data/transactions.sqlite')
print(conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0])
"

# MCP 连接测试（从 Hermes 容器内）
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp test aisecretary

# MCP tools 列表
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp list
```

## 使用方式

飞书上对 Hermes 说「列出当前事务」「汇总事务状态」即可通过 MCP 访问数据库。Hermes 的 `mcp_servers` 配置在 `~/.hermes/config.yaml`，skill 走 `external_dirs` 自动发现。

## 集成验证

```bash
./scripts/test-aisecretary-integration.sh
```

TDD 风格，9 项检查。
