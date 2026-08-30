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
umask 077

# hermes user home = /opt/data; Hermes terminal HOME = /opt/data/home
# gh CLI reads $HOME/.config/gh/ — 仅显式授权的 profile 终端 home 链接到 host 挂载的配置目录。
# 必须先 rm -rf，否则如果目标已存在为目录，ln 会把链接建在目录里面而非替换它。
#
# ── gh config symlink — 仅显式授权的 profile 终端 home ──────────
# 安全隔离：默认不授予任何 agent gh 权限。要授权某 agent，
# 把它的 profile home（{HERMES_HOME}/profiles/<name>/home）加入列表。
# Hermes 会从 terminal 子进程环境剥离 GH_TOKEN/GITHUB_TOKEN（Tier-1 secrets，
# env_passthrough 无法放行），因此 gh 认证只能走 hosts.yml 文件通道 —— 链接到这里即授权。
GH_ENABLED_PROFILE_HOMES="profiles/coder/home"

# 先清除历史遗留的主 home 全局链接（爱玛士/道元/爱码士 finance 终端将不再有 gh 权限）
rm -rf /opt/data/.config/gh /opt/data/home/.config/gh /root/.config/gh

for rel in $GH_ENABLED_PROFILE_HOMES; do
  profile_home="/opt/data/$rel"
  [ -d "$profile_home" ] || continue
  mkdir -p "$profile_home/.config"
  rm -rf "$profile_home/.config/gh"
  ln -sf /opt/gh-config "$profile_home/.config/gh"
  echo "   🔗 gh config → ${profile_home}/.config/gh"
done

# ── gh hosts.yml 自动同步 ───────────────────────────────────
# 每次容器启动时，用 .env 里的 GH_TOKEN（传入为 GITHUB_TOKEN）更新
# hosts.yml 中的 oauth_token。始终用 heredoc 重新生成（不用 sed），
# 避免 token 出现在 /proc/pid/cmdline 中。
#
# 使用 legacy 单账户格式（不加 users: 键），避免触发 gh 2.96+ 的
# 多账户迁移路径——该路径需要 dbus secret service，在 Docker 容器里不可用。
if [ -n "${GITHUB_TOKEN:-}" ]; then
  mkdir -p /opt/gh-config
  # 清理旧格式 config.yml（触发多账户迁移的源头）
  rm -f /opt/gh-config/config.yml
  cat > /opt/gh-config/hosts.yml << HOSTSEOF
github.com:
    user: OuyangWenyu
    oauth_token: ${GITHUB_TOKEN}
    git_protocol: https
HOSTSEOF
  echo "   🔑 gh hosts.yml 已同步当前 GITHUB_TOKEN"
else
  # GITHUB_TOKEN 未设置，检查 host 挂载的 hosts.yml 是否已有有效 token
  if [ -f /opt/gh-config/hosts.yml ] && grep -q 'oauth_token: [a-zA-Z0-9_]' /opt/gh-config/hosts.yml; then
    echo "   🔑 未检测到 GITHUB_TOKEN，使用 host 端已有 hosts.yml token"
  else
    echo "   ⚠️  GITHUB_TOKEN 未设置且 hosts.yml 无有效 token — gh CLI 将不可用"
  fi
fi

# 确保权限正确（放在 if 块外面：覆盖 token 更新和 fallback 两种情况）
[ -f /opt/gh-config/hosts.yml ] && {
  chmod 600 /opt/gh-config/hosts.yml
  chown hermes:hermes /opt/gh-config/hosts.yml 2>/dev/null || true
}

# ── 验证 gh auth 是否可用 ───────────────────────────────────
# 只验证显式授权列表（GH_ENABLED_PROFILE_HOMES）中的 profile home
# stdout 重定向到 /dev/null，避免 gh auth status 输出写入 Docker 日志
if command -v gh &>/dev/null; then
  for rel in $GH_ENABLED_PROFILE_HOMES; do
    profile_home="/opt/data/$rel"
    [ -d "$profile_home" ] || continue
    if su -s /bin/bash hermes -c "HOME='$profile_home' gh auth status --hostname github.com" >/dev/null 2>&1; then
      echo "   ✅ gh auth 验证通过 ($profile_home)"
    else
      echo "   ⚠️  gh auth 验证失败 ($profile_home) — 请检查 GH_TOKEN 或 hosts.yml"
    fi
  done
fi

