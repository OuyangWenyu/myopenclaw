# Plan: mylibrary 源码安装解耦 — 本地优先 + Git 回退

**来源**: feat/issue-29-zotero-mcp 分支 — mylibrary skills volume mount 耦合问题
**复杂度**: Small（4 个文件改动，主要逻辑在 Dockerfile）

## 摘要

当前 `docker-compose.yml` 通过 volume mount 将 `~/code/mylibrary/skills` 挂入容器。这导致容器运行时强依赖宿主机源码路径。本计划将 mylibrary 改为 **build 时安装**：优先从本地源码 `uv pip install`，本地不存在时从 GitHub clone 安装。Skills 在 build 时从同一源码复制到镜像内，不再需要运行时 volume mount。

## 宁缺毋滥原则

- **不改** `docker/zotero-mcp/` — 它只依赖 pyzotero，不依赖 mylibrary 包
- **不改** hermes profile config 中的 `external_dirs` — `/opt/mylibrary-skills` 路径不变，只是来源从 volume mount 变为 build 时 COPY
- **不碰** `entrypoint-wrapper.sh` 中已删除的 zotero-cli-cc 配置
- **不碰** `paper-to-zotero.py` / `run-paper-pipeline.sh` 等已删除的脚本（这些由 mylibrary 包提供替代）
- 只做：mylibrary 安装方式从「运行时 mount」改为「build 时安装」

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| Dockerfile 安装 | `docker/hermes/Dockerfile:10-12` | cardamum 多阶段构建 — COPY from builder |
| Dockerfile 安装 | `docker/hermes/Dockerfile:22-29` | gh CLI — apt-get install + cleanup |
| Dockerfile 安装 | `docker/hermes/Dockerfile:83` | uv — COPY --from 静态二进制 |
| start.sh build | `scripts/start.sh:198-213` | 已有 mylibrary external_dirs 注入逻辑 — 保留不变 |
| start.sh install | `scripts/start.sh:217-240` | `install_paper_fetch()` 函数 — git clone + cp skills 模式，可复用 |
| Skills 挂载 | `docker-compose.yml:24,88` | `${HOME}/code/mylibrary/skills:/opt/mylibrary-skills:ro` — 删除 |
| gitignore | 项目根 `.gitignore` | 已有 `docker/hermes/.cardamum-*` 等 build 临时文件 |

## Files to Change

| File | Action | Why |
|---|---|---|
| `docker/hermes/Dockerfile` | UPDATE | 添加 mylibrary 安装步骤（本地优先 + git 回退） |
| `docker-compose.yml` | UPDATE | 删除 3 个 profile 中的 mylibrary skills volume mount |
| `scripts/start.sh` | UPDATE | build 前将本地 mylibrary 源码 rsync 到 build context |
| `.gitignore` | UPDATE | 忽略 build context 中的临时 mylibrary 源码 |

## Tasks

### Task 1: Dockerfile — 添加 mylibrary 安装步骤

- **文件**: `docker/hermes/Dockerfile`
- **位置**: 在 `COPY --from=ghcr.io/astral-sh/uv:latest` 之后，`# ── Config directories` 之前
- **改动**:

```dockerfile
# ── Install mylibrary (hydrolitagent) — local source preferred, git fallback ──
# start.sh rsyncs ~/code/mylibrary into .mylibrary-src/ before build when available.
# When missing (e.g. CI / remote build), falls back to git clone.
# Skills are copied from the same source so package and skills stay in sync.
COPY .mylibrary-src/ /tmp/mylibrary-src/
RUN if [ -f /tmp/mylibrary-src/pyproject.toml ]; then \
      echo "   📚 mylibrary: installing from local source" && \
      uv pip install --system /tmp/mylibrary-src/ && \
      cp -r /tmp/mylibrary-src/skills /opt/mylibrary-skills; \
    else \
      echo "   📚 mylibrary: local source not found, cloning from GitHub" && \
      apt-get update && apt-get install -y --no-install-recommends git && \
      git clone --depth 1 https://github.com/OuyangWenyu/mylibrary.git /tmp/mylibrary-git && \
      uv pip install --system /tmp/mylibrary-git/ && \
      cp -r /tmp/mylibrary-git/skills /opt/mylibrary-skills && \
      apt-get purge -y git && \
      rm -rf /var/lib/apt/lists/*; \
    fi && \
    rm -rf /tmp/mylibrary-src /tmp/mylibrary-git && \
    chown -R hermes:hermes /opt/mylibrary-skills
```

- **Mirror**: 
  - `uv pip install --system` 模式来自 cardamum Rust 构建（多阶段 COPY）
  - `apt-get install + purge` 模式来自 lark-cli 安装（临时依赖用完即删）
