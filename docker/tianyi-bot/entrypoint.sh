#!/bin/sh
set -eu

# ============================================================================
# Tianyi (天一) OpenClaw entrypoint
# Phase 1 (root): install CLI tools, then re-exec as node user.
# Phase 2 (node): configure auth, wait for MCP, render config, start gateway.
# ============================================================================

# ── Phase 1: root — install system packages ──────────────────────────────
if [ "$(id -u)" = "0" ]; then
  # Install gh CLI (same pattern as docker/hermes/Dockerfile)
  if ! command -v gh >/dev/null 2>&1; then
    echo "📦 Installing GitHub CLI..."
    apt-get update -qq
    apt-get install -y -qq curl gpg >/dev/null 2>&1
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
    chmod 644 /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      | tee /etc/apt/sources.list.d/github-cli.list >/dev/null
    apt-get update -qq
    apt-get install -y -qq gh >/dev/null 2>&1
    echo "✅ gh CLI installed: $(gh --version | head -1)"
  fi

  # Install gitcode-cli (same pattern as docker/hermes/Dockerfile)
  if ! command -v gc >/dev/null 2>&1; then
    echo "📦 Installing gitcode-cli..."
    npm install -g gitcode-cli >/dev/null 2>&1
    echo "✅ gitcode-cli installed: $(gc --version 2>&1 || true)"
  fi

  # Fix ownership of home dir after root-level npm/cp operations
  chown -R node:node /home/node 2>/dev/null || true

  # Re-exec as node user for Phase 2
  echo "🔑 Switching to node user..."
  exec su node -s /bin/sh -c "exec sh $0"
fi

# ── Phase 2: node — configure and start ──────────────────────────────────

STATE_DIR="/home/node/.openclaw"
CONFIG_FILE="${STATE_DIR}/openclaw.json"
PLUGIN_MARKER="${STATE_DIR}/.feishu-plugin-installed"
WORKSPACE_DIR="${STATE_DIR}/workspace"

mkdir -p "${STATE_DIR}" "${WORKSPACE_DIR}"

# ── Configure CLI authentication from env vars ───────────────────────────
mkdir -p /home/node/.config/gh /home/node/.gitcode

if [ -n "${TIANYI_BOT_GITHUB_TOKEN:-}" ]; then
  cat > /home/node/.config/gh/hosts.yml << YAML
github.com:
    oauth_token: "${TIANYI_BOT_GITHUB_TOKEN}"
    user: tianyi-bot
    git_protocol: https
YAML
  chmod 600 /home/node/.config/gh/hosts.yml
  export GITHUB_TOKEN="${TIANYI_BOT_GITHUB_TOKEN}"
fi

if [ -n "${TIANYI_BOT_GITCODE_TOKEN:-}" ]; then
  cat > /home/node/.gitcode/config.json << JSON
{
  "host": "gitcode.com",
  "token": "${TIANYI_BOT_GITCODE_TOKEN}"
}
JSON
  chmod 600 /home/node/.gitcode/config.json
  export GITCODE_TOKEN="${TIANYI_BOT_GITCODE_TOKEN}"
fi

if [ -n "${TIANYI_BOT_MODEL_API_KEY:-}" ]; then
  export DEEPSEEK_API_KEY="${TIANYI_BOT_MODEL_API_KEY}"
fi

# ── Wait for repo-scanner-mcp ────────────────────────────────────────────
echo "⏳ Waiting for repo-scanner-mcp (http://repo-scanner-mcp:8001)..."
for i in $(seq 1 30); do
  if curl -fsS "http://repo-scanner-mcp:8001/health" 2>/dev/null; then
    echo ""
    echo "✅ repo-scanner-mcp is ready"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo ""
    echo "⚠️  repo-scanner-mcp not reachable after 60s. Starting without it."
    echo "   Read tools (get_daily_report, query_commits) will be unavailable."
  fi
  printf "."
  sleep 2
done

# ── Sync workspace policy files ──────────────────────────────────────────
for source_file in /opt/tianyi-workspace/*; do
  target_file="${WORKSPACE_DIR}/$(basename "${source_file}")"
  cp "${source_file}" "${target_file}"
done

# ── Install feishu plugin (once) ─────────────────────────────────────────
if [ ! -f "${PLUGIN_MARKER}" ]; then
  echo "📦 Installing OpenClaw feishu plugin..."
  node /app/openclaw.mjs plugins install @openclaw/feishu
  touch "${PLUGIN_MARKER}"
fi

# ── Render config from template ──────────────────────────────────────────
node /opt/tianyi-bot/render-config.mjs \
  /opt/tianyi-bot/openclaw.json.template \
  "${CONFIG_FILE}"

echo "🔍 Validating OpenClaw config..."
node /app/openclaw.mjs config validate

echo "🚀 Starting tianyi feishu bot (天一)..."
exec node /app/openclaw.mjs gateway --allow-unconfigured
