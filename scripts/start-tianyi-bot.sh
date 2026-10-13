#!/usr/bin/env bash
# =============================================================
# start-tianyi-bot.sh — 启动天一飞书研发助手
# 用法: ./scripts/start-tianyi-bot.sh [--build]
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env.tianyi-bot"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.tianyi-bot.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "❌ 缺少 ${ENV_FILE}"
  echo "   cp .env.tianyi-bot.example .env.tianyi-bot"
  echo "   然后填写飞书、模型和 token 配置。"
  exit 1
fi

# ── Validate required env vars ──────────────────────────────────────────
required_vars=(
  TIANYI_BOT_FEISHU_APP_ID
  TIANYI_BOT_FEISHU_APP_SECRET
  TIANYI_BOT_MODEL_API_KEY
)

for name in "${required_vars[@]}"; do
  value="$(grep -E "^${name}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
  if [[ -z "${value}" || "${value}" == "replace_me" || "${value}" == *"xxxx"* ]]; then
    echo "❌ ${name} 未在 .env.tianyi-bot 中正确设置"
    exit 1
  fi
done

# ── Validate optional server-monitoring MCP wiring ──────────────────────
# 这层最早发现配置错误：compose up 对 crash-loop 的容器照样返回 0，
# 「URL 非空而 token 为空」若不拦在这里，要等容器起来后看日志才知道。
monitoring_url="$(grep -E '^TIANYI_BOT_SERVER_MONITORING_MCP_URL=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
monitoring_token="$(grep -E '^TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
if [[ -n "${monitoring_url}" ]]; then
  if [[ -z "${monitoring_token}" || "${monitoring_token}" == "replace_me" || "${monitoring_token}" == *"xxxx"* ]]; then
    echo "❌ TIANYI_BOT_SERVER_MONITORING_MCP_URL 已设置，但 TIANYI_BOT_MCP_SERVER_MONITORING_TOKEN 未正确设置"
    echo "   两者要么都配好，要么清空 URL 以完全关闭服务器监控能力（详见 docs/server-monitoring-mcp.md）。"
    exit 1
  fi
  echo "✅ server-monitoring MCP 已配置（URL 非空 + token 已设置）"
fi

# ── Validate main stack is running ──────────────────────────────────────
if ! docker inspect repo-scanner-mcp --format '{{.State.Status}}' 2>/dev/null | grep -q running; then
  echo "❌ repo-scanner-mcp 未运行"
  echo "   请先启动主栈: ./scripts/start.sh"
  exit 1
fi
echo "✅ repo-scanner-mcp 运行中"

# ── Validate data dir does not overlap with main openclaw dir ────────────
data_dir="$(grep -E '^TIANYI_BOT_DATA_DIR=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
data_dir="${data_dir:-${HOME}/.openclaw-tianyi}"
data_dir="${data_dir/#\~/${HOME}}"
if [[ "${data_dir}" != /* ]]; then
  data_dir="${REPO_ROOT}/${data_dir}"
fi
mkdir -p "${data_dir}"
data_dir="$(cd "${data_dir}" && pwd -P)"

main_openclaw_dir="${HOME}/.openclaw"
if [[ -d "${main_openclaw_dir}" ]]; then
  main_openclaw_dir="$(cd "${main_openclaw_dir}" && pwd -P)"
fi
if [[ "${data_dir}" == "${main_openclaw_dir}" ]] \
  || [[ "${data_dir}" == "${main_openclaw_dir}/"* ]]; then
  echo "❌ TIANYI_BOT_DATA_DIR 不能位于现有 ~/.openclaw 内: ${data_dir}"
  exit 1
fi

# ── Parse arguments ─────────────────────────────────────────────────────
build_requested=false
if [[ "${1:-}" == "--build" ]]; then
  build_requested=true
elif [[ -n "${1:-}" ]]; then
  echo "❌ 未知参数: $1"
  echo "用法: ./scripts/start-tianyi-bot.sh [--build]"
  exit 1
fi

# ── Start ───────────────────────────────────────────────────────────────
echo "🔍 校验天一 Compose 配置..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" config --quiet

echo "🚀 启动天一飞书研发助手..."
if [[ "${build_requested}" == "true" ]]; then
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
    up -d --force-recreate --build
else
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
    up -d --force-recreate
fi

echo "✅ 启动命令已提交"
echo "   状态: docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml ps"
echo "   日志: docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml logs -f openclaw-tianyi"
echo "   MCP:  docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml exec openclaw-tianyi node /app/openclaw.mjs mcp probe repo-scanner --json"
echo "   监控: docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml exec openclaw-tianyi node /app/openclaw.mjs mcp probe server-monitoring --json"
echo "   gh:   docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml exec openclaw-tianyi gh --version"
echo "   gc:   docker compose --env-file .env.tianyi-bot -f docker-compose.tianyi-bot.yml exec openclaw-tianyi gc --version"
