#!/usr/bin/env bash
# =============================================================
# start.sh — 启动所有服务
# 用法: ./scripts/start.sh [--build]
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 检查 .env ────────────────────────────────────────────────
if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "⚠️  .env 不存在，从模板创建..."
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  echo "   请编辑 .env 填写配置后重新运行"
  exit 1
fi

# ── 从 .cloud.conf 解析 BACKUP_ROOT ─────────────────────────
CONF_FILE="${REPO_ROOT}/.cloud.conf"
if [[ ! -f "${CONF_FILE}" ]]; then
  echo "❌ 未找到 .cloud.conf，请先运行 ./scripts/setup-cloud.sh"
  exit 1
fi

# shellcheck source=/dev/null
source "${CONF_FILE}"

case "${CLOUD_PROVIDER:-google_drive}" in
  google_drive) CLOUD_ROOT="${GOOGLE_DRIVE_PATH}" ;;
  onedrive)     CLOUD_ROOT="${ONEDRIVE_PATH}" ;;
  custom)       CLOUD_ROOT="${CUSTOM_CLOUD_PATH}" ;;
esac
CLOUD_ROOT="${CLOUD_ROOT/#\~/$HOME}"
export BACKUP_ROOT="${CLOUD_ROOT}/${BACKUP_SUBDIR:-myopenclaw-backups}"

if [[ ! -d "${CLOUD_ROOT}" ]]; then
  echo "❌ 云盘目录不存在: ${CLOUD_ROOT}，请确认云盘客户端已登录"
  exit 1
fi

mkdir -p "${BACKUP_ROOT}/hermes" "${BACKUP_ROOT}/openclaw"

# ── 确保工具配置目录存在（volume mount 需要）──────────────────
mkdir -p "${HOME}/.config/gh" "${HOME}/.config/opencode" "${HOME}/.lark-cli"
if [[ ! -f "${HOME}/.config/opencode/opencode.json" ]]; then
  cp "${REPO_ROOT}/hermes/config/opencode.json.example" "${HOME}/.config/opencode/opencode.json"
  echo "   📝 已创建 opencode 配置: ~/.config/opencode/opencode.json"
fi

# ── 确保 Claude Code 配置目录存在（volume mount 需要）──────────────
mkdir -p "${HOME}/.claude"
if [[ ! -f "${HOME}/.claude/settings.json" ]]; then
  cp "${REPO_ROOT}/hermes/config/claude-settings.json.example" "${HOME}/.claude/settings.json"
  echo "   📝 已创建 Claude Code 配置: ~/.claude/settings.json"
fi

# ── 确保 OpenClaw 配置存在（首次启动从模板创建）──────────────────
mkdir -p "${HOME}/.openclaw"
if [[ ! -f "${HOME}/.openclaw/openclaw.json" ]]; then
  cp "${REPO_ROOT}/openclaw/config/openclaw.json.example" "${HOME}/.openclaw/openclaw.json"
  echo "   📝 已创建 OpenClaw 配置: ~/.openclaw/openclaw.json"
fi

# ── 确保 OpenClaw skills 目录存在并安装 paper-fetch ─────────────
OPENCLAW_SKILLS_DIR="${HOME}/.openclaw/skills"
mkdir -p "${OPENCLAW_SKILLS_DIR}"
if [[ ! -d "${OPENCLAW_SKILLS_DIR}/paper-fetch/.git" ]]; then
  echo "   📥 安装 paper-fetch skill（公开论文 PDF 下载器）..."
  git clone https://github.com/Agents365-ai/paper-fetch.git "${OPENCLAW_SKILLS_DIR}/paper-fetch"
  echo "   ✅ paper-fetch 已安装到 ~/.openclaw/skills/paper-fetch"
else
  echo "   ✅ paper-fetch skill 已存在，跳过安装"
fi

# ── 注入 OpenClaw GitHub token ──────────────────────────────────
# 从 .env 读取 OPENCLAW_GH_TOKEN，替换 openclaw.json 中的占位符
# MCP server 不继承容器环境变量，必须在 JSON 的 env 块中显式声明
OPENCLAW_CONFIG="${HOME}/.openclaw/openclaw.json"
if [[ -f "${OPENCLAW_CONFIG}" && -n "${OPENCLAW_GH_TOKEN:-}" ]]; then
  if grep -q '__OPENCLAW_GH_TOKEN__' "${OPENCLAW_CONFIG}"; then
    # 使用 python3 做 JSON 安全的字符串替换
    python3 -c "
import json, sys
with open('${OPENCLAW_CONFIG}') as f:
    d = json.load(f)
github_mcp = d.get('mcp', {}).get('servers', {}).get('github', {})
if 'env' in github_mcp and github_mcp['env'].get('GITHUB_PERSONAL_ACCESS_TOKEN') == '__OPENCLAW_GH_TOKEN__':
    github_mcp['env']['GITHUB_PERSONAL_ACCESS_TOKEN'] = sys.argv[1]
    with open('${OPENCLAW_CONFIG}', 'w') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print('done')
else:
    print('skip')
" "${OPENCLAW_GH_TOKEN}"
    echo "   🔑 已注入 OpenClaw GitHub token"
  fi
fi

# ── 修复 OpenClaw 第三方插件的 module 解析 ──────────────────────
# 第三方插件安装在 ~/.openclaw/extensions/ 下，但 openclaw 包本体
# 在容器的 /app/ 目录，不在标准 node_modules 路径，导致
# import "openclaw/plugin-sdk/core" 失败。创建 symlink 解决。
for _ext_dir in "${HOME}/.openclaw/extensions"/*/; do
  _ext_name="$(basename "${_ext_dir}")"
  _nm_dir="${_ext_dir}node_modules"
  if [[ -d "${_nm_dir}" && ! -e "${_nm_dir}/openclaw" && ! -L "${_nm_dir}/openclaw" ]]; then
    mkdir -p "${_nm_dir}"
    ln -s /app "${_nm_dir}/openclaw"
    echo "   🔗 已为插件 ${_ext_name} 创建 openclaw SDK symlink"
  fi
done

cd "${REPO_ROOT}"

BUILD_FLAG=""
if [[ "${1:-}" == "--build" ]]; then
  BUILD_FLAG="--build"
fi

echo "🚀 启动服务..."
echo "   备份目录: ${BACKUP_ROOT}"
docker compose up -d ${BUILD_FLAG}
echo "✅ 服务已启动"
docker compose ps
