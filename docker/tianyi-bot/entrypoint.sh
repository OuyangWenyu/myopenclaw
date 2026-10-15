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

  # Install feishu + deepseek plugins as root (OpenClaw requires root-owned plugin dirs)
  PLUGIN_MARKER="/home/node/.openclaw/.plugins-installed"
  mkdir -p /home/node/.openclaw
  if [ ! -f "${PLUGIN_MARKER}" ]; then
    echo "📦 Installing OpenClaw feishu plugin..."
    HOME=/home/node node /app/openclaw.mjs plugins install @openclaw/feishu
    echo "📦 Installing OpenClaw deepseek plugin..."
    HOME=/home/node node /app/openclaw.mjs plugins install @openclaw/deepseek-provider
    touch "${PLUGIN_MARKER}"
  fi

  # Fix ownership: state dir + config belong to node; npm/ stays root for plugins
  chown node:node /home/node /home/node/.openclaw 2>/dev/null || true
  [ -d /home/node/.config ] && chown -R node:node /home/node/.config 2>/dev/null || true
  [ -d /home/node/.gitcode ] && chown -R node:node /home/node/.gitcode 2>/dev/null || true

  # Pre-create OpenClaw's temp root (/home/node/.cache) as node-owned — race guard.
  # 2026-10-14 实测事故：某个 root 上下文的 openclaw 调用（HOME=/home/node）以 uid 0
  # 抢建 /home/node/.cache（root:700），随后真正的 gateway（node, uid 1000）连遍历都
  # EACCES → "SQLite read-only worker Unsafe fallback ... openclaw-1000" → crash-loop
  # （restart 复用同一容器可写层，永久无法自愈；全新容器无此状态则正常）。预建 +
  # chown -R 让这个竞态无论谁先跑都安全。
  mkdir -p /home/node/.cache
  chown -R node:node /home/node/.cache 2>/dev/null || true

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
  export OPENAI_API_KEY="${TIANYI_BOT_MODEL_API_KEY}"
fi

# ── Wait for repo-scanner-mcp ────────────────────────────────────────────
# 端点可配（2026-10-14 参数化）：默认 = 主栈同网逻辑地址（行为不变）；
# 赤兔迁移 / 异地消费时经 TIANYI_BOT_REPO_SCANNER_MCP_URL 覆盖。
# 归一后再推导 health：去首尾空白 / query / 尾斜杠 / /mcp 后缀——尾斜杠等常见
# 粘贴形态若不归一，探测会轰一个不存在的路径，给出误导性的 "not reachable"。
REPO_SCANNER_MCP_URL="${TIANYI_BOT_REPO_SCANNER_MCP_URL:-http://repo-scanner-mcp:8001/mcp}"
REPO_SCANNER_MCP_URL="${REPO_SCANNER_MCP_URL#"${REPO_SCANNER_MCP_URL%%[![:space:]]*}"}"
REPO_SCANNER_MCP_URL="${REPO_SCANNER_MCP_URL%"${REPO_SCANNER_MCP_URL##*[![:space:]]}"}"
REPO_SCANNER_MCP_URL="${REPO_SCANNER_MCP_URL%%\?*}"
REPO_SCANNER_MCP_URL="${REPO_SCANNER_MCP_URL%/}"
case "${REPO_SCANNER_MCP_URL}" in */mcp) REPO_SCANNER_MCP_URL="${REPO_SCANNER_MCP_URL%/mcp}" ;; esac
REPO_SCANNER_HEALTH_URL="${REPO_SCANNER_MCP_URL}/health"
echo "⏳ Waiting for repo-scanner-mcp (${REPO_SCANNER_HEALTH_URL})..."
for i in $(seq 1 30); do
  if curl -fsS "${REPO_SCANNER_HEALTH_URL}" 2>/dev/null; then
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

# ── Validate CA cert for optional server-monitoring MCP ──────────────────
if [ -n "${TIANYI_BOT_SERVER_MONITORING_MCP_URL:-}" ] \
  && [ ! -f /opt/tianyi-bot/ca/server-monitoring-ca.pem ]; then
  echo "❌ 已启用服务器监控 MCP（TIANYI_BOT_SERVER_MONITORING_MCP_URL 非空），但缺少 CA 证书："
  echo "   /opt/tianyi-bot/ca/server-monitoring-ca.pem 不存在。"
  echo "   该文件随仓库提供 —— 请确认 docker/tianyi-bot/ca/ 完整（git checkout 可恢复）。"
  exit 1
fi

