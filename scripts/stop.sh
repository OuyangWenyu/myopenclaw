#!/usr/bin/env bash
# =============================================================
# stop.sh — 停止所有服务
# 用法: ./scripts/stop.sh [--remove-volumes]
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

if [[ "${1:-}" == "--remove-volumes" ]]; then
  echo "⚠️  停止并移除容器（volumes 保留在云盘，不受影响）"
  docker compose down
else
  echo "🛑 停止服务..."
  docker compose stop
fi

echo "✅ 服务已停止"