# himalaya + ortie — config lives on /opt/data volume (~/.hermes on host)
# Symlink for root (docker exec) and the terminal HOME (/opt/data/home)
# so both `docker compose exec` and Hermes agent terminals find the files.
mkdir -p /opt/data/.config/himalaya /opt/data/.config/ortie/tokens
mkdir -p /root/.config /opt/data/home/.config
chmod 700 /opt/data/.config/ortie /opt/data/.config/ortie/tokens
rm -rf /root/.config/himalaya /root/.config/ortie
rm -rf /opt/data/home/.config/himalaya /opt/data/home/.config/ortie
ln -sfn /opt/data/.config/himalaya /root/.config/himalaya
ln -sfn /opt/data/.config/ortie /root/.config/ortie
ln -sfn /opt/data/.config/himalaya /opt/data/home/.config/himalaya
ln -sfn /opt/data/.config/ortie /opt/data/home/.config/ortie

# lark-cli reads config from $HOME/.lark-cli/ (NOT .config/lark-cli/)
# symlink to host-mounted config dir for persistence across container recreates
mkdir -p /opt/lark-config
ln -sf /opt/lark-config /root/.lark-cli

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

# ── morning-briefing skill → Hermes skills ─────────────────────
# Symlink from read-only volume mount to hermes skills dir
if [ -d /opt/hermes-skills/morning-briefing ] && [ ! -L /opt/data/skills/morning-briefing ]; then
  mkdir -p /opt/data/skills
  ln -sf /opt/hermes-skills/morning-briefing /opt/data/skills/morning-briefing
  echo "   📋 morning-briefing skill 已安装"
fi

# ── morning-triage-v2 skill → Hermes skills ─────────────────────
if [ -d /opt/hermes-skills/morning-triage-v2 ] && [ ! -L /opt/data/skills/morning-triage-v2 ]; then
  mkdir -p /opt/data/skills
  ln -sf /opt/hermes-skills/morning-triage-v2 /opt/data/skills/morning-triage-v2
  echo "   📋 morning-triage-v2 skill 已安装"
fi

# ── daily-dev-report skill → Hermes skills ───────────────────────
if [ -d /opt/hermes-skills/daily-dev-report ] && [ ! -L /opt/data/skills/daily-dev-report ]; then
  mkdir -p /opt/data/skills
  ln -sf /opt/hermes-skills/daily-dev-report /opt/data/skills/daily-dev-report
  echo "   📋 daily-dev-report skill 已安装"
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
  "OPENCODE_API_KEY=opencode-api-key" \
  "GITHUB_TOKEN=github-token"; do
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
# EMAIL_* 加载优先级：容器环境（repo .env 经 compose 注入）优先，~/.hermes/.env 兜底。
# 变量只赋值为本脚本 shell 变量，不导出——绝不进入 Hermes 进程环境
# （EMAIL_PASSWORD 等真机密留在文件里，Hermes 不把 email 当消息平台）。
HIMALAYA_CONFIG="/opt/data/.config/himalaya/config.toml"
if [[ -f /opt/data/.env ]]; then
  while IFS= read -r kv; do
    key="${kv%%=*}"
    if [[ -n "${key}" && -z "${!key:-}" ]]; then
      eval "${kv}"
    fi
  done < <(sed 's/^#[[:space:]]*//' /opt/data/.env 2>/dev/null | grep -E '^EMAIL_' || true)
fi
if [[ -f /opt/data/.env && ! -f "${HIMALAYA_CONFIG}" ]]; then
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

# ── Outlook / Microsoft 365 via ortie OAuth ─────────────────
# Idempotent: appends [accounts.outlook] to an existing himalaya config
# and writes ~/.config/ortie/config.toml. First-time auth is interactive
# (`ortie auth get`) and is intentionally not run here.
if [[ -n "${EMAIL_OUTLOOK_ADDRESS:-}" ]]; then
  # 失败降级：Outlook 是可选增强，配置错误不应拖垮整个 Hermes 容器
  if ! HERMES_DATA=/opt/data /opt/hermes/configure-outlook.sh; then
    echo "   ⚠️  Outlook 配置生成失败（检查 EMAIL_OUTLOOK_* 设置），跳过"
  fi
  chown -R hermes:hermes /opt/data/.config/himalaya /opt/data/.config/ortie 2>/dev/null || true
fi

