#!/usr/bin/env bash
# =============================================================
# restore.sh — 从云盘快照恢复 hermes / openclaw / ~/.myagentdata 数据
# 用法: ./scripts/restore.sh [hermes|openclaw|data|all] [TIMESTAMP|latest]
# 示例:
#   ./scripts/restore.sh all latest          # 恢复全部最新快照
#   ./scripts/restore.sh hermes latest       # 仅恢复 hermes 最新快照
#   ./scripts/restore.sh data 2026-04-23_090000  # 恢复指定快照
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONF_FILE="${REPO_ROOT}/.cloud.conf"

SERVICE="${1:-all}"
SNAPSHOT="${2:-latest}"

# ── 参数校验 ─────────────────────────────────────────────────
if [[ ! "${SERVICE}" =~ ^(hermes|openclaw|data|all)$ ]]; then
  echo "用法: $0 [hermes|openclaw|data|all] [TIMESTAMP|latest]"
  exit 1
fi

# ── 读取云盘配置 ─────────────────────────────────────────────
if [[ ! -f "${CONF_FILE}" ]]; then
  echo "❌ 未找到 .cloud.conf，请先复制模板并填写本机路径："
  echo "   cp .cloud.conf.example .cloud.conf"
  exit 1
fi

# shellcheck source=/dev/null
source "${CONF_FILE}"

BACKUP_SUBDIR="${BACKUP_SUBDIR:-myopenclaw-backups}"

case "${CLOUD_PROVIDER:-google_drive}" in
  google_drive) CLOUD_ROOT="${GOOGLE_DRIVE_PATH}" ;;
  onedrive)     CLOUD_ROOT="${ONEDRIVE_PATH}" ;;
  custom)       CLOUD_ROOT="${CUSTOM_CLOUD_PATH}" ;;
  *)
    echo "❌ 未知的 CLOUD_PROVIDER: ${CLOUD_PROVIDER}"
    exit 1
    ;;
esac

CLOUD_ROOT="${CLOUD_ROOT/#\~/$HOME}"
BACKUP_ROOT="${CLOUD_ROOT}/${BACKUP_SUBDIR}"

# ── 恢复单个服务 ─────────────────────────────────────────────
restore_service() {
  local svc="$1"
  local snapshot="$2"
  local dest

  case "${svc}" in
    hermes)   dest="${HOME}/.hermes" ;;
    openclaw) dest="${HOME}/.openclaw" ;;
    data)     dest="${HOME}/.myagentdata" ;;
  esac

  local src="${BACKUP_ROOT}/${svc}/${snapshot}"

  if [[ ! -d "${src}" ]]; then
    echo "❌ 快照不存在: ${src}"
    echo "   可用快照列表:"
    ls "${BACKUP_ROOT}/${svc}/" 2>/dev/null || echo "   （无）"
    return 1
  fi

  echo ""
  echo "══════════════════════════════════════════════════════"
  echo "  服务: ${svc}  快照: ${snapshot}"
  echo "  源:  ${src}"
  echo "  目标: ${dest}"
  echo "══════════════════════════════════════════════════════"

  # 预览将要恢复的文件
  echo ""
  echo "📋 将要恢复的文件（dry-run）:"
  rsync -a --dry-run --itemize-changes "${src}/" "${dest}/" | head -50
  echo ""
  read -r -p "确认恢复？现有文件会加 .bak 后缀保留 [y/N] " confirm
  if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
    echo "   已取消"
    return 0
  fi

  mkdir -p "${dest}"
  rsync -a --backup --suffix=".bak" "${src}/" "${dest}/"
  echo "✅ ${svc} 恢复完成 → ${dest}"
  echo "   （原文件已以 .bak 后缀保留）"
}

# ── 执行恢复 ─────────────────────────────────────────────────
if [[ "${SERVICE}" == "all" || "${SERVICE}" == "hermes" ]]; then
  restore_service hermes "${SNAPSHOT}"
fi

if [[ "${SERVICE}" == "all" || "${SERVICE}" == "openclaw" ]]; then
  restore_service openclaw "${SNAPSHOT}"
fi

if [[ "${SERVICE}" == "all" || "${SERVICE}" == "data" ]]; then
  restore_service data "${SNAPSHOT}"
fi

echo ""
echo "✅ 恢复操作完成"
