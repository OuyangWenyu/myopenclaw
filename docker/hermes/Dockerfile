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
FROM nousresearch/hermes-agent:latest

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

# ── Entrypoint wrapper ──────────────────────────────────────
# gh expects config at $HOME/.config/gh/ (= /opt/data/.config/gh/)
# because hermes user home is /opt/data. We symlink it to the
# mounted host dir so gh auth state survives container rebuilds.
COPY entrypoint-wrapper.sh configure-outlook.sh ortie-store-token.sh load-email-env.sh /opt/hermes/
RUN chmod +x /opt/hermes/entrypoint-wrapper.sh /opt/hermes/configure-outlook.sh /opt/hermes/ortie-store-token.sh /opt/hermes/load-email-env.sh

ENTRYPOINT ["/opt/hermes/entrypoint-wrapper.sh"]
