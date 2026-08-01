# Plan: Documentation Overhaul + Portability Audit

**Branch**: `worktree-issue-42-docs-overhaul`
**Complexity**: Medium
**Type**: Docs-only — no Dockerfiles, no docker-compose.yml, no runtime changes.

## Summary

Current README is 674 lines of dense technical manual — setup guides, architecture deep-dives, email config, contacts, lark-cli, TDAI memory, dailyinfo scheduling, Google Drive rclone, backup details, and security boundaries all in one file. A README should showcase what the project does and why it exists; detailed how-to belongs in docs. This plan splits README into a ~150-line capability showcase + a mkdocs-powered docs site deployable to GitHub Pages, then audits and documents what breaks on a fresh machine.

## Current State

| Aspect | Now |
|--------|-----|
| README | 674 lines — 11 services, 8 setup steps, 5 deep-dive sections |
| docs/ | 7 files, ~930 lines — good standalone pages, no index/nav |
| mkdocs | **Does not exist** — no `mkdocs.yml`, no `pyproject.toml` |
| Package mgmt | None for this repo — pure Docker + shell scripts |
| Portability | Multiple hardcoded sibling-repo paths, macOS-only launchd, 2 build contexts outside repo |

## Ecosystem Map

```
myopenclaw (this repo)
  │
  ├─ docker-compose builds FROM:
  │   ├─ ../aisecretary              ← sibling repo (build context)
  │   └─ ../git-contribution-stats   ← sibling repo (build context)
  │
  ├─ docker-compose mounts FROM:
  │   ├─ ~/code:/home/node/code      ← all sibling repos live here
  │   ├─ ~/code/myloop               ← skills symlink for CC飞总
  │   ├─ ~/code/dailyinfo            ← launchd scheduling + ai-news-weekly-polish skill
  │   └─ ~/code/aisecretary          ← skills mount for Hermes
  │
  ├─ Data persisted on host:
  │   ├─ ~/.hermes/       (Hermes config, SOUL.md, memories, skills, cron)
  │   ├─ ~/.claude/       (Claude Code settings, projects, skills)
  │   ├─ ~/.openclaw/     (OpenClaw config, agents, flows, memory)
  │   ├─ ~/.cc-connect/   (cc-connect Feishu bridge config)
  │   ├─ ~/.myagentdata/  (tdai-memory, aisecretary, dailyinfo, repo-scanner)
  │   ├─ ~/.config/gh/    (GitHub CLI auth)
  │   ├─ ~/.config/opencode/ (opencode config)
  │   └─ ~/.uptime-kuma/  (monitoring SQLite + config)
  │
  └─ Scheduling (macOS launchd):
      ├─ dailyinfo (7 timers: arxiv, papers, code, ai_news, push ×3)
      ├─ morning-triage (07:50 daily)
      ├─ healthchecks-ping (every 60s)
      ├─ collect-agentops (07:45 daily)
      └─ repo-triage (optional)
```

## Patterns to Mirror

| Category | Source | What to Match |
|----------|--------|---------------|
| Script naming | `scripts/start.sh`, `scripts/stop.sh` | kebab-case or snake_case, `set -euo pipefail`, Chinese output |
| Config templates | `.env.example`, `.cloud.conf.example` | `.example` suffix, inline comments, `${VAR:-default}` |
| Doc language | existing `docs/*.md` | Chinese prose, emoji-free technical tone |
| Commit style | `git log --oneline -5` | `feat:` / `docs:` prefix |
| GitHub Actions | not present yet | No existing pattern to mirror |

## Files to Change

### Phase 1: mkdocs scaffolding

| File | Action | Why |
|------|--------|-----|
| `pyproject.toml` | CREATE | uv-managed docs toolchain — mkdocs-material only |
| `mkdocs.yml` | CREATE | Nav structure, material theme, Chinese search |
| `.github/workflows/docs.yml` | CREATE | Deploy to GitHub Pages on push to main |
| `.gitignore` | UPDATE | Add `site/` (mkdocs build output) |

### Phase 2: README rewrite

| File | Action | Why |
|------|--------|-----|
| `README.md` | REWRITE | 674 → ~150 lines. Capability showcase, ecosystem map, quick start, link to docs |

### Phase 3: Migrate README content to docs/

| File | Action | Why |
|------|--------|-----|
| `docs/index.md` | CREATE | mkdocs landing page — what this project is |
| `docs/setup.md` | CREATE | Steps 1-8 from current README, condensed and cleaned |
| `docs/architecture.md` | CREATE | Service architecture, data dirs, network, security boundaries |
| `docs/tdai-memory.md` | UPDATE | Merge README TDAI section into existing doc |
| `docs/dailyinfo.md` | CREATE | dailyinfo scheduling from README |
| `docs/email.md` | CREATE | himalaya email config from README |
| `docs/contacts.md` | CREATE | cardamum contacts from README |
| `docs/lark-cli.md` | CREATE | lark-cli setup + auth from README |
| `docs/backup.md` | CREATE | Backup pipeline from README |
| `docs/portability.md` | CREATE | Dependency graph, clone checklist, macOS/Linux differences |