# ── Auto-detect sent/drafts/trash folder names ─────────────
# himalaya v1.2.0 uses `folder.aliases.sent` (dotted key) to know where to
# save sent-mail copies. Without this, `message.send.save-copy` (default: true)
# fails with "Folder not exist" because the default sent folder name "Sent"
# rarely matches real servers (QQ→"Sent Messages", Coremail→"Sent Items", etc.).
# Detect actual folder names via `himalaya folder list` and add aliases.
if command -v himalaya &>/dev/null && [[ -f "${HIMALAYA_CONFIG}" ]]; then
  # Get account names from config: lines like [accounts.xxx]
  HIMALAYA_ACCOUNTS="$(grep -oP '^\[accounts\.\K[^]]+' "${HIMALAYA_CONFIG}" 2>/dev/null || true)"
  for H_ACCT in ${HIMALAYA_ACCOUNTS}; do
    # Skip if this account already has folder.aliases.sent configured
    # NOTE: -q suppresses -A output, so drop -q on the first grep for the pipe.
    if grep -A50 "^\[accounts\.${H_ACCT}\]" "${HIMALAYA_CONFIG}" 2>/dev/null | \
       grep -q "^folder\.aliases\.sent\s*=" 2>/dev/null; then
      continue
    fi
    # List folders and detect sent/drafts/trash names
    H_FOLDERS="$(himalaya folder list -a "${H_ACCT}" 2>/dev/null || true)"
    if [[ -z "${H_FOLDERS}" ]]; then
      continue
    fi
    H_SENT="$(echo "${H_FOLDERS}" | awk -F'|' 'NR>1 {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' | grep -i -m1 'sent')"
    H_DRAFTS="$(echo "${H_FOLDERS}" | awk -F'|' 'NR>1 {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' | grep -i -m1 'draft')"
    H_TRASH="$(echo "${H_FOLDERS}" | awk -F'|' 'NR>1 {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' | grep -i -m1 'trash\|deleted')"
    if [[ -n "${H_SENT}" ]]; then
      # Append folder.aliases as dotted keys after the account section.
      # We find the line after the last setting of this account (before next
      # [accounts.xxx] or end of file) and insert there.
      H_NEXT_SECTION="$(grep -n "^\[accounts\." "${HIMALAYA_CONFIG}" | grep -A1 "^[0-9]*:\[accounts\.${H_ACCT}\]" | tail -1 | cut -d: -f1)"
      if [[ -n "${H_NEXT_SECTION}" ]]; then
        # Insert before the next section
        sed -i "${H_NEXT_SECTION}i\\
folder.aliases.sent = \"${H_SENT}\"${H_DRAFTS:+\\
folder.aliases.drafts = \"${H_DRAFTS}\"}${H_TRASH:+\\
folder.aliases.trash = \"${H_TRASH}\"}\\
" "${HIMALAYA_CONFIG}"
      else
        # No next section — append at end of file
        cat >> "${HIMALAYA_CONFIG}" << TOML
folder.aliases.sent = "${H_SENT}"${H_DRAFTS:+
folder.aliases.drafts = "${H_DRAFTS}"}${H_TRASH:+
folder.aliases.trash = "${H_TRASH}"}
TOML
      fi
      echo "   📧 himalaya folder.aliases → ${H_ACCT}: sent=${H_SENT}${H_DRAFTS:+, drafts=${H_DRAFTS}}${H_TRASH:+, trash=${H_TRASH}}"
    fi
  done
fi

# ── Auto-configure zotero (paper pipeline config) ──────────
# paper_to_zotero.py reads Zotero API credentials from this config file.
# Generated from env vars set in docker-compose.yml.
ZOT_CONFIG_DIR="/opt/data/.config/zot"
ZOT_CONFIG="${ZOT_CONFIG_DIR}/config.toml"
mkdir -p "${ZOT_CONFIG_DIR}"
if [[ ! -f "${ZOT_CONFIG}" ]]; then
  if [[ -n "${ZOTERO_API_KEY:-}" && -n "${ZOTERO_LIBRARY_ID:-}" ]]; then
    cat > "${ZOT_CONFIG}" << TOML
[zotero]
data_dir = '${ZOT_DATA_DIR:-}'
library_id = '${ZOTERO_LIBRARY_ID}'
api_key = '${ZOTERO_API_KEY}'
semantic_scholar_api_key = ''

[output]
default_format = 'table'
limit = 50

[export]
default_style = 'bibtex'
TOML
    ln -sf "${ZOT_CONFIG_DIR}" /root/.config/zot
    chown -R hermes:hermes "${ZOT_CONFIG_DIR}"
    echo "   📚 zotero 配置已生成 — library ${ZOTERO_LIBRARY_ID}"
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
    ln -sf "${ZOT_CONFIG_DIR}" /root/.config/zot
    chown -R hermes:hermes "${ZOT_CONFIG_DIR}"
    echo "   📚 zotero 配置已生成（只读模式 — 未设置 ZOTERO_API_KEY）"
  fi
fi

# ── Auto-configure cardamum (CLI contact manager) ─────────
# cardamum uses ~/.config/cardamum/config.toml (pimalaya_config default).
# Hermes terminal HOME = /opt/data/home (not /opt/data), so config goes there.
# Contacts (vdir) stored at /opt/data/.contacts/ — root of volume, covered by backup.
CONTACTS_DIR="/opt/data/.contacts"
CARDAMUM_CONFIG_DIR="/opt/data/home/.config/cardamum"
CARDAMUM_CONFIG="${CARDAMUM_CONFIG_DIR}/config.toml"

mkdir -p "${CONTACTS_DIR}" "${CARDAMUM_CONFIG_DIR}"

if [[ ! -f "${CARDAMUM_CONFIG}" ]]; then
  cat > "${CARDAMUM_CONFIG}" << TOML
[accounts.default]
default = true
vdir.home-dir = "${CONTACTS_DIR}"
TOML
  echo "   📇 cardamum 已自动配置 — vdir: ${CONTACTS_DIR}"
elif ! grep -q "vdir.home-dir = \"${CONTACTS_DIR}\"" "${CARDAMUM_CONFIG}" 2>/dev/null; then
  sed -i "s|vdir.home-dir = \".*\"|vdir.home-dir = \"${CONTACTS_DIR}\"|" "${CARDAMUM_CONFIG}"
  echo "   📇 cardamum vdir 路径已修正 → ${CONTACTS_DIR}"
fi

# Migrate any contacts from old default location to the persistent directory
OLD_VDIR="/opt/data/home/.local/share/cardamum"
if [[ -d "${OLD_VDIR}" ]] && [[ "$(ls -A "${OLD_VDIR}" 2>/dev/null)" ]]; then
  for ab_dir in "${OLD_VDIR}"/*/; do
    ab_name="$(basename "${ab_dir}")"
    if [[ ! -d "${CONTACTS_DIR}/${ab_name}" ]]; then
      cp -r "${ab_dir}" "${CONTACTS_DIR}/${ab_name}"
      echo "   📇 已迁移通讯录: ${ab_name} → ${CONTACTS_DIR}"
    fi
  done
fi

chown -R hermes:hermes "${CONTACTS_DIR}" "${CARDAMUM_CONFIG_DIR}"

# Symlink for root access (docker exec runs as root) — must exist before
# running cardamum commands below so they find the config.
mkdir -p /root/.config
ln -sf "${CARDAMUM_CONFIG_DIR}" /root/.config/cardamum

# Clean up old incorrect config paths from previous entrypoint versions
rm -f /opt/data/home/.config/cardamum.toml /root/.config/cardamum.toml 2>/dev/null || true
rm -rf /opt/data/.config/cardamum 2>/dev/null || true

# Auto-create addressbook if vdir is empty (fresh machine / first run).
# cardamum addressbook create generates a UUID directory; we capture it
# and write it as addressbook.default so Hermes can use `card list`
# without hardcoding the ID.
if ! grep -q "^addressbook.default" "${CARDAMUM_CONFIG}" 2>/dev/null && \
   command -v cardamum &>/dev/null; then
  # Check if any addressbook already exists in the vdir
  EXISTING_AB="$(ls -d "${CONTACTS_DIR}"/*/displayname 2>/dev/null || true)"
  if [[ -z "${EXISTING_AB}" ]]; then
    # No addressbook yet — create one
    AB_CREATE_OUT="$(cardamum -c "${CARDAMUM_CONFIG}" addressbook create "contacts" 2>&1 || true)"
    echo "   📇 ${AB_CREATE_OUT}"
  fi
  # Discover the addressbook UUID and persist it as the default
  AB_ID="$(cardamum -c "${CARDAMUM_CONFIG}" addressbook list 2>/dev/null | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
  if [[ -n "${AB_ID}" ]]; then
    if grep -q "^\[addressbook\]" "${CARDAMUM_CONFIG}" 2>/dev/null; then
      # Check for existing default under [addressbook] only (not [accounts.default])
      if sed -n '/^\[addressbook\]/,/^\[/p' "${CARDAMUM_CONFIG}" | grep -q "^default = "; then
        # Replace existing default under [addressbook] only (handles UUID changes)
        sed -i "/^\[addressbook\]/,/^\[/ s/^default = .*/default = \"${AB_ID}\"/" "${CARDAMUM_CONFIG}"
        echo "   📇 cardamum addressbook.default → ${AB_ID}"
      else
        sed -i "/^\[addressbook\]/a default = \"${AB_ID}\"" "${CARDAMUM_CONFIG}"
        echo "   📇 cardamum addressbook.default = ${AB_ID}"
      fi
    else
      cat >> "${CARDAMUM_CONFIG}" << TOML

