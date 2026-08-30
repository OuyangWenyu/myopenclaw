#!/usr/bin/env bash
# Generate ortie + himalaya config for an Outlook mailbox that uses OAuth 2.0.
#
# Ortie owns the OAuth grant and token refresh. Himalaya v1.2.0 consumes the
# access token through `backend.auth.access-token.cmd` (XOAUTH2). This script
# is idempotent for our own Outlook sections and refuses to clobber a foreign
# account that already uses the same name.
#
# Required environment:
#   EMAIL_OUTLOOK_ADDRESS  Mailbox address used as IMAP/SMTP login.
#
# Optional environment:
#   EMAIL_OUTLOOK_ACCOUNT_NAME  Himalaya/ortie account id (default: outlook).
#   EMAIL_OUTLOOK_DISPLAY_NAME  From-header display name.
#   EMAIL_OUTLOOK_GRANT         device | authorization-code (default: device).
#   EMAIL_OUTLOOK_CLIENT_ID     Azure public client id (Thunderbird default).
#   EMAIL_OUTLOOK_IMAP_HOST     Default outlook.office365.com.
#   EMAIL_OUTLOOK_IMAP_PORT     Default 993.
#   EMAIL_OUTLOOK_SMTP_HOST     Default smtp.office365.com.
#   EMAIL_OUTLOOK_SMTP_PORT     Default 587.
#   HERMES_DATA                 Config root (default: /opt/data).
#   ORTIE_STORE_TOKEN           Token writer path (default: /opt/hermes/...).
set -euo pipefail
umask 077

HERMES_DATA="${HERMES_DATA:-/opt/data}"
ADDRESS="${EMAIL_OUTLOOK_ADDRESS:-}"

if [[ -z "${ADDRESS}" ]]; then
  exit 0
fi

ACCT="${EMAIL_OUTLOOK_ACCOUNT_NAME:-outlook}"
DISPLAY_NAME="${EMAIL_OUTLOOK_DISPLAY_NAME:-${ADDRESS}}"
GRANT="${EMAIL_OUTLOOK_GRANT:-device}"
# Thunderbird public client registered for Outlook IMAP/SMTP (not Graph).
# Source: mozilla-central mailnews/base/src/OAuth2Providers.sys.mjs
CLIENT_ID="${EMAIL_OUTLOOK_CLIENT_ID:-9e5f94bc-e8a4-4e73-b8be-63364c29d753}"
IMAP_HOST="${EMAIL_OUTLOOK_IMAP_HOST:-outlook.office365.com}"
IMAP_PORT="${EMAIL_OUTLOOK_IMAP_PORT:-993}"
SMTP_HOST="${EMAIL_OUTLOOK_SMTP_HOST:-smtp.office365.com}"
SMTP_PORT="${EMAIL_OUTLOOK_SMTP_PORT:-587}"
ORTIE_STORE_TOKEN="${ORTIE_STORE_TOKEN:-/opt/hermes/ortie-store-token.sh}"

toml_string() {
  # Quote a value as a TOML basic string. Rejects newlines.
  local s="$1"
  if [[ "${s}" == *$'\n'* || "${s}" == *$'\r'* ]]; then
    echo "   ❌ value must not contain newlines: ${s@Q}" >&2
    return 1
  fi
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '"%s"' "${s}"
}

