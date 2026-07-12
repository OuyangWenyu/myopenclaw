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

# Zotero CLI (zotero-cli-cc — Zotero literature management)
docker compose exec hermes-coder zot stats                      # Zotero library statistics
docker compose exec hermes-coder zot search "keyword" --limit 5 # Search papers

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

# Paper pipeline (paper-fetch → Google Drive → Zotero linked_file)
# One-shot: download PDF + upload to Drive + create Zotero entry with metadata + cleanup
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh '<DOI>'
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh --dry-run '<DOI>'  # Preview only

# Already in Zotero? Link an uploaded PDF to an existing entry
docker compose exec hermes-coder /opt/hermes/scripts/zot-link-gdrive.py <ZOTERO_KEY> '<filename>'

# dailyinfo launchd scheduling
./scripts/launchd/install-dailyinfo.sh
./scripts/launchd/uninstall-dailyinfo.sh

# Morning triage — MyLoop Daily Command Center
./scripts/launchd/install-morning-triage.sh     # Install daily 7:50 AM schedule
launchctl start ai.myloop.morning-triage         # Manual trigger (one-shot)
docker compose exec claude-code python3 /home/node/code/myloop/scripts/morning-triage-send.py  # Run directly in container

# Gateway error loop detection（检测 OpenClaw 配置兼容性导致的日志刷屏）
./scripts/check-gateway-errors.sh            # 人类可读
./scripts/check-gateway-errors.sh --json     # JSON 输出（适合 cron/监控）

# Monitoring（Uptime Kuma + Healthchecks.io）
open http://localhost:3001                                    # Uptime Kuma 监控面板
./scripts/launchd/install-healthchecks-ping.sh                # 安装 Healthchecks.io 心跳任务
launchctl start ai.myopenclaw.healthchecks-ping               # 手动触发心跳
tail -f logs/healthchecks-ping.log                            # 查看心跳日志

