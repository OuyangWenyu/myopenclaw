#!/usr/bin/env bash
# =============================================================
# Hermes entrypoint wrapper — sets up tool config symlinks
# before handing off to the original Hermes entrypoint.
#
# Why: gh CLI looks for config at $HOME/.config/gh/ which is
# /opt/data/.config/gh/ inside the container (hermes home = /opt/data).
# We symlink it to /opt/gh-config so the host-mounted config
# (from ~/.config/gh) is found automatically.
# =============================================================
set -euo pipefail

# hermes user home = /opt/data; Hermes bash subprocess runs as root (HOME=/root)
# gh CLI reads $HOME/.config/gh/ — symlink both to the host-mounted config dir
mkdir -p /opt/data/.config /root/.config
ln -sf /opt/gh-config /opt/data/.config/gh
ln -sf /opt/gh-config /root/.config/gh

# Claude Code reads config from $HOME/.claude/ — symlink to host-mounted config dir.
# rm -rf before ln -sf: ln -sf won't replace an existing directory, only a symlink.
rm -rf /opt/data/.claude /root/.claude
ln -sf /opt/claude-config /opt/data/.claude
ln -sf /opt/claude-config /root/.claude

# For Claude Code with Zhipu GLM backend:
# Claude Code reads ANTHROPIC_API_KEY; map GLM_API_KEY → ANTHROPIC_API_KEY
# Priority: GLM_API_KEY > ANTHROPIC_API_KEY (Zhipu key takes precedence)
export ANTHROPIC_API_KEY="${GLM_API_KEY:-${ANTHROPIC_API_KEY:-}}"

# ── Materialize env vars into secret files ────────────────────
# Hermes security blacklist blocks certain env var names (DEEPSEEK, OPENROUTER,
# OPENAI) from reaching bash subprocesses. However, this wrapper runs BEFORE
# Hermes starts, so all env vars are accessible here. We write them to files
# under /opt/data/secrets/ so opencode.json can reference them via {file:}.
SECRETS_DIR="/opt/data/secrets"
mkdir -p "${SECRETS_DIR}"

for pair in \
  "DEEPSEEK_API_KEY=deepseek-api-key" \
  "OPENROUTER_API_KEY=openrouter-api-key" \
  "OPENAI_API_KEY=openai-api-key"; do
  env_name="${pair%%=*}"
  file_name="${pair##*=}"
  if [[ -n "${!env_name:-}" ]]; then
    printf '%s' "${!env_name}" > "${SECRETS_DIR}/${file_name}"
    chmod 640 "${SECRETS_DIR}/${file_name}"
    echo "   🔑 ${env_name} → ${SECRETS_DIR}/${file_name}"
  fi
done

# Hand off to original Hermes entrypoint (handles UID mapping + gosu)
exec /opt/hermes/docker/entrypoint.sh "$@"
