# Plan: Cross-Platform Milestone 1 — 合并 #46 + #50

**Source PRD**: `.claude/prds/cross-platform-linux-mac.prd.md`
**Selected Milestone**: 1 — 合并 #46 + #50，解决冲突，双平台验证
**Complexity**: Small

## Summary

从 main 新建 `feat/gitcode-cli` 分支（替代原计划的 `feat/cross-platform-m1`），将 #46（gitcode-cli）和 #50（gh-token-auth，已随 main 合并）两个 PR 合并，修复 Linux 部署阻塞点。验证通过后合回 main。

## Current Status

| Task | Status |
|------|:------:|
| ✅ 分支 `feat/gitcode-cli` 已创建，main（含 #50）已合并 | 完成 |
| ✅ #46 内容已合入 | 完成 |
| ⬜ Task 4: 修复 Linux 阻塞点 | **待做** |
| ⬜ Task 5: Mac 回归验证 | 待做 |
| ⬜ Task 6: 创建 PR 合回 main | 待做 |

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| 分支命名 | 已有 `feat/gitcode-cli` | `feat/` 前缀 + kebab-case |
| 合并策略 | — | merge（非 rebase），保留 PR 的独立提交历史 |
| 错误处理 | `start.sh:20-23` | `echo "⚠️ ..."` + 跳过，不 exit |
| Shell 兼容 | — | 用 POSIX 兼容写法，避免 BSD/GNU 差异 |
| 日志风格 | `start.sh` 现有风格 | 中文输出 + emoji + `echo` |

## Files to Change

| File | Action | Why |
|---|---|---|
| `scripts/start.sh` | UPDATE | 3 处 Linux 阻塞点修复（见 Task 4） |
| `scripts/setup-dns.sh` | UPDATE | `sed -i ''` → POSIX 兼容写法 |
| `docker-compose.yml` | NO-OP | #46 + #50 已合并，无冲突 |
| `.env.example` | NO-OP | 同上 |

---

## Task 4: 修复 Linux 部署阻塞点

### 4a: `.cloud.conf` 缺失 → warning + 跳过，不 exit

**文件**: `scripts/start.sh:52-57`

**现状**:
```bash
if [[ ! -f "${CONF_FILE}" ]]; then
  echo "❌ 未找到 .cloud.conf，请先运行 ./scripts/setup-cloud.sh"
  exit 1
fi
```

**问题**: Linux 用户可能没有云盘，不需要备份功能。硬 `exit 1` 阻止了所有后续服务的启动。

**修改**: 将 `.cloud.conf` 缺失从 fatal error 改为 warning，跳过备份相关配置，但不阻
止后续的 Docker 服务启动。同时设 `BACKUP_ROOT` 默认值为 `/tmp/myopenclaw-backups`
（备份 cron 容器有 `restart: unless-stopped`，无有效 BACKUP_ROOT 时 backup-all-docker.sh
自身会报错但不影响其他服务）。

**实现细节**:
- `source` 和后续 `CLOUD_ROOT` 推导逻辑需要放在 `if` 块内
- `mkdir -p "${BACKUP_ROOT}/..."` 也放在 `if` 块内
- `GDRIVE_PAPERS_LOCAL_PATH` 自动推导依赖 `CLOUD_ROOT`，也需要跳过
- `docker compose up -d` 前打印的 `echo "   备份目录: ${BACKUP_ROOT}"` 需要处理默认值

### 4b: 云盘目录不存在 → warning + 跳过，不 exit

**文件**: `scripts/start.sh:70-73`

**现状**:
```bash
if [[ ! -d "${CLOUD_ROOT}" ]]; then
  echo "❌ 云盘目录不存在: ${CLOUD_ROOT}，请确认云盘客户端已登录"
  exit 1
fi
```

**问题**: Linux 上没有云盘客户端，目录自然不存在。同样应该是 warning。

**修改**: 与 4a 一起处理——`.cloud.conf` 走 warning 分支后，`CLOUD_ROOT` 不会被设置，
此检查也不需要执行。

### 4c: OpenClaw npm 路径硬编码 macOS Homebrew

**文件**: `scripts/start.sh:259`

**现状**:
```bash
if [[ -x /opt/homebrew/lib/node_modules/openclaw/dist/index.js ]]; then
```

**问题**: `/opt/homebrew/lib/` 是 macOS Homebrew 路径，Linux 上 openclaw 可能安装在其他
位置（`/usr/local/lib/`、`/usr/lib/` 或 npm global prefix）。

