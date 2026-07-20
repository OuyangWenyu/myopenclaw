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

# Read GDRIVE_PAPERS_LOCAL_PATH from .env (can't source directly — cron expressions break bash)
if [[ -z "${GDRIVE_PAPERS_LOCAL_PATH:-}" ]]; then
  GDRIVE_PAPERS_LOCAL_PATH=$(grep '^GDRIVE_PAPERS_LOCAL_PATH=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2-)
  GDRIVE_PAPERS_LOCAL_PATH="${GDRIVE_PAPERS_LOCAL_PATH/#\~/$HOME}"
  if [[ -n "${GDRIVE_PAPERS_LOCAL_PATH}" ]]; then
    export GDRIVE_PAPERS_LOCAL_PATH
    echo "   📁 GDRIVE_PAPERS_LOCAL_PATH 已从 .env 读取"
  fi
fi

# ── 检查依赖仓库（非阻塞，仅警告）─────────────────────────────
echo "🔍 检查依赖仓库..."
MISSING_REPOS=()
for repo in "${HOME}/code/aisecretary" "${HOME}/code/git-contribution-stats" "${HOME}/code/myloop" "${HOME}/code/dailyinfo"; do
  if [[ ! -d "${repo}" ]]; then
    MISSING_REPOS+=("$(basename "${repo}")")
  fi
done
if [[ ${#MISSING_REPOS[@]} -gt 0 ]]; then
  echo "   ⚠️  未找到依赖仓库: ${MISSING_REPOS[*]}"
  echo "   部分功能可能不可用。克隆依赖仓库: ./scripts/clone-deps.sh"
  echo "   详情: docs/portability.md"
else
  echo "   ✅ 所有依赖仓库已就绪"
fi
echo ""

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

mkdir -p "${BACKUP_ROOT}/hermes" "${BACKUP_ROOT}/openclaw" "${BACKUP_ROOT}/claude"

# ── 自动推导 GDRIVE_PAPERS_LOCAL_PATH（若 .env 未设置）────────────
if [[ -z "${GDRIVE_PAPERS_LOCAL_PATH:-}" ]]; then
  export GDRIVE_PAPERS_LOCAL_PATH="${CLOUD_ROOT}/Papers/Zotero_Papers"
  echo "   📁 GDRIVE_PAPERS_LOCAL_PATH 自动推导: ${GDRIVE_PAPERS_LOCAL_PATH}"
fi

# ── 确保工具配置目录存在（volume mount 需要）──────────────────
mkdir -p "${HOME}/.config/gh" "${HOME}/.config/opencode" "${HOME}/.lark-cli"
mkdir -p "${HOME}/.myagentdata/aisecretary"
mkdir -p "${HOME}/.myagentdata/tdai-memory"
if [[ ! -f "${HOME}/.config/opencode/opencode.json" ]]; then
  cp "${REPO_ROOT}/hermes/config/opencode.json.example" "${HOME}/.config/opencode/opencode.json"
  echo "   📝 已创建 opencode 配置: ~/.config/opencode/opencode.json"
fi

# ── 确保 Claude Code 配置目录存在（volume mount 需要）──────────────
mkdir -p "${HOME}/.claude"
if [[ ! -f "${HOME}/.claude/settings.json" ]]; then
  cp "${REPO_ROOT}/claude/config/settings.json.example" "${HOME}/.claude/settings.json"
  echo "   📝 已创建 Claude Code 配置: ~/.claude/settings.json"
fi
# 清理残留的符号链接（旧版 Hermes 容器泄漏到宿主机）
if [[ -L "${HOME}/.claude/claude-config" ]]; then
  rm -f "${HOME}/.claude/claude-config"
  echo "   🧹 已清理残留符号链接: ~/.claude/claude-config"
fi

# ── 确保 cc-connect 配置目录存在（volume mount 需要）────────────
mkdir -p "${HOME}/.cc-connect"
if [[ ! -f "${HOME}/.cc-connect/config.toml" ]]; then
  cp "${REPO_ROOT}/claude/config/cc-connect.toml.example" "${HOME}/.cc-connect/config.toml"
  echo "   📝 已创建 cc-connect 配置: ~/.cc-connect/config.toml"
fi

# ── 确保 OpenClaw 配置存在（首次启动从模板创建）──────────────────
mkdir -p "${HOME}/.openclaw"
if [[ ! -f "${HOME}/.openclaw/openclaw.json" ]]; then
  cp "${REPO_ROOT}/openclaw/config/openclaw.json.example" "${HOME}/.openclaw/openclaw.json"
  echo "   📝 已创建 OpenClaw 配置: ~/.openclaw/openclaw.json"
fi

# ── 确保 Hermes coder profile 使用 deepseek-v4-pro ──────────────
CODER_CONFIG="${HOME}/.hermes/profiles/coder/config.yaml"
mkdir -p "$(dirname "${CODER_CONFIG}")"
if [[ ! -f "${CODER_CONFIG}" ]]; then
  cat > "${CODER_CONFIG}" << 'YAML'
model:
  default: deepseek-v4-pro
  provider: deepseek
  base_url: https://api.deepseek.com
fallback_providers:
- zai
fallback_model:
  provider: zai
  model: glm-5.1
YAML
  echo "   📝 已创建 Hermes coder profile 配置（模型: deepseek-v4-pro）"
fi

# ── 确保 skills 目录存在并安装 paper-fetch ───────────────────────
install_paper_fetch() {
  local skills_dir="$1"
  local label="$2"
  mkdir -p "${skills_dir}"
  # Check idempotently: .git exists AND SKILL.md at root (not monorepo subdir)
  if [[ -d "${skills_dir}/paper-fetch/.git" && -f "${skills_dir}/paper-fetch/SKILL.md" ]]; then
    echo "   ✅ paper-fetch skill 已存在于 ${label}，跳过安装"
    return
  fi
  echo "   📥 安装 paper-fetch skill 到 ${label}（公开论文 PDF 下载器）..."
  if [[ -d "${skills_dir}/paper-fetch" ]]; then
    rm -rf "${skills_dir}/paper-fetch"
  fi
  git clone https://github.com/Agents365-ai/paper-fetch.git "${skills_dir}/paper-fetch"
  # Repo is a monorepo; move inner skill to root if needed
  if [[ -d "${skills_dir}/paper-fetch/skills/paper-fetch" ]]; then
    cp -r "${skills_dir}/paper-fetch/skills/paper-fetch/"* "${skills_dir}/paper-fetch/"
    rm -rf "${skills_dir}/paper-fetch/skills"
  fi
  echo "   ✅ paper-fetch 已安装到 ${skills_dir}/paper-fetch"
}
install_paper_fetch "${HOME}/.openclaw/skills" "~/.openclaw/skills"
install_paper_fetch "${HOME}/.hermes/skills" "~/.hermes/skills"

# ── 安装 zotero-cli-cc skill ────────────────────────────────────
install_zotero_skill() {
  local skills_dir="$1"
  local label="$2"
  mkdir -p "${skills_dir}"
  # Check idempotently: .git exists AND SKILL.md at root (not monorepo subdir)
  if [[ -d "${skills_dir}/zotero-cli-cc/.git" && -f "${skills_dir}/zotero-cli-cc/SKILL.md" ]]; then
    echo "   ✅ zotero-cli-cc skill 已存在于 ${label}，跳过安装"
    return
  fi
  echo "   📥 安装 zotero-cli-cc skill 到 ${label}（Zotero 文献管理）..."
  if [[ -d "${skills_dir}/zotero-cli-cc" ]]; then
    rm -rf "${skills_dir}/zotero-cli-cc"
  fi
  git clone --depth 1 https://github.com/Agents365-ai/zotero-cli-cc.git \
    "${skills_dir}/zotero-cli-cc"
  cd "${skills_dir}/zotero-cli-cc"
  # Repo contains the skill at skill/zotero-cli-cc/; move to root
  if [[ -d "skill/zotero-cli-cc" ]]; then
    cp -r skill/zotero-cli-cc/* .
    rm -rf skill
  fi
  cd - > /dev/null
  echo "   ✅ zotero-cli-cc 已安装到 ${skills_dir}/zotero-cli-cc"
}
install_zotero_skill "${HOME}/.hermes/skills" "~/.hermes/skills"

# ── 安装 paper-to-zotero skill（项目自有 skill）────────────────────
install_paper_to_zotero_skill() {
  local skills_dir="$1"
  local label="$2"
  local src="${REPO_ROOT}/skills/paper-to-zotero"
  mkdir -p "${skills_dir}/paper-to-zotero"
  # Check idempotently: if SKILL.md hasn't changed, skip
  if [[ -f "${skills_dir}/paper-to-zotero/SKILL.md" ]]; then
    if cmp -s "${src}/SKILL.md" "${skills_dir}/paper-to-zotero/SKILL.md"; then
      echo "   ✅ paper-to-zotero skill 已存在于 ${label}，跳过安装"
      return
    fi
  fi
  echo "   📥 安装 paper-to-zotero skill 到 ${label}（paper-fetch → Drive → Zotero 完整工作流）..."
  cp "${src}/SKILL.md" "${skills_dir}/paper-to-zotero/SKILL.md"
  # Initialize git repo if missing — Hermes only discovers skills with .git
  if [[ ! -d "${skills_dir}/paper-to-zotero/.git" ]]; then
    git -C "${skills_dir}/paper-to-zotero" init -q
    git -C "${skills_dir}/paper-to-zotero" add SKILL.md
    git -C "${skills_dir}/paper-to-zotero" -c user.email="skill@myopenclaw" -c user.name="myopenclaw" commit -qm "paper-to-zotero skill" --no-gpg-sign
  elif ! git -C "${skills_dir}/paper-to-zotero" diff --quiet; then
    git -C "${skills_dir}/paper-to-zotero" add SKILL.md
    git -C "${skills_dir}/paper-to-zotero" -c user.email="skill@myopenclaw" -c user.name="myopenclaw" commit -qm "update paper-to-zotero skill" --no-gpg-sign
  fi
  echo "   ✅ paper-to-zotero 已安装到 ${skills_dir}/paper-to-zotero"
}
install_paper_to_zotero_skill "${HOME}/.hermes/skills" "~/.hermes/skills"
# Also install to coder profile's research skills（爱码士 Discord bot 使用的 skill 路径）
install_paper_to_zotero_skill "${HOME}/.hermes/profiles/coder/skills/research" "coder profile"

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

# ── OpenClaw：版本可见性 + 配置兼容性检查 ─────────────────────
OPENCLAW_NPM_VERSION=""
OPENCLAW_DOCKER_VERSION=""
if [[ -x /opt/homebrew/lib/node_modules/openclaw/dist/index.js ]]; then
  OPENCLAW_NPM_VERSION=$(/opt/homebrew/lib/node_modules/openclaw/dist/index.js --version 2>/dev/null | head -1 || echo "unknown")
fi
if docker compose config 2>/dev/null | grep -q "openclaw-gateway"; then
  OPENCLAW_DOCKER_VERSION=$(docker compose run --rm --entrypoint "node" openclaw-gateway openclaw.mjs --version 2>/dev/null | tail -1 || echo "unknown")
fi

echo ""
echo "🦞 OpenClaw 版本检查"
echo "   launchd 网关 (npm):  ${OPENCLAW_NPM_VERSION:-未安装}"
echo "   Docker 网关 (镜像): ${OPENCLAW_DOCKER_VERSION:-未安装}"

if [[ -n "${OPENCLAW_NPM_VERSION}" && -n "${OPENCLAW_DOCKER_VERSION}" ]] \
  && [[ "${OPENCLAW_NPM_VERSION}" != "${OPENCLAW_DOCKER_VERSION}" ]]; then
  echo "   ⚠️  版本不一致！npm 和 Docker 镜像应保持相同版本，避免配置格式不兼容"
  echo "   升级方法: npm install -g openclaw@<版本> && 更新 .env OPENCLAW_IMAGE"
elif [[ -z "${OPENCLAW_NPM_VERSION}" && -z "${OPENCLAW_DOCKER_VERSION}" ]]; then
  echo "   ℹ️  未检测到 OpenClaw，跳过版本检查"
fi

# 用 Docker 镜像的 openclaw 校验配置文件兼容性
# 如果 Docker 版本不认识配置格式，会在这里提前发现，而不是启动后沉默打 762MB 日志
echo ""
echo "🔍 校验 OpenClaw 配置兼容性 (Docker 镜像版本 ${OPENCLAW_DOCKER_VERSION})..."
set +e
VALIDATE_OUTPUT=$(docker compose run --rm --entrypoint "node" openclaw-gateway openclaw.mjs config validate 2>&1)
VALIDATE_EXIT=$?
set -e
if [[ "${VALIDATE_EXIT}" -ne 0 ]] || echo "${VALIDATE_OUTPUT}" | grep -qiE "invalid|problem|error"; then
  echo "   ⚠️  配置校验发现问题（Docker 镜像视角）："
  echo "${VALIDATE_OUTPUT}" | sed 's/^/   │  /'
  echo "   ⚠️  继续启动，但请关注上述警告。网关可能反复打印相同错误到"
  echo "         ~/.openclaw/logs/gateway.err.log"
  echo "   修复: 不要从 host 运行 openclaw doctor --fix，应在容器内操作："
  echo "         docker compose run --rm --entrypoint \"node\" openclaw-gateway openclaw.mjs doctor --fix"
else
  echo "   ✅ 配置兼容（Docker 镜像版本可以正确解析）"
fi
echo ""

cd "${REPO_ROOT}"

BUILD_FLAG=""
if [[ "${1:-}" == "--build" ]]; then
  BUILD_FLAG="--build"
fi

echo "🚀 启动服务..."
echo "   备份目录: ${BACKUP_ROOT}"
docker compose up -d ${BUILD_FLAG}
echo "✅ 服务已启动"

HERMES_BIN="/opt/hermes/.venv/bin/hermes"
HERMES_CONFIG="${HOME}/.hermes/config.yaml"

# ── 启用 Hermes Cron Scheduler ─────────────────────────────────
if [[ -f "${HERMES_CONFIG}" ]]; then
  if ! grep -q 'cron_mode: allow' "${HERMES_CONFIG}" 2>/dev/null; then
    python3 -c "
import yaml
with open('${HERMES_CONFIG}') as f:
    cfg = yaml.safe_load(f)
cfg['cron_mode'] = 'allow'
with open('${HERMES_CONFIG}', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
"
    echo "   ⏰ Hermes cron_mode: allow (已启用)"
  else
    echo "   ⏰ Hermes cron_mode: 已启用，跳过"
  fi
fi

# ── 注册 Morning Triage v2 Cron Job ───────────────────────────
# 仅在 cron_mode=allow 时注册
if [[ -f "${HERMES_CONFIG}" ]] && grep -q 'cron_mode: allow' "${HERMES_CONFIG}" 2>/dev/null; then
  # 等待 Hermes 就绪
  for i in $(seq 1 15); do
    if docker compose ps hermes 2>/dev/null | grep -q 'Up'; then break; fi
    sleep 2
  done
  if docker compose ps hermes 2>/dev/null | grep -q 'Up'; then
    EXISTING=$(docker compose exec -T hermes "${HERMES_BIN}" cron list 2>/dev/null | grep -c "Daily Command Center" || true)
    if [ "${EXISTING:-0}" -lt 1 ]; then
      docker compose exec -T hermes "${HERMES_BIN}" cron create \
        "50 23 * * *" \
        "执行 morning-triage-v2 skill：查询 TDAI 记忆 + AgentOps 健康信号 + 生成 Daily Command Center 汇总。回复即飞书推送。" \
        --skill morning-triage-v2 \
        --name "Daily Command Center" 2>/dev/null && \
        echo "   📋 Morning Triage v2 cron job 已注册 (每日 7:50 北京)" || \
        echo "   ⚠️  Morning Triage v2 cron job 注册失败"
    else
      echo "   📋 Morning Triage v2 cron job 已存在，跳过"
    fi
  fi
else
  echo "   ⚠️  cron_mode 未启用，跳过 Morning Triage cron job 注册"
fi

# ── 幂等初始化 Uptime Kuma 监控项 ──────────────────────────────
if docker compose ps uptime-kuma 2>/dev/null | grep -q 'Up'; then
  "${REPO_ROOT}/scripts/setup-uptime-kuma.sh" --quiet || true
fi

docker compose ps
