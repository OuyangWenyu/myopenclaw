#!/usr/bin/env bash
# =============================================================
# restore-all.sh — 从云盘恢复 openclaw + hermes 数据
# 用法: ./scripts/restore-all.sh [TIMESTAMP]
#   TIMESTAMP: 可选，格式 YYYYMMDD_HHMMSS，省略则使用最新备份
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_LINK="${REPO_ROOT}/.data"
TIMESTAMP="${1:-}"

if [[ ! -L "${DATA_LINK}" ]]; then
  echo "❌ .data 软链接不存在，请先运行 ./scripts/setup-cloud.sh"
  exit 1
fi

echo "▶ 恢复 openclaw..."
bash "${REPO_ROOT}/openclaw/scripts/restore.sh" "${TIMESTAMP}" || echo "⚠️  openclaw 恢复失败，继续..."

echo ""
echo "▶ 恢复 hermes..."
bash "${REPO_ROOT}/hermes/scripts/restore.sh" "${TIMESTAMP}" || echo "⚠️  hermes 恢复失败，继续..."

echo ""
echo "✅ 恢复完成"
