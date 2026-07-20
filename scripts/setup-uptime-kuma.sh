#!/usr/bin/env bash
# =============================================================
# scripts/setup-uptime-kuma.sh
# 幂等注册所有监控项到 Uptime Kuma（直接操作 SQLite）。
#
# 用法:
#   ./scripts/setup-uptime-kuma.sh             # 交互式（首次引导创建账号）
#   ./scripts/setup-uptime-kuma.sh --quiet     # 静默模式（start.sh 调用）
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
KUMA_DB="${KUMA_DB_PATH:-${HOME}/.uptime-kuma/kuma.db}"
QUIET="${1:-}"

log() {
    [[ "${QUIET}" == "--quiet" ]] && return
    echo "$@"
}

sql_escape() {
    # Escape single quotes for safe embedding in SQL string literals
    sed "s/'/''/g"
}

# ── 等待 Uptime Kuma 就绪 ──────────────────────────────────────
log "⏳ 等待 Uptime Kuma 容器就绪..."

MAX_WAIT=60
WAITED=0
while [[ ${WAITED} -lt ${MAX_WAIT} ]]; do
    if docker compose ps uptime-kuma 2>/dev/null | grep -q 'healthy'; then
        log "   ✅ Uptime Kuma 已就绪"
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
done

if [[ ${WAITED} -ge ${MAX_WAIT} ]]; then
    echo "❌ Uptime Kuma 未在 ${MAX_WAIT}s 内就绪，请检查容器状态" >&2
    exit 1
fi

# ── 等待 SQLite 数据库出现 ───────────────────────────────────
if [[ ! -f "${KUMA_DB}" ]]; then
    for _ in $(seq 1 15); do
        if [[ -f "${KUMA_DB}" ]]; then
            break
        fi
        sleep 2
    done
    if [[ ! -f "${KUMA_DB}" ]]; then
        echo "❌ Uptime Kuma 数据库未出现: ${KUMA_DB}" >&2
        echo "   请先在浏览器中访问 http://localhost:3001 完成初始化" >&2
        exit 1
    fi
fi

# ── 检查是否有管理员账号 ──────────────────────────────────────
USER_COUNT=$(sqlite3 "${KUMA_DB}" "SELECT COUNT(*) FROM user;" 2>/dev/null || echo "0")
if [[ "${USER_COUNT}" -eq 0 ]]; then
    echo "" >&2
    echo "⚠️  Uptime Kuma 还没有管理员账号" >&2
    echo "   请在浏览器中访问 http://localhost:3001 创建管理员账号" >&2
    echo "   创建完成后重新运行: ./scripts/setup-uptime-kuma.sh" >&2
    exit 1
fi
USER_ID=$(sqlite3 "${KUMA_DB}" "SELECT id FROM user ORDER BY id LIMIT 1;")

log "   👤 管理员用户 ID: ${USER_ID}"

# ── 确保 docker_host 条目存在 ─────────────────────────────────
DOCKER_HOST_EXISTS=$(sqlite3 "${KUMA_DB}" "SELECT COUNT(*) FROM docker_host WHERE docker_type='socket' AND docker_daemon='/var/run/docker.sock';")
if [[ "${DOCKER_HOST_EXISTS}" -eq 0 ]]; then
    sqlite3 "${KUMA_DB}" "INSERT INTO docker_host (docker_type, docker_daemon, name) VALUES ('socket', '/var/run/docker.sock', 'Docker Socket');"
    log "   ✅ 已创建 docker_host 条目"
else
    log "   ✅ docker_host 条目已存在"
fi

DOCKER_HOST_ID=$(sqlite3 "${KUMA_DB}" "SELECT id FROM docker_host WHERE docker_type='socket' LIMIT 1;")

