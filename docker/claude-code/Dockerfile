# =============================================================
# Claude Code + cc-connect — 独立编码 Agent 容器
# Base: node:22-slim (Debian-based)
# =============================================================
FROM node:22-slim

USER root

# ── Install git + ca-certificates ─────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends git wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# ── Install GitHub CLI (direct binary) ───────────────────────
RUN ARCH=$(dpkg --print-architecture) && \
    GH_VERSION="2.92.0" && \
    wget -qO /tmp/gh.tar.gz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz" && \
    tar -xzf /tmp/gh.tar.gz -C /tmp && \
    cp /tmp/gh_${GH_VERSION}_linux_${ARCH}/bin/gh /usr/local/bin/gh && \
    rm -rf /tmp/gh*

# ── Install Claude Code CLI ───────────────────────────────────
RUN npm install -g @anthropic-ai/claude-code

# ── Install cc-connect (Feishu/DingTalk/Telegram bridge) ─────
RUN npm install -g cc-connect

# ── Config directories for host-side persistence ──────────────
# Mounted from host via docker-compose volumes:
#   ~/.claude          → /opt/claude-config   (Claude Code settings + credentials)
#   ~/.config/gh       → /opt/gh-config       (gh auth + settings)
#   ~/.cc-connect      → /opt/cc-config       (cc-connect config)
# Reuse the built-in 'node' user (UID 1000) instead of creating a new one
RUN mkdir -p /opt/claude-config /opt/gh-config /opt/cc-config /home/node/code && \
    chown -R node:node /opt/claude-config /opt/gh-config /opt/cc-config /home/node/code

# ── Entrypoint wrapper ────────────────────────────────────────
COPY entrypoint.sh /opt/claude-code/entrypoint.sh
RUN chmod +x /opt/claude-code/entrypoint.sh

ENTRYPOINT ["/opt/claude-code/entrypoint.sh"]
