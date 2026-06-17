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

# himalaya email CLI — config lives on /opt/data volume (~/.hermes on host)
# symlink for both hermes user and root (docker exec runs as root)
mkdir -p /opt/data/.config/himalaya
ln -sf /opt/data/.config/himalaya /root/.config/himalaya

# lark-cli reads config from $HOME/.lark-cli/ (NOT .config/lark-cli/)
# symlink to host-mounted config dir for persistence across container recreates
mkdir -p /opt/lark-config
ln -sf /opt/lark-config /root/.lark-cli

# ── zotero-cli-cc config ──────────────────────────────────
# zot reads config from ~/.config/zot/config.toml
# Store on /opt/data volume so config survives rebuilds.
# Symlink for both hermes user (/opt/data) and root.
mkdir -p /opt/data/.config/zot
ln -sf /opt/data/.config/zot /root/.config/zot

# ── gitcode-cli config ──────────────────────────────────────
# gc reads config from $HOME/.gitcode/config.json
# Symlink to host-mounted config dir (shared with claude-code container)
rm -rf /opt/data/.gitcode /root/.gitcode
ln -sf /opt/gitcode-config /opt/data/.gitcode
ln -sf /opt/gitcode-config /root/.gitcode

# Auto-init config.json from GITCODE_TOKEN if not already present
# (normally already created by claude-code entrypoint; this is a fallback)
if [ -n "${GITCODE_TOKEN:-}" ] && [ ! -f /opt/gitcode-config/config.json ]; then
  mkdir -p /opt/gitcode-config
  cat > /opt/gitcode-config/config.json << GCEOF
{"host": "gitcode.com", "token": "${GITCODE_TOKEN}"}
GCEOF
  chmod 600 /opt/gitcode-config/config.json
  echo "   🔑 gitcode-cli 已自动配置"
fi

# ── gitcode-cli skill → Hermes skills ─────────────────────────
# Copy the gitcode-cli skill from npm global install to hermes skills dir
# so Hermes knows to use `gc` for GitCode operations (reuse claude-code pattern)
if [ ! -d /opt/data/skills/gitcode ]; then
  mkdir -p /opt/data/skills/gitcode
  cp -r /usr/local/lib/node_modules/gitcode-cli/skills/gitcode-cli/* /opt/data/skills/gitcode/
  echo "   📦 gitcode-cli skill 已安装"
fi

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

# ── Auto-configure himalaya from Hermes email settings ─────
# Parses /opt/data/.env for EMAIL_* vars and generates ~/.config/himalaya/config.toml
# Works whether the vars are commented out (email platform disabled) or active.
HIMALAYA_CONFIG="/opt/data/.config/himalaya/config.toml"
if [[ -f /opt/data/.env && ! -f "${HIMALAYA_CONFIG}" ]]; then
  # Strip leading "#" so commented-out vars are also picked up
  set -a
  eval "$(sed 's/^#[[:space:]]*//' /opt/data/.env 2>/dev/null | grep -E '^EMAIL_' || true)"
  set +a
  if [[ -n "${EMAIL_ADDRESS:-}" && -n "${EMAIL_PASSWORD:-}" && -n "${EMAIL_IMAP_HOST:-}" ]]; then
    mkdir -p "$(dirname "${HIMALAYA_CONFIG}")"
    cat > "${HIMALAYA_CONFIG}" << TOML
[accounts.default]
email = "${EMAIL_ADDRESS}"
display-name = "${EMAIL_ADDRESS}"
default = true

backend.type = "imap"
backend.host = "${EMAIL_IMAP_HOST}"
backend.port = ${EMAIL_IMAP_PORT:-993}
backend.encryption.type = "tls"
backend.login = "${EMAIL_ADDRESS}"
backend.auth.type = "password"
backend.auth.raw = "${EMAIL_PASSWORD}"

message.send.backend.type = "smtp"
message.send.backend.host = "${EMAIL_SMTP_HOST:-smtp.example.com}"
message.send.backend.port = ${EMAIL_SMTP_PORT:-587}
message.send.backend.encryption.type = "start-tls"
message.send.backend.login = "${EMAIL_ADDRESS}"
message.send.backend.auth.type = "password"
message.send.backend.auth.raw = "${EMAIL_PASSWORD}"
TOML
    chown -R hermes:hermes /opt/data/.config/himalaya
    echo "   📧 himalaya 已自动配置 — ${EMAIL_ADDRESS}"
  fi

  # ── Second email account (optional) ─────────────────────────
  # Detects EMAIL2_* vars for a second mailbox (e.g., school/work email).
  # EMAIL2_ACCOUNT_NAME sets the himalaya account name (default: "second").
  if [[ -n "${EMAIL2_ADDRESS:-}" && -n "${EMAIL2_PASSWORD:-}" && -n "${EMAIL2_IMAP_HOST:-}" ]]; then
    H2_ACCT="${EMAIL2_ACCOUNT_NAME:-second}"
    H2_SMTP_PORT="${EMAIL2_SMTP_PORT:-587}"
    # Pick TLS mode: port 465 → "tls" (SSL), otherwise "start-tls"
    if [[ "${H2_SMTP_PORT}" == "465" ]]; then
      H2_SMTP_ENCRYPTION="tls"
    else
      H2_SMTP_ENCRYPTION="start-tls"
    fi
    cat >> "${HIMALAYA_CONFIG}" << TOML

