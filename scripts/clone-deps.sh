#!/usr/bin/env bash
# =============================================================
# clone-deps.sh — 克隆 myopenclaw 依赖的所有兄弟仓库到正确路径
# 用法: ./scripts/clone-deps.sh
# =============================================================
set -euo pipefail

CODE_DIR="${CODE_DIR:-${HOME}/code}"
mkdir -p "${CODE_DIR}"

readonly YUQUE_MCP_REPO_URL="https://gitcode.com/dlut-water/yuque_mcp_server.git"
readonly YUQUE_MCP_SOURCE_REF="codex/docs-yuque-mcp-deployment-status"
readonly YUQUE_MCP_PINNED_COMMIT="cc68fd0df172d3b8f24ae325998d56bdfd0e36e6"

# ── 检查 gh CLI 认证状态 ──────────────────────────────────────
check_gh_auth() {
  if command -v gh &>/dev/null && gh auth status &>/dev/null 2>&1; then
    return 0
  else
    echo "⚠️  gh CLI 未登录或未安装，跳过私有仓库克隆"
    echo "   安装: brew install gh && gh auth login"
    return 1
  fi
}

# ── 克隆或更新仓库 ────────────────────────────────────────────
clone_or_update() {
  local repo_url="$1"
  local target_dir="$2"
  local description="$3"

  if [[ -d "${target_dir}/.git" ]]; then
    echo "✅ ${description} 已存在: ${target_dir}"
    echo "   如需更新: cd ${target_dir} && git pull"
  else
    echo "📥 克隆 ${description}..."
    if [[ -d "${target_dir}" ]]; then
      echo "   ⚠️  目录已存在但不是 git 仓库，跳过: ${target_dir}"
      return 1
    fi
    git clone "${repo_url}" "${target_dir}"
    echo "   ✅ 完成: ${target_dir}"
  fi
}

clone_or_verify_pinned() {
  local target_dir="${CODE_DIR}/yuque_mcp_server"

  if [[ -d "${target_dir}/.git" ]]; then
    local current_sha
    current_sha="$(git -C "${target_dir}" rev-parse HEAD)"
    if ! git -C "${target_dir}" cat-file -e "${YUQUE_MCP_PINNED_COMMIT}^{commit}" 2>/dev/null; then
      echo "❌ yuque_mcp_server 缺少固定 commit: ${YUQUE_MCP_PINNED_COMMIT}"
      echo "   请手动执行: git -C ${target_dir} fetch origin ${YUQUE_MCP_SOURCE_REF}"
      return 1
    fi
    if [[ "${current_sha}" != "${YUQUE_MCP_PINNED_COMMIT}" ]]; then
      echo "⚠️  yuque_mcp_server 当前版本偏离固定版本，未自动修改"
      echo "   当前: ${current_sha}"
      echo "   目标: ${YUQUE_MCP_PINNED_COMMIT}"
      echo "   请确认工作区后手动执行: git -C ${target_dir} checkout ${YUQUE_MCP_PINNED_COMMIT}"
      return 1
    fi
    echo "✅ yuque_mcp_server 已固定: ${YUQUE_MCP_PINNED_COMMIT}"
    return 0
  fi

  if [[ -e "${target_dir}" ]]; then
    echo "❌ 目录已存在但不是 git 仓库，跳过: ${target_dir}"
    return 1
  fi

  echo "📥 克隆 yuque_mcp_server（语雀 MCP）..."
  git clone --branch "${YUQUE_MCP_SOURCE_REF}" "${YUQUE_MCP_REPO_URL}" "${target_dir}"
  git -C "${target_dir}" cat-file -e "${YUQUE_MCP_PINNED_COMMIT}^{commit}"
  git -C "${target_dir}" checkout --detach "${YUQUE_MCP_PINNED_COMMIT}"
  echo "   ✅ 已固定: ${YUQUE_MCP_PINNED_COMMIT}"
}

echo "============================================"
echo "  myopenclaw 依赖仓库克隆"
echo "============================================"
echo ""

# ── 1. aisecretary（硬依赖：build context）────────────────────
clone_or_update \
  "https://github.com/OuyangWenyu/aisecretary.git" \
  "${CODE_DIR}/aisecretary" \
  "aisecretary（事务数据库 MCP）"

# ── 2. git-contribution-stats（硬依赖：build context）──────────
clone_or_update \
  "https://github.com/OuyangWenyu/git-contribution-stats.git" \
  "${CODE_DIR}/git-contribution-stats" \
  "git-contribution-stats（研发日报数据服务）"

# ── 3. dailyinfo（软依赖：launchd 调度）───────────────────────
clone_or_update \
  "https://github.com/iHeadWater/dailyinfo.git" \
  "${CODE_DIR}/dailyinfo" \
  "dailyinfo（AI 情报聚合）"

# ── 4. yuque_mcp_server（可选：Hermes 语雀知识库）────────────
clone_or_verify_pinned

echo ""
echo "============================================"
echo "  依赖仓库状态"
echo "============================================"
for dir in \
  "${CODE_DIR}/aisecretary" \
  "${CODE_DIR}/git-contribution-stats" \
  "${CODE_DIR}/dailyinfo" \
  "${CODE_DIR}/yuque_mcp_server"; do
  if [[ -d "${dir}/.git" ]]; then
    echo "  ✅ $(basename "${dir}")"
  else
    echo "  ❌ $(basename "${dir}") — 未克隆"
  fi
done

echo ""
echo "完成。现在可以运行 ./scripts/start.sh 启动服务。"
echo ""
echo "如果部分仓库克隆失败（私有仓库），不影响核心服务启动。"
echo "缺失哪些功能，详见 docs/portability.md"
