#!/usr/bin/env bash
# =============================================================
# scripts/healthchecks-ping.sh — Ping Healthchecks.io 死信开关
# 由 host launchd 每 60 秒调用
# 附带系统信息（hostname, uptime, disk, load）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

# ── 读取 HEALTHCHECKS_PING_URL ──────────────────────────────
# 优先从 .env 读取，其次从环境变量读取
PING_URL=""
if [[ -f "${ENV_FILE}" ]]; then
    PING_URL=$(grep '^HEALTHCHECKS_PING_URL=' "${ENV_FILE}" 2>/dev/null | cut -d'=' -f2- | xargs)
fi
PING_URL="${PING_URL:-${HEALTHCHECKS_PING_URL:-}}"

if [[ -z "${PING_URL}" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] HEALTHCHECKS_PING_URL not set — skipping ping" >&2
    exit 1
fi

# ── 收集系统信息作为 ping body ──────────────────────────────
SYSTEM_INFO="$(
    echo "host=$(hostname 2>/dev/null || echo 'unknown')"
    echo "uptime=$(uptime 2>/dev/null | sed 's/.*up //' | sed 's/,.*//' || echo 'unknown')"
    echo "disk=$(df -h / 2>/dev/null | awk 'NR==2 {print $5}' | tr -d ' %' || echo 'unknown')"
    echo "load=$(sysctl -n vm.loadavg 2>/dev/null | awk '{print $2","$3","$4}' || echo 'unknown')"
)"

# ── Ping Healthchecks.io ────────────────────────────────────
# -m 10: 10s timeout (launchd fires every 60s, so this gives room)
# --data-raw sends system info as the request body
CURL_OUT=$(curl -fsS -m 10 \
    -o /dev/null \
    -w '%{http_code}' \
    --data-raw "${SYSTEM_INFO}" \
    "${PING_URL}" 2>/tmp/healthchecks-ping-curl.err)
CURL_EXIT=$?

if [[ ${CURL_EXIT} -ne 0 ]]; then
    CURL_ERR=$(cat /tmp/healthchecks-ping-curl.err 2>/dev/null || true)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  curl failed (exit ${CURL_EXIT}): ${CURL_ERR}" >&2
    exit 1
fi

if [[ "${CURL_OUT}" == "200" ]] || [[ "${CURL_OUT}" == "201" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ ping OK (HTTP ${CURL_OUT})"
    exit 0
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  ping failed (HTTP ${CURL_OUT})" >&2
    exit 1
fi
