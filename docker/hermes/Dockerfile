# =============================================================
# Custom Hermes image with opencode + GitHub CLI + Feishu CLI
# Base: nousresearch/hermes-agent:latest (debian:13.4)
# =============================================================

# ── Stage 1: Build cardamum from source (master) ───────────────
# v0.2.0-dev has cards create <id> <VCARD> — vCard passed directly,
# no EDITOR hack needed. Stable enough for CLI use.
FROM rust:1.96-slim-bookworm AS cardamum-builder
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && \
    rm -rf /var/lib/apt/lists/*
RUN cargo install --git https://github.com/pimalaya/cardamum.git --rev 1090cad2bdaa cardamum

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

# ── TDAI Memory plugin (long-term memory L0→L3 for Hermes) ──
RUN npm install -g @tencentdb-agent-memory/memory-tencentdb@0.3.6

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
# Pre-built static binary for Linux x86_64
# Provides IMAP/SMTP email management from terminal (list, read, search, reply, etc.)
# Used by the built-in email skill at /opt/hermes/skills/email/himalaya/
RUN HIMALAYA_VER="1.2.0" && \
    ARCH=$(uname -m | sed 's/x86_64/x86_64/;s/aarch64/aarch64/') && \
    wget -qO /tmp/himalaya.tgz "https://github.com/pimalaya/himalaya/releases/download/v${HIMALAYA_VER}/himalaya.${ARCH}-linux.tgz" && \
    tar xzf /tmp/himalaya.tgz -C /usr/local/bin himalaya && \
    chmod +x /usr/local/bin/himalaya && \
    rm /tmp/himalaya.tgz

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

# ── Install uv (Python package manager) ──────────────────────
# Copy the static binary from the official uv image — no pip/python3-pip needed.
# https://docs.astral.sh/uv/guides/integration/docker/
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Install zotero-cli-cc (Zotero CLI) via uv ────────────────
# uv tool install creates an isolated venv and exposes the `zot` binary.
# UV_TOOL_DIR + UV_TOOL_BIN_DIR place the venv under /opt so both
# root and hermes users can access it (defaults are under ~/.local).
RUN UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/opt/uv-tools/bin \
      uv tool install --python-preference only-system zotero-cli-cc && \
    ln -sf /opt/uv-tools/bin/zot /usr/local/bin/zot && \
    chmod -R a+rX /opt/uv-tools

# ── Config directories for host-side persistence ────────────
# Mounted from host via docker-compose volumes:
#   ~/.config/gh          → /opt/gh-config        (gh auth + settings)
#   ~/.config/opencode    → /opt/opencode-config   (opencode preferences)
#   ~/.lark-cli           → /opt/lark-config       (lark-cli auth + config)
RUN mkdir -p /opt/gh-config /opt/opencode-config /opt/lark-config /opt/gitcode-config && \
    chown -R hermes:hermes /opt/gh-config /opt/opencode-config /opt/lark-config /opt/gitcode-config

# opencode respects OPENCODE_CONFIG_DIR to find its config
ENV OPENCODE_CONFIG_DIR=/opt/opencode-config

# ── Copy zot-link-gdrive bridge script ─────────────────────
# Creates linked_file attachments in Zotero pointing to Google Drive PDFs.
# Path auto-constructed from $GDRIVE_PAPERS_LOCAL_PATH + filename.
# Uses pyzotero from the zotero-cli-cc venv; reads credentials from config.toml.
COPY scripts/zot-link-gdrive.py /opt/hermes/scripts/zot-link-gdrive.py
RUN chmod +x /opt/hermes/scripts/zot-link-gdrive.py

# ── Copy paper-to-zotero script ─────────────────────────────
# Creates complete Zotero item (metadata + linked_file attachment).
# Enriches metadata from Crossref/arXiv; writes linked_file path to Zotero.
COPY scripts/paper-to-zotero.py /opt/hermes/scripts/paper-to-zotero.py
RUN chmod +x /opt/hermes/scripts/paper-to-zotero.py

# ── Copy paper pipeline one-shot script ─────────────────────
# Atomic pipeline: paper-fetch → rclone → paper-to-zotero → cleanup.
# Single command replaces the 4-step manual orchestration.
COPY scripts/run-paper-pipeline.sh /opt/hermes/scripts/run-paper-pipeline.sh
RUN chmod +x /opt/hermes/scripts/run-paper-pipeline.sh

# ── Tests for paper pipeline scripts ──────────────────────────
COPY scripts/test-paper-to-zotero.sh /opt/hermes/scripts/test-paper-to-zotero.sh
COPY scripts/test-run-paper-pipeline.sh /opt/hermes/scripts/test-run-paper-pipeline.sh
RUN chmod +x /opt/hermes/scripts/test-paper-to-zotero.sh \
    /opt/hermes/scripts/test-run-paper-pipeline.sh

# ── Entrypoint wrapper ──────────────────────────────────────
# gh expects config at $HOME/.config/gh/ (= /opt/data/.config/gh/)
# because hermes user home is /opt/data. We symlink it to the
# mounted host dir so gh auth state survives container rebuilds.
COPY entrypoint-wrapper.sh /opt/hermes/entrypoint-wrapper.sh
RUN chmod +x /opt/hermes/entrypoint-wrapper.sh

# ── Build-time guard: verify EBADF patch target exists ─────────
# entrypoint-wrapper.sh patches this line at startup to add "OSError"
# to _TRANSIENT_TRANSPORT_ERRORS. Fail fast if upstream moves/renames it.
RUN grep -q '"APIConnectionError", "APITimeoutError"' /opt/hermes/run_agent.py

ENTRYPOINT ["/opt/hermes/entrypoint-wrapper.sh"]
