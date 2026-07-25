# Plan: Cross-Platform Milestone 1 — 合并 #46 + #50

**Source PRD**: `.claude/prds/cross-platform-linux-mac.prd.md`
**Selected Milestone**: 1 — 合并 #46 + #50，解决冲突，双平台验证
**Complexity**: Small

## Summary

从 main 新建 `feat/cross-platform-m1` 分支，将 #46（gitcode-cli）和 #50（gh-token-auth）两个 PR 合并进来，解决冲突，修复 Linux 部署阻塞点。验证通过后合回 main。

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| 分支命名 | 已有 `feat/gitcode-cli` | `feat/cross-platform-m1` — 前缀 `feat/` + kebab-case |
| 合并策略 | — | merge（非 rebase），保留 PR 的独立提交历史 |
| 错误处理 | `start.sh:48-50` | 当前是 `exit 1` 硬阻塞 → 改为 `echo warning` + 跳过 |
| Shell 兼容 | `setup-dns.sh:132` | `sed -i ''` 是 BSD 语法 → 改为 `sed -i.bak` + `rm`（POSIX 兼容） |
| 日志风格 | `start.sh` 现有风格 | 中文输出 + emoji + `echo` |

## Branch Strategy

```
main ─────────────────────────────────────────────
  │
  └── feat/cross-platform-m1 (新建)
        │
        ├── merge feat/gitcode-cli  (#46)
        ├── merge fix/gh-token-auth  (#50)
        ├── resolve conflicts
        ├── fix Linux blockers
        └── → PR back to main
```

## Files to Change

| File | Action | Why |
|---|---|---|
| `.env.example` | MERGE | #46 加 GITCODE_TOKEN，#50 重组 GH/飞书注释段，需手动合并 |
| `docker-compose.yml` | MERGE | #46 加 gc volumes，#50 加 GH_TOKEN env，不同行自动合并 |
| `scripts/start.sh` | UPDATE | 软 `.cloud.conf` 和云盘目录检查：缺失时 warning + 跳过备份注册，不 exit |
| `skills/daily-dev-report/SKILL.md` | NO-OP | #50 改的 "UTC→北京时间" 已在 main 上提交，合并时自动跳过 |
| (new) `README.md` | UPDATE (from #46) | GitCode CLI 编译安装说明，Linux 和 Mac 通用 |
| (new) `docs/linux-server-setup.md` | ADD (from #46) | 387 行 Linux 部署文档 |

## Tasks

### Task 1: 从 main 创建 `feat/cross-platform-m1` 分支

- **Action**: `git checkout main && git checkout -b feat/cross-platform-m1`
- **Validate**: `git branch --show-current` → `feat/cross-platform-m1`

### Task 2: 依次合并 #46 和 #50

- **Action**: 
  1. `git merge feat/gitcode-cli` — 合入 GitCode CLI 集成
  2. `git merge fix/gh-token-auth` — 合入 gh CLI 认证修复
- **Conflict prediction**: 第二个 merge 时 `.env.example` 有轻微冲突
- **Validate**: `git diff --check` 无冲突标记残留

### Task 3: 解决合并冲突

- **Action**: 手动合并 `.env.example` 和 `docker-compose.yml`，确保两边的新增内容都保留
- **Conflict map**:

```
.env.example:
  #46 add: GITCODE_TOKEN 注释行 (line ~27)
  #50 add: GH_TOKEN 注释段重组 (lines ~23-80)
  → 手动合并：把 #46 的 GITCODE_TOKEN 行放入 #50 重组的注释结构中

docker-compose.yml:
  #46 add: gc volumes (hermes, openclaw-gateway, openclaw-cli)
  #50 add: GH_TOKEN env (所有服务)
  → 不同行，git 自动合并无冲突 ✅
```

- **Validate**: `git diff --stat` 确认两个 PR 的文件改动都在

### Task 4: 修复 Linux 部署阻塞点

- **Action**: 改 `scripts/start.sh` 中两处：
  1. `.cloud.conf` 缺失：`echo "⚠️ 未找到 .cloud.conf，跳过云盘备份配置"` + 设 `BACKUP_ROOT` 默认值，不 exit
  2. 云盘目录不存在：`echo "⚠️ 云盘目录不存在，跳过备份"`，不 exit
  3. `setup-dns.sh:132` 的 `sed -i ''` → `sed -i.bak` 兼容 GNU sed
- **Mirror**: 现有 warning 风格 `echo "   ⚠️  ..."`
- **Validate**: Linux 上 `./scripts/start.sh` 不会因缺少云盘而 exit 1

### Task 5: Mac 回归验证

- **Action**: 在当前 Mac 环境验证合并后所有服务正常
- **Validate**:
  ```bash
  docker compose ps  # 8 services all Up
  docker compose exec hermes gh auth status
  docker compose exec openclaw-gateway gc auth status 2>/dev/null || echo "gc binary not yet compiled on Mac"
  ```

### Task 6: 创建 PR 合回 main

- **Action**: 在 GitHub 创建 PR：`feat/cross-platform-m1` → `main`
- **Validate**: `git diff origin/main...main --stat` 确认改动范围

## Validation

```bash
# 1. 确认合并无残留
cd ~/code/myopenclaw && git diff --check

# 2. 确认 docker-compose 语法正确
docker compose config --quiet

# 3. Mac 回归 — 启动所有服务
./scripts/start.sh
docker compose ps  # 确认 8 个服务全部 Up

# 4. 验证新能力
docker compose exec hermes gh auth status          # gh CLI 认证
ls ~/.openclaw/bin/gc 2>/dev/null && echo "gc binary exists" || echo "gc needs compilation"

# 5. Linux 模拟 — 验证 start.sh 不因无云盘而退出
# (在 Linux 机器上执行，或 CI)
CLOUD_PROVIDER=none bash -x ./scripts/start.sh 2>&1 | grep -E "❌|⚠️|✅"
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| 合并后 Mac 服务行为变化 | Low | 只改了 volumes + env，不碰 Dockerfile |
| `.env.example` 合并后两个 PR 的逻辑矛盾 | Low | 一个是新增 GITCODE_TOKEN，一个是重组注释，无逻辑冲突 |
| `start.sh` 软阻塞后备份 cron 注册失败 | Medium | 备份 cron 容器本身有 `restart: unless-stopped`，无 BACKUP_ROOT 时 backup-all-docker.sh 自身会报错但不影响其他服务 |
| SKILL.md 已提前合入导致 #50 合并时该文件无变化 | Low | Git 自动检测为 already applied，跳过 |

## Acceptance

- [ ] #46 + #50 合并到 `feat/cross-platform-m1`，无冲突标记
- [ ] `.cloud.conf` 缺失时 `start.sh` 不退出，输出 warning
- [ ] Mac 上 `docker compose ps` 全部 Up
- [ ] gh CLI 认证在容器内通过
- [ ] PR #50 标记为包含 #46 内容，说明合并关系
- [ ] 合并到 main 后，Linux 用户可按照 `docs/linux-server-setup.md` 部署