### Phase 4: Portability tooling

| File | Action | Why |
|------|--------|-----|
| `scripts/clone-deps.sh` | CREATE | One-shot clone of all sibling repos to correct paths |
| `scripts/start.sh` | UPDATE | Friendly warnings for missing sibling repos (instead of silent skip) |

### Not changed

| File | Why |
|------|-----|
| `docker-compose.yml` | No runtime changes — out of scope |
| `docker/**/Dockerfile` | No runtime changes |
| `CLAUDE.md` | Already thorough — update only if doc links drift |
| All existing `docs/*.md` | Content preserved, only `tdai-memory.md` gets merged with README content |

## Proposed README Structure (target ~150 lines)

```markdown
# myopenclaw

个人多 Agent 协作平台 —— Docker Compose 一键部署，
整合 Hermes、Claude Code、OpenClaw 三个 AI Agent 框架，
配合长期记忆、飞书/Discord 桥接、每日三签、论文管线等能力。

## 能力地图

| 能力 | 实现方式 | 依赖仓库 |
|------|----------|----------|
| 多 Agent 协作 | Hermes ×3 + Claude Code + OpenClaw | — |
| 跨 Agent 长期记忆 | TDAI Memory L0→L3，4 agent 共享 | — |
| 飞书直连 | cc-connect (Claude Code) + lark-cli (Hermes) | — |
| Discord 桥接 | Hermes coder + OpenClaw 虾酱 | — |
| 晨间三签 | MyLoop morning-triage → 飞书推送 | myloop |
| AI 情报聚合 | dailyinfo → 飞书推送 | dailyinfo |
| 研发日报 | repo-scanner MCP + Hermes skill | git-contribution-stats |
| 论文管线 | paper-fetch → Google Drive → Zotero | — |
| 事务追踪 | aisecretary MCP → SQLite | aisecretary |
| 云端备份 | 定时 rsync + sqlite3 热备 → 云盘 | — |
| 服务监控 | Uptime Kuma + Healthchecks.io | — |

## 仓库配合

（依赖图 —— 哪些仓库、放在哪里、提供什么能力）

## 快速开始

（5 行：clone, cp .env, ./scripts/clone-deps.sh, ./scripts/start.sh）

## 服务一览

（紧凑表格：服务名、端口、说明）

## 文档

详细文档 → https://ouyangwenyu.github.io/myopenclaw
```

## Proposed mkdocs nav

```yaml
nav:
  - 首页: index.md
  - 快速开始: setup.md
  - 架构: architecture.md
  - 服务:
      - Hermes 渠道: hermes-channels.md
      - OpenClaw 渠道: openclaw-channels.md
      - TDAI 长期记忆: tdai-memory.md
      - 邮件 (himalaya): email.md
      - 联系人 (cardamum): contacts.md
      - 飞书 CLI (lark-cli): lark-cli.md
      - Google Drive 论文: google-drive-rclone.md
      - Zotero 文献: zotero-cli-cc.md
  - 集成:
      - MyLoop 集成: myloop-integration.md
      - dailyinfo 调度: dailyinfo.md
      - 备份系统: backup.md
      - 服务监控: monitoring.md
  - 运维:
      - DNS 配置: dns-setup.md
      - 可移植性: portability.md
```

## Portability Audit — Full Findings

### Hard dependencies (must exist to build)

| Dependency | Expected Path | Used By | Failure Mode |
|------------|---------------|---------|--------------|
| aisecretary repo | `../aisecretary` (sibling to myopenclaw) | `docker-compose.yml` build context for aisecretary service | `docker compose build` fails |
| git-contribution-stats repo | `../git-contribution-stats` | `docker-compose.yml` build context for repo-scanner-mcp | `docker compose build` fails |

### Soft dependencies (gracefully skipped if absent)

| Dependency | Expected Path | Used By | Behavior if Missing |
|------------|---------------|---------|---------------------|
| myloop | `~/code/myloop` | Claude Code entrypoint → skills symlink | Warning: "myloop 未挂载，跳过 skill 安装" |
| dailyinfo | `~/code/dailyinfo` | launchd scheduling + ai-news-weekly-polish skill | Daily cron jobs fail; skill not loaded |
| ai-news-weekly-polish | `~/code/dailyinfo/skills/ai-news-weekly-polish` | Docker volume mount (line 214) | Empty mount or Docker warning |
| aisecretary skills | `~/code/aisecretary/skills` | Docker volume mount (lines 21, 79) | Empty mount or Docker warning |