**修改**: 使用 `which openclaw` 或 `npm list -g openclaw --depth=0 2>/dev/null` 来定位
openclaw，而非硬编码路径。如果都找不到则跳过版本检查而非报错。

### 4d: `sed -i ''` → POSIX 兼容

**文件**: `scripts/setup-dns.sh:132`

**现状**:
```bash
sudo sed -i '' "s/${existing_ip}[[:space:]]\\+${domain}/${ip}	${domain}/" /etc/hosts
```

**问题**: `sed -i ''` 是 BSD sed（macOS）语法，GNU sed（Linux）会将其理解为编辑 `''` 
这个文件。跨平台写法是 `sed -i.bak` + `rm .bak`。

**修改**:
```bash
sudo sed -i.bak "s/..." /etc/hosts && sudo rm -f /etc/hosts.bak
```

**注意**: 当前 `setup-dns.sh` 在第 64 行有 macOS 专属检查 `if [[ "$(uname)" != "Darwin" ]]`
会直接 `exit 1`，所以 Linux 上此脚本不会执行到这里。但修正 `sed -i ''` 是预防性措施，
确保脚本在任何环境下语法正确。

### 4e: 验证 — Mac 回归测试

```bash
# 1. Mac 上执行 start.sh，确认所有服务正常
docker compose ps  # 12 services all Up

# 2. 验证 gh CLI 认证（三个容器）
docker compose exec hermes gh auth status
docker compose exec hermes-coder gh auth status
docker compose exec claude-code gh auth status

# 3. 验证 gc CLI 可用（hermes-coder）
docker compose exec hermes-coder gc auth status

# 4. 验证 gc binary 存在于 host
ls ~/.openclaw/bin/gc 2>/dev/null && echo "gc binary exists" || echo "gc needs compilation"

# 5. 验证 .cloud.conf 缺失时 start.sh 不 exit
# （临时 mv .cloud.conf，跑 start.sh --dry-run 或直接检查逻辑）
```

---

## Task 5: 创建 PR 合回 main

- 在 GitHub 创建 PR：`feat/gitcode-cli` → `main`
- PR 描述包含：
  - #46 GitCode CLI + Linux 部署文档
  - #50 gh CLI 认证修复（已随 main 合入）
  - Linux 部署阻塞点修复（`.cloud.conf` 软阻塞等）
- 引用 PRD: `.claude/prds/cross-platform-linux-mac.prd.md`

---

## Validation

```bash
# 1. 确认合并无残留
cd ~/code/myopenclaw && git diff --check

# 2. 确认 docker-compose 语法正确
docker compose config --quiet

# 3. Mac 回归 — 重建并启动所有服务
./scripts/start.sh --build
docker compose ps  # 确认 12 个服务全部 Up

# 4. 验证 GitHub + GitCode 能力
docker compose exec hermes-coder gh auth status
docker compose exec hermes-coder gc auth status

# 5. 验证 .cloud.conf 缺失不阻塞
# (Linux 上执行，或 Mac 上临时移走 .cloud.conf)
mv .cloud.conf .cloud.conf.bak
./scripts/start.sh  # 不应 exit 1，应输出 warning 并继续启动
mv .cloud.conf.bak .cloud.conf
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `.cloud.conf` 软阻塞后备份 cron 注册失败 | Low | backup-cron 容器有 `restart: unless-stopped`，无 BACKUP_ROOT 时自身报错但不影响其他服务 |
| OpenClaw npm 路径在 Linux 上找不到 | Low | 用 `which`/`npm list` fallback，找不到则跳过版本检查 |
| `sed -i.bak` 在某些极端环境行为不同 | Very Low | POSIX 标准 `sed -i` 扩展行为，主流发行版一致 |
| 合并后 Mac 服务行为变化 | Low | 只改了 start.sh 和 setup-dns.sh，不碰 Dockerfile |

## Acceptance

- [ ] `scripts/start.sh` 中 `.cloud.conf` 缺失 → warning + 继续，不 exit
- [ ] `scripts/start.sh` 中云盘目录不存在 → warning + 继续，不 exit
- [ ] `scripts/start.sh` 中 OpenClaw 路径不再硬编码 Homebrew
- [ ] `scripts/setup-dns.sh` 中 `sed -i ''` → `sed -i.bak`
- [ ] Mac 上 `docker compose ps` 全部 Up
- [ ] gh CLI 认证在三个容器内通过
- [ ] gc CLI 认证在爱码士容器内通过
- [ ] 合并到 main 后，Linux 用户可按照 `docs/linux-server-setup.md` 部署
