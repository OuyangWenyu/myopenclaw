# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

myopenclaw is a Docker-based deployment for running Hermes Agent (with opencode and gh CLI), Claude Code (with cc-connect for Feishu integration), and OpenClaw gateway, with automated cloud-backup snapshots and dailyinfo scheduling. All persistent data lives on the host (`~/.hermes`, `~/.openclaw`, `~/.myagentdata`, `~/.claude`, `~/.cc-connect`); this repo only holds configuration, Dockerfiles, and shell scripts.

## Common Commands

```bash
./scripts/start.sh           # Start all services (reads .env + .cloud.conf)
./scripts/start.sh --build   # Start with image rebuild (required after Dockerfile changes)
./scripts/stop.sh            # Stop all services

# Service status & logs
docker compose ps
docker compose logs -f hermes
docker compose logs -f claude-code
docker compose logs -f openclaw-gateway

# Manual backup (inside backup-cron container)
docker compose exec backup-cron /scripts/backup-all-docker.sh

# Restore from snapshot
./scripts/restore.sh all latest
./scripts/restore.sh claude 2026-04-23_090000

# OpenClaw CLI (one-shot)
docker compose --profile cli run --rm openclaw-cli

# cc-connect web admin
open http://localhost:9090

# Claude Code development environment (Python 3.12 + uv + build-essential)
docker compose exec claude-code python3 --version    # Python 3.12.x
docker compose exec claude-code uv --version          # uv package manager
docker compose exec claude-code git clone https://github.com/OuyangWenyu/torchhydro.git ~/code/OuyangWenyu/torchhydro  # Private repo clone (GITHUB_TOKEN auth)

# Google Drive papers (rclone — scoped to target folder)
docker compose exec hermes rclone ls gdrive:                    # List papers
docker compose exec hermes rclone copy paper.pdf gdrive:         # Upload a paper
docker compose exec hermes rclone deletefile gdrive:paper.pdf    # Delete a paper

# Cardamum contacts CLI
docker compose exec hermes cardamum addressbook create "contacts"    # Create addressbook (first use, auto-done by entrypoint)
docker compose exec hermes cardamum card list                        # List all contacts (uses addressbook.default)
docker compose exec hermes cardamum card read <id>                   # Read contact details
echo 'BEGIN:VCARD
VERSION:4.0
FN:Name
EMAIL:email@example.com
END:VCARD' | docker compose exec -T hermes cardamum card create -   # Add contact via stdin

# Himalaya email CLI (QQ / DLUT password + Outlook OAuth via ortie)
docker compose exec hermes himalaya account list
docker compose exec hermes himalaya envelope list --page-size 5          # default (QQ)
docker compose exec hermes himalaya envelope list -a dlut --page-size 5
docker compose exec -u hermes -it hermes ortie auth get -a outlook                  # one-time Outlook OAuth
docker compose exec -u hermes hermes himalaya envelope list -a outlook --page-size 5

# Zotero CLI + paper pipeline → migrated to ~/code/mylibrary
# Zotero MCP — 共享文献查询与分析服务（端口 8002，SSE transport）
# 独立 Docker 服务，CC飞总 / Hermes / 未来文献 agent 均可通过 http://zotero-mcp:8002/mcp 连接。
# 双通道：Web API (api.zotero.org) 搜索/元数据 + Local API (host.docker.internal:23119) 全文/PDF。
# 需要宿主机 Zotero Desktop 运行并开启 "Allow other applications to communicate with Zotero"。
# 环境变量: ZOTERO_API_KEY + ZOTERO_LIBRARY_ID + ZOTERO_LIBRARY_TYPE + ZOTERO_LOCAL_API_URL
# MCP server: docker/zotero-mcp/server.py（FastMCP + pyzotero + httpx，6 tools）
docker compose exec zotero-mcp python3 -c "print('healthy')"  # Zotero MCP 健康检查
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp test zotero  # MCP 连接测试（Hermes → zotero-mcp）

# aisecretary — 事务数据库 MCP 服务
curl -s http://localhost:8000/health                           # Health check
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp test aisecretary  # MCP 连接测试
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp list             # MCP tools 列表
docker compose exec aisecretary python3 -c "import sqlite3; conn=sqlite3.connect('/data/transactions.sqlite'); print(conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0])"  # 事务计数
./scripts/test-aisecretary-integration.sh                      # 集成验证（9 项检查）
./scripts/setup-uptime-kuma.sh                       # Uptime Kuma 监控项幂等注册（直接 SQLite，无需 API 凭证）
./scripts/setup-openclaw-memory.sh                    # 虾酱 (Discord) OpenClaw memory plugin（local 模式）

# TDAI Memory Gateway（Agent 长期记忆，4 agent 双向共享 L0→L3）
curl -s http://localhost:8420/health                           # Health check
docker compose logs -f tdai-memory                            # Gateway 日志
# 查记忆（L0 原始对话 / L1 结构化事实）
curl -s -X POST http://localhost:8420/search/conversations -H 'Content-Type: application/json' -d '{"query":"关键词","limit":5}'
curl -s -X POST http://localhost:8420/search/memories -H 'Content-Type: application/json' -d '{"query":"关键词","limit":5}'
# CC飞总 capture 心跳日志（成功/失败诊断）
docker compose exec claude-code tail -f /home/node/.myagentdata/tdai-memory/capture-hook.log
./scripts/setup-openclaw-memory.sh                             # 虾酱 OpenClaw memory plugin（独立体系 local 模式）

# Paper pipeline — mylibrary (hydrolitagent) build-time install, local-first + git fallback
# run_paper_pipeline.sh installed from mylibrary source at build time.
# Uses python3 -m hydrolitagent.zotero.paper_to_zotero internally.
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh '<DOI>'       # One-shot pipeline
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh --dry-run '<DOI>'  # Preview only
docker compose exec hermes-coder python3 -m hydrolitagent.zotero.paper_to_zotero --help  # Direct invocation

# 道元 (hermes-daoyuan) — 文献学者 Agent，飞书 bot，Zotero 只读
docker compose exec hermes-daoyuan /opt/hermes/.venv/bin/hermes mcp test zotero   # MCP 连接测试
docker compose exec -it hermes-daoyuan /opt/hermes/.venv/bin/hermes               # 交互式终端
docker compose logs -f hermes-daoyuan                                              # 道元日志
# 道元飞书配置: DAOYUAN_FEISHU_APP_ID/SECRET + 群内开放 (FEISHU_GROUP_POLICY=open)
# 道元 Zotero 只读: DAOYUAN_ZOTERO_API_KEY (仅 Allow library access)

# dailyinfo launchd scheduling
./scripts/launchd/install-dailyinfo.sh
./scripts/launchd/uninstall-dailyinfo.sh

# Morning triage — Hermes cron skill (morning-triage-v2)
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list | grep "Daily Command"  # 查看 cron 状态
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron run <job_id>                 # 手动触发

# Gateway error loop detection（检测 OpenClaw 配置兼容性导致的日志刷屏）
./scripts/check-gateway-errors.sh            # 人类可读
./scripts/check-gateway-errors.sh --json     # JSON 输出（适合 cron/监控）

# Monitoring（Uptime Kuma + Healthchecks.io）
open http://localhost:3001                                    # Uptime Kuma 监控面板
./scripts/launchd/install-all-schedulers.sh                   # 一键安装所有宿主机定时任务
./scripts/launchd/install-healthchecks-ping.sh                # 单独安装 Healthchecks.io 心跳任务
launchctl start ai.myopenclaw.healthchecks-ping               # 手动触发心跳
tail -f logs/healthchecks-ping.log                            # 查看心跳日志

# AgentOps auto-collection（morning-triage 数据采集）
python3 scripts/collect_agentops.py                           # 手动运行采集
python3 scripts/collect_agentops.py --dry-run                 # 预览模式（不写入 ledger）
./scripts/launchd/install-collect-agentops.sh                 # 安装每天 7:45 定时采集
launchctl start ai.myopenclaw.collect-agentops                # 手动触发采集
tail -f logs/collect-agentops.log                             # 查看采集日志

# Repo scanner — git-contribution-stats（27 仓库每日采集 + 研发日报推送）
cd ~/code/git-contribution-stats && python3 scripts/collect.py           # 手动全量采集（写入 SQLite）
cd ~/code/git-contribution-stats && python3 scripts/collect.py --dry-run # 预览模式
python3 ~/code/git-contribution-stats/core/report.py                     # 查看日报数据
bash ~/code/git-contribution-stats/scripts/launchd/install-collect.sh    # 安装每天 07:45 采集
launchctl start ai.git-contribution-stats.collect                        # 手动触发采集
tail -f ~/code/git-contribution-stats/logs/collect.log                   # 查看采集日志

# Daily dev report — Hermes skill（研发日报 MCP + LLM + 飞书推送）
docker compose exec hermes /opt/hermes/.venv/bin/hermes cron list | grep daily-dev  # 查看 cron 状态
docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp list | grep repo-scanner  # 查看 MCP 连接
docker compose exec repo-scanner-mcp python3 -c "from core.report import daily_report_as_dict; print(daily_report_as_dict())"  # 查看日报数据
cat /tmp/report.txt | docker compose exec -T hermes python3 /opt/hermes-skills/daily-dev-report/tools/send_card.py  # 手动推送测试

# zhixun 飞书机器人（知汛助手）— 独立 Compose 栈，水文查询专用
# 使用独立的 .env.zhixun-bot 配置 + docker-compose.zhixun-bot.yml
# 依赖外部 zhixun-agent 仓库（../zhixun-agent，需提前 clone）
./scripts/start-zhixun-bot.sh --build                        # 首次启动（构建 MCP 镜像 + 拉取 OpenClaw）
./scripts/start-zhixun-bot.sh                                # 启动（使用已有镜像）
docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml ps     # 服务状态
docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml logs -f openclaw-zhixun  # 网关日志
docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml logs -f zhixun-water-mcp  # MCP 日志
docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml exec openclaw-zhixun node /app/openclaw.mjs mcp probe water_unified --json  # MCP 工具列表（43 tools）
docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml stop   # 停止服务

# tianyi 飞书机器人（天一研发助手）— 独立 Compose 栈，GitHub/GitCode 仓库访问专用
# 使用独立的 .env.tianyi-bot 配置 + docker-compose.tianyi-bot.yml
# 前提：需要主栈已启动（共享 myopenclaw-net，复用 repo-scanner-mcp）
./scripts/start-tianyi-bot.sh --build                        # 首次启动
./scripts/start-tianyi-bot.sh                                # 启动（使用已有镜像）
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml ps     # 服务状态
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml logs -f openclaw-tianyi  # 网关日志
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml exec openclaw-tianyi node /app/openclaw.mjs mcp probe repo-scanner --json  # MCP 连接测试
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml exec openclaw-tianyi gh --version  # gh CLI 版本
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml exec openclaw-tianyi gc --version  # gitcode-cli 版本
docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml stop   # 停止服务
```