# AgentOps auto-collection（morning-triage 数据采集）
python3 scripts/collect_agentops.py                           # 手动运行采集
python3 scripts/collect_agentops.py --dry-run                 # 预览模式（不写入 ledger）
./scripts/launchd/install-collect-agentops.sh                 # 安装每天 7:45 定时采集
launchctl start ai.myopenclaw.collect-agentops                # 手动触发采集
tail -f logs/collect-agentops.log                             # 查看采集日志
```

## ⚠️ OpenClaw 配置安全规则

**两个网关共享同一份配置** `~/.openclaw/openclaw.json`：
- launchd 网关 (npm global, 端口 18790) — dailyinfo Discord 推送
- Docker 网关 (镜像, 端口 18789) — 虾酱主机器人

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

**升级流程**（保持两个网关版本一致）：
```bash
# 1. 更新 npm global 版本
npm install -g openclaw@<版本>
# 2. 更新 .env 中的 OPENCLAW_IMAGE
# 3. 拉取新镜像并重启
docker compose pull openclaw-gateway
./scripts/start.sh
```

## Architecture

**Seven Docker services** orchestrated by `docker-compose.yml` on a shared `myopenclaw-net` bridge network:

0. **uptime-kuma** — Official `louislam/uptime-kuma:latest` image. Port 3001. Monitors all service HTTP endpoints + Docker container status via mounted Docker socket (ro). Alerts to Feishu group webhook. Resource limits: 512M/0.5 CPU. Full setup: `docs/monitoring.md`.

1. **hermes** — Custom image (`docker/hermes/Dockerfile`) extending `nousresearch/hermes-agent:latest` with gh CLI, opencode-ai, himalaya (CLI email client), cardamum (CLI contact manager), lark-cli (Feishu CLI), rclone (Google Drive), and zotero-cli-cc (Zotero CLI, via uv). Entry point is `entrypoint-wrapper.sh` which symlinks gh/himalaya/cardamum/lark-cli/zot config dirs, auto-initializes lark-cli/himalaya/cardamum/zot configs from env vars, and sets `OPENCODE_CONFIG_DIR` before handing off to the original Hermes entrypoint. Three profiles: default (port 8642), coder (8643, Discord via DISCORD_BOT_TOKEN, model deepseek-v4-pro), finance (8644). Dashboard on port 9119.

2. **claude-code** — Custom image (`docker/claude-code/Dockerfile`) based on `ubuntu:24.04` with Python 3.12, uv, build-essential, Node.js 22 (tarball), Claude Code CLI, cc-connect, git, and gh CLI (direct binary). Creates a `node` user for volume mount compatibility. cc-connect bridges Claude Code to Feishu via WebSocket (no public IP needed). Entry point is `entrypoint.sh` which symlinks config dirs, sets up git credential helper (GITHUB_TOKEN for private repo access), creates code directory skeleton (`~/code/opensource/`, `~/code/OuyangWenyu/`, `~/code/iHeadWater/`), auto-symlinks myloop skills from `/home/node/code/myloop/skills/` into CC飞总's skill directory, maps `DEEPSEEK_API_KEY → ANTHROPIC_API_KEY`, sets `ANTHROPIC_BASE_URL` (DeepSeek Anthropic-compatible endpoint), bootstraps ECC on first run, then runs `cc-connect` as the main process. Claude Code uses `deepseek-v4-pro` as the default model. Port 9090 (cc-connect web admin).

3. **openclaw-gateway** — Stock `ghcr.io/openclaw/openclaw:latest` image. Port 18789. Has healthcheck via `/healthz`.

4. **backup-cron** — Alpine image (`docker/backup-cron/Dockerfile`) with rsync + sqlite3. Runs crond with a single job calling `backup-all-docker.sh`. Also executes an initial backup on container startup.

5. **hermes-dashboard** — Stock Hermes image running `dashboard --host 0.0.0.0`. Read-only, shares the hermes data volume.

6. **tdai-memory** — Custom image (`docker/tdai-memory/Dockerfile`) based on `ubuntu:24.04` with Node.js 22 and `@tencentdb-agent-memory/memory-tencentdb@0.3.6`. Port 8420. Provides shared L0→L3 memory pipeline (Gateway HTTP API) for personal agents. LLM backend: DeepSeek (`TDAI_LLM_API_KEY` env). Data stored at `~/.myagentdata/tdai-memory/`. Resource limit 1G (OOM at 512M during large-JSON init). 4 agents share this Gateway bidirectionally — see **Agent Memory (TDAI)** design decision below.

**Backup pipeline**: `backup-all-docker.sh` → calls individual `hermes/scripts/backup.sh`, `openclaw/scripts/backup.sh`, `claude/scripts/backup.sh`, `scripts/backup-data.sh`, and `tdai-memory/scripts/backup.sh` in sequence, tracking per-step failures and exiting non-zero if any fail. Each script does selective rsync to timestamped snapshots under `BACKUP_ROOT`, maintains a `latest/` symlink, and prunes snapshots older than `BACKUP_KEEP_DAYS`. OpenClaw's SQLite DBs (`memory/main.sqlite` + 虾酱 `memory-tdai/memories.sqlite`) and TDAI's `memories.sqlite` use `sqlite3 .backup` for hot backup (no `cp` fallback — fails loud if sqlite3 missing). Claude Code backup covers `settings.json`, `projects/`, `skills/`, `plans/`, `tasks/` and cc-connect config.

**dailyinfo scheduling**: Managed via host launchd (not Docker). `scripts/launchd/` contains plist templates and install/uninstall scripts. dailyinfo is a sibling repo (`../dailyinfo`) with its own Docker services (FreshRSS).

## MyLoop Integration（赛博永生）

myopenclaw 是 MyLoop 的**执行层**。MyLoop 定义 loop 设计（skill 合同、分类规则、输出格式），myopenclaw 负责执行（CC飞总、脚本、调度）。

### Skill 加载机制

myloop skills 通过 **symlink、不复制** 的方式注入 CC飞总：

```
~/code/myloop/skills/*/  ──symlink──→  ~/.claude/skills/*/
       (设计源)                              (CC飞总可读)