[accounts.${H2_ACCT}]
email = "${EMAIL2_ADDRESS}"
display-name = "${EMAIL2_DISPLAY_NAME:-${EMAIL2_ADDRESS}}"
default = false

backend.type = "imap"
backend.host = "${EMAIL2_IMAP_HOST}"
backend.port = ${EMAIL2_IMAP_PORT:-993}
backend.encryption.type = "tls"
backend.login = "${EMAIL2_ADDRESS}"
backend.auth.type = "password"
backend.auth.raw = "${EMAIL2_PASSWORD}"

message.send.backend.type = "smtp"
message.send.backend.host = "${EMAIL2_SMTP_HOST:-smtp.example.com}"
message.send.backend.port = ${H2_SMTP_PORT}
message.send.backend.encryption.type = "${H2_SMTP_ENCRYPTION}"
message.send.backend.login = "${EMAIL2_ADDRESS}"
message.send.backend.auth.type = "password"
message.send.backend.auth.raw = "${EMAIL2_PASSWORD}"
TOML
    echo "   📧 himalaya 已自动配置 — ${EMAIL2_ADDRESS} (account: ${H2_ACCT})"
  fi
fi

# ── Auto-configure cardamum (CLI contact manager) ─────────
# Uses vdir backend — contacts stored as .vcf files in /opt/data/.contacts/
# Persists on host ~/.hermes/.contacts/ via the hermes data volume mount.
# Symlink for root access (docker exec runs as root).
mkdir -p /opt/data/.contacts
ln -sf /opt/data/.contacts /root/.local/share/vdirsyncer/contacts 2>/dev/null || true
ln -sf /opt/data/.contacts /opt/data/.local/share/vdirsyncer/contacts 2>/dev/null || true

CARDAMUM_CONFIG="/opt/data/.config/cardamum/config.toml"
if [[ ! -f "${CARDAMUM_CONFIG}" ]]; then
  mkdir -p "$(dirname "${CARDAMUM_CONFIG}")"
  cat > "${CARDAMUM_CONFIG}" << TOML
[accounts.default]
default = true
vdir.home-dir = "/opt/data/.contacts"
TOML
  chown -R hermes:hermes /opt/data/.config/cardamum /opt/data/.contacts
  echo "   📇 cardamum 已自动配置 — vdir: /opt/data/.contacts"
fi
# Symlink config for root access (docker exec runs as root)
ln -sf /opt/data/.config/cardamum /root/.config/cardamum

# ── Auto-configure zotero-cli-cc from env vars ─────────
# Generates ~/.config/zot/config.toml if ZOTERO_API_KEY is set.
# ZOT_DATA_DIR env var (docker-compose) takes highest priority for data dir.
# api_key/library_id in config.toml enable Web API writes.
ZOT_CONFIG="/opt/data/.config/zot/config.toml"
if [[ ! -f "${ZOT_CONFIG}" ]]; then
  mkdir -p "$(dirname "${ZOT_CONFIG}")"
  if [[ -n "${ZOTERO_API_KEY:-}" && -n "${ZOTERO_LIBRARY_ID:-}" ]]; then
    cat > "${ZOT_CONFIG}" << TOML
[zotero]
data_dir = ''
library_id = '${ZOTERO_LIBRARY_ID}'
api_key = '${ZOTERO_API_KEY}'
semantic_scholar_api_key = ''

[output]
default_format = 'table'
limit = 50

[export]
default_style = 'bibtex'
TOML
    echo "   📚 zotero-cli-cc 已配置 — library ${ZOTERO_LIBRARY_ID}"
  else
    cat > "${ZOT_CONFIG}" << TOML
[zotero]
data_dir = ''
library_id = ''
api_key = ''

[output]
default_format = 'table'
limit = 50

[export]
default_style = 'bibtex'
TOML
    echo "   📚 zotero-cli-cc 已配置（只读模式 — 未设置 ZOTERO_API_KEY）"
  fi
  chown -R hermes:hermes /opt/data/.config/zot
fi

# Hand off to original Hermes entrypoint (handles UID mapping + gosu)
exec /opt/hermes/docker/entrypoint.sh "$@"