## ⚠️ OpenClaw 配置安全规则

OpenClaw 网关（Docker 镜像, 端口 18789）负责 虾酱 Discord 主机器人。配置文件 `~/.openclaw/openclaw.json` 通过 volume mount 挂载到容器内。

**禁止从 host 运行任何会写入配置的 openclaw 命令**，必须在 Docker 容器内操作：

```bash
# ❌ 禁止（host 的 npm 版本可能与 Docker 镜像版本不同，写出的配置格式 Docker 不认识）
openclaw doctor --fix
openclaw config set ...

# ✅ 正确（在容器内操作，使用 Docker 镜像的版本）
docker compose run --rm --entrypoint "node" openclaw-gateway openclaw.mjs doctor --fix
docker compose run --rm --entrypoint "node" openclaw-gateway openclaw.mjs config set ...
```

**原因**：2026.3.31 因为 host 上运行的 `openclaw doctor --fix` 写出了 Docker 不认识的 streaming 配置格式，导致 gateway.err.log 在 3 个月内增长到 762MB（2380 万行重复错误），无人察觉。

**升级流程**：
```bash
# 1. 更新 .env 中的 OPENCLAW_IMAGE（默认 latest 自动跟随最新 stable）
# 2. start.sh 启动前会自动 docker compose pull 拉取最新镜像
./scripts/start.sh
```