[addressbook]
default = "${AB_ID}"
TOML
      echo "   📇 cardamum addressbook.default = ${AB_ID}"
    fi
  fi
fi

# ── TDAI Memory plugin install + inject provider ───────────────
# Runtime install (not in Dockerfile) to avoid cache invalidation of the
# cardamum Rust build stage. The npm package ships a Python MemoryProvider
# at hermes-plugin/memory/memory_tencentdb/ that Hermes discovers under
# /opt/hermes/plugins/memory/<name>/.
#
# Key facts learned from integration:
#   - The provider reads the Gateway address from env vars
#     MEMORY_TENCENTDB_GATEWAY_HOST / _PORT (set in docker-compose.yml),
#     NOT from config.yaml. So config only needs `memory.provider`.
#   - Hermes scans for a real directory with __init__.py; a symlink is NOT
#     recognized — the plugin dir must be copied in place.
#   - The registered provider name is `memory_tencentdb` (no _v2 suffix).
PKG="@tencentdb-agent-memory/memory-tencentdb"
PKG_DIR="/usr/local/lib/node_modules/${PKG}"
PLUGIN_SRC="${PKG_DIR}/hermes-plugin/memory/memory_tencentdb"
PLUGIN_DST="/opt/hermes/plugins/memory/memory_tencentdb"