validate_port() {
  local name="$1" port="$2"
  if [[ ! "${port}" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
    echo "   ❌ ${name} must be an integer 1-65535: ${port}" >&2
    return 1
  fi
}

validate_host() {
  local name="$1" host="$2"
  if [[ ! "${host}" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
    echo "   ❌ ${name} is not a valid hostname: ${host}" >&2
    return 1
  fi
}

if [[ ! "${ACCT}" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "   ❌ EMAIL_OUTLOOK_ACCOUNT_NAME must be alphanumeric, '_' or '-': ${ACCT}" >&2
  exit 1
fi

if [[ "${GRANT}" != "device" && "${GRANT}" != "authorization-code" ]]; then
  echo "   ❌ EMAIL_OUTLOOK_GRANT must be 'device' or 'authorization-code': ${GRANT}" >&2
  exit 1
fi

if [[ "${ADDRESS}" != *@* || "${ADDRESS}" == *@*@* || "${ADDRESS}" == *[[:space:]]* ]]; then
  echo "   ❌ EMAIL_OUTLOOK_ADDRESS is not a valid mailbox: ${ADDRESS}" >&2
  exit 1
fi

if [[ ! "${CLIENT_ID}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "   ❌ EMAIL_OUTLOOK_CLIENT_ID contains unsupported characters" >&2
  exit 1
fi

validate_host EMAIL_OUTLOOK_IMAP_HOST "${IMAP_HOST}"
validate_host EMAIL_OUTLOOK_SMTP_HOST "${SMTP_HOST}"
validate_port EMAIL_OUTLOOK_IMAP_PORT "${IMAP_PORT}"
validate_port EMAIL_OUTLOOK_SMTP_PORT "${SMTP_PORT}"
toml_string "${ADDRESS}" >/dev/null
toml_string "${DISPLAY_NAME}" >/dev/null

ORTIE_DIR="${HERMES_DATA}/.config/ortie"
TOKEN_DIR="${ORTIE_DIR}/tokens"
TOKEN_FILE="${TOKEN_DIR}/${ACCT}.json"
ORTIE_CONFIG="${ORTIE_DIR}/config.toml"
HIMALAYA_CONFIG="${HERMES_DATA}/.config/himalaya/config.toml"
LOCK_DIR="${HERMES_DATA}/.config/outlook-configure.lock"

mkdir -p "${TOKEN_DIR}" "${HERMES_DATA}/.config/himalaya"
chmod 700 "${ORTIE_DIR}" "${TOKEN_DIR}"

# Directory lock (atomic mkdir) so concurrent Hermes profiles sharing
# /opt/data cannot append the same [accounts.*] section twice.
# A lock left by a SIGKILLed run (EXIT trap never fires; the dir sits on the
# persistent volume) would block every future start — reclaim it when its
# mtime is older than 60s, which no live run could plausibly still hold.
lock_age_seconds() {
  local path="$1" mtime
  if [[ "$(uname)" == "Darwin" ]]; then
    mtime="$(stat -f %m "${path}")"
  else
    mtime="$(stat -c %Y "${path}")"
  fi
  echo $(( $(date +%s) - mtime ))
}

cleanup_lock() {
  rmdir "${LOCK_DIR}" 2>/dev/null || true
}

acquire_lock() {
  local _i
  for _i in $(seq 1 100); do
    if mkdir "${LOCK_DIR}" 2>/dev/null; then
      return 0
    fi
    sleep 0.05
  done
  return 1
}

if ! acquire_lock; then
  age="$(lock_age_seconds "${LOCK_DIR}")"
  if (( age > 60 )); then
    echo "   ⚠️  清理过期的 Outlook 配置锁（${age}s，疑似上次启动被中断）"
    rm -rf "${LOCK_DIR}"
    if ! acquire_lock; then
      echo "   ❌ timed out waiting for Outlook config lock" >&2
      exit 1
    fi
  else
    echo "   ❌ timed out waiting for Outlook config lock" >&2
    exit 1
  fi
fi
trap cleanup_lock EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

toml_account_exists() {
  local file="$1"
  [[ -f "${file}" ]] && grep -q "^\[accounts\.${ACCT}\]" "${file}"
}

toml_section_body() {
  local file="$1"
  [[ -f "${file}" ]] || return 0
  awk -v acct="${ACCT}" '
    $0 == "[accounts." acct "]" {p=1; next}
    p && /^\[/ {exit}
    p {print}
  ' "${file}"
}

himalaya_section_is_ours() {
  local body email_line
  body="$(toml_section_body "${HIMALAYA_CONFIG}")"
  email_line="email = $(toml_string "${ADDRESS}")"
  grep -q 'backend.auth.type = "oauth2"' <<<"${body}" \
    && grep -q 'backend.auth.access-token.cmd' <<<"${body}" \
    && grep -q 'ortie' <<<"${body}" \
    && grep -Fxq "${email_line}" <<<"${body}"
}

ortie_section_is_ours() {
  local body
  body="$(toml_section_body "${ORTIE_CONFIG}")"
  grep -Fq "${TOKEN_FILE}" <<<"${body}" \
    && grep -q "client-id = $(toml_string "${CLIENT_ID}")" <<<"${body}"
}

# Idempotency keeps the written section even when EMAIL_OUTLOOK_* drifts from
# it — surface that loudly instead of silently ignoring the new values.
declare -a DRIFT=()

check_drift() {
  local label="$1" expected="$2" body="$3"
  if ! grep -Fxq "${expected}" <<<"${body}"; then
    DRIFT+=("${label}")
  fi
}

warn_drift() {
  local side="$1" config_file="$2"
  if ((${#DRIFT[@]} > 0)); then
    echo "   ⚠️  检测到 EMAIL_OUTLOOK_* 与已写入配置不一致（${side}: ${DRIFT[*]}，旧值保留）。如需应用新值，删除 ${config_file} 中 [accounts.${ACCT}] 段后重启"
    DRIFT=()
  fi
}

SKIP_ORTIE=0
SKIP_HIMALAYA=0

if toml_account_exists "${ORTIE_CONFIG}"; then
  if ortie_section_is_ours; then
    SKIP_ORTIE=1
    echo "   📧 ortie account '${ACCT}' already present — skip"
  else
    echo "   ❌ account '${ACCT}' already exists in ortie and is not this Outlook mailbox" >&2
    exit 1
  fi
fi

if toml_account_exists "${HIMALAYA_CONFIG}"; then
  if himalaya_section_is_ours; then
    SKIP_HIMALAYA=1
    echo "   📧 himalaya account '${ACCT}' already present — skip"
  else
    echo "   ❌ account '${ACCT}' already exists in himalaya (collision with EMAIL_OUTLOOK_ACCOUNT_NAME)" >&2
    exit 1
  fi
fi

if [[ "${SKIP_ORTIE}" -eq 1 ]]; then
  ortie_body="$(toml_section_body "${ORTIE_CONFIG}")"
  check_drift "client-id" "client-id = $(toml_string "${CLIENT_ID}")" "${ortie_body}"
  check_drift "grant" "grant = \"${GRANT}\"" "${ortie_body}"
  warn_drift "ortie" "${ORTIE_CONFIG}"
fi

if [[ "${SKIP_HIMALAYA}" -eq 1 ]]; then
  himalaya_body="$(toml_section_body "${HIMALAYA_CONFIG}")"
  check_drift "display-name" "display-name = $(toml_string "${DISPLAY_NAME}")" "${himalaya_body}"
  check_drift "imap-host" "backend.host = $(toml_string "${IMAP_HOST}")" "${himalaya_body}"
  check_drift "imap-port" "backend.port = ${IMAP_PORT}" "${himalaya_body}"
  check_drift "smtp-host" "message.send.backend.host = $(toml_string "${SMTP_HOST}")" "${himalaya_body}"
  check_drift "smtp-port" "message.send.backend.port = ${SMTP_PORT}" "${himalaya_body}"
  warn_drift "himalaya" "${HIMALAYA_CONFIG}"
fi

if [[ "${SKIP_ORTIE}" -eq 0 ]]; then
  if [[ "${GRANT}" == "device" ]]; then
    GRANT_BLOCK="$(cat << EOF
grant = "device"
endpoints.authorization = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
endpoints.token = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
endpoints.device-authorization = "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"
EOF
)"
  else
    GRANT_BLOCK="$(cat << EOF
grant = "authorization-code"
pkce = true
endpoints.authorization = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
endpoints.token = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
endpoints.redirection = "https://localhost"
EOF
)"
  fi

  if [[ ! -f "${ORTIE_CONFIG}" ]]; then
    cat > "${ORTIE_CONFIG}" << EOF
# Generated by configure-outlook.sh — Outlook IMAP/SMTP OAuth via ortie.
EOF
  fi

  cat >> "${ORTIE_CONFIG}" << EOF

[accounts.${ACCT}]
client-id = $(toml_string "${CLIENT_ID}")
${GRANT_BLOCK}
scopes = [
  "offline_access",
  "https://outlook.office.com/IMAP.AccessAsUser.All",
  "https://outlook.office.com/SMTP.Send",
]
auto-refresh = true
storage.read.command = ["cat", $(toml_string "${TOKEN_FILE}")]
storage.write.command = [$(toml_string "${ORTIE_STORE_TOKEN}"), $(toml_string "${TOKEN_FILE}")]
EOF
  chmod 600 "${ORTIE_CONFIG}"
  echo "   📧 ortie 已配置 — ${ADDRESS} (account: ${ACCT}, grant: ${GRANT})"
fi

TOKEN_CMD="ortie -c ${ORTIE_CONFIG} token show -a ${ACCT}"

if [[ "${SKIP_HIMALAYA}" -eq 0 ]]; then
  DEFAULT_FLAG="false"
  if [[ ! -f "${HIMALAYA_CONFIG}" ]] || ! grep -q "^\[accounts\." "${HIMALAYA_CONFIG}"; then
    DEFAULT_FLAG="true"
  fi
  cat >> "${HIMALAYA_CONFIG}" << EOF

[accounts.${ACCT}]
email = $(toml_string "${ADDRESS}")
display-name = $(toml_string "${DISPLAY_NAME}")
default = ${DEFAULT_FLAG}

backend.type = "imap"
backend.host = $(toml_string "${IMAP_HOST}")
backend.port = ${IMAP_PORT}
backend.encryption.type = "tls"
backend.login = $(toml_string "${ADDRESS}")
backend.auth.type = "oauth2"
backend.auth.method = "xoauth2"
# Required by himalaya v1.2.0 to deserialize oauth2 auth config even when
# only access-token.cmd is used — missing any of these fails the WHOLE
# config.toml parse (breaking password accounts too). pkce=false: ortie
# owns the actual grant, himalaya only presents the token.
backend.auth.auth-url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
backend.auth.token-url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
backend.auth.pkce = false
backend.auth.scopes = [
  "https://outlook.office.com/IMAP.AccessAsUser.All",
  "https://outlook.office.com/SMTP.Send",
]
backend.auth.client-id = $(toml_string "${CLIENT_ID}")
backend.auth.access-token.cmd = $(toml_string "${TOKEN_CMD}")

message.send.backend.type = "smtp"
message.send.backend.host = $(toml_string "${SMTP_HOST}")
message.send.backend.port = ${SMTP_PORT}
message.send.backend.encryption.type = "start-tls"
message.send.backend.login = $(toml_string "${ADDRESS}")
message.send.backend.auth.type = "oauth2"
message.send.backend.auth.method = "xoauth2"
message.send.backend.auth.auth-url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
message.send.backend.auth.token-url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
message.send.backend.auth.pkce = false
message.send.backend.auth.scopes = [
  "https://outlook.office.com/IMAP.AccessAsUser.All",
  "https://outlook.office.com/SMTP.Send",
]
message.send.backend.auth.client-id = $(toml_string "${CLIENT_ID}")
message.send.backend.auth.access-token.cmd = $(toml_string "${TOKEN_CMD}")
EOF
  chmod 600 "${HIMALAYA_CONFIG}"
  echo "   📧 himalaya 已配置 Outlook — ${ADDRESS} (account: ${ACCT})"
fi

if [[ -s "${TOKEN_FILE}" ]]; then
  echo "   🔑 Outlook OAuth token present — ${TOKEN_FILE}"
else
  echo "   ⚠️  Outlook OAuth 尚未授权。容器内一次性执行（以 hermes 用户）："
  echo "      docker compose exec -u hermes -it hermes ortie auth get -a ${ACCT}"
  if [[ "${GRANT}" == "device" ]]; then
    echo "      （device grant：打开 https://microsoft.com/devicelogin 并输入显示的代码即可，无需 auth resume）"
  else
    echo "      （authorization-code：用浏览器打开打印的 URL，再 ortie auth resume <redirect-uri>）"
  fi
fi