**zhixun bot 配置独立**：zhixun 飞书机器人使用独立的 `openclaw.json`（位于 `~/.openclaw-zhixun/`），不与虾酱主配置共享。配置由 `render-config.mjs` 从 `openclaw.json.template` 渲染生成，凭据从 `.env.zhixun-bot` 注入。修改 zhixun bot 配置需在容器内操作：
```bash
docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml run --rm --entrypoint "node" openclaw-zhixun openclaw.mjs config validate
```

## Architecture

**Ten Docker services** orchestrated by `docker-compose.yml` on a shared `myopenclaw-net` bridge network (13 total including profile-gated containers). Plus two **separate bot stacks**: **zhixun** (`docker-compose.zhixun-bot.yml`, isolated `zhixun-bot-net`) and **tianyi** (`docker-compose.tianyi-bot.yml`, shares `myopenclaw-net`).

0. **uptime-kuma** — Official `louislam/uptime-kuma:latest` image. Port 3001. Monitors all service HTTP endpoints + Docker container status via mounted Docker socket (ro). Alerts to Feishu group webhook. Resource limits: 512M/0.5 CPU. Full setup: `docs/monitoring.md`.

1. **hermes** — Custom image (`docker/hermes/Dockerfile`) extending `nousresearch/hermes-agent:latest` with gh CLI, opencode-ai, himalaya (CLI email client), ortie (OAuth token broker for Outlook), cardamum (CLI contact manager), lark-cli (Feishu CLI), rclone (Google Drive), and mylibrary (hydrolitagent, build-time install). Entry point is `entrypoint-wrapper.sh` which symlinks gh/himalaya/ortie/cardamum/lark-cli config dirs, auto-initializes lark-cli/himalaya/ortie/cardamum/zot configs from env vars, and sets `OPENCODE_CONFIG_DIR` before handing off to the original Hermes entrypoint. Profiles: default (爱玛士, port 8642, Feishu), coder (爱码士, 8643, Discord, paper injection), finance (8644, Feishu).

2. **claude-code** — Custom image (`docker/claude-code/Dockerfile`) based on `ubuntu:24.04` with Python 3.12, uv, build-essential, Node.js 22 (tarball), Claude Code CLI, cc-connect, git, and gh CLI (direct binary). Creates a `node` user for volume mount compatibility. cc-connect bridges Claude Code to Feishu via WebSocket (no public IP needed). Entry point is `entrypoint.sh` which symlinks config dirs, sets up git credential helper (GITHUB_TOKEN for private repo access), creates code directory skeleton (`~/code/opensource/`, `~/code/OuyangWenyu/`, `~/code/iHeadWater/`), maps `DEEPSEEK_API_KEY → ANTHROPIC_API_KEY`, sets `ANTHROPIC_BASE_URL` (DeepSeek Anthropic-compatible endpoint), bootstraps ECC on first run, then runs `cc-connect` as the main process. Claude Code uses `deepseek-v4-flash` as the default model; the Opus tier maps to `deepseek-v4-pro` via `ANTHROPIC_DEFAULT_OPUS_MODEL` (see entrypoint). Port 9090 (cc-connect web admin).