### macOS-specific (won't work on Linux)

| Feature | Why macOS-only | Linux Alternative |
|---------|---------------|-------------------|
| launchd scheduling | Uses `~/Library/LaunchAgents/` plists | systemd user timers |
| `/var/run/docker.sock` | Path standard on macOS Docker Desktop | Same path on Linux (usually fine) |

### Hardcoded user paths found

| Location | Path | Fix |
|----------|------|-----|
| `README.md:578,585,606,630,638` | `/Users/owen/code/dailyinfo` | Already being moved to docs; use `$HOME` or `~/code/dailyinfo` |
| `scripts/launchd/install-collect-agentops.sh:72` | `~/code/myloop/...` | Already uses `~`, acceptable |
| `openclaw/scripts/fix-host-paths.sh:4` | `/Users/owen/...` | Already handles host paths at runtime |

### Things that work on any machine (no local state needed)

- All services that don't need sibling repos (hermes, claude-code, openclaw-gateway, uptime-kuma, backup-cron, tdai-memory, freshrss)
- All config from `.example` templates (first run auto-generates)
- All API key materialization (entrypoint scripts handle this)
- Cloud backup restore

## uv vs Docker Relationship

```
THIS REPO:
  uv → ONLY for mkdocs (docs toolchain)
  Docker → EVERYTHING else (services, build, runtime)

SIBLING REPOS (dailyinfo, aisecretary, etc.):
  May use uv independently → their concern, not ours
```

This repo is not a Python project. `pyproject.toml` exists solely so `uv run mkdocs serve` works for local docs preview. It has zero relationship with the Docker services.

## Tasks

### Task 1: Set up mkdocs + pyproject.toml
- **Action**: Create `pyproject.toml` with `[project]` metadata + `[tool.uv]` + `[dependency-groups]` for docs (mkdocs-material)
- **Action**: Create `mkdocs.yml` with material theme, Chinese search, full nav tree
- **Action**: Create `.github/workflows/docs.yml` — on push to main, `uv run mkdocs build` → `actions/upload-pages-artifact` → `actions/deploy-pages`
- **Validate**: `uv sync --group docs && uv run mkdocs build --strict`

### Task 2: Rewrite README
- **Action**: Write new README — capability map, ecosystem diagram, quick start, service table, link to docs
- **Mirror**: Chinese prose style from existing README, compact table style from CLAUDE.md
- **Validate**: Read the result — is it skimmable in 60 seconds? Does it show what the project DOES?

### Task 3: Migrate content to docs/
- **Action**: Create new docs pages (setup, architecture, email, contacts, lark-cli, dailyinfo, backup, portability)
- **Action**: Merge README TDAI section into existing `docs/tdai-memory.md`
- **Action**: Update `docs/index.md` as mkdocs landing
- **Validate**: `uv run mkdocs build --strict` passes, all internal links resolve

### Task 4: Portability audit + clone script
- **Action**: Create `docs/portability.md` with full dependency graph
- **Action**: Create `scripts/clone-deps.sh` — clones aisecretary, git-contribution-stats, myloop, dailyinfo to expected paths
- **Action**: Update `scripts/start.sh` with pre-flight warning for missing sibling repos
- **Validate**: Run `bash -n scripts/clone-deps.sh`, verify it lists the right repos

## Validation

```bash
# Docs build clean
uv sync --group docs
uv run mkdocs build --strict

# All internal links resolve (no dead links)
uv run mkdocs build 2>&1 | grep -i warning && exit 1 || echo "no warnings"

# Shell scripts pass syntax check
for f in scripts/*.sh; do bash -n "$f" && echo "OK: $f"; done

# README is under 200 lines
wc -l README.md
```

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Sibling repos are private — clone-deps.sh fails for others | High | Script checks `gh auth status` first; docs label private repos clearly |
| launchd can't be fixed for Linux | High | Document as known limitation in portability.md; suggest systemd timer equivalents |
| mkdocs material theme needs customization for Chinese | Low | material theme has good CJK support out of box |
| GitHub Pages needs repo settings change (enable Pages, set source to Actions) | Medium | Document in setup.md as a one-time manual step |

## Acceptance

- [ ] `uv run mkdocs build --strict` passes with no warnings
- [ ] README is under 200 lines, skimmable, shows capabilities
- [ ] All current README technical content has a home in docs/
- [ ] `docs/portability.md` lists every external dependency with expected paths
- [ ] `scripts/clone-deps.sh` exists and is syntax-valid
- [ ] `.github/workflows/docs.yml` is ready for Pages deploy
- [ ] No Dockerfiles or runtime behavior changed
