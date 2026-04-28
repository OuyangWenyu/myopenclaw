# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

myopenclaw is a Docker-based deployment for running Hermes Agent (with opencode, Claude Code, and gh CLI) and OpenClaw gateway, with automated cloud-backup snapshots and dailyinfo scheduling. All persistent data lives on the host (`~/.hermes`, `~/.openclaw`, `~/.myagentdata`, `~/.claude`); this repo only holds configuration, Dockerfiles, and shell scripts.

## Common Commands

```bash
./scripts/start.sh           # Start all services (reads .env + .cloud.conf)
./scripts/start.sh --build   # Start with image rebuild (required after Dockerfile changes)
./scripts/stop.sh            # Stop all services

# Service status & logs
docker compose ps
docker compose logs -f hermes
docker compose logs -f openclaw-gateway

# Manual backup (inside backup-cron container)
docker compose exec backup-cron /scripts/backup-all-docker.sh

# Restore from snapshot
./scripts/restore.sh all latest
./scripts/restore.sh hermes 2026-04-23_090000

# OpenClaw CLI (one-shot)
docker compose --profile cli run --rm openclaw-cli

# dailyinfo launchd scheduling
./scripts/launchd/install-dailyinfo.sh
./scripts/launchd/uninstall-dailyinfo.sh
```

## Architecture

**Four Docker services** orchestrated by `docker-compose.yml` on a shared `myopenclaw-net` bridge network:

1. **hermes** — Custom image (`docker/hermes/Dockerfile`) extending `nousresearch/hermes-agent:latest` with gh CLI, opencode-ai, and Claude Code CLI. Entry point is `entrypoint-wrapper.sh` which symlinks gh/Claude Code config dirs and sets `OPENCODE_CONFIG_DIR` before handing off to the original Hermes entrypoint. Claude Code is preconfigured to use Zhipu GLM models via `ANTHROPIC_BASE_URL`. Ports: 8642 (gateway), 9119 (dashboard via separate container).

2. **openclaw-gateway** — Stock `ghcr.io/openclaw/openclaw:latest` image. Port 18789. Has healthcheck via `/healthz`.

3. **backup-cron** — Alpine image (`docker/backup-cron/Dockerfile`) with rsync + sqlite3. Runs crond with a single job calling `backup-all-docker.sh`. Also executes an initial backup on container startup.

4. **hermes-dashboard** — Stock Hermes image running `dashboard --host 0.0.0.0`. Read-only, shares the hermes data volume.

**Backup pipeline**: `backup-all-docker.sh` → calls individual `hermes/scripts/backup.sh`, `openclaw/scripts/backup.sh`, and `scripts/backup-data.sh` in sequence. Each script does selective rsync to timestamped snapshots under `BACKUP_ROOT`, maintains a `latest/` symlink, and prunes snapshots older than `BACKUP_KEEP_DAYS`. OpenClaw's SQLite DB uses `sqlite3 .backup` for hot backup.

**dailyinfo scheduling**: Managed via host launchd (not Docker). `scripts/launchd/` contains plist templates and install/uninstall scripts. dailyinfo is a sibling repo (`../dailyinfo`) with its own Docker services (FreshRSS).

## Key Design Decisions

- **Secret isolation**: Hermes holds all personal keys; OpenClaw holds none. All keys are configured in `.env`. Keys blocked by Hermes's env blacklist (DEEPSEEK, OPENROUTER, OPENAI) are passed into the container via docker-compose, then materialized into `/opt/data/secrets/` files by the entrypoint wrapper (before Hermes starts), so opencode.json can reference them via `{file:}`. Keys not on the blacklist (GH_TOKEN→GITHUB_TOKEN, OPENCODE_API_KEY, GLM_API_KEY, ANTHROPIC_API_KEY) pass through `.env` + `env_passthrough`. For Claude Code, `GLM_API_KEY` is mapped to `ANTHROPIC_API_KEY` in the entrypoint wrapper (Zhipu key takes priority).

- **Tool config persistence**: Three tools use host-side config persistence via volume mounts + symlinks: gh (`~/.config/gh` → `/opt/gh-config`, symlinked), opencode (`~/.config/opencode` → `/opt/opencode-config`, via `OPENCODE_CONFIG_DIR`), Claude Code (`~/.claude` → `/opt/claude-config`, symlinked). First-run initialization in `start.sh` seeds config from `.example` templates.

- **Two config files**: `.env` (ports, cron, non-sensitive keys) and `.cloud.conf` (cloud drive paths, machine-specific). Both are gitignored; `.example` templates are committed.

- **Cloud-agnostic backups**: `BACKUP_ROOT` is resolved at runtime from `.cloud.conf` (Google Drive / OneDrive / custom). The host-side scripts (`backup-all.sh`, `restore.sh`) read `.cloud.conf`; the container-side script (`backup-all-docker.sh`) just uses `BACKUP_ROOT=/backup` from the volume mount.

- **Container paths differ from host paths**: Inside backup-cron, hermes data is at `/root/.hermes` (HOME=/root), openclaw at `/root/.openclaw`. Inside hermes container, home is `/opt/data`. The `entrypoint-wrapper.sh` creates symlinks so gh and Claude Code find their config at the expected `$HOME/.config/gh/` and `$HOME/.claude/` respectively.

## File Layout Conventions

- `docker/<service>/Dockerfile` — custom images (hermes, backup-cron)
- `hermes/scripts/`, `openclaw/scripts/` — per-service backup scripts, mounted read-only into backup-cron
- `scripts/` — top-level orchestration scripts (start, stop, restore, cloud setup, launchd)
- `.secrets/` — encrypted via git-crypt (hermes.env.example, openclaw.env.example)
- All scripts use `set -euo pipefail` and Chinese-language output/emojis
