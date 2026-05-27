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

# dailyinfo launchd scheduling
./scripts/launchd/install-dailyinfo.sh
./scripts/launchd/uninstall-dailyinfo.sh
```

## Architecture

**Five Docker services** orchestrated by `docker-compose.yml` on a shared `myopenclaw-net` bridge network:

1. **hermes** — Custom image (`docker/hermes/Dockerfile`) extending `nousresearch/hermes-agent:latest` with gh CLI, opencode-ai, and lark-cli (Feishu CLI). Entry point is `entrypoint-wrapper.sh` which symlinks gh/lark-cli config dirs, auto-initializes lark-cli profiles from env vars, and sets `OPENCODE_CONFIG_DIR` before handing off to the original Hermes entrypoint. Three profiles: default (port 8642), coder (8643), finance (8644). Dashboard on port 9119.

2. **claude-code** — Custom image (`docker/claude-code/Dockerfile`) based on `node:22-slim` with Claude Code CLI, cc-connect, git, and gh CLI (direct binary). Reuses the built-in `node` user (UID 1000). cc-connect bridges Claude Code to Feishu via WebSocket (no public IP needed). Entry point is `entrypoint.sh` which symlinks config dirs, maps `GLM_API_KEY → ANTHROPIC_API_KEY`, then runs `cc-connect` as the main process. Claude Code uses Zhipu GLM models via `ANTHROPIC_BASE_URL`. Port 9090 (cc-connect web admin).

3. **openclaw-gateway** — Stock `ghcr.io/openclaw/openclaw:latest` image. Port 18789. Has healthcheck via `/healthz`.

4. **backup-cron** — Alpine image (`docker/backup-cron/Dockerfile`) with rsync + sqlite3. Runs crond with a single job calling `backup-all-docker.sh`. Also executes an initial backup on container startup.

5. **hermes-dashboard** — Stock Hermes image running `dashboard --host 0.0.0.0`. Read-only, shares the hermes data volume.

**Backup pipeline**: `backup-all-docker.sh` → calls individual `hermes/scripts/backup.sh`, `openclaw/scripts/backup.sh`, `claude/scripts/backup.sh`, and `scripts/backup-data.sh` in sequence. Each script does selective rsync to timestamped snapshots under `BACKUP_ROOT`, maintains a `latest/` symlink, and prunes snapshots older than `BACKUP_KEEP_DAYS`. OpenClaw's SQLite DB uses `sqlite3 .backup` for hot backup. Claude Code backup covers `settings.json`, `projects/`, `skills/`, `plans/`, `tasks/` and cc-connect config.

**dailyinfo scheduling**: Managed via host launchd (not Docker). `scripts/launchd/` contains plist templates and install/uninstall scripts. dailyinfo is a sibling repo (`../dailyinfo`) with its own Docker services (FreshRSS).

## Key Design Decisions

- **Secret isolation**: Hermes holds its own keys; Claude Code holds its own keys; OpenClaw holds none. All keys are configured in `.env`. Hermes keys blocked by its env blacklist (DEEPSEEK, OPENROUTER, OPENAI) are passed into the container via docker-compose, then materialized into `/opt/data/secrets/` files by the entrypoint wrapper (before Hermes starts), so opencode.json can reference them via `{file:}`. Keys not on the blacklist (GH_TOKEN→GITHUB_TOKEN, OPENCODE_API_KEY, LARK_CLI_APP_ID/SECRET, LARK_CLI_IDM_APP_ID/SECRET) pass through `.env` + `env_passthrough`. Claude Code keys (GLM_API_KEY, ANTHROPIC_API_KEY, CC_CONNECT_FEISHU_APP_ID/SECRET) are passed directly to the claude-code container.

- **Tool config persistence**: Host-side config persistence via volume mounts + symlinks: gh (`~/.config/gh` → `/opt/gh-config`, symlinked in both Hermes and claude-code), opencode (`~/.config/opencode` → `/opt/opencode-config`, via `OPENCODE_CONFIG_DIR`), Claude Code (`~/.claude` → `/opt/claude-config`, symlinked), cc-connect (`~/.cc-connect` → `/opt/cc-config`, symlinked), lark-cli (`~/.lark-cli` → `/opt/lark-config`, symlinked). First-run initialization in `start.sh` seeds config from `.example` templates. cc-connect config uses `${VAR_NAME}` for env var substitution, filled at runtime by cc-connect itself. lark-cli profiles are auto-initialized by `entrypoint-wrapper.sh` from `LARK_CLI_APP_ID/SECRET` and `LARK_CLI_IDM_APP_ID/SECRET` env vars; OAuth authorization (`lark-cli auth login`) must be done manually after first deploy.

- **Two config files**: `.env` (ports, cron, non-sensitive keys) and `.cloud.conf` (cloud drive paths, machine-specific). Both are gitignored; `.example` templates are committed.

- **Cloud-agnostic backups**: `BACKUP_ROOT` is resolved at runtime from `.cloud.conf` (Google Drive / OneDrive / custom). The host-side scripts (`backup-all.sh`, `restore.sh`) read `.cloud.conf`; the container-side script (`backup-all-docker.sh`) just uses `BACKUP_ROOT=/backup` from the volume mount.

- **Container paths differ from host paths**: Inside backup-cron, hermes data is at `/root/.hermes` (HOME=/root), openclaw at `/root/.openclaw`, claude at `/root/.claude`, cc-connect at `/root/.cc-connect`. Inside hermes container, home is `/opt/data`. Inside claude-code container, home is `/home/node`. The entrypoint wrappers create symlinks so tools find their config at expected paths.

- **Hermes email**: Configured via `~/.hermes/.env` (not project `.env`). QQ mail works with app password: `EMAIL_IMAP_HOST=imap.qq.com:993`, `EMAIL_SMTP_HOST=smtp.qq.com:587` (must use 587 — Hermes uses STARTTLS, not SSL). Server IP `58.254.165.67` must be in Astrill whitelist. Reference: https://hermes-agent.nousresearch.com/docs/zh-Hans/user-guide/messaging/email

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
- `.secrets/` — encrypted via git-crypt (hermes.env.example, openclaw.env.example)
- All scripts use `set -euo pipefail` and Chinese-language output/emojis
