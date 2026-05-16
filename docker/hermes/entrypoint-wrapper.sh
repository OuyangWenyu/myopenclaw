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

# lark-cli reads config from $HOME/.lark-cli/ (NOT .config/lark-cli/)
# symlink to host-mounted config dir for persistence across container recreates
mkdir -p /opt/lark-config
ln -sf /opt/lark-config /root/.lark-cli

# Auto-configure lark-cli if credentials are available via env vars
# LARK_CLI_APP_ID / LARK_CLI_APP_SECRET — primary app (Hermes)
# LARK_CLI_IDM_APP_ID / LARK_CLI_IDM_APP_SECRET — secondary app (爱码士)
# If not set, falls back to Hermes's built-in Feishu app via `config bind`
if [[ -n "${LARK_CLI_APP_ID:-}" && -n "${LARK_CLI_APP_SECRET:-}" ]]; then
  if [[ ! -f /opt/lark-config/hermes/config.json ]]; then
    printf '%s' "${LARK_CLI_APP_SECRET}" | lark-cli config init \
      --app-id "${LARK_CLI_APP_ID}" --app-secret-stdin --brand feishu --force-init 2>/dev/null && \
      echo "   📎 lark-cli 已初始化 — Hermes (app: ${LARK_CLI_APP_ID})"
  fi
  # Configure second app (爱码士) as a separate profile
  if [[ -n "${LARK_CLI_IDM_APP_ID:-}" && -n "${LARK_CLI_IDM_APP_SECRET:-}" ]] && \
     [[ ! -f /opt/lark-config/idm/config.json ]]; then
    printf '%s' "${LARK_CLI_IDM_APP_SECRET}" | lark-cli config init \
      --app-id "${LARK_CLI_IDM_APP_ID}" --app-secret-stdin --brand feishu \
      --name idm --force-init 2>/dev/null && \
      echo "   📎 lark-cli 已初始化 — 爱码士 (app: ${LARK_CLI_IDM_APP_ID})"
  fi
else
  lark-cli config bind --source hermes --identity bot-only 2>/dev/null && \
    echo "   📎 lark-cli 已绑定 Hermes 飞书应用" || \
    echo "   ⚠️  lark-cli 未配置，请设置 LARK_CLI_APP_ID/SECRET 或手动运行 lark-cli config init"
fi

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
  "OPENAI_API_KEY=openai-api-key" \
  "OPENCODE_API_KEY=opencode-api-key"; do
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
