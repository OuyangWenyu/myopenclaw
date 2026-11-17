# =============================================================
# Custom Hermes image with opencode + GitHub CLI + Feishu CLI
# Base: nousresearch/hermes-agent:latest (debian:13.4)
# =============================================================

# ── Stage 1: Rust builds (cardamum + himalaya, both from source) ──
# cardamum: v0.2.0-dev has cards create <id> <VCARD> — vCard passed
# directly, no EDITOR hack needed. Stable enough for CLI use.
# himalaya: GitHub release binaries ship with default cargo features only,
# which EXCLUDE `oauth2` — config parsing then rejects
# `backend.auth.type = "oauth2"` and breaks EVERY account in config.toml.
FROM rust:1.96-slim-bookworm AS cardamum-builder
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && \
    rm -rf /var/lib/apt/lists/*
RUN cargo install --git https://github.com/pimalaya/cardamum.git --rev 771879c1d510b6625dfd7f33c59112b0eab2775d cardamum
# oauth2 implies keyring (secret-lib) at compile time only — token
# consumption goes through backend.auth.access-token.cmd, not the keyring.
RUN cargo install himalaya --version 1.2.0 --locked --features oauth2

# ── Stage 2: Hermes custom image ──────────────────────────────
# 上游 base **钉版本**（原来是 :latest，不钉）。原因：v0.23.1（2026-11-10）把
# feishu adapter 的 send() 重构成 _send_one + send_chunks，patch-feishu.py 的 A3
# 锚点只对得上 v0.23.0 及更早；不钉版本时每次 build 都可能拉到新上游，锚点随机失配。
# 升级 base 之前，先用目标版本的文件重跑一遍 patch-feishu.py / patch-image-limit.py
# 的锚点校验（两个脚本失配都是硬失败）。
FROM nousresearch/hermes-agent:v0.23.0

# Base image runs as root initially (entrypoint does gosu drop),
# but Dockerfile USER may be set to hermes — switch to root for installs.
USER root

# ── Install GitHub CLI from official APT repository ──────────
# https://github.com/cli/cli/blob/trunk/docs/install_linux.md
RUN apt-get update && \
    apt-get install -y --no-install-recommends gpg wget && \
    wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        > /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y gh && \
    rm -rf /var/lib/apt/lists/*

# ── Install opencode (AI coding agent) via npm ──────────────
# opencode-ai is the official npm package (Node.js 18+ required, already in base)
RUN npm install -g opencode-ai gitcode-cli

# ── Install Feishu/Lark CLI via npm ─────────────────────────
# @larksuite/cli is the official Feishu CLI tool (200+ commands, 24 Agent Skills)
# Two-step install: npm package + binary bootstrap via `install` command
# curl is required by the postinstall script to download the lark binary
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    npm install -g @larksuite/cli && \
    npx @larksuite/cli@latest install && \
    apt-get purge -y curl && \
    rm -rf /var/lib/apt/lists/*

# ── Install himalaya (CLI email client) ─────────────────────
# Built from source in stage 1 with `--features oauth2` (see stage 1 comment:
# release binaries cannot parse `backend.auth.type = "oauth2"`).
# Provides IMAP/SMTP email management from terminal (list, read, search, reply, etc.)
# Used by the built-in email skill at /opt/hermes/skills/email/himalaya/
COPY --from=cardamum-builder /usr/local/cargo/bin/himalaya /usr/local/bin/himalaya

# ── Install ortie (OAuth 2.0 token broker) ──────────────────
# Pre-built static binary. Himalaya v1.2.0 has no OAuth wizard that
# works headless; ortie owns authorization + refresh, himalaya reads
# the access token via `backend.auth.access-token.cmd`.
# Used for Outlook / Microsoft 365 (basic auth retired).
RUN ORTIE_VER="2.2.0" && \
    ARCH=$(uname -m) && \
    wget -qO /tmp/ortie.tgz "https://github.com/pimalaya/ortie/releases/download/v${ORTIE_VER}/ortie.${ARCH}-linux.tgz" && \
    tar xzf /tmp/ortie.tgz -C /usr/local/bin ortie && \
    chmod +x /usr/local/bin/ortie && \
    rm /tmp/ortie.tgz

# ── Install cardamum (CLI contact manager) ────────────────────
# Built from source (master) — v0.1.0 is the only binary release and
# uses the old EDITOR-based cards create flow. v0.2.0 accepts vCard
# content directly as a positional argument, no EDITOR needed.
# Multi-stage: compile in Rust builder, copy binary to final image.
COPY --from=cardamum-builder /usr/local/cargo/bin/cardamum /usr/local/bin/cardamum

# ── Install rclone (Google Drive / cloud storage CLI) ────────
# Single binary for uploading papers to Google Drive via service account.
# Uses rclone env vars (RCLONE_CONFIG_GDRIVE_*) for headless auth — no config file needed.
RUN RCLONE_VER="1.69.2" && \
    ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/') && \
    wget -qO /tmp/rclone.deb "https://downloads.rclone.org/v${RCLONE_VER}/rclone-v${RCLONE_VER}-linux-${ARCH}.deb" && \
    dpkg -i /tmp/rclone.deb && \
    rm /tmp/rclone.deb

# ── Install TDAI Memory plugin (npm) ──────────────────────────
# Pre-installed at build time to avoid blocking gateway startup.
# Version pinned to 0.3.6 for provider API stability.
RUN npm install -g @tencentdb-agent-memory/memory-tencentdb@0.3.6

# ── Install uv (Python package manager) ──────────────────────
# Copy the static binary from the official uv image — no pip/python3-pip needed.
# https://docs.astral.sh/uv/guides/integration/docker/
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Feishu 平台 SDK（platform 插件的运行时依赖）──────────────────
# v0.23 的 base image 不再预装 [feishu] extra；plugin.yaml 的 requires_env 只校验
# 凭据，SDK 缺失时网关启动会报 "requirements not met" 并跳过飞书平台
# （日志：Failed to download lark-oapi==1.7.5）。版本与上游 hermes-agent[feishu]
# 声明的保持一致。构建时可换源（国内网络 PyPI 常超时）：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple hermes
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN uv pip install --index-url "${PIP_INDEX_URL}" \
        --python /opt/hermes/.venv/bin/python3 lark-oapi==1.7.5

# ── Install mylibrary (hydrolitagent) — local source preferred, git fallback ──
# start.sh rsyncs ~/code/mylibrary into .mylibrary-src/ before build when available.
# When missing (e.g. CI / remote build), falls back to git clone with GITHUB_TOKEN.
# Skills are copied from the same source so package and skills stay in sync.
ARG GITHUB_TOKEN=""
#
# IMPORTANT: mylibrary declares mcp>=2.0.0 as a dependency (for its zotero-mcp server),
# but the Hermes container only needs the paper-pipeline client code (paper_to_zotero,
# zot_link_gdrive).  mcp 2.0 breaks the Hermes agent's built-in MCP tool
# (streamable_http_client returns 2 values instead of 3).  Install with --no-deps
# and only pull in the dependencies the container actually uses.
COPY .mylibrary-src/ /tmp/mylibrary-src/
RUN if [ -f /tmp/mylibrary-src/pyproject.toml ]; then \
      echo "   📚 mylibrary: installing from local source" && \
      uv pip install --python /opt/hermes/.venv/bin/python3 --no-deps /tmp/mylibrary-src/ && \
      uv pip install --python /opt/hermes/.venv/bin/python3 graphifyy openai httpx pyzotero && \
      cp -r /tmp/mylibrary-src/skills /opt/mylibrary-skills && cp /tmp/mylibrary-src/scripts/run_paper_pipeline.sh /opt/hermes/scripts/run-paper-pipeline.sh && chmod +x /opt/hermes/scripts/run-paper-pipeline.sh; \
    else \
      echo "   📚 mylibrary: local source not found, cloning from GitHub" && \
      apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
      if [ -n "${GITHUB_TOKEN}" ]; then \
        git clone --depth 1 "https://oauth2:${GITHUB_TOKEN}@github.com/OuyangWenyu/mylibrary.git" /tmp/mylibrary-git; \
      else \
        git clone --depth 1 https://github.com/OuyangWenyu/mylibrary.git /tmp/mylibrary-git; \
      fi && \
      uv pip install --python /opt/hermes/.venv/bin/python3 --no-deps /tmp/mylibrary-git/ && \
      uv pip install --python /opt/hermes/.venv/bin/python3 graphifyy openai httpx pyzotero && \
      cp -r /tmp/mylibrary-git/skills /opt/mylibrary-skills && cp /tmp/mylibrary-git/scripts/run_paper_pipeline.sh /opt/hermes/scripts/run-paper-pipeline.sh && chmod +x /opt/hermes/scripts/run-paper-pipeline.sh && \
      apt-get purge -y git && \
      rm -rf /var/lib/apt/lists/*; \
    fi && \
    rm -rf /tmp/mylibrary-src /tmp/mylibrary-git && \
    chown -R hermes:hermes /opt/mylibrary-skills

# DuckDuckGo web search backend for Hermes web_search (no API key).
# Shared by all four profiles that use this image. If DDG is unreachable
# from the host network, switch config.yaml web.search_backend to brave_free.
RUN uv pip install --python /opt/hermes/.venv/bin/python3 ddgs

# ── Config directories for host-side persistence ────────────
# Mounted from host via docker-compose volumes:
#   ~/.config/gh          → /opt/gh-config        (gh auth + settings)
#   ~/.config/opencode    → /opt/opencode-config   (opencode preferences)
#   ~/.lark-cli           → /opt/lark-config       (lark-cli auth + config)
RUN mkdir -p /opt/gh-config /opt/opencode-config /opt/lark-config /opt/gitcode-config && \
    chown -R hermes:hermes /opt/gh-config /opt/opencode-config /opt/lark-config /opt/gitcode-config

# opencode respects OPENCODE_CONFIG_DIR to find its config
ENV OPENCODE_CONFIG_DIR=/opt/opencode-config

# ── LaTeX 公式渲染（Feishu 公式→图片流水线）──────────────────
# 公式在飞书消息（post 与卡片同）里只能以图呈现（2026-10-14 实测 + 官方语法
# 清单）。渲染走独立子进程脚本（render-formula.py，stdin JSON → PNG），网关
# 进程不加载 matplotlib（护住内存限制）。MPLCONFIGDIR 落数据卷 → 字体缓存
# 跨容器重建持久化。行内公式不转图（图片整行换行），用 pylatexenc 转 Unicode
# 文本（\eta → η、\langle → ⟨）；块级仍走图片。
RUN uv pip install --python /opt/hermes/.venv/bin/python3 matplotlib pylatexenc
COPY render-formula.py /opt/hermes/scripts/render-formula.py
ENV MPLCONFIGDIR=/opt/data/.cache/matplotlib

# ── 飞书聊天体验补丁（myopenclaw）─────────────────────────────
# ① markdown 回复卡片化：post 渲染器把 GFM 表格露成生管道符源码、标题降级为
#    普通文字；卡片（Card JSON 2.0）能真渲染。上游 PR #21000/#29630 未 merge。
# ② 话题回复锚点：gateway 缺 reply_to_message_id 时适配器拿 thread_id 当
#    receive_id（无效字段 99992402），omt_ 话题里 bot 的回复永远送不出去。
# 两处都是对 upstream 源文件做文本手术（feishu_markdown_card.py 原样内联 +
# adapter.py 四处/gateway/run.py 一处锚点改写），先内存里全部成功才落盘。
# base image 是 :latest（无 pin）——锚点失配时脚本非零退出、**构建失败**，
# 不会静默出厂未打补丁的镜像。守卫: bash tests/test-feishu-card-patch.sh
COPY feishu_markdown_card.py patch-feishu.py /tmp/
RUN /opt/hermes/.venv/bin/python3 /tmp/patch-feishu.py && \
    rm -f /tmp/feishu_markdown_card.py /tmp/patch-feishu.py

# ── 图片尺寸误判补丁（myopenclaw）─────────────────────────────
# DeepSeek 把「图太大」报成 "You have uploaded an unsupported image"，措辞里没有
# 任何尺寸词汇 ⇒ 上游 error_classifier 落到 format_error（不可重试）直接终止。
# 补丁把这句话加进 _IMAGE_TOO_LARGE_PATTERNS，归一到 image_too_large 即走上游自带
# 的「缩到最长边 8000px 再重试一次」（2026-11-12 实测 391×8000 可通过）。
# 同样是对 upstream 源文件的锚点手术：失配即构建失败。守卫:
# tests/test_hermes_image_limit_patch.py
COPY patch-image-limit.py /tmp/
RUN /opt/hermes/.venv/bin/python3 /tmp/patch-image-limit.py && \
    rm -f /tmp/patch-image-limit.py

# Auto-TTS writes its temp audio to /tmp/hermes_voice (gateway/platforms/base.py).
# The upstream image pins HERMES_WRITE_SAFE_ROOT=/opt/data, so the TTS tool
# rejects that path as "protected credential or system path" and the gateway
# silently falls back to a text-only reply.  Add only the auto-TTS temp dir.
ENV HERMES_WRITE_SAFE_ROOT=/opt/data:/tmp/hermes_voice

# ── DeepSeek voice capability (TTS/ASR command providers) ────
# Shipped in the image so every Hermes profile and every bind agent gets the
# same runtime; config injection happens at provisioning time
# (scripts/start.sh + scripts/bind_agent.py).  Stdlib-only; ffmpeg is already
# present from the base image.
COPY voice/ /opt/hermes-voice/
RUN chmod 755 /opt/hermes-voice/ds_voice.py && \
    chmod 644 /opt/hermes-voice/voice_config.py && \
    rm -rf /opt/hermes-voice/__pycache__

# ── 钉钉 Workspace CLI (dws) ──────────────────────────────────
# 官方 CLI（npm 包，内含 darwin/linux/win 五平台预编译 Go 二进制 + 官方 skill），
# 覆盖 AI表格/日历/通讯录/群聊/待办/审批/日志/文档/云盘/AI听记/邮箱/知识库等
# 17 个钉钉产品。**只有默认 profile（爱玛士）可用**：二进制全 profile 可见
# （与 gh / himalaya 一致），但 token 目录只挂给 hermes（agents/hermes/
# default.yaml），其余 profile 既没有 DWS_CONFIG_DIR 也没有 token，调不通。
# 认证是**用户身份** OAuth（`dws auth login --device`，一次性）。凭据落点有两处：
# `DWS_CONFIG_DIR`（app.json / identity / logs / cache）与 `DWS_KEYCHAIN_DIR`
# （**真正的 token**：`$DWS_KEYCHAIN_DIR/dws-cli/auth-token.enc` + `dek`，
# 默认 `$HOME/.local/share/dws-cli` = 容器内 /root，重建即丢）。两个都只在
# default.yaml 里指到挂载卷 /opt/dws-config —— 只设前者会出现「登录过但重建即失效」。
# 与 lark-cli 的 bot 身份不同，这里不能靠 .env 里的 AppKey 免登录。
# 守卫: tests/test_dws_cli_wiring.py
# 注意：postinstall 会把官方 skill 复制进 $HOME/.hermes/skills 等 agent 目录，
# 构建期 HOME 是 /root —— 那是纯垃圾，仓库里另存一份 skills/dws 由 start.sh
# 链进默认 profile。HOME/cache 指到 /tmp 就是为了避开这个副作用。
# unzip 是 postinstall 解 dws-skills.zip 的硬依赖（基础镜像没带；tar 有），
# 装完即卸 —— 运行期不需要。
RUN apt-get update && \
    apt-get install -y --no-install-recommends unzip && \
    HOME=/tmp/dws-npm-home npm_config_cache=/tmp/dws-npm-cache \
      npm install -g dingtalk-workspace-cli@1.0.26 && \
    rm -rf /tmp/dws-npm-home /tmp/dws-npm-cache && \
    apt-get purge -y unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /opt/dws-config && chown hermes:hermes /opt/dws-config && \
    HOME=/tmp/dws-verify dws --version && rm -rf /tmp/dws-verify

# ── Entrypoint wrapper ──────────────────────────────────────
# gh expects config at $HOME/.config/gh/ (= /opt/data/.config/gh/)
# because hermes user home is /opt/data. We symlink it to the
# mounted host dir so gh auth state survives container rebuilds.
COPY entrypoint-wrapper.sh configure-outlook.sh ortie-store-token.sh load-email-env.sh /opt/hermes/
RUN chmod +x /opt/hermes/entrypoint-wrapper.sh /opt/hermes/configure-outlook.sh /opt/hermes/ortie-store-token.sh /opt/hermes/load-email-env.sh

ENTRYPOINT ["/opt/hermes/entrypoint-wrapper.sh"]
