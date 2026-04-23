#!/usr/bin/env bash
# =============================================================
# backup-all.sh — 备份 openclaw + hermes 的运行时数据到云盘
# 用法: ./scripts/backup-all.sh
# 依赖: docker 正在运行（用于 db dump）；或直接复制文件
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_LINK="${REPO_ROOT}/.data"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

if [[ ! -L "${DATA_LINK}" ]]; then
  echo "❌ .data 软链接不存在，请先运行 ./scripts/setup-cloud.sh"
  exit 1
fi

CLOUD_DATA_DIR="$(readlink "${DATA_LINK}")"
echo "📦 备份目标: ${CLOUD_DATA_DIR}"
echo "⏰ 时间戳: ${TIMESTAMP}"

# ── 子脚本备份 ───────────────────────────────────────────────
echo ""
echo "▶ 备份 openclaw..."
bash "${REPO_ROOT}/openclaw/scripts/backup.sh" "${TIMESTAMP}" || echo "⚠️  openclaw 备份失败，继续..."

echo ""
echo "▶ 备份 hermes..."
bash "${REPO_ROOT}/hermes/scripts/backup.sh" "${TIMESTAMP}" || echo "⚠️  hermes 备份失败，继续..."

echo ""
echo "✅ 全部备份完成 → ${CLOUD_DATA_DIR}"
