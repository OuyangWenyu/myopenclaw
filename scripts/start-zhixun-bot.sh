#!/usr/bin/env bash
# =============================================================
# start-zhixun-bot.sh — 在服务器启动独立 zhixun 飞书机器人
# 用法: ./scripts/start-zhixun-bot.sh [--build]
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env.zhixun-bot"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.zhixun-bot.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "❌ 缺少 ${ENV_FILE}"
  echo "   cp .env.zhixun-bot.example .env.zhixun-bot"
  echo "   然后填写飞书、模型和服务器路径配置。"
  exit 1
fi

required_vars=(
  ZHIXUN_BOT_FEISHU_APP_ID
  ZHIXUN_BOT_FEISHU_APP_SECRET
  ZHIXUN_BOT_MODEL_API_KEY
)

for name in "${required_vars[@]}"; do
  value="$(grep -E "^${name}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
  if [[ -z "${value}" || "${value}" == "replace_me" || "${value}" == *"xxxx"* ]]; then
    echo "❌ ${name} 未在 .env.zhixun-bot 中正确设置"
    exit 1
  fi
done

zhixun_path="$(grep -E '^ZHIXUN_AGENT_PATH=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
zhixun_path="${zhixun_path:-../zhixun-agent}"
if [[ "${zhixun_path}" != /* ]]; then
  zhixun_path="${REPO_ROOT}/${zhixun_path}"
fi

if [[ ! -f "${zhixun_path}/mcp_servers/water/mcp_server_unified.py" ]]; then
  echo "❌ ZHIXUN_AGENT_PATH 无效: ${zhixun_path}"
  echo "   需要新版 zhixun-agent，并包含 mcp_servers/water/mcp_server_unified.py。"
  exit 1
fi

data_dir="$(grep -E '^ZHIXUN_BOT_DATA_DIR=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
data_dir="${data_dir:-${HOME}/.openclaw-zhixun}"
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
  echo "❌ ZHIXUN_BOT_DATA_DIR 不能位于现有 ~/.openclaw 内: ${data_dir}"
  exit 1
fi

build_requested=false
if [[ "${1:-}" == "--build" ]]; then
  build_requested=true
elif [[ -n "${1:-}" ]]; then
  echo "❌ 未知参数: $1"
  echo "用法: ./scripts/start-zhixun-bot.sh [--build]"
  exit 1
fi

echo "🔍 校验 zhixun 机器人 Compose 配置..."
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" config --quiet

echo "🚀 启动独立 zhixun 飞书机器人..."
if [[ "${build_requested}" == "true" ]]; then
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
    up -d --force-recreate --build
else
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
    up -d --force-recreate
fi

echo "✅ 启动命令已提交"
echo "   状态: docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml ps"
echo "   日志: docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml logs -f openclaw-zhixun"
echo "   MCP:  docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml exec openclaw-zhixun node /app/openclaw.mjs mcp probe water_unified --json"
