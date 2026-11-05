#!/usr/bin/env bash
# =============================================================
# start-zhixun-bot.sh — 在服务器启动独立 zhixun 飞书机器人
# 用法: ./scripts/start-zhixun-bot.sh [--build] [--with-local-mcp]
#   --with-local-mcp  额外启动本机 zhixun-water-mcp（调试用，默认不启动；
#                     bot 默认连 .env.zhixun-bot 里的远程 water MCP）
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
  ZHIXUN_BOT_WATER_MCP_URL
)

for name in "${required_vars[@]}"; do
  value="$(grep -E "^${name}=" "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
  if [[ -z "${value}" || "${value}" == "replace_me" || "${value}" == *"xxxx"* ]]; then
    echo "❌ ${name} 未在 .env.zhixun-bot 中正确设置"
    exit 1
  fi
done

water_token="$(grep -E '^ZHIXUN_BOT_WATER_MCP_TOKEN=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
water_token_file="$(grep -E '^ZHIXUN_BOT_WATER_MCP_TOKEN_FILE=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
if [[ -n "${water_token_file}" ]]; then
  if [[ -n "${water_token}" ]]; then
    echo "❌ Water MCP token 和 token 文件不能同时配置"
    exit 1
  fi
elif [[ -z "${water_token}" || "${water_token}" == "replace_me" || "${water_token}" == *"xxxx"* ]]; then
  echo "❌ 请设置 ZHIXUN_BOT_WATER_MCP_TOKEN 或挂载并设置 ZHIXUN_BOT_WATER_MCP_TOKEN_FILE"
  exit 1
fi

# ── GLM 兜底（可选）：一行可见性；空/replace_me = 未配置（不阻塞启动）──────
glm_key="$(grep -E '^ZHIXUN_BOT_GLM_API_KEY=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
if [[ -n "${glm_key}" && "${glm_key}" != "replace_me" && "${glm_key}" != *"xxxx"* ]]; then
  echo "✅ GLM 兜底已配置（zhipu/glm-5.3-flash，国内端点）"
else
  echo "⚠️  GLM 兜底未配置（可选）——主模型故障时无自动切换；填 ZHIXUN_BOT_GLM_API_KEY 启用"
fi

water_url="$(grep -E '^ZHIXUN_BOT_WATER_MCP_URL=' "${ENV_FILE}" | tail -1 | cut -d= -f2- || true)"
if [[ ! "${water_url}" =~ ^https?:// ]]; then
  echo "❌ ZHIXUN_BOT_WATER_MCP_URL 必须以 http:// 或 https:// 开头: ${water_url}"
  exit 1
fi

build_requested=false
with_local_mcp=false
for arg in "$@"; do
  case "${arg}" in
    --build)
      build_requested=true
      ;;
    --with-local-mcp)
      with_local_mcp=true
      ;;
    *)
      echo "❌ 未知参数: ${arg}"
      echo "用法: ./scripts/start-zhixun-bot.sh [--build] [--with-local-mcp]"
      exit 1
      ;;
  esac
done

if [[ "${with_local_mcp}" == "true" ]]; then
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

compose_args=(--env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")
if [[ "${with_local_mcp}" == "true" ]]; then
  compose_args+=(--profile local-water-mcp)
fi

echo "🔍 校验 zhixun 机器人 Compose 配置..."
docker compose "${compose_args[@]}" config --quiet

echo "🚀 启动独立 zhixun 飞书机器人..."
up_args=(up -d --force-recreate)
if [[ "${build_requested}" == "true" ]]; then
  up_args+=(--build)
fi
docker compose "${compose_args[@]}" "${up_args[@]}"

echo "✅ 启动命令已提交"
echo "   远程 MCP: ${water_url}"
echo "   状态: docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml ps"
echo "   日志: docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml logs -f openclaw-zhixun"
echo "   MCP:  docker compose --env-file .env.zhixun-bot -f docker-compose.zhixun-bot.yml exec openclaw-zhixun node /app/openclaw.mjs mcp probe water_unified --json"