# ── 监控项定义 ────────────────────────────────────────────────
# 格式: "名称|类型|URL|容器名|状态码"
# 类型: http, docker
declare -a MONITORS=(
    # ── HTTP 监控 ──────────────────────────────────────────
    "Hermes Dashboard|http|http://hermes-dashboard:9119||[\"200-299\"]"
    "OpenClaw Gateway|http|http://openclaw-gateway:18789/healthz||[\"200-299\"]"
    "aisecretary|http|http://aisecretary:8000/health||[\"200-299\"]"
    "TDAI Memory|http|http://tdai-memory:8420/health||[\"200-299\"]"
    "Repo Scanner MCP|http|http://repo-scanner-mcp:8001/health||[\"200-299\"]"
    "FreshRSS|http|http://dailyinfo_freshrss:80||[\"200-399\"]"

    # ── Docker 容器监控 ────────────────────────────────────
    "Docker: hermes|docker||hermes|"
    "Docker: hermes-coder|docker||hermes-coder|"
    "Docker: hermes-finance|docker||hermes-finance|"
    "Docker: hermes-dashboard|docker||hermes-dashboard|"
    "Docker: claude-code|docker||claude-code|"
    "Docker: openclaw-gateway|docker||openclaw-gateway|"
    "Docker: uptime-kuma|docker||uptime-kuma|"
    "Docker: backup-cron|docker||backup-cron|"
    "Docker: aisecretary|docker||aisecretary|"
    "Docker: tdai-memory|docker||tdai-memory|"
    "Docker: repo-scanner-mcp|docker||repo-scanner-mcp|"
    "Docker: dailyinfo_freshrss|docker||dailyinfo_freshrss|"
)

# ── 幂等创建监控项 ────────────────────────────────────────────
ADDED=0
SKIPPED=0

for entry in "${MONITORS[@]}"; do
    IFS='|' read -r name mon_type url container status_codes <<< "${entry}"

    # Escape fields for safe embedding into SQL string literals
    esc_name=$(sql_escape <<< "${name}")
    esc_url=$(sql_escape <<< "${url}")
    esc_container=$(sql_escape <<< "${container}")

    EXISTS=$(sqlite3 "${KUMA_DB}" "SELECT COUNT(*) FROM monitor WHERE name='${esc_name}';")
    if [[ "${EXISTS}" -gt 0 ]]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    if [[ "${mon_type}" == "docker" ]]; then
        sqlite3 "${KUMA_DB}" "
            INSERT INTO monitor (name, type, docker_host, docker_container, active, user_id, interval)
            VALUES ('${esc_name}', 'docker', ${DOCKER_HOST_ID}, '${esc_container}', 1, ${USER_ID}, 60);
        "
    else
        STATUS_JSON="${status_codes:-[\"200-299\"]}"
        sqlite3 "${KUMA_DB}" "
            INSERT INTO monitor (name, type, url, active, user_id, interval, accepted_statuscodes_json)
            VALUES ('${esc_name}', 'http', '${esc_url}', 1, ${USER_ID}, 60, '${STATUS_JSON}');
        "
    fi
    ADDED=$((ADDED + 1))
    log "   ➕ 已注册: ${name}"
done

log ""
log "📊 注册结果: ${ADDED} 新增, ${SKIPPED} 已存在（跳过）"

# ── 通知渠道检查 ──────────────────────────────────────────────
NOTIF_COUNT=$(sqlite3 "${KUMA_DB}" "SELECT COUNT(*) FROM notification WHERE active=1;" 2>/dev/null || echo "0")
if [[ "${NOTIF_COUNT}" -eq 0 ]]; then
    log ""
    log "🔔 尚未配置告警通知渠道。建议添加:"
    log "   1. 打开 http://localhost:3001/settings#notifications"
    log "   2. 点击「设置通知」→ 选择 webhook / Feishu / Discord 等"
    log "   3. 填入 webhook URL，并设为默认通知方式"
fi

# ── 仅在新增监控项时重启容器 ──────────────────────────────
if [[ ${ADDED} -gt 0 ]]; then
    log ""
    log "🔄 重启 Uptime Kuma 以加载新配置..."
    docker compose restart uptime-kuma >/dev/null 2>&1
    log "✅ Uptime Kuma 配置完成"
else
    log ""
    log "✅ Uptime Kuma 配置无变更，无需重启"
fi