if [ -d "${PLUGIN_SRC}" ]; then
    # Copy (not symlink) so Hermes's plugin scanner discovers it.
    rm -rf "${PLUGIN_DST}"
    cp -r "${PLUGIN_SRC}" "${PLUGIN_DST}"
    chown -R hermes:hermes "${PLUGIN_DST}" 2>/dev/null || true
    echo "   🧠 TDAI Memory plugin 已安装 (memory_tencentdb)"
else
    echo "   ⚠️  TDAI Memory plugin 未找到 — 跳过"
fi

# ── Inject memory.provider into profile configs ────────────────
# Only sets `provider: memory_tencentdb` in the existing `memory:` section.
# Gateway address comes from env vars, so no gateway_url/session_id needed
# in config. Idempotent: skips if the provider is already set.
if [ -d "${PLUGIN_DST}" ]; then
    for config in /opt/data/config.yaml /opt/data/profiles/*/config.yaml; do
        [ -f "${config}" ] || continue
        if grep -q 'provider: memory_tencentdb' "${config}" 2>/dev/null; then
            echo "   ✅ $(dirname "${config}"): memory provider 已配置，跳过"
            continue
        fi
        # Replace the empty built-in provider with memory_tencentdb.
        if grep -qE "^  provider: ''" "${config}" 2>/dev/null; then
            python3 - "${config}" << 'PYEOF'
import sys
path = sys.argv[1]
with open(path) as f:
    lines = f.readlines()
out = []
section = None
done = False
for line in lines:
    # Track the current top-level section (memory:, delegation:, etc.)
    if line and not line[0].isspace() and line.rstrip('\n').endswith(':'):
        section = line.rstrip('\n')[:-1]
    # Only replace the empty provider INSIDE the top-level memory: block.
    if not done and section == 'memory' and line.strip() == "provider: ''":
        out.append("  provider: memory_tencentdb\n")
        done = True
        continue
    out.append(line)
with open(path, 'w') as f:
    f.writelines(out)
PYEOF
            echo "   🧠 $(dirname "${config}"): memory provider → memory_tencentdb"
        else
            echo "   ⚠️  $(dirname "${config}"): 未找到可替换的 provider 字段，跳过"
        fi
    done
fi

# ── Ensure profile auto-starts after container restart ──────────
# v0.18.2 multi-profile reconciliation reads desired_state from per-profile
# gateway_state.json. Multiple containers share $HERMES_HOME; when one gateway
# stops another container's profile, it writes gateway_state:stopped. Without
# an explicit desired_state:running, the next reconcile registers but does NOT
# auto-start the profile — so the bot stays silent.
#
# Write operator intent for THIS container's profile, and REMOVE desired_state
# from other profiles so they don't auto-start here (each container only owns
# one profile). Deleting the state file doesn't affect already-running gateways
# in other containers — reconcile only matters at boot time.
HERMES_HOME="${HERMES_HOME:-/opt/data}"
PROFILE="${HERMES_PROFILE:-default}"

# Ensure this profile's state says desired_state=running
STATE_FILE="${HERMES_HOME}/gateway_state.json"
if [ "${PROFILE}" != "default" ]; then
    STATE_FILE="${HERMES_HOME}/profiles/${PROFILE}/gateway_state.json"
    mkdir -p "$(dirname "${STATE_FILE}")"
fi
if [ ! -f "${STATE_FILE}" ] || ! grep -q '"desired_state"' "${STATE_FILE}" 2>/dev/null; then
    python3 - "${STATE_FILE}" << 'PYEOF'
import sys, json, time
state_file = sys.argv[1]
try:
    with open(state_file) as f:
        data = json.load(f)
except Exception:
    data = {}
data["desired_state"] = "running"
data.setdefault("gateway_state", "running")
data["timestamp"] = int(time.time())
with open(state_file, 'w') as f:
    json.dump(data, f)
PYEOF
    echo "   🔄 ${PROFILE} desired_state → running"
fi

# Delete gateway_state.json for OTHER profiles so reconcile doesn't
# auto-start them in this container. A missing file → prior_state=None →
# not in _AUTOSTART_STATES → registered but not started. This is safe
# because other containers' already-running gateways are unaffected by
# the file deletion (reconcile only matters at boot time).
if [ -d "${HERMES_HOME}/profiles" ]; then
    for pdir in "${HERMES_HOME}/profiles"/*/; do
        pname="$(basename "${pdir}")"
        [ "${pname}" = "${PROFILE}" ] && continue
        rm -f "${pdir}/gateway_state.json"
    done