- **关键细节**:
  - `--depth 1` 加速 clone
  - git 用完 purge 掉，不留在镜像里
  - `/opt/mylibrary-skills` 路径不变，与现有 `external_dirs` 配置兼容

### Task 2: start.sh — build 前复制本地源码到 build context

- **文件**: `scripts/start.sh`
- **位置**: `docker compose build` 命令之前
- **改动**:

```bash
# ── 复制 mylibrary 源码到 build context（本地优先）──────────────
# 容器 build 时 Dockerfile 会优先使用本地源码进行 pip install。
# 如果本地没有 mylibrary，Dockerfile 会从 GitHub clone。
MYLIBRARY_SRC="${HOME}/code/mylibrary"
MYLIBRARY_CTX="${REPO_ROOT}/docker/hermes/.mylibrary-src"
if [[ -d "${MYLIBRARY_SRC}" ]]; then
    echo "   📚 mylibrary 本地源码已检测到，复制到 build context..."
    rsync -a --delete \
        --exclude '.git' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.venv' \
        --exclude 'venv' \
        --exclude 'node_modules' \
        --exclude '.mypy_cache' \
        --exclude '.pytest_cache' \
        --exclude '*.egg-info' \
        "${MYLIBRARY_SRC}/" "${MYLIBRARY_CTX}/"
else
    # 确保 build context 干净（没有上次残留的源码）
    rm -rf "${MYLIBRARY_CTX}"
    echo "   📚 mylibrary 本地未找到，build 时将使用 GitHub clone"
fi
```

- **Mirror**: `install_paper_fetch()` 函数的 `git clone` 模式 + `.gitignore` 排除

### Task 3: docker-compose.yml — 删除 mylibrary volume mount

- **文件**: `docker-compose.yml`
- **删除 3 处** volume mount（hermes, hermes-coder, hermes-daoyuan）:
  ```
  - ${HOME}/code/mylibrary/skills:/opt/mylibrary-skills:ro
  ```
- **保留**: `start.sh` 中的 `external_dirs` 注入逻辑（`/opt/mylibrary-skills` 路径不变，来源变为镜像内置）

### Task 4: .gitignore — 忽略 build context 临时文件

- **文件**: `.gitignore`
- **添加**:
  ```
  # mylibrary build context (rsync'd by start.sh)
  docker/hermes/.mylibrary-src/
  ```

## 验证

```bash
# 1. 确认本地源码存在时的 build
./scripts/start.sh --build

# 2. 验证 skills 在容器内可用
docker compose exec hermes-coder ls /opt/mylibrary-skills/
# 应输出: paper-to-zotero  run-paper-to-zotero

# 3. 验证 hydrolitagent 包已安装
docker compose exec hermes-coder python3 -c "import hydrolitagent; print(hydrolitagent.__file__)"

# 4. 验证没有残留的 volume mount 依赖
docker compose exec hermes-coder ls /opt/mylibrary-skills/paper-to-zotero/SKILL.md

# 5. 验证 zotero-mcp 不受影响
docker compose exec hermes-coder /opt/hermes/.venv/bin/hermes mcp test zotero

# 6. 模拟无本地源码场景（CI / 远端）
mv ~/code/mylibrary ~/code/mylibrary.bak
./scripts/start.sh --build   # 应从 GitHub clone
mv ~/code/mylibrary.bak ~/code/mylibrary
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| mylibrary pyproject.toml 依赖冲突 | Low | `uv pip install --system` 与现有 hermes 环境兼容性高；hydrolitagent 依赖（pyzotero, httpx, mcp）都是常见包 |
| Docker build context 变大（含 mylibrary 源码） | Low | rsync 排除 .git/__pycache__/.venv，源码本身约几百 KB |
| `apt-get install git` 增加 build 时间（仅 fallback 时） | Low | 本地源码存在时不装 git；fallback 用 `--depth 1` 浅克隆 |
| 旧容器还在用 volume mount 配置 | Low | `start.sh --build` 会重建镜像+重启容器，docker-compose.yml 已删除 mount |

## Acceptance

- [ ] 本地源码存在时：`--build` 成功，skills 来自本地源码
- [ ] 本地源码不存在时：`--build` 成功，skills 来自 GitHub clone
- [ ] `/opt/mylibrary-skills/` 在容器内可用，路径不变
- [ ] `hydrolitagent` 包可被 `python3 -c "import hydrolitagent"` 导入
- [ ] zotero-mcp 服务不受影响
- [ ] 没有 `${HOME}/code/mylibrary` 的运行时 volume mount 残留
- [ ] `docker/hermes/.mylibrary-src/` 被 gitignore