# ── Render config from template ──────────────────────────────────────────
node /opt/tianyi-bot/render-config.mjs \
  /opt/tianyi-bot/openclaw.json.template \
  "${CONFIG_FILE}"

echo "🔍 Validating OpenClaw config..."
node /app/openclaw.mjs config validate

# ── Declare daily monitoring digest automation (declarative, idempotent) ──
# 拉起即有：URL 非空 = 声明每日服务器日报 automation（与 MCP 同开关）。幂等语义
# 2026-10-14 实测：同 declaration-key 重加 = created:false/updated:true（原地更新、
# ID 不变）——改本段后重启自动更新，不会重复。**OFF 态主动回收**：框架不会自动
# 回收 service 声明的 job（只有 system-owned 类型启动时收敛，独立复核 2026-10-14
# 镜像内证据），所以 URL 清空 / DIGEST_CRON=off 时这里按 declaration-key 找到并
# cron rm（cron rm 只吃 id，经 cron-job-id.mjs 提取）。全程后台等网关健康再动作，
# 绝不阻塞启动；缺 OWNER id 只警告（推给谁是必要输入）。
wait_gateway_healthy() {
  i=0
  until curl -fsS http://127.0.0.1:18792/healthz >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -gt 90 ] && return 1
    sleep 2
  done
  return 0
}

find_digest_job_id() {
  node /app/openclaw.mjs cron list --all --json 2>/dev/null \
    | node /opt/tianyi-bot/cron-job-id.mjs tianyi-monitoring-daily
}

digest_cron="${TIANYI_BOT_MONITORING_DIGEST_CRON:-30 8 * * *}"
if [ -n "${TIANYI_BOT_SERVER_MONITORING_MCP_URL:-}" ]; then
  if [ "${digest_cron}" = "off" ]; then
    (
      wait_gateway_healthy || { echo "⚠️ 每日监控摘要：等待网关健康超时，回收跳过（重启后重试）"; exit 0; }
      job_id="$(find_digest_job_id)"
      if [ -n "${job_id}" ]; then
        if node /app/openclaw.mjs cron rm "${job_id}" >/dev/null 2>&1; then
          echo "🗑️ 每日监控摘要：已显式关闭（DIGEST_CRON=off），既有 automation 已回收"
        else
          echo "⚠️ 每日监控摘要：回收失败（id=${job_id}），下次启动重试"
        fi
      else
        echo "⏰ 每日监控摘要：已显式关闭（DIGEST_CRON=off），无既有 automation"
      fi
    ) &
  elif [ -z "${TIANYI_BOT_OWNER_OPEN_ID:-}" ]; then
    echo "⚠️ 每日监控摘要未声明：缺 TIANYI_BOT_OWNER_OPEN_ID（推给谁？）—— 其它能力不受影响"
  else
    (
      wait_gateway_healthy || { echo "⚠️ 每日监控摘要：等待网关健康超时，本次跳过（重启后幂等重试）"; exit 0; }
      if out=$(node /app/openclaw.mjs cron add \
        --name server-monitoring-daily \
        --declaration-key tianyi-monitoring-daily \
        --cron "${digest_cron}" --tz Asia/Shanghai \
        --message "【服务器日报】请用 server-monitoring 工具汇总当前服务器整体情况（服务器状态与告警 / Prometheus 目标 / GFS 流水线 / 各项目运行时与最近构建），按证据纪律如实报告：UNKNOWN≠健康、缺失≠0；先给结论与需要关注的异常，再给细节，全文控制在 400 字内。" \
        --channel feishu --to "${TIANYI_BOT_OWNER_OPEN_ID}" --announce 2>&1); then
        echo "✅ 每日监控摘要 automation 就位（${digest_cron} Asia/Shanghai）"
      else
        echo "❌ 每日监控摘要 automation 创建失败：${out}"
      fi
    ) &
  fi
else
  (
    wait_gateway_healthy || { echo "⚠️ 每日监控摘要：等待网关健康超时，回收跳过（重启后重试）"; exit 0; }
    job_id="$(find_digest_job_id)"
    if [ -n "${job_id}" ]; then
      if node /app/openclaw.mjs cron rm "${job_id}" >/dev/null 2>&1; then
        echo "🗑️ 每日监控摘要：监控未启用（URL 为空），既有 automation 已回收"
      else
        echo "⚠️ 每日监控摘要：回收失败（id=${job_id}），下次启动重试"
      fi
    fi
  ) &
fi

echo "🚀 Starting tianyi feishu bot (天一)..."
exec node /app/openclaw.mjs gateway --allow-unconfigured