3. **openclaw-gateway** — Stock `ghcr.io/openclaw/openclaw:latest` image. Port 18789. Has healthcheck via `/healthz`.

4. **backup-cron** — Alpine image (`docker/backup-cron/Dockerfile`) with rsync + sqlite3. Runs crond with a single job calling `backup-all-docker.sh`. Also executes an initial backup on container startup.

5. **hermes-dashboard** — Stock Hermes image running `dashboard --host 0.0.0.0`. Read-only, shares the hermes data volume.

6. **tdai-memory** — Custom image (`docker/tdai-memory/Dockerfile`) based on `ubuntu:24.04` with Node.js 22 and `@tencentdb-agent-memory/memory-tencentdb@0.3.6`. Port 8420. Provides shared L0→L3 memory pipeline (Gateway HTTP API) for personal agents. LLM backend: DeepSeek (`TDAI_LLM_API_KEY` env). Data stored at `~/.myagentdata/tdai-memory/`. Resource limit 1G (OOM at 512M during large-JSON init). 4 agents share this Gateway bidirectionally — see **Agent Memory (TDAI)** design decision below.

7. **repo-scanner-mcp** — Custom image from `../git-contribution-stats` (`docker/mcp-server/Dockerfile`) using `python:3.12-slim` + `mcp==1.28.1`. Port 8001. Streamable HTTP MCP server exposing 3 tools: `get_daily_report` (person-centric daily R&D report), `query_commits` (raw commit query), `query_authors` (active authors). Data source: `~/.myagentdata/repo-scanner/repos.sqlite` (read-only mount). Used by Hermes via MCP client (`~/.hermes/config.yaml` → `mcp_servers.repo-scanner`). Resource limits: 256M/0.5 CPU.

8. **zotero-mcp** — Custom image (`docker/zotero-mcp/Dockerfile`) using `python:3.12-slim` + `mcp` + `httpx` + `pyzotero`. Port 8002. SSE MCP server exposing 12 tools:
   - Web API (api.zotero.org): `zotero_search` / `zotero_fulltext_search` / `zotero_search_by_tag` / `zotero_get_item` / `zotero_get_attachments` / `zotero_get_annotations` / `zotero_get_recent` / `zotero_get_collection_items` / `zotero_get_collections` / `zotero_get_tags`
   - Local API (host.docker.internal:23119): `zotero_get_fulltext` / `zotero_get_file_info`
   Requires Zotero Desktop running on host with local API enabled. Accessible by any agent on `myopenclaw-net` via `http://zotero-mcp:8002/mcp`. Source code owned by mylibrary, consumed at build time. Resource limits: 256M/0.5 CPU.

9. **hermes-daoyuan** (道元·文献学者) — Separate container using the hermes image with `--profile daoyuan`. Port 8645. Connected to Feishu via independent bot (`DAOYUAN_FEISHU_APP_ID/SECRET`), group-open access (`FEISHU_GROUP_POLICY=open` + `GATEWAY_ALLOW_ALL_USERS=true`). Zotero access is **read-only** via Zotero MCP — can query the library but cannot create/modify items. Uses `zotero-query` skill for MCP-based literature queries. Memory is **isolated** — uses Hermes built-in memory (`memory_enabled: true`, no TDAI provider), not shared with other agents. Paper injection capability (paper-to-zotero) is intentionally restricted to 爱码士 (coder profile). Resource limits: 4G/2 CPU.

**zhixun bot stack** (`docker-compose.zhixun-bot.yml`, independent `zhixun-bot-net` network, managed separately from the main stack):

10. **zhixun-water-mcp** — Custom image (`docker/zhixun-bot/Dockerfile.mcp`) using `python:3.12-slim` + MCP + httpx + pypinyin. SSE MCP server on port 18201. Wraps the upstream Water MCP from zhixun-agent with 3 compatibility layers: `zhixun_core_v2_compat.py` (station name index, v2 response parsing), `related_page_compat.py` (auto-attaches frontend page links to query results), `briefing_compat.py` (hydromodel routing). 43 MCP tools total, 15 write tools filtered by default. Build uses BuildKit multi-context to copy `mcp_servers/water/` from `../zhixun-agent`. Resource limits: 1G/1 CPU.

11. **openclaw-zhixun** — Stock `docker.m.daocloud.io/openclaw/openclaw:2026.7.1` image with custom entrypoint. Port 18791 (loopback only, not exposed). Connected to Feishu via independent bot (`ZHIXUN_BOT_FEISHU_APP_ID/SECRET`), group-open (`groupPolicy: open` + `requireMention: true`) + DM-open. Model: deepseek-v4-flash with independent API key. Only MCP tools allowed (no code execution, browser, or file access). Uses `render-config.mjs` to inject credentials into `openclaw.json.template` at startup. Resource limits: 2G/1 CPU.

