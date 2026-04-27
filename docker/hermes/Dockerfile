# =============================================================
# Custom Hermes image with opencode + GitHub CLI
# Base: nousresearch/hermes-agent:latest (debian:13.4)
# =============================================================
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
RUN npm install -g opencode-ai

# ── Config directories for host-side persistence ────────────
# Mounted from host via docker-compose volumes:
#   ~/.config/gh          → /opt/gh-config        (gh auth + settings)
#   ~/.config/opencode    → /opt/opencode-config   (opencode preferences)
RUN mkdir -p /opt/gh-config /opt/opencode-config && \
    chown -R hermes:hermes /opt/gh-config /opt/opencode-config

# opencode respects OPENCODE_CONFIG_DIR to find its config
ENV OPENCODE_CONFIG_DIR=/opt/opencode-config

# ── Entrypoint wrapper ──────────────────────────────────────
# gh expects config at $HOME/.config/gh/ (= /opt/data/.config/gh/)
# because hermes user home is /opt/data. We symlink it to the
# mounted host dir so gh auth state survives container rebuilds.
COPY entrypoint-wrapper.sh /opt/hermes/entrypoint-wrapper.sh
RUN chmod +x /opt/hermes/entrypoint-wrapper.sh

ENTRYPOINT ["/opt/hermes/entrypoint-wrapper.sh"]
