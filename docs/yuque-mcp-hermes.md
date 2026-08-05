# 语雀 MCP 接入（Hermes）

> 最后更新：2026-08-05

本机 Hermes（`hermes` / `hermes-coder` 容器）通过远程 SSE 接入语雀 MCP 服务，读取、搜索、备份语雀知识库并查询服务端生成的变更报告。

## 架构边界

- 服务端：`yuque_mcp_server` 以 `RUN_MODE=cloud` 部署在服务器上，持有 `YUQUE_TOKEN`，端口 18000。
- 客户端：本机只需远程 SSE 地址（`YUQUE_MCP_URL`）和访问 key（`MCP_YUQUE_MCP_API_KEY`），**不需要** `YUQUE_TOKEN`。
- 本机配置由 [scripts/bootstrap_hermes.sh](../scripts/bootstrap_hermes.sh) 写入 `~/.hermes/config.yaml`、`~/.hermes/.env`；skill 由 docker-compose 只读挂载（`./skills/yuque-knowledge` → `/opt/hermes-skills/yuque-knowledge`）。

## 配置

在仓库根目录 `.env` 中填写：

```env
YUQUE_MCP_URL=https://服务器/sse
MCP_YUQUE_MCP_API_KEY=服务端下发的访问 key
```

`MCP_API_KEY` 是兼容别名；同时填写时命令行参数优先，其次环境变量，最后 `.env`。

## 注册 MCP 服务

```bash
./scripts/bootstrap_hermes.sh
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--url URL` / `--api-key KEY` | 覆盖 `.env` 中的 URL/key |
| `--dry-run` | 只打印将要做的改动，不写文件 |
| `--disable` | 移除已注册的 `yuque-mcp` 配置和 `.env` key |
| `--force` | 替换/删除同名但非本脚本管理的条目 |
| `--no-skill` | 不安装 skill（本机 skill 已由 docker-compose 挂载） |

脚本只写 Hermes 侧文件，不会部署服务端、不会打印 key。

## 生效与验证

修改文件后重启 Hermes 生效：

```bash
docker compose restart hermes hermes-coder
```

验证：

```bash
# 确认 MCP 服务已注册（在 hermes 容器内）
docker compose exec hermes hermes mcp list

# 确认环境变量存在（只输出 set/unset，不打印值）
docker compose exec hermes sh -c 'if [ -n "$MCP_YUQUE_MCP_API_KEY" ]; then echo set; else echo unset; fi'
```

## 故障排查

| 现象 | 处理 |
|------|------|
| `Hermes config not found` | 本机 Hermes 未初始化，先启动一次 Hermes 生成 `~/.hermes/config.yaml` |
| 注册成功但 MCP 调用 401 | `~/.hermes/.env` 中的 key 与服务端 `MCP_API_KEY` 不一致，或重启后未加载 |
| `PyYAML is required` | 用 `PYTHON_BIN=/path/to/python` 指定带 PyYAML 的解释器 |
| skill 未生效 | 确认 `docker compose config` 中有 `./skills/yuque-knowledge` 挂载，且容器已重启 |