**tianyi bot stack** (`docker-compose.tianyi-bot.yml`, shares `myopenclaw-net` network with main stack, managed separately):

12. **openclaw-tianyi** (天一研发助手) — Stock `docker.m.daocloud.io/openclaw/openclaw:2026.7.1` image with custom entrypoint. Port 18792 (loopback only, not exposed). Connected to Feishu via independent bot (`TIANYI_BOT_FEISHU_APP_ID/SECRET`), group-open (`groupPolicy: open` + `requireMention: true`) + DM-open. Model: deepseek-v4-flash with independent API key. **Coding profile** (terminal + MCP): reads repo activity via shared `repo-scanner-mcp`, creates GitHub/GitCode issues via `gh` and `gc` CLI (installed at startup in entrypoint). No code execution sandbox. Data dir: `~/.openclaw-tianyi`. Requires main stack running (repo-scanner-mcp). Resource limits: 2G/1 CPU.

**Backup pipeline**: `backup-all-docker.sh` → calls individual `hermes/scripts/backup.sh`, `openclaw/scripts/backup.sh`, `claude/scripts/backup.sh`, `scripts/backup-data.sh`, and `tdai-memory/scripts/backup.sh` in sequence, tracking per-step failures and exiting non-zero if any fail. Each script does selective rsync to timestamped snapshots under `BACKUP_ROOT`, maintains a `latest/` symlink, and prunes snapshots older than `BACKUP_KEEP_DAYS`. OpenClaw's SQLite DBs (`memory/main.sqlite` + 虾酱 `memory-tdai/memories.sqlite`) and TDAI's `memories.sqlite` use `sqlite3 .backup` for hot backup (no `cp` fallback — fails loud if sqlite3 missing). Claude Code backup covers `settings.json`, `projects/`, `skills/`, `plans/`, `tasks/` and cc-connect config.

**dailyinfo scheduling**: Managed via host launchd (not Docker). `scripts/launchd/` contains plist templates and install/uninstall scripts. dailyinfo is a sibling repo (`../dailyinfo`) with its own Docker services (FreshRSS).