fi
if [ "${PROFILE}" != "default" ]; then
    rm -f "${HERMES_HOME}/gateway_state.json"
fi

# ── Profile-scoped gateway isolation ──────────────────────────
# Hermes dynamically creates s6-supervised gateway services for ALL
# profiles after startup. They all share the same container env vars and
# config.yaml, so they compete for single-connection platform tokens
# (e.g. Discord). Launch a background task that waits for the gateway
# services to appear, then kills every one whose profile doesn't match
# HERMES_PROFILE, and finally restarts the correct gateway so it gets a
# clean connection.
HERMES_PROFILE="${HERMES_PROFILE:-default}"
S6_SVC="/package/admin/s6-2.15.0.0/command/s6-svc"
if [ -x "${S6_SVC}" ]; then
  (
    # Wait for Hermes to create the gateway supervision trees
    for _ in $(seq 1 12); do
      sleep 5
      ls /run/service/gateway-*/run >/dev/null 2>&1 && break
    done
    for svc_dir in /run/service/gateway-*; do
      [ -d "${svc_dir}" ] || continue
      svc_name="$(basename "${svc_dir}")"
      profile_suffix="${svc_name#gateway-}"
      # Keep the matching profile
      if [ "${profile_suffix}" = "${HERMES_PROFILE}" ]; then
        "${S6_SVC}" -k "${svc_dir}" 2>/dev/null || true
        echo "   🔄 重启 ${HERMES_PROFILE} gateway（清理多余连接）"
        continue
      fi
      # gateway-default = profile "default"
      if [ "${profile_suffix}" = "default" ] && [ "${HERMES_PROFILE}" = "default" ]; then
        "${S6_SVC}" -k "${svc_dir}" 2>/dev/null || true
        echo "   🔄 重启 default gateway（清理多余连接）"
        continue
      fi
      # Kill and disable every other gateway
      "${S6_SVC}" -dk "${svc_dir}" 2>/dev/null || true
      echo "   🚫 停止多余 gateway: ${svc_name}"
    done
  ) &
fi

# Hand off to s6-overlay init (v0.18+). The init system runs cont-init.d
# scripts, then executes its first argument as the "main program".
# main-wrapper.sh routes "$@" (Docker CMD, e.g. "gateway run") to "hermes gateway run".
exec /init /opt/hermes/docker/main-wrapper.sh "$@"