```

容器启动时 `entrypoint.sh` 自动检测 `/home/node/code/myloop/skills/`，存在则 symlink 全部 skill 目录。新机器只需 `git clone myloop ~/code/myloop` 即可自动加载。

```
📎 myloop skills: knowledge-sync morning-triage paper-ingest session-memory verify-and-ship weekly-digest
```

### 当前已实现的 Loop

| Loop | 状态 | 触发方式 |
|------|------|----------|
| morning-triage | ✅ MVP | launchd 每天 07:50，调用 `scripts/morning-triage-send.py` |
| session-memory | 设计完成 | 待实现 |
| knowledge-sync | 设计完成 | 待实现 |
| paper-ingest | 设计完成 | 待实现 |
| verify-and-ship | 设计完成 | 待实现 |
| weekly-digest | 设计完成 | 待实现 |

### 架构规则

- **设计归 myloop，执行归 myopenclaw**。Skill 文件永远不复制、不分叉。
- myloop skill 修改后，CC飞总 下次启动自动加载新版本（symlink 跟随）。
- 执行脚本（如 `morning-triage-send.py`）放在 myopenclaw，因为它依赖容器环境、飞书 API 凭证等执行层细节。
- cc-connect cron 不可用于 myloop skills（session→platform 解析限制），改用宿主机 launchd。

**Monitoring**: Dual-layer via Uptime Kuma (service-level, Docker container) + Healthchecks.io (host-level, cloud dead man's switch). See `docs/monitoring.md` for full architecture and setup instructions. Healthchecks.io heartbeat via host launchd every 60s.

## Key Design Decisions

- **Secret isolation**: Hermes holds its own keys; Claude Code holds its own keys; OpenClaw holds none. All keys are configured in `.env`. Hermes keys blocked by its env blacklist (DEEPSEEK, OPENROUTER, OPENAI) are passed into the container via docker-compose, then materialized into `/opt/data/secrets/` files by the entrypoint wrapper (before Hermes starts), so opencode.json can reference them via `{file:}`. Keys not on the blacklist (GH_TOKEN→GITHUB_TOKEN, OPENCODE_API_KEY, LARK_CLI_APP_ID/SECRET, LARK_CLI_IDM_APP_ID/SECRET) pass through `.env` + `env_passthrough`. Claude Code keys (DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, GITHUB_TOKEN, CC_CONNECT_FEISHU_APP_ID/SECRET) are passed directly to the claude-code container. `ANTHROPIC_API_KEY` is set from `DEEPSEEK_API_KEY` by the entrypoint; `ANTHROPIC_BASE_URL` defaults to DeepSeek's Anthropic-compatible endpoint (`https://api.deepseek.com/anthropic`).

- **Tool config persistence**: Host-side config persistence via volume mounts + symlinks: gh (`~/.config/gh` → `/opt/gh-config`, symlinked in both Hermes and claude-code), opencode (`~/.config/opencode` → `/opt/opencode-config`, via `OPENCODE_CONFIG_DIR`), Claude Code (`~/.claude` → `/opt/claude-config`, symlinked), cc-connect (`~/.cc-connect` → `/opt/cc-config`, symlinked), lark-cli (`~/.lark-cli` → `/opt/lark-config`, symlinked), himalaya (`~/.hermes/.config/himalaya/` on `/opt/data` volume, auto-generated by entrypoint wrapper from `EMAIL_*` vars in `~/.hermes/.env`, symlinked to `/root/.config/himalaya` for root access), cardamum (`~/.hermes/.contacts/` on `/opt/data` volume, auto-generated by entrypoint wrapper with vdir backend, symlinked for root access). First-run initialization in `start.sh` seeds config from `.example` templates. cc-connect config uses `${VAR_NAME}` for env var substitution, filled at runtime by cc-connect itself. lark-cli profiles are auto-initialized by `entrypoint-wrapper.sh` from `LARK_CLI_APP_ID/SECRET` and `LARK_CLI_IDM_APP_ID/SECRET` env vars; OAuth authorization (`lark-cli auth login`) must be done manually after first deploy.

