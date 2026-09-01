# 语雀 MCP 接入（Hermes）

> 最后更新：2026-08-31

本机通过远程 SSE 接入语雀 MCP 服务（服务端为 `yuque_mcp_server` 的 `RUN_MODE=cloud` 部署），读取、搜索、备份语雀知识库并查询服务端生成的变更报告。

**当前消费者**（2026-08-29 双端 E2E 验证通过：`hermes mcp test yuque-mcp` Connected / 7 tools；天一 probe + `list_repos` 真实调用）：

- **Hermes 侧**：`hermes` / `hermes-coder` / `hermes-daoyuan` / `hermes-finance` 四个容器共享 `~/.hermes` 配置，MCP 注册全部生效（`skills/yuque-knowledge` 仅挂载前两者）
- **天一（openclaw-tianyi）**：经 `docker/tianyi-bot/openclaw.json.template` 独立注册（SSE + Bearer，凭据 `TIANYI_BOT_YUQUE_MCP_URL` / `TIANYI_BOT_MCP_YUQUE_MCP_API_KEY` 来自 `.env.tianyi-bot`，compose 以无前缀 `MCP_YUQUE_MCP_API_KEY` 注入容器环境供 OpenClaw 运行时展开）

## 架构边界

- 服务端：`yuque_mcp_server` 以 `RUN_MODE=cloud` 部署在服务器上，持有 `YUQUE_TOKEN`，端口 18000。
- 客户端：本机只需远程 SSE 地址（`YUQUE_MCP_URL`）和访问 key（`MCP_YUQUE_MCP_API_KEY`），**不需要** `YUQUE_TOKEN`。
- 本机配置由 `scripts/bootstrap_hermes.sh` 写入 `~/.hermes/config.yaml`、`~/.hermes/.env`；skill 由 docker-compose 只读挂载（`./skills/yuque-knowledge` → `/opt/hermes-skills/yuque-knowledge`）。

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
| 注册成功但 MCP 调用 401 | key 必须放在仓库根 `.env`（`MCP_YUQUE_MCP_API_KEY`）并经 compose 注入容器环境（4 个 hermes 服务已注入）；`~/.hermes/.env` 里的同名变量**不参与** Hermes MCP 客户端 `${VAR}` 展开。仍 401 则核对与服务端 `MCP_API_KEY` 是否一致 |
| `PyYAML is required` | 用 `PYTHON_BIN=/path/to/python` 指定带 PyYAML 的解释器 |
| skill 未生效 | 确认 `docker compose config` 中有 `./skills/yuque-knowledge` 挂载，且容器已重启 |

## 每日变更推送（yuque-daily-digest）

在查询能力之上，Hermes 还可通过 cron 每日早间把语雀知识库变更日报推送到飞书私聊（issue #65）：

```
服务端 07:00 生成变更报告 → Hermes cron 08:10（北京）触发 yuque-daily-digest skill
  → agent 逐库调用 get_change_summary（纯只读）→ 重点变更文档 get_doc_content 做中文摘要
  → cron --deliver 自动推送飞书私聊
```

行为约定：有变更 → 变更清单 + 摘要日报；全部无变更 → 静默；`not_available` / `initialized` / 401 / 连接失败 → 推送简短警告（不静默）。

### 启用

1. 完成上面的 MCP 注册（`YUQUE_MCP_URL` + `MCP_YUQUE_MCP_API_KEY`），**key 必须放在仓库根 `.env`**（`bootstrap_hermes.sh` 写入 `~/.hermes/.env` 的那份不参与 MCP 客户端展开）
2. 在 `.env` 中填写要跟踪的知识库显示名（逗号分隔，可先在容器内用 `list_repos` 确认名称）：

   ```env
   YUQUE_DAILY_PUSH_REPOS=知识库显示名A,知识库显示名B
   ```

3. `./scripts/start.sh` —— 检测到三个变量（`YUQUE_DAILY_PUSH_REPOS` / `YUQUE_MCP_URL` / `MCP_YUQUE_MCP_API_KEY`）即自动注册 cron（`10 0 * * *` UTC = 08:10 北京）；任一缺失则警告并跳过。已注册后修改知识库列表，重跑 `start.sh` 会自动更新 job prompt

### 验证

```bash
# cron job 已注册（每日 8:10 北京）
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list | grep yuque-daily-digest

# 手动触发一次，飞书私聊应收到日报（或按行为约定静默/警告）
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron run <job_id>

# 配置静态断言
bash skills/yuque-daily-digest/test-cron-config.sh
```

### 说明

- 仅推送**飞书私聊**（复用 `LARK_USER_OPEN_ID` / `FEISHU_HOME_CHANNEL`）；群推送不在本期范围（见 issue #60 三Agent群推送定调）
- 摘要由 Hermes agent 主模型生成，skill 不指定模型
- skill 挂载于 `hermes` / `hermes-coder`（`./skills/yuque-daily-digest` → `/opt/hermes-skills/yuque-daily-digest`），cron 注册在 `hermes` 容器
- 天一复用此能力延后至 issue #60 统一处理