**Monitoring**: Dual-layer via Uptime Kuma (service-level, Docker container) + Healthchecks.io (host-level, cloud dead man's switch). AgentOps auto-collects health signals (container restart, backup freshness, disk usage, gateway errors) daily at 07:45 for morning-triage. See `docs/monitoring.md` and `docs/agentops.md` for full architecture and setup instructions. Healthchecks.io heartbeat via host launchd every 60s.

## Key Design Decisions

- **Secret isolation**: Hermes holds its own keys; Claude Code holds its own keys; OpenClaw holds none of the personal key domains — the single exception is `XIAOMI_API_KEY`. All keys are configured in `.env`. Hermes keys blocked by its env blacklist (DEEPSEEK, OPENROUTER, OPENAI) are passed into the container via docker-compose, then materialized into `/opt/data/secrets/` files by the entrypoint wrapper (before Hermes starts), so opencode.json can reference them via `{file:}`. Keys not on the blacklist (GH_TOKEN→GITHUB_TOKEN, OPENCODE_API_KEY, LARK_CLI_APP_ID/SECRET, LARK_CLI_IDM_APP_ID/SECRET) pass through `.env` + `env_passthrough`. Claude Code keys (DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, GITHUB_TOKEN, CC_CONNECT_FEISHU_APP_ID/SECRET) are passed directly to the claude-code container. `ANTHROPIC_API_KEY` is set from `DEEPSEEK_API_KEY` by the entrypoint; `ANTHROPIC_BASE_URL` defaults to DeepSeek's Anthropic-compatible endpoint (`https://api.deepseek.com/anthropic`). `XIAOMI_API_KEY` (小米 MiMo) is injected into hermes / hermes-coder (爱玛士/爱码士 `mimo-v2.5`(-pro) 主模型) and openclaw-gateway (虾酱 `mimo-v2.5` 主模型 + `mimo-v2.5-tts` 语音回复, see `openclaw/config/openclaw.json.example` messages.tts).

- **Tool config persistence**: Host-side config persistence via volume mounts + symlinks: gh (`~/.config/gh` → `/opt/gh-config`, symlinked in both Hermes and claude-code), opencode (`~/.config/opencode` → `/opt/opencode-config`, via `OPENCODE_CONFIG_DIR`), Claude Code (`~/.claude` → `/opt/claude-config`, symlinked), cc-connect (`~/.cc-connect` → `/opt/cc-config`, symlinked), lark-cli (`~/.lark-cli` → `/opt/lark-config`, symlinked), himalaya (`~/.hermes/.config/himalaya/` on `/opt/data` volume, auto-generated by entrypoint wrapper from `EMAIL_*` vars in `~/.hermes/.env`, symlinked to `/root/.config/himalaya` and `/opt/data/home/.config/himalaya`), ortie (`~/.hermes/.config/ortie/` on `/opt/data` volume, Outlook OAuth tokens + config from `EMAIL_OUTLOOK_*`, same symlink pattern), cardamum (`~/.hermes/.contacts/` on `/opt/data` volume, auto-generated by entrypoint wrapper with vdir backend, symlinked for root access). First-run initialization in `start.sh` seeds config from `.example` templates. cc-connect config uses `${VAR_NAME}` for env var substitution, filled at runtime by cc-connect itself. lark-cli profiles are auto-initialized by `entrypoint-wrapper.sh` from `LARK_CLI_APP_ID/SECRET` and `LARK_CLI_IDM_APP_ID/SECRET` env vars; OAuth authorization (`lark-cli auth login`) must be done manually after first deploy.

- **Two config files**: `.env` (ports, cron, non-sensitive keys) and `.cloud.conf` (cloud drive paths, machine-specific). Both are gitignored; `.example` templates are committed.

- **Cloud-agnostic backups**: `BACKUP_ROOT` is resolved at runtime from `.cloud.conf` (Google Drive / OneDrive / custom). The host-side scripts (`backup-all.sh`, `restore.sh`) read `.cloud.conf`; the container-side script (`backup-all-docker.sh`) just uses `BACKUP_ROOT=/backup` from the volume mount.

- **Container paths differ from host paths**: Inside backup-cron, hermes data is at `/root/.hermes` (HOME=/root), openclaw at `/root/.openclaw`, claude at `/root/.claude`, cc-connect at `/root/.cc-connect`. Inside hermes container, home is `/opt/data`. Inside claude-code container, home is `/home/node`. The entrypoint wrappers create symlinks so tools find their config at expected paths.

- **Hermes email**: Email is intentionally NOT used as a Hermes messaging platform (risk of auto-replying to anyone who sends an email). Instead, [himalaya](https://github.com/pimalaya/himalaya) v1.2.0 is installed as a CLI email tool — Hermes can list/read/search/send emails only when explicitly instructed. himalaya config at `~/.hermes/.config/himalaya/config.toml` is auto-generated by entrypoint wrapper on first run (parses `EMAIL_*` vars from `~/.hermes/.env`, works whether commented out or not). Config persists on `/opt/data` volume; symlinked to `/root/.config/himalaya` and the terminal HOME. QQ mail: IMAP port 993 (TLS), SMTP port 587 (STARTTLS — not 465). Server IP `58.254.165.67` must be in Astrill whitelist. The `~/.hermes/.env` EMAIL_* vars are kept commented out to prevent Hermes from using email as a messaging platform. **Multi-account**: Supports multiple email accounts via `[accounts.xxx]` TOML sections. Entrypoint auto-generates a second password account from `EMAIL2_*` vars. **Outlook / Microsoft 365**: Microsoft retired basic auth, so Outlook uses [ortie](https://github.com/pimalaya/ortie) v2.2.0 as an OAuth broker (device grant by default, Thunderbird public client, IMAP/SMTP + XOAUTH2 — not Microsoft Graph). Non-secret `EMAIL_OUTLOOK_*` vars live in the repo-root `.env` (injected via docker-compose into all four hermes services; see `.env.example`), unlike QQ/DLUT passwords which stay in `~/.hermes/.env`. The wrapper loads EMAIL_* as shell-local vars with compose-env precedence (never exported into the Hermes process env) and calls `configure-outlook.sh` in a degraded mode — a config error warns and skips, it must not crash the container. tokens live at `~/.hermes/.config/ortie/tokens/` and are covered by hermes backup. First-time auth is interactive: `docker compose exec -u hermes -it hermes ortie auth get -a outlook`. Switch with `-a <account>` flag on himalaya commands; default (no `-a`) uses the `[accounts.default]` entry.

- **Contacts (cardamum)**: [cardamum](https://github.com/pimalaya/cardamum) v0.2.0 is built from source (Rust multi-stage Docker build, latest rev `771879c`) as the only binary release (v0.1.0) uses the old `$EDITOR`-based `cards create` flow. Uses **vdir** backend — contacts stored as `.vcf` files in `~/.hermes/.contacts/` (persists on `/opt/data` volume, backed up by backup-cron). Config at `~/.hermes/home/.config/cardamum/config.toml` auto-generated by entrypoint wrapper; symlinked to `/root/.config/cardamum/` for root access. Addressbook is auto-created on first run; its UUID is persisted as `addressbook.default` so `cardamum card list` works without `-k`. Key commands: `cardamum card list` (list contacts, uses default addressbook), `cardamum card read <id>` (read details), `echo '...' | cardamum card create -` (add via stdin — v0.2.0 accepts vCard content directly, no `$EDITOR` needed). QQ mail and DLUT (Coremail) do not support CardDAV, so the vdir local backend is used instead. Contacts are included in cloud backups via `hermes/scripts/backup.sh`.

- **Google Drive (rclone)**: rclone v1.69.2 is installed in the hermes image for direct Google Drive API uploads. OAuth token stored in `~/.hermes/rclone/rclone.conf` (chmod 600, not in git). Remote `gdrive:` is scoped to a target folder via `root_folder_id`. Hermes uses `rclone copy <pdf> gdrive:` to upload papers. Full setup guide: `docs/google-drive-rclone.md`.

- **Hermes coder Discord + Zotero**: hermes-coder (爱码士, port 8643, model mimo-v2.5-pro via xiaomi provider) is connected to Discord via `DISCORD_BOT_TOKEN` env var. Access restricted to a single user via `DISCORD_ALLOWED_USERS`. This is a separate Discord Bot from OpenClaw's 虾酱. Has full Zotero write access — paper-to-zotero pipeline downloads PDFs, uploads to Google Drive, and creates Zotero entries with linked_file attachments. Zotero query is via the shared zotero-mcp service (port 8002).

- **Zotero access model — write vs read-only**: 爱码士 (coder) has full write access via `ZOTERO_API_KEY` for paper injection. 道元 (daoyuan) has **read-only** access via `DAOYUAN_ZOTERO_API_KEY` — can query the library but cannot create/modify items. This separation is enforced at the Zotero API key level (read-only key only has "Allow library access", no write permission).

- **zhixun bot — fully isolated stack**: ... Full docs: `docs/zhixun-feishu-bot.md`.

- **tianyi bot (天一) — shared-network stack**: The tianyi feishu bot runs as a separate Compose stack but shares `myopenclaw-net` with the main stack (unlike zhixun's fully isolated network). This allows it to reuse the existing `repo-scanner-mcp` for read operations (daily reports, commits, authors). For write operations, the entrypoint installs `gh` CLI (APT) and `gitcode-cli` (npm) at startup — the same pattern used by the Hermes Dockerfile. The agent uses `profile: "coding"` (terminal + MCP) rather than `bundle-mcp`, so it can run CLI commands directly. Startup dependency: main stack must be running first (`scripts/start.sh` before `scripts/start-tianyi-bot.sh`). Data dir: `~/.openclaw-tianyi`.

- **mylibrary (hydrolitagent) — build-time install, local-first**: Paper pipeline code (paper_to_zotero, zot_link_gdrive, run_paper_pipeline.sh) lives in `~/code/mylibrary` and is installed into the hermes image at build time. `start.sh` rsyncs the local source into the Docker build context before `docker compose build`; the Dockerfile installs via `uv pip install` (with `--no-deps` to avoid mcp 2.0 conflicts with the Hermes agent). When local source is unavailable (CI / remote), falls back to `git clone --depth 1`. Skills from the same source are copied to `/opt/mylibrary-skills/` and registered via `external_dirs` in Hermes config. See `docs/zotero-cli-cc.md` for legacy zotero-cli-cc docs.

- **Agent Memory (TDAI) — bidirectional cross-agent sharing**: 4 personal agents share long-term memory (L0→L3) via the tdai-memory Gateway. **Two physically-isolated systems** (separate SQLite files, not permission-based): personal (`~/.myagentdata/tdai-memory/`, 4 agents) and 虾酱 (`~/.openclaw/memory-tdai/`, multi-user OpenClaw plugin, local mode). Three integration paths, each with a critical gotcha learned during integration:
  - **Hermes adapter** (default/爱玛士/finance): The npm package ships a Python `MemoryProvider` at `hermes-plugin/memory/memory_tencentdb/`. `entrypoint-wrapper.sh` installs it at **runtime** (not Dockerfile — avoids cardamum cache invalidation), deploys via `cp -r` (NOT symlink — Hermes's plugin scanner doesn't follow symlinks), and injects `provider: memory_tencentdb` (NOT `_v2`) into the `memory:` section only (section-scoped, so `delegation.provider` isn't clobbered). The provider reads the Gateway address from env `MEMORY_TENCENTDB_GATEWAY_HOST`/`_PORT` (NOT config.yaml `gateway_url`). Writes happen automatically via provider lifecycle hooks (`sync_turn`/`on_session_end`).
  - **CC飞总 read** (claude-code): MCP server `docker/tdai-memory/mcp-server/server.py` (stdio, 4 read tools: `memory_search`/`conversation_search`/`read_scenario`/`read_core`), registered in `settings.json` mcpServers by `entrypoint.sh`.
  - **CC飞总 write** (claude-code): `docker/claude-code/capture-to-gateway.py` Stop hook, registered in `settings.json` hooks.Stop. Every turn end, it reads the transcript's last user+assistant turn (merges contiguous assistant records, extracts only `text` blocks, skips slash-commands/caveats/tool output), POSTs to Gateway `/capture` with `session_id=personal_ccfeizong`. **Never blocks CC飞总** (exit 0 on any error) but writes a heartbeat/failure log to `~/.myagentdata/tdai-memory/capture-hook.log` (RotatingFileHandler, 1MB×2 — bounded, unlike the 762MB incident) so a broken pipeline is diagnosable. TDAI pipeline handles L1 value-filtering/dedup/layering, so raw verbosity in gets distilled to key facts.
  - **Restart auto-recovery**: `docker compose up -d` / `./scripts/start.sh` recovers all memory wiring with zero manual steps — hermes entrypoint re-installs the plugin + re-injects config; claude-code entrypoint re-registers the Stop hook. Verified by force-recreate. LLM key reuses `DEEPSEEK_API_KEY` (4th independent key domain per isolation philosophy). Bearer auth off (Docker internal network). Full design in `.claude/prds/agent-memory.prd.md`.

- **Daily R&D Report (repo-scanner MCP + Hermes skill)**: git-contribution-stats collects 27 repos daily (GitHub + GitCode) into SQLite (`~/.myagentdata/repo-scanner/repos.sqlite`). A streamable HTTP MCP server (`repo-scanner-mcp`, port 8001) exposes `get_daily_report` / `query_commits` / `query_authors`. Hermes `daily-dev-report` skill calls MCP → DeepSeek LLM polish → Feishu private chat push. Cron: 07:45 launchd collection → 07:55 Hermes cron push. MCP config: `~/.hermes/config.yaml` (`mcp_servers.repo-scanner` + `platform_toolsets.cli`). Skill at `skills/daily-dev-report/SKILL.md`. Full design in `.claude/prds/daily-dev-report.prd.md`.

- **Hermes image rebuild**: ✅ Fixed 2026-07-20 — cardamum pin updated to `771879c` (2026-07-18). OSError patch removed (fixed upstream in v0.18.2). Entrypoint now hands off to s6-overlay `/init` instead of deprecated `entrypoint.sh`. Image rebuilds clean with `docker compose build hermes`.

## Network & DNS

When the system DNS (e.g., overseas DNS servers) cannot resolve Chinese domains, services fail with `ENOTFOUND` / `NameResolutionError`. The fix is per-domain DNS routing via macOS `/etc/resolver/`.

**DNS resolution chain**: Container app → Docker DNS (127.0.0.11) → Host DNS → `/etc/resolver/<domain>` → 223.5.5.5 (Alibaba public DNS). Docker containers benefit automatically; no `extra_hosts` hardcoding needed in `docker-compose.yml`.

**Critical CNAME chain issue**: `api.dingtalk.com` resolves through a CNAME chain that passes through `gds.alibabadns.com` (Alibaba Cloud GSLB internal domain). This domain is outside `dingtalk.com`, so it needs its own `/etc/resolver/alibabadns.com` entry. Without it, `api.dingtalk.com` resolution fails even when `dingtalk.com` resolver is correct.

**Resolver domains** (all → 223.5.5.5): Service domains: `bigmodel.cn`, `deepseek.com`, `dingtalk.com`, `feishu.cn`, `gitcode.com`, `moonshot.cn`, `open.bigmodel.cn`, `qq.com` (QQ mail), `xiaomimimo.com` (Xiaomi MiMo API), `xiaomi.com` (Xiaomi MiMo CNAME chain), `workbuddy.cn` (WorkBuddy portal), `zhipu.ai`. CDN/GSLB external domains (required for CNAME chain resolution): `alibabadns.com` (DingTalk), `eo.dnse1.com` (DeepSeek/Volcengine CDN), `eo.dnse5.com` (WorkBuddy/Tencent EdgeOne CDN — a separate chain from dnse1), `bytedns1.com` (Feishu/ByteDance CDN), `aliyunddos1022.com` (Moonshot/Alibaba DDoS), `yundunwaf3.com` (Zhipu/Alibaba WAF), `cdngslb.com` (CDN GSLB), `gtm-a4b8.com` (Zhipu GTM), `queniuyk.com` (Feishu `open.feishu.cn` CNAME terminal, Kingsoft CDN), `queniuck.com` (Feishu `msg-frontier.feishu.cn` CNAME terminal).

**`/etc/hosts` backup entries**: `open.bigmodel.cn`, `mcp.dingtalk.com`, `wss-open-connection.dingtalk.com`, `imap.qq.com`, `smtp.qq.com`, `api.xiaomimimo.com`, `workbuddy.cn`, `www.workbuddy.cn`. These provide a safety net but IPs go stale (CDN rotation). Run `./scripts/setup-dns.sh` to refresh. Use python3 (not sed) to edit `openclaw.json` — sed with token special characters can corrupt the file.

**Setup script**: `./scripts/setup-dns.sh` — creates/updates `/etc/resolver/` entries and `/etc/hosts` backup IPs, then validates resolution. See `docs/dns-setup.md` for full documentation.

## File Layout Conventions

- `docker/<service>/Dockerfile` — custom images (hermes, claude-code, backup-cron)
- `docker/zhixun-bot/` — zhixun bot MCP Dockerfile + compat layers + config template
- `docker/tianyi-bot/` — tianyi bot entrypoint + config template + render script
- `openclaw-zhixun/workspace/` — zhixun bot agent policy files (AGENTS.md, SOUL.md)
- `openclaw-tianyi/workspace/` — tianyi bot agent policy files (AGENTS.md, SOUL.md)
- `hermes/scripts/`, `openclaw/scripts/`, `claude/scripts/` — per-service backup scripts, mounted read-only into backup-cron
- `scripts/` — top-level orchestration scripts (start, stop, restore, cloud setup, launchd, start-zhixun-bot)
- `scripts/launchd/` — macOS launchd plist 模板 + install 脚本（dailyinfo, agentops, healthchecks）
- `skills/` — 执行层 skill（morning-triage-v2 等 Hermes cron skill）
- `.secrets/` — encrypted via git-crypt (hermes.env.example, openclaw.env.example)
- All scripts use `set -euo pipefail` and Chinese-language output/emojis