- **Two config files**: `.env` (ports, cron, non-sensitive keys) and `.cloud.conf` (cloud drive paths, machine-specific). Both are gitignored; `.example` templates are committed.

- **Cloud-agnostic backups**: `BACKUP_ROOT` is resolved at runtime from `.cloud.conf` (Google Drive / OneDrive / custom). The host-side scripts (`backup-all.sh`, `restore.sh`) read `.cloud.conf`; the container-side script (`backup-all-docker.sh`) just uses `BACKUP_ROOT=/backup` from the volume mount.

- **Container paths differ from host paths**: Inside backup-cron, hermes data is at `/root/.hermes` (HOME=/root), openclaw at `/root/.openclaw`, claude at `/root/.claude`, cc-connect at `/root/.cc-connect`. Inside hermes container, home is `/opt/data`. Inside claude-code container, home is `/home/node`. The entrypoint wrappers create symlinks so tools find their config at expected paths.

- **Hermes email**: Email is intentionally NOT used as a Hermes messaging platform (risk of auto-replying to anyone who sends an email). Instead, [himalaya](https://github.com/pimalaya/himalaya) v1.2.0 is installed as a CLI email tool — Hermes can list/read/search/send emails only when explicitly instructed. himalaya config at `~/.hermes/.config/himalaya/config.toml` is auto-generated by entrypoint wrapper on first run (parses `EMAIL_*` vars from `~/.hermes/.env`, works whether commented out or not). Config persists on `/opt/data` volume; symlinked to `/root/.config/himalaya` for root access. QQ mail: IMAP port 993 (TLS), SMTP port 587 (STARTTLS — not 465). Server IP `58.254.165.67` must be in Astrill whitelist. The `~/.hermes/.env` EMAIL_* vars are kept commented out to prevent Hermes from using email as a messaging platform. **Multi-account**: Supports multiple email accounts via `[accounts.xxx]` TOML sections. Entrypoint auto-generates second account from `EMAIL2_*` vars. Switch with `-a <account>` flag on himalaya commands; default (no `-a`) uses the `[accounts.default]` entry.

- **Contacts (cardamum)**: [cardamum](https://github.com/pimalaya/cardamum) v0.2.0 is built from source (Rust multi-stage Docker build, rev `1090cad2`) as the only binary release (v0.1.0) uses the old `$EDITOR`-based `cards create` flow. Uses **vdir** backend — contacts stored as `.vcf` files in `~/.hermes/.contacts/` (persists on `/opt/data` volume, backed up by backup-cron). Config at `~/.hermes/home/.config/cardamum/config.toml` auto-generated by entrypoint wrapper; symlinked to `/root/.config/cardamum/` for root access. Addressbook is auto-created on first run; its UUID is persisted as `addressbook.default` so `cardamum card list` works without `-k`. Key commands: `cardamum card list` (list contacts, uses default addressbook), `cardamum card read <id>` (read details), `echo '...' | cardamum card create -` (add via stdin — v0.2.0 accepts vCard content directly, no `$EDITOR` needed). QQ mail and DLUT (Coremail) do not support CardDAV, so the vdir local backend is used instead. Contacts are included in cloud backups via `hermes/scripts/backup.sh`.

- **Google Drive (rclone)**: rclone v1.69.2 is installed in the hermes image for direct Google Drive API uploads. OAuth token stored in `~/.hermes/rclone/rclone.conf` (chmod 600, not in git). Remote `gdrive:` is scoped to a target folder via `root_folder_id`. Hermes uses `rclone copy <pdf> gdrive:` to upload papers. Full setup guide: `docs/google-drive-rclone.md`.

- **Hermes coder Discord + Zotero**: hermes-coder (爱码士, port 8643, model deepseek-v4-pro) is connected to Discord via `DISCORD_BOT_TOKEN` env var. Access restricted to a single user via `DISCORD_ALLOWED_USERS`. This is a separate Discord Bot from OpenClaw's 虾酱. The coder profile config at `~/.hermes/profiles/coder/config.yaml` is auto-created by `start.sh` on first run with deepseek-v4-pro as the default model. Has paper-fetch skill and rclone for paper download + Google Drive upload, plus zotero-cli-cc for Zotero library management (SQLite reads + Web API writes). Zotero data dir (`~/Zotero`) is mounted read-only; writes go through the Zotero Web API. PDFs are stored in Google Drive (not Zotero cloud) and linked to Zotero entries via `linked_file` attachments created by `paper-to-zotero.py`. Full workflow: paper-fetch download → rclone upload → paper-to-zotero (metadata + linked_file). Full docs: `docs/zotero-cli-cc.md`.

- **Agent Memory (TDAI) — bidirectional cross-agent sharing**: 4 personal agents share long-term memory (L0→L3) via the tdai-memory Gateway. **Two physically-isolated systems** (separate SQLite files, not permission-based): personal (`~/.myagentdata/tdai-memory/`, 4 agents) and 虾酱 (`~/.openclaw/memory-tdai/`, multi-user OpenClaw plugin, local mode). Three integration paths, each with a critical gotcha learned during integration:
  - **Hermes adapter** (default/爱玛士/finance): The npm package ships a Python `MemoryProvider` at `hermes-plugin/memory/memory_tencentdb/`. `entrypoint-wrapper.sh` installs it at **runtime** (not Dockerfile — avoids cardamum cache invalidation), deploys via `cp -r` (NOT symlink — Hermes's plugin scanner doesn't follow symlinks), and injects `provider: memory_tencentdb` (NOT `_v2`) into the `memory:` section only (section-scoped, so `delegation.provider` isn't clobbered). The provider reads the Gateway address from env `MEMORY_TENCENTDB_GATEWAY_HOST`/`_PORT` (NOT config.yaml `gateway_url`). Writes happen automatically via provider lifecycle hooks (`sync_turn`/`on_session_end`).
  - **CC飞总 read** (claude-code): MCP server `docker/tdai-memory/mcp-server/server.py` (stdio, 4 read tools: `memory_search`/`conversation_search`/`read_scenario`/`read_core`), registered in `settings.json` mcpServers by `entrypoint.sh`.
  - **CC飞总 write** (claude-code): `docker/claude-code/capture-to-gateway.py` Stop hook, registered in `settings.json` hooks.Stop. Every turn end, it reads the transcript's last user+assistant turn (merges contiguous assistant records, extracts only `text` blocks, skips slash-commands/caveats/tool output), POSTs to Gateway `/capture` with `session_id=personal_ccfeizong`. **Never blocks CC飞总** (exit 0 on any error) but writes a heartbeat/failure log to `~/.myagentdata/tdai-memory/capture-hook.log` (RotatingFileHandler, 1MB×2 — bounded, unlike the 762MB incident) so a broken pipeline is diagnosable. TDAI pipeline handles L1 value-filtering/dedup/layering, so raw verbosity in gets distilled to key facts.
  - **Restart auto-recovery**: `docker compose up -d` / `./scripts/start.sh` recovers all memory wiring with zero manual steps — hermes entrypoint re-installs the plugin + re-injects config; claude-code entrypoint re-registers the Stop hook. Verified by force-recreate. LLM key reuses `DEEPSEEK_API_KEY` (4th independent key domain per isolation philosophy). Bearer auth off (Docker internal network). Full design in `.claude/prds/agent-memory.prd.md`.

- **⚠️ Hermes image cannot be rebuilt with `docker compose build`**: The current `myopenclaw/hermes:latest` was patched (based on an existing image, COPYing the updated `entrypoint-wrapper.sh`) rather than built clean, because the cardamum Rust build stage fails against an upstream `io-addressbook` incompatible change (unrelated to this project). **Daily `docker compose up` / restart / host reboot all work fine** (uses the existing image + entrypoint automation). Only `docker compose build hermes` or `up --build` hits the cardamum error. Do NOT touch the cardamum stage — contacts work as-is. If the hermes image ever needs a clean rebuild, the cardamum upstream issue must be fixed first (or pin to a compilable rev / use the v0.1.0 binary).

## Network & DNS

When the system DNS (e.g., overseas DNS servers) cannot resolve Chinese domains, services fail with `ENOTFOUND` / `NameResolutionError`. The fix is per-domain DNS routing via macOS `/etc/resolver/`.

**DNS resolution chain**: Container app → Docker DNS (127.0.0.11) → Host DNS → `/etc/resolver/<domain>` → 223.5.5.5 (Alibaba public DNS). Docker containers benefit automatically; no `extra_hosts` hardcoding needed in `docker-compose.yml`.

**Critical CNAME chain issue**: `api.dingtalk.com` resolves through a CNAME chain that passes through `gds.alibabadns.com` (Alibaba Cloud GSLB internal domain). This domain is outside `dingtalk.com`, so it needs its own `/etc/resolver/alibabadns.com` entry. Without it, `api.dingtalk.com` resolution fails even when `dingtalk.com` resolver is correct.

**Resolver domains** (all → 223.5.5.5): Service domains: `bigmodel.cn`, `deepseek.com`, `dingtalk.com`, `feishu.cn`, `gitcode.com`, `moonshot.cn`, `open.bigmodel.cn`, `qq.com` (QQ mail), `zhipu.ai`. CDN/GSLB external domains (required for CNAME chain resolution): `alibabadns.com` (DingTalk), `eo.dnse1.com` (DeepSeek/Volcengine CDN), `bytedns1.com` (Feishu/ByteDance CDN), `aliyunddos1022.com` (Moonshot/Alibaba DDoS), `yundunwaf3.com` (Zhipu/Alibaba WAF), `cdngslb.com` (CDN GSLB), `gtm-a4b8.com` (Zhipu GTM).

**`/etc/hosts` backup entries**: `open.bigmodel.cn`, `mcp.dingtalk.com`, `wss-open-connection.dingtalk.com`, `imap.qq.com`, `smtp.qq.com`. These provide a safety net but IPs go stale (CDN rotation). Run `./scripts/setup-dns.sh` to refresh. Use python3 (not sed) to edit `openclaw.json` — sed with token special characters can corrupt the file.

**Setup script**: `./scripts/setup-dns.sh` — creates/updates `/etc/resolver/` entries and `/etc/hosts` backup IPs, then validates resolution. See `docs/dns-setup.md` for full documentation.

## File Layout Conventions

- `docker/<service>/Dockerfile` — custom images (hermes, claude-code, backup-cron)
- `hermes/scripts/`, `openclaw/scripts/`, `claude/scripts/` — per-service backup scripts, mounted read-only into backup-cron
- `scripts/` — top-level orchestration scripts (start, stop, restore, cloud setup, launchd)
- `scripts/morning-triage-send.py` — MyLoop morning-triage 执行脚本（读 ledger → 分类 → 飞书推送）
- `scripts/launchd/` — macOS launchd plist 模板 + install 脚本（dailyinfo, morning-triage）
- `skills/` — 执行层 skill（仅 myopenclaw 特有的 skill；myloop skills 通过 symlink 加载，不放在这里）
- `.secrets/` — encrypted via git-crypt (hermes.env.example, openclaw.env.example)
- All scripts use `set -euo pipefail` and Chinese-language output/emojis
