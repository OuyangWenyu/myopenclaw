#!/usr/bin/env bash
# =============================================================
# start.sh — 启动所有服务
# 用法: ./scripts/start.sh [--build]
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 检查 .data 软链接
if [[ ! -L "${REPO_ROOT}/.data" ]]; then
  echo "⚠️  .data 软链接不存在，先运行 ./scripts/setup-cloud.sh"
  exit 1
fi

# 检查 .env（从 .env.example 复制）
if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "⚠️  .env 不存在，从模板创建..."
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  echo "   请编辑 .env 填写配置后重新运行"
  exit 1
fi

cd "${REPO_ROOT}"

BUILD_FLAG=""
if [[ "${1:-}" == "--build" ]]; then
  BUILD_FLAG="--build"
fi

echo "🚀 启动服务..."
docker compose up -d ${BUILD_FLAG}
echo "✅ 服务已启动"
docker compose ps
