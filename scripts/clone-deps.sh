#!/usr/bin/env bash
# =============================================================
# clone-deps.sh — 克隆 myopenclaw 依赖的所有兄弟仓库到正确路径
# 用法: ./scripts/clone-deps.sh
# =============================================================
set -euo pipefail

CODE_DIR="${CODE_DIR:-${HOME}/code}"
mkdir -p "${CODE_DIR}"

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

echo ""
echo "============================================"
echo "  依赖仓库状态"
echo "============================================"
for dir in \
  "${CODE_DIR}/aisecretary" \
  "${CODE_DIR}/git-contribution-stats" \
  "${CODE_DIR}/dailyinfo"; do
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
