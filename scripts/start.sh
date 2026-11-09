#!/usr/bin/env bash
# =============================================================
# start.sh — 启动所有服务
# 用法: ./scripts/start.sh [--build]
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# issue-watcher/notifier 以宿主 UID/GID 运行，避免 root 写 bind mount。
# 直接 docker compose up（不经本脚本）时 compose 回退到 0:0（root）。
export HOST_UID="${HOST_UID:-$(id -u)}"
export HOST_GID="${HOST_GID:-$(id -g)}"

# ── 检查 .env ────────────────────────────────────────────────
if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "⚠️  .env 不存在，从模板创建..."
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  echo "   请编辑 .env 填写配置后重新运行"
  exit 1
fi

# Kuma push 从容器内发出，127.0.0.1/localhost 在容器里指向容器自己；
# 自动换成 compose 网络里的 uptime-kuma。直接 docker compose up 时需在
# .env 里提供 KUMA_PUSH_URL_DOCKER（见 .env.example）。
if [[ -z "${KUMA_PUSH_URL_DOCKER:-}" ]]; then
  _kuma_push="$(grep '^KUMA_PUSH_URL=' "${REPO_ROOT}/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  if [[ -n "${_kuma_push}" ]]; then
    _kuma_push="${_kuma_push//127.0.0.1/uptime-kuma}"
    _kuma_push="${_kuma_push//localhost/uptime-kuma}"
    export KUMA_PUSH_URL_DOCKER="${_kuma_push}"
  fi
  unset _kuma_push
fi

# ── OpenClaw 配置文件路径（凭据注入块先使用后赋值，须提前定义）──
OPENCLAW_CONFIG="${HOME}/.openclaw/openclaw.json"

# ── 检查 GH_TOKEN 是否设置（gh CLI / git 认证用）────────────
if ! grep -q '^GH_TOKEN=.\+' "${REPO_ROOT}/.env" 2>/dev/null; then
  echo "   ⚠️  GH_TOKEN 未设置 — gh CLI 和 git clone 私有仓库将不可用"
  echo "   在 .env 中设置 GH_TOKEN（OuyangWenyu 个人 GitHub 令牌）"
fi

# ── 检查 YUQUE_TOKEN 是否设置（本机语雀 MCP 服务端用）────────
# 缺 token 时 yuque-mcp 容器照常 Up，但每夜快照与全部语雀工具静默失败 ——
# 这里大声警告（不阻断：其余服务不受影响）。
if ! grep -q '^YUQUE_TOKEN=.\+' "${REPO_ROOT}/.env" 2>/dev/null; then
  echo "   ⚠️  YUQUE_TOKEN 未设置 — 语雀 MCP 快照/查询将失败"
  echo "   语雀后台创建只读 token（repo:read + doc:read）后写入 .env"
fi

# ── 检查 MCP_YUQUE_MCP_API_KEY 是否设置（yuque-mcp 服务端 Bearer key）──
# 缺 key 时上游行为是**无认证直通**：myopenclaw-net 上任意容器（含可被提示
# 注入的 agent）都能免鉴权读团队文档。监控只接受 401 探针兜底，这里先警告。
if ! grep -q '^MCP_YUQUE_MCP_API_KEY=.\+' "${REPO_ROOT}/.env" 2>/dev/null; then
  echo "   ⚠️  MCP_YUQUE_MCP_API_KEY 未设置 — yuque-mcp 将无认证直通（网内容器可直读团队文档）"
fi

# Read GDRIVE_PAPERS_LOCAL_PATH from .env (can't source directly — cron expressions break bash)
if [[ -z "${GDRIVE_PAPERS_LOCAL_PATH:-}" ]]; then
  GDRIVE_PAPERS_LOCAL_PATH=$(grep '^GDRIVE_PAPERS_LOCAL_PATH=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)
  GDRIVE_PAPERS_LOCAL_PATH="${GDRIVE_PAPERS_LOCAL_PATH/#\~/$HOME}"
  if [[ -n "${GDRIVE_PAPERS_LOCAL_PATH}" ]]; then
    export GDRIVE_PAPERS_LOCAL_PATH
    echo "   📁 GDRIVE_PAPERS_LOCAL_PATH 已从 .env 读取"
  fi
fi

# ── 检查依赖仓库（非阻塞，仅警告）─────────────────────────────
echo "🔍 检查依赖仓库..."
MISSING_REPOS=()
for repo in "${HOME}/code/aisecretary" "${HOME}/code/git-contribution-stats" "${HOME}/code/yuque_mcp_server" "${HOME}/code/dailyinfo"; do
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

# ── dailyinfo 独立栈（软依赖：本机存在则顺手拉起）──────────────
# dailyinfo 有自己的 compose project（freshrss + scheduler + dailyinfo-mcp）。
# 这里只做「拉起本项目时顺带拉起它」，不合并 compose；失败只警告，不阻断主栈。
# dailyinfo 部署在远程（如 81）时设 DAILYINFO_LOCAL_STACK=0 关闭本地拉起。
DAILYINFO_DIR="${DAILYINFO_DIR:-${HOME}/code/dailyinfo}"
if [[ "${DAILYINFO_LOCAL_STACK:-1}" != "0" && -f "${DAILYINFO_DIR}/compose.yml" ]]; then
  echo "📚 启动 dailyinfo 独立栈..."
  if (cd "${DAILYINFO_DIR}" && docker compose up -d); then
    echo "   ✅ dailyinfo 已启动（freshrss / scheduler / dailyinfo-mcp）"
  else
    echo "   ⚠️  dailyinfo 启动失败；主栈继续。手动检查: cd ${DAILYINFO_DIR} && docker compose up -d"
  fi
  echo ""
fi

# ── 从 .cloud.conf 解析 BACKUP_ROOT ─────────────────────────
# Linux 兼容：.cloud.conf 和云盘目录缺失时降级为 warning，不阻
# 止后续 Docker 服务启动。备份 cron 容器自身会在缺失时优雅失败。
CONF_FILE="${REPO_ROOT}/.cloud.conf"
HAS_CLOUD_CONF=false
if [[ ! -f "${CONF_FILE}" ]]; then
  echo "   ⚠️  未找到 .cloud.conf，跳过云盘备份配置"
  echo "   .cloud.conf 用于配置云端备份路径（Google Drive / OneDrive）"
  echo "   如需备份功能，请从 .cloud.conf.example 创建模板并配置"
else
  # shellcheck source=/dev/null
  source "${CONF_FILE}"

  case "${CLOUD_PROVIDER:-google_drive}" in
    google_drive) CLOUD_ROOT="${GOOGLE_DRIVE_PATH}" ;;
    onedrive)     CLOUD_ROOT="${ONEDRIVE_PATH}" ;;
    custom)       CLOUD_ROOT="${CUSTOM_CLOUD_PATH}" ;;
  esac
  CLOUD_ROOT="${CLOUD_ROOT/#\~/$HOME}"

  if [[ ! -d "${CLOUD_ROOT}" ]]; then
    echo "   ⚠️  云盘目录不存在: ${CLOUD_ROOT}，跳过备份配置"
    echo "   请确认云盘客户端已登录，或创建 .cloud.conf 指向有效路径"
  else
    export BACKUP_ROOT="${CLOUD_ROOT}/${BACKUP_SUBDIR:-myopenclaw-backups}"
    mkdir -p "${BACKUP_ROOT}/hermes" "${BACKUP_ROOT}/openclaw" "${BACKUP_ROOT}/claude"
    HAS_CLOUD_CONF=true

    # ── 写入备份根目录标记（哨兵）────────────────────────────
    # backup-cron 启动时校验此文件；缺失即拒绝启动。没有它的话，直接用
    # `docker compose up -d backup-cron`（不经本脚本）会让 compose 静默回退到
    # /tmp/myopenclaw-backups —— 备份写进会被系统清理的目录且毫无报错。
    printf '%s\n' "${BACKUP_ROOT}" > "${BACKUP_ROOT}/.myopenclaw-backup-root"

    # ── 自动推导 GDRIVE_PAPERS_LOCAL_PATH（若 .env 未设置）────
    if [[ -z "${GDRIVE_PAPERS_LOCAL_PATH:-}" ]]; then
      export GDRIVE_PAPERS_LOCAL_PATH="${CLOUD_ROOT}/Papers/Zotero_Papers"
      echo "   📁 GDRIVE_PAPERS_LOCAL_PATH 自动推导: ${GDRIVE_PAPERS_LOCAL_PATH}"
    fi
  fi
fi

# ── 确保 gc (GitCode CLI) 二进制存在（volume mount 需要）─────
# gc 是 Linux 专用二进制（从 GitCode 源码编译），Mac 上不存在。
# Docker volume mount 要求源路径存在，否则容器启动失败。
# 如果 gc 不存在（目录或缺失），创建占位文件让挂载不报错。
GC_BIN="${HOME}/.openclaw/bin/gc"
if [[ ! -f "${GC_BIN}" ]]; then
  mkdir -p "$(dirname "${GC_BIN}")"
  rm -rf "${GC_BIN}"  # 可能是空目录
  touch "${GC_BIN}"
  echo "   ⚠️  gc (GitCode CLI) 二进制不存在，已创建占位文件"
  echo "   Linux 部署请从源码编译: https://gitcode.com/gitcode-cli"
fi

# ── 确保工具配置目录存在（volume mount 需要）──────────────────
mkdir -p "${HOME}/.config/gh" "${HOME}/.config/opencode" "${HOME}/.lark-cli"
mkdir -p "${HOME}/.myagentdata/aisecretary"
mkdir -p "${HOME}/.myagentdata/tdai-memory"
mkdir -p "${HOME}/.myagentdata/agentops"
# 论文清单（虾酱写、mylibrary 读）。必须在 compose up 之前建好：否则 Docker 会
# 以 root 身份创建这个挂载点，容器里的 node 用户随后写不进去。
mkdir -p "${HOME}/.myagentdata/paper-queue"
# 语雀 MCP 本机服务端：快照/报告库与备份输出。必须在 compose up 前建好 ——
# 否则 Docker 以 root 建目录（本容器 user: root 时功能上无碍，但宿主归属/权限会乱）。
mkdir -p "${HOME}/.myagentdata/yuque-mcp/change_data" "${HOME}/.myagentdata/yuque-mcp/backup"
# issue-notifier：repo-scanner 事件 → 飞书群。真实人员映射不入库，
# 首次从示例播种；示例含占位符，需手工替换。
mkdir -p "${HOME}/.myagentdata/issue-notifier"
if [[ ! -f "${HOME}/.myagentdata/issue-notifier/config.toml" ]]; then
  cp "${REPO_ROOT}/config/issue-notifier.example.toml" "${HOME}/.myagentdata/issue-notifier/config.toml"
  echo "   📝 已创建 issue-notifier 配置（需填人员映射）: ~/.myagentdata/issue-notifier/config.toml"
fi
if grep -q 'example-gitcode' "${HOME}/.myagentdata/issue-notifier/config.toml" 2>/dev/null; then
  echo "   ⚠️  issue-notifier: 人员映射仍是示例占位符，请编辑 ~/.myagentdata/issue-notifier/config.toml"
fi
_in_mode="$(grep -E '^ISSUE_NOTIFIER_FEISHU_MODE=' "${REPO_ROOT}/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
if [[ "${_in_mode:-app}" == "app" ]] && ! grep -q '^ISSUE_NOTIFIER_FEISHU_CHAT_ID=.\+' "${REPO_ROOT}/.env" 2>/dev/null; then
  echo "   ⚠️  issue-notifier: ISSUE_NOTIFIER_FEISHU_CHAT_ID 未设置（app 模式必填）"
fi
if ! grep -q '^ISSUE_WATCHER_GITHUB_TOKEN=.\+' "${REPO_ROOT}/.env" 2>/dev/null; then
  echo "   ⚠️  issue-watcher: ISSUE_WATCHER_GITHUB_TOKEN 未设置 —— GitHub 匿名仅公开仓库（60 请求/小时），私有 org 会持续 error"
fi
# inbox.md is produced by collect_agentops.py (host launchd). Without the
# directory, Docker's ro mount of ~/.myagentdata has no agentops/ subdir
# and Daily Command Center reports a false "AgentOps 未部署".
if [[ "$(uname -s)" == "Darwin" ]]; then
  if [[ ! -f "${HOME}/Library/LaunchAgents/ai.myopenclaw.collect-agentops.plist" ]]; then
    echo "   ⚠️  AgentOps launchd 未安装，inbox.md 不会自动生成"
    echo "      运行: ./scripts/launchd/install-collect-agentops.sh"
  fi
fi
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

# ── 确保 Hermes coder profile 使用 deepseek-flash ──────────────
CODER_CONFIG="${HOME}/.hermes/profiles/coder/config.yaml"
mkdir -p "$(dirname "${CODER_CONFIG}")"
if [[ ! -f "${CODER_CONFIG}" ]]; then
  cat > "${CODER_CONFIG}" << 'YAML'
model:
  default: deepseek-flash
  provider: deepseek
  base_url: https://api.deepseek.com
fallback_providers:
- zai
fallback_model:
  provider: zai
  model: glm-5.3-flash
agent:
  reasoning_overrides:
    glm-5.3-flash: low
YAML
  echo "   📝 已创建 Hermes coder profile 配置（模型: deepseek-flash）"
fi

# ── 确保 Hermes daoyuan profile（道元·文献学者）──────────────
DAOYUAN_CONFIG="${HOME}/.hermes/profiles/daoyuan/config.yaml"
mkdir -p "$(dirname "${DAOYUAN_CONFIG}")"
if [[ ! -f "${DAOYUAN_CONFIG}" ]]; then
  cat > "${DAOYUAN_CONFIG}" << 'YAML'
model:
  default: deepseek-flash
  provider: deepseek
  base_url: https://api.deepseek.com
fallback_providers:
- zai
fallback_model:
  provider: zai
  model: glm-5.3-flash
agent:
  reasoning_overrides:
    glm-5.3-flash: low
mcp_servers:
  zotero:
    url: http://zotero-mcp:8002/mcp
    timeout: 120
memory:
  memory_enabled: true
YAML
  echo "   📝 已创建 Hermes daoyuan profile 配置（模型: deepseek-flash + zotero-mcp + memory 隔离）"
fi

# 道元 Feishu 访问收紧：DM 只允许指定用户；留空 = pairing 模式（不是 deny-all），
# 所有私聊都会被要求配对码。群聊由 compose 的 FEISHU_GROUP_POLICY=disabled 关闭。
DAOYUAN_FEISHU_APP_ID_VAL="$(grep '^DAOYUAN_FEISHU_APP_ID=.' "${REPO_ROOT}/.env" 2>/dev/null | tail -1 | cut -d'=' -f2- | tr -d '[:space:]' || true)"
DAOYUAN_FEISHU_ALLOWED_VAL="$(grep '^DAOYUAN_FEISHU_ALLOWED_USERS=.' "${REPO_ROOT}/.env" 2>/dev/null | tail -1 | cut -d'=' -f2- | tr -d '[:space:]' || true)"
if [[ -n "${DAOYUAN_FEISHU_APP_ID_VAL}" && -z "${DAOYUAN_FEISHU_ALLOWED_VAL}" ]]; then
  echo "   ⚠️  DAOYUAN_FEISHU_ALLOWED_USERS 为空：道元会进入 pairing 模式，所有私聊都会被拒/要求配对码"
  echo "      在 .env 填道元应用维度的 open_id（ou_...）后重跑本脚本"
fi

# ── 迁移既有 profile 的主模型到 deepseek-flash（幂等）───────────
# 上面两个 heredoc 只在文件**不存在**时生效：已在运行的机器、以及从快照
# `restore.sh` 恢复出来的机器都不会被自动迁移，会静默留在旧模型上，而
# 文档已经宣称三个 agent 统一是 deepseek-flash。
# 这一步只改「已知的历史取值」（mimo-v2.5(-pro) / 非 canonical 的 deepseek-v*），
# 不覆盖操作者主动选的其它模型 —— 见 scripts/ensure_hermes_model.py。
for _cfg in \
  "${HOME}/.hermes/config.yaml" \
  "${CODER_CONFIG}" \
  "${DAOYUAN_CONFIG}" \
  "${HOME}/.hermes/profiles/finance/config.yaml"; do
  [[ -f "${_cfg}" ]] || continue
  _st="$(python3 "${REPO_ROOT}/scripts/ensure_hermes_model.py" "${_cfg}" || true)"
  echo "   🎛️  ${_cfg/#${HOME}/\~} → ${_st##*→ }"
done

# ── 确保 zotero-mcp 在默认配置的 mcp_servers 中 ─────────────────
# Hermes profile 不支持覆盖 mcp_servers，必须在默认配置中注入。
HERMES_DEFAULT_CONFIG="${HOME}/.hermes/config.yaml"
if [[ -f "${HERMES_DEFAULT_CONFIG}" ]]; then
  if ! grep -q 'zotero-mcp:8002' "${HERMES_DEFAULT_CONFIG}"; then
    python3 -c "
import pathlib
p = pathlib.Path('${HERMES_DEFAULT_CONFIG}')
c = p.read_text()
entry = '  zotero:\n    connect_timeout: 60\n    enabled: true\n    timeout: 120\n    url: http://zotero-mcp:8002/mcp\n'
c = c.replace('platform_toolsets:', entry + '\nplatform_toolsets:')
p.write_text(c)
print('done')
"
    echo "   🔗 zotero-mcp 已注入到 Hermes 默认配置"
  else
    echo "   ✅ zotero-mcp 已在 Hermes 默认配置中"
  fi
  # DuckDuckGo web_search backend (no API key). Idempotent; does not
  # overwrite an operator-chosen backend such as brave_free.
  # The helper uses surgical YAML edits and does not require host PyYAML.
  python3 "${REPO_ROOT}/scripts/ensure_hermes_web_search.py" \
    "${HERMES_DEFAULT_CONFIG}" || \
    echo "   ⚠️  Hermes web.search_backend 注入失败（不影响启动）"
else
  echo "   ⚠️  ${HERMES_DEFAULT_CONFIG} 不存在，跳过 zotero-mcp 注入"
fi

# ── server-monitoring MCP（爱玛士 + 爱码士，各自独立）────────────
# 两个 profile 都不继承彼此的 mcp_servers，必须分别写入。URL/token 来自
# .env；URL 为空 = 移除受管条目（与天一「URL 非空 = 开关」一致）。
# 配置写的是 ${VAR} 占位符，真实域名/token 只在容器环境里展开。
DEFAULT_MCP_STATUS=""
CODER_MCP_STATUS=""
if [[ -f "${HERMES_DEFAULT_CONFIG}" ]]; then
  if DEFAULT_MCP_STATUS="$(python3 "${REPO_ROOT}/scripts/ensure_hermes_server_monitoring.py" \
      "${HERMES_DEFAULT_CONFIG}" --env-file "${REPO_ROOT}/.env" 2>&1)"; then
    echo "   🛰️  爱玛士 server-monitoring MCP: ${DEFAULT_MCP_STATUS}"
  else
    echo "   ⚠️  爱玛士 server-monitoring MCP 注册失败: ${DEFAULT_MCP_STATUS}"
    DEFAULT_MCP_STATUS="failed"
  fi
fi
if [[ -f "${CODER_CONFIG}" ]]; then
  if CODER_MCP_STATUS="$(python3 "${REPO_ROOT}/scripts/ensure_hermes_server_monitoring.py" \
      "${CODER_CONFIG}" --env-file "${REPO_ROOT}/.env" 2>&1)"; then
    echo "   🛰️  爱码士 server-monitoring MCP: ${CODER_MCP_STATUS}"
  else
    echo "   ⚠️  爱码士 server-monitoring MCP 注册失败: ${CODER_MCP_STATUS}"
    CODER_MCP_STATUS="failed"
  fi
fi

# ── dailyinfo 只读 MCP（道元 + 虾酱）─────────────────────────────
# dailyinfo 是独立 compose 栈（FreshRSS + scheduler + MCP），本仓只注册
# MCP 客户端条目：道元 = Hermes daoyuan profile，虾酱 = OpenClaw 主网关。
# 配置写 ${VAR} 占位符，真实 URL/key 只在容器环境里展开；两者为空 = 移除条目。
DAOYUAN_DAILYINFO_STATUS=""
if [[ -f "${DAOYUAN_CONFIG}" ]]; then
  if DAOYUAN_DAILYINFO_STATUS="$(python3 "${REPO_ROOT}/scripts/ensure_hermes_dailyinfo.py" \
      "${DAOYUAN_CONFIG}" --env-file "${REPO_ROOT}/.env" 2>&1)"; then
    echo "   📚 道元 dailyinfo MCP: ${DAOYUAN_DAILYINFO_STATUS}"
  else
    echo "   ⚠️  道元 dailyinfo MCP 注册失败: ${DAOYUAN_DAILYINFO_STATUS}"
    DAOYUAN_DAILYINFO_STATUS="failed"
  fi
fi
OPENCLAW_DAILYINFO_STATUS=""
if [[ -f "${OPENCLAW_CONFIG}" ]]; then
  if OPENCLAW_DAILYINFO_STATUS="$(python3 "${REPO_ROOT}/scripts/ensure_openclaw_dailyinfo.py" \
      "${OPENCLAW_CONFIG}" --env-file "${REPO_ROOT}/.env" 2>&1)"; then
    echo "   📚 虾酱 dailyinfo MCP: ${OPENCLAW_DAILYINFO_STATUS}"
  else
    echo "   ⚠️  虾酱 dailyinfo MCP 注册失败: ${OPENCLAW_DAILYINFO_STATUS}"
    OPENCLAW_DAILYINFO_STATUS="failed"
  fi
fi

# ── 道元 no-agent cron 的脚本安装与配置 ──────────────────────────
# Hermes cron 的 --script 只认 HERMES_HOME/scripts，且拒绝解析到该目录之外的
# symlink ⇒ 必须拷贝而不是链接。每次 start.sh 覆盖安装：仓库是唯一版本源，
# 脚本内容更新不需要改 cron job。
DAOYUAN_SCRIPTS_DIR="${HOME}/.hermes/profiles/daoyuan/scripts"
mkdir -p "${DAOYUAN_SCRIPTS_DIR}"
install_hermes_profile_script() {
  local src="$1" name tmp
  name="$(basename "${src}")"
  tmp="${DAOYUAN_SCRIPTS_DIR}/.${name}.tmp.$$"
  if install -m 644 "${src}" "${tmp}"; then
    mv -f "${tmp}" "${DAOYUAN_SCRIPTS_DIR}/${name}"
    echo "   📜 ${name} 已安装（~/.hermes/profiles/daoyuan/scripts/）"
  else
    rm -f "${tmp}"
    echo "   ⚠️  ${name} 安装失败（对应 cron job 将无法运行）"
  fi
}
install_hermes_profile_script "${REPO_ROOT}/scripts/dailyinfo_digest.py"
install_hermes_profile_script "${REPO_ROOT}/scripts/paper_queue_sync.py"

# cron.wrap_response=false：否则每条投递都带 "Cronjob Response: ..." 头尾样板。
DAOYUAN_CRON_STATUS="$(python3 "${REPO_ROOT}/scripts/ensure_hermes_cron_config.py" \
  "${DAOYUAN_CONFIG}" 2>&1 || true)"
case "${DAOYUAN_CRON_STATUS}" in
  written*|unchanged*) echo "   ⏰ 道元 cron.wrap_response: ${DAOYUAN_CRON_STATUS}" ;;
  *) echo "   ⚠️  道元 cron.wrap_response 配置失败: ${DAOYUAN_CRON_STATUS}（消息会带 Cronjob Response 包裹）" ;;
esac

# server-monitoring-digest skill：两个 profile 共用同一个 repo 只读挂载。
# 目标路径是容器内路径；宿主机上这条 symlink 是"悬空"的，但在容器里有效
# （与 daily-dev-report 的既有模式一致）。只补缺/改指向，不覆盖真实目录。
ensure_skill_symlink() {
  local link="$1" target="$2" label="$3"
  mkdir -p "$(dirname "${link}")"
  if [[ -L "${link}" ]]; then
    if [[ "$(readlink "${link}")" != "${target}" ]]; then
      ln -sfn "${target}" "${link}"
      echo "   🔗 ${label} skill 链接已更新: ${link}"
    fi
  elif [[ -e "${link}" ]]; then
    echo "   ⚠️  ${link} 已存在且不是 symlink，跳过（避免覆盖 agent 维护的 skill）"
  else
    ln -s "${target}" "${link}"
    echo "   🔗 ${label} skill 已链接: ${link}"
  fi
}
ensure_skill_symlink "${HOME}/.hermes/skills/server-monitoring-digest" \
  "/opt/hermes-skills/server-monitoring-digest" "server-monitoring-digest"
ensure_skill_symlink "${HOME}/.hermes/profiles/coder/skills/server-monitoring-digest" \
  "/opt/hermes-skills/server-monitoring-digest" "server-monitoring-digest"

# 论文清单 skill：唯一一份源码在 skills/paper-queue，道元通过 symlink 复用。
_PQ_SKILL_LINK="${HOME}/.hermes/profiles/daoyuan/skills/paper-queue"
_pq_skill_before="$(readlink "${_PQ_SKILL_LINK}" 2>/dev/null || true)"
ensure_skill_symlink "${_PQ_SKILL_LINK}" "/opt/hermes-skills/paper-queue" "paper-queue"
_pq_skill_after="$(readlink "${_PQ_SKILL_LINK}" 2>/dev/null || true)"
PQ_SKILL_CHANGED=false
if [[ "${_pq_skill_before}" != "${_pq_skill_after}" ]]; then
  PQ_SKILL_CHANGED=true
fi

# ── 确保 mylibrary skills 在 external_dirs 中 ────────────────────
# aisecretary 模式：宿主机源码 → 只读挂载 → external_dirs 发现。
if [[ -f "${HERMES_DEFAULT_CONFIG}" ]]; then
  if ! grep -q 'mylibrary-skills' "${HERMES_DEFAULT_CONFIG}"; then
    python3 -c "
import pathlib
p = pathlib.Path('${HERMES_DEFAULT_CONFIG}')
c = p.read_text()
entry = '    - /opt/mylibrary-skills\n'
c = c.replace('  external_dirs:\n    - /opt/data/code/aisecretary/skills\n', '  external_dirs:\n    - /opt/data/code/aisecretary/skills\n' + entry)
p.write_text(c)
print('done')
"
    echo "   📂 mylibrary skills 已加入 Hermes external_dirs"
  else
    echo "   ✅ mylibrary skills 已在 Hermes external_dirs 中"
  fi
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


# ── 保留论文清单的旧归属签名密钥 ───────────────────────────────────
# 旧 OpenClaw HMAC 链路已下架；密钥保留在 .env 供未来道元多用户身份插件复用。
# compose 默认**不注入** sidecar（注入即重开「持密钥者可写任意 actor」的旁路）。
# 轮换 = 改 .env 里的值再跑一次本脚本。
if ! grep -q '^PAPER_QUEUE_ACTOR_SECRET=.\+' "${REPO_ROOT}/.env" 2>/dev/null; then
  _pq_secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  {
    echo ""
    echo "# 论文清单：插件与服务端共享的归属签名密钥（自动生成；轮换 = 改这里再跑一次 start.sh）"
    printf 'PAPER_QUEUE_ACTOR_SECRET=%s\n' "${_pq_secret}"
  } >> "${REPO_ROOT}/.env"
  echo "   🔑 已生成论文清单签名密钥并写入 .env"
fi
PAPER_QUEUE_ACTOR_SECRET="${PAPER_QUEUE_ACTOR_SECRET:-$(grep '^PAPER_QUEUE_ACTOR_SECRET=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)}"


# ── 道元 paper-queue 的 Bearer token / MCP 地址 ────────────────────
# token 由服务端绑定到固定 actor Owen；轮换 = 删掉 .env 里的 PAPER_QUEUE_DAOYUAN_TOKEN
# 再跑一次 start.sh（up -d 会重建 paper-queue-mcp 与 hermes-daoyuan）。
if ! grep -q '^PAPER_QUEUE_MCP_URL=' "${REPO_ROOT}/.env" 2>/dev/null; then
  {
    echo ""
    echo "# 道元 → paper-queue MCP 地址（同 compose 网络；显式留空 = 停用）"
    echo "PAPER_QUEUE_MCP_URL=http://paper-queue-mcp:8003/mcp"
  } >> "${REPO_ROOT}/.env"
  echo "   🔗 已写入默认 PAPER_QUEUE_MCP_URL"
fi

_pq_daoyuan_token="${PAPER_QUEUE_DAOYUAN_TOKEN:-}"
if [[ -z "${_pq_daoyuan_token}" ]]; then
  _pq_daoyuan_token="$(grep '^PAPER_QUEUE_DAOYUAN_TOKEN=.\+' "${REPO_ROOT}/.env" 2>/dev/null | tail -1 | cut -d'=' -f2- || true)"
fi
if [[ -z "${_pq_daoyuan_token}" ]]; then
  _pq_daoyuan_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  # token 经环境变量传给子进程，避免出现在 ps 的 argv 里；临时文件用 mkstemp +
  # 失败清理，避免中断时在仓库根留下一个未 gitignore 的明文 token。
  PQ_TOKEN="${_pq_daoyuan_token}" python3 - "${REPO_ROOT}/.env" <<'PY'
import os, pathlib, sys, tempfile
path = pathlib.Path(sys.argv[1])
token = os.environ["PQ_TOKEN"]
lines = [line for line in path.read_text().splitlines()
         if not line.startswith("PAPER_QUEUE_DAOYUAN_TOKEN=")]
while lines and not lines[-1].strip():
    lines.pop()
lines += ["",
          "# 道元 → paper-queue MCP 的固定归属 token（自动生成；轮换 = 删掉本行再跑 start.sh）",
          f"PAPER_QUEUE_DAOYUAN_TOKEN={token}"]
fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    os.chmod(tmp_name, os.stat(path).st_mode & 0o777)
    os.replace(tmp_name, path)
except BaseException:
    try:
        os.unlink(tmp_name)
    except OSError:
        pass
    raise
PY
  echo "   🔑 已生成道元 paper-queue token 并写入 .env"
fi

# 固定归属 actor：优先 .env 的显式配置，否则取 DISCORD_ALLOWED_USERS 的第一项
# （Owen 的 Discord 雪花）。真实 ID 只写 .env，不落公开仓库。
_pq_daoyuan_actor_id="${PAPER_QUEUE_DAOYUAN_ACTOR_ID:-$(grep '^PAPER_QUEUE_DAOYUAN_ACTOR_ID=' "${REPO_ROOT}/.env" 2>/dev/null | tail -1 | cut -d'=' -f2- || true)}"
if [[ -z "${_pq_daoyuan_actor_id}" ]]; then
  _pq_daoyuan_actor_id="$(grep '^DISCORD_ALLOWED_USERS=' "${REPO_ROOT}/.env" 2>/dev/null | tail -1 | cut -d'=' -f2- | cut -d',' -f1 | tr -d '[:space:]' | tr -d '"' | tr -d "'" || true)"
fi
if [[ -n "${_pq_daoyuan_actor_id}" && ! "${_pq_daoyuan_actor_id}" =~ ^[0-9]{10,25}$ ]]; then
  echo "   ⚠️  PAPER_QUEUE_DAOYUAN_ACTOR_ID 不是合法 Discord 雪花（${_pq_daoyuan_actor_id}），拒绝写入"
  echo "      请在 .env 显式设置 PAPER_QUEUE_DAOYUAN_ACTOR_ID"
  _pq_daoyuan_actor_id=""
fi
if [[ -n "${_pq_daoyuan_actor_id}" ]] && ! grep -q '^PAPER_QUEUE_DAOYUAN_ACTOR_ID=' "${REPO_ROOT}/.env" 2>/dev/null; then
  {
    echo "PAPER_QUEUE_DAOYUAN_ACTOR_ID=${_pq_daoyuan_actor_id}"
    echo "PAPER_QUEUE_DAOYUAN_ACTOR_NAME=${PAPER_QUEUE_DAOYUAN_ACTOR_NAME:-Owen}"
  } >> "${REPO_ROOT}/.env"
  echo "   🪪 已写入道元 paper-queue 归属 actor（取自 DISCORD_ALLOWED_USERS）"
fi
if [[ -z "${_pq_daoyuan_actor_id}" ]]; then
  echo "   ⚠️  PAPER_QUEUE_DAOYUAN_ACTOR_ID 未配置且 DISCORD_ALLOWED_USERS 为空；道元写入会被拒绝"
fi
if ! grep -q '^PAPER_QUEUE_DAOYUAN_ACTOR_NAME=' "${REPO_ROOT}/.env" 2>/dev/null; then
  echo "PAPER_QUEUE_DAOYUAN_ACTOR_NAME=${PAPER_QUEUE_DAOYUAN_ACTOR_NAME:-Owen}" >> "${REPO_ROOT}/.env"
fi

# ── 道元 paper-queue MCP（Bearer token → 服务端固定归属 Owen）──────
# 必须在 token/URL 生成之后、docker compose up 之前运行，否则首跑会因缺 token 移除条目。
DAOYUAN_PAPER_QUEUE_STATUS=""
if [[ -f "${DAOYUAN_CONFIG}" ]]; then
  if DAOYUAN_PAPER_QUEUE_STATUS="$(python3 "${REPO_ROOT}/scripts/ensure_hermes_paper_queue.py" \
      "${DAOYUAN_CONFIG}" --env-file "${REPO_ROOT}/.env" 2>&1)"; then
    echo "   📄 道元 paper-queue MCP: ${DAOYUAN_PAPER_QUEUE_STATUS}"
    if [[ "${DAOYUAN_PAPER_QUEUE_STATUS}" == *:no-token ]]; then
      echo "   ⚠️  PAPER_QUEUE_DAOYUAN_TOKEN 为空，道元 paper-queue 已停用"
    fi
  else
    echo "   ⚠️  道元 paper-queue MCP 注册失败: ${DAOYUAN_PAPER_QUEUE_STATUS}"
    DAOYUAN_PAPER_QUEUE_STATUS="failed"
  fi
fi

# ── 虾酱侧 paper-queue 已下架：清理 live 配置与安装副本 ────────────
# 代码（插件 / ensure 脚本）保留在仓库，作为未来道元多用户身份插件的参考实现。
# 这个标志必须**在这里之前**初始化：清理与其它配置注入都可能把它置 true。
OPENCLAW_RESTART_NEEDED=false
if [[ -f "${OPENCLAW_CONFIG}" ]] && grep -q "paper-queue" "${OPENCLAW_CONFIG}" 2>/dev/null; then
  _pq_cleanup="$(python3 "${REPO_ROOT}/scripts/remove_openclaw_paper_queue.py" "${OPENCLAW_CONFIG}" 2>&1 || true)"
  case "${_pq_cleanup}" in
    removed)
      OPENCLAW_RESTART_NEEDED=true
      echo "   🧹 已从虾酱配置移除 paper-queue（MCP / 插件 / skill）" ;;
    unchanged) : ;;
    *)
      echo "   ⚠️  虾酱 paper-queue 清理失败: ${_pq_cleanup}" ;;
  esac
fi
if [[ -d "${HOME}/.openclaw/skills/paper-queue" || -d "${HOME}/.openclaw/extensions/paper-queue-actor" ]]; then
  rm -rf "${HOME}/.openclaw/skills/paper-queue" "${HOME}/.openclaw/extensions/paper-queue-actor"
  OPENCLAW_RESTART_NEEDED=true
  echo "   🧹 已删除虾酱侧的 paper-queue skill/plugin 副本"
fi


# ── 注入 OpenClaw Discord Bot Token ─────────────────────────────
# 从 .env 读取 OPENCLAW_DISCORD_BOT_TOKEN，注入到 openclaw.json 的 channels.discord.token
OPENCLAW_DISCORD_BOT_TOKEN="${OPENCLAW_DISCORD_BOT_TOKEN:-$(grep '^OPENCLAW_DISCORD_BOT_TOKEN=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)}"
if [[ -f "${OPENCLAW_CONFIG}" && -n "${OPENCLAW_DISCORD_BOT_TOKEN:-}" ]]; then
  python3 -c "
import json, sys
with open('${OPENCLAW_CONFIG}') as f:
    d = json.load(f)
channels = d.setdefault('channels', {})
discord = channels.setdefault('discord', {})
old_token = discord.get('token', '')
discord['token'] = sys.argv[1]
if old_token != sys.argv[1]:
    with open('${OPENCLAW_CONFIG}', 'w') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print('updated')
else:
    print('unchanged')
" "${OPENCLAW_DISCORD_BOT_TOKEN}"
  echo "   🔑 已注入 OpenClaw Discord Bot Token"
fi

# ── 注入 OpenClaw 飞书凭据 ──────────────────────────────────────
# 从 .env 读取 OPENCLAW_FEISHU_APP_ID / OPENCLAW_FEISHU_APP_SECRET，
# 注入到 openclaw.json 的 channels.feishu 段。仅当两个值都非空时注入。
OPENCLAW_FEISHU_APP_ID="${OPENCLAW_FEISHU_APP_ID:-$(grep '^OPENCLAW_FEISHU_APP_ID=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)}"
OPENCLAW_FEISHU_APP_SECRET="${OPENCLAW_FEISHU_APP_SECRET:-$(grep '^OPENCLAW_FEISHU_APP_SECRET=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)}"
if [[ -f "${OPENCLAW_CONFIG}" && -n "${OPENCLAW_FEISHU_APP_ID:-}" && -n "${OPENCLAW_FEISHU_APP_SECRET:-}" ]]; then
  python3 -c "
import json, sys
with open('${OPENCLAW_CONFIG}') as f:
    d = json.load(f)
channels = d.setdefault('channels', {})
feishu = channels.setdefault('feishu', {})
old_id = feishu.get('appId', '')
old_secret = feishu.get('appSecret', '')
feishu['appId'] = sys.argv[1]
feishu['appSecret'] = sys.argv[2]
if old_id != sys.argv[1] or old_secret != sys.argv[2]:
    with open('${OPENCLAW_CONFIG}', 'w') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print('updated')
else:
    print('unchanged')
" "${OPENCLAW_FEISHU_APP_ID}" "${OPENCLAW_FEISHU_APP_SECRET}"
  echo "   🔑 已注入 OpenClaw 飞书凭据"
fi

# ── 注入 OpenClaw 钉钉凭据 ──────────────────────────────────────
# 从 .env 读取 OPENCLAW_DINGTALK_CLIENT_ID / OPENCLAW_DINGTALK_CLIENT_SECRET，
# 注入到 openclaw.json 的 channels.dingtalk-connector 段。仅当两个值都非空时注入。
OPENCLAW_DINGTALK_CLIENT_ID="${OPENCLAW_DINGTALK_CLIENT_ID:-$(grep '^OPENCLAW_DINGTALK_CLIENT_ID=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)}"
OPENCLAW_DINGTALK_CLIENT_SECRET="${OPENCLAW_DINGTALK_CLIENT_SECRET:-$(grep '^OPENCLAW_DINGTALK_CLIENT_SECRET=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)}"
if [[ -f "${OPENCLAW_CONFIG}" && -n "${OPENCLAW_DINGTALK_CLIENT_ID:-}" && -n "${OPENCLAW_DINGTALK_CLIENT_SECRET:-}" ]]; then
  python3 -c "
import json, sys
with open('${OPENCLAW_CONFIG}') as f:
    d = json.load(f)
channels = d.setdefault('channels', {})
dt = channels.setdefault('dingtalk-connector', {})
accounts = dt.setdefault('accounts', {})
acct = accounts.setdefault('__default__', {})
old_id = acct.get('clientId', '')
old_secret = acct.get('clientSecret', '')
acct['clientId'] = sys.argv[1]
acct['clientSecret'] = sys.argv[2]
if old_id != sys.argv[1] or old_secret != sys.argv[2]:
    with open('${OPENCLAW_CONFIG}', 'w') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print('updated')
else:
    print('unchanged')
" "${OPENCLAW_DINGTALK_CLIENT_ID}" "${OPENCLAW_DINGTALK_CLIENT_SECRET}"
  echo "   🔑 已注入 OpenClaw 钉钉凭据"
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

# ── 论文清单已交给道元；虾酱侧不再注入 paper-queue ─────────────
# 清理逻辑见上面的 remove_openclaw_paper_queue.py。旧的 ensure 脚本与插件源码
# 保留在仓库，作为未来道元多用户身份插件的参考实现。

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

# ── 拉取 OpenClaw 镜像（.env 里钉住的 tag，不是 latest）────────
# 用 sed 而非 grep|cut：① 取「最后一个冒号之后」才对（registry 可能带端口，
# 且正常只有一个冒号，cut -f3 会取到空串）；② sed 无匹配时退出码仍是 0，
# 不会在 `set -o pipefail` 下把管道判成失败。
OPENCLAW_PIN="$(sed -n 's/^OPENCLAW_IMAGE=.*:\(.*\)$/\1/p' "${REPO_ROOT}/.env" 2>/dev/null | head -1)"
echo "🦞 拉取 OpenClaw 镜像（pin: ${OPENCLAW_PIN:-未设置}）..."
set +e
PULL_OUTPUT=$(docker compose pull openclaw-gateway 2>&1)
PULL_EXIT=$?
set -e
if [[ "${PULL_EXIT}" -ne 0 ]]; then
  echo "   ⚠️  OpenClaw 镜像拉取失败，将使用本地缓存继续启动"
  echo "${PULL_OUTPUT}" | tail -3 | sed 's/^/   │  /'
else
  echo "   ✅ OpenClaw 镜像已更新"
fi
echo ""

# ── OpenClaw：版本可见性 + 配置兼容性检查 ─────────────────────
OPENCLAW_DOCKER_VERSION=""
if docker compose config 2>/dev/null | grep -q "openclaw-gateway"; then
  OPENCLAW_DOCKER_VERSION=$(docker compose run --rm --entrypoint "node" openclaw-gateway openclaw.mjs --version 2>/dev/null | tail -1 || echo "unknown")
fi

echo ""
echo "🦞 OpenClaw 版本检查"
echo "   Docker 网关 (镜像): ${OPENCLAW_DOCKER_VERSION:-未安装}"

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

# ── 复制 mylibrary 源码到 build context（本地优先）──────────────
# 容器 build 时 Dockerfile 会优先使用本地源码进行 pip install。
# 如果本地没有 mylibrary，Dockerfile 会从 GitHub clone。
MYLIBRARY_SRC="${HOME}/code/mylibrary"
MYLIBRARY_CTX="${REPO_ROOT}/docker/hermes/.mylibrary-src"
if [[ -d "${MYLIBRARY_SRC}" ]]; then
    echo "   📚 mylibrary 本地源码已检测到，复制到 build context..."
    rsync -a --delete \
        --exclude '.git' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.venv' \
        --exclude 'venv' \
        --exclude 'node_modules' \
        --exclude '.mypy_cache' \
        --exclude '.pytest_cache' \
        --exclude '*.egg-info' \
        "${MYLIBRARY_SRC}/" "${MYLIBRARY_CTX}/"
else
    # Keep empty dir so Docker COPY doesn't fail — git fallback in Dockerfile handles it
    mkdir -p "${MYLIBRARY_CTX}"
    echo "   📚 mylibrary 本地未找到，build 时将使用 GitHub clone"
fi

# ── 复制 mylibrary 源码到 zotero-mcp build context（本地优先）────
ZOTERO_MCP_CTX="${REPO_ROOT}/docker/zotero-mcp/.mylibrary-src"
if [[ -d "${MYLIBRARY_SRC}" ]]; then
    echo "   📚 zotero-mcp: mylibrary 本地源码已检测到，复制到 build context..."
    rsync -a --delete \
        --exclude '.git' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.venv' \
        --exclude 'venv' \
        --exclude 'node_modules' \
        --exclude '.mypy_cache' \
        --exclude '.pytest_cache' \
        --exclude '*.egg-info' \
        "${MYLIBRARY_SRC}/" "${ZOTERO_MCP_CTX}/"
else
    # Keep empty dir so Docker COPY doesn't fail — git fallback in Dockerfile handles it
    mkdir -p "${ZOTERO_MCP_CTX}"
    echo "   📚 zotero-mcp: mylibrary 本地未找到，build 时将使用 GitHub clone"
fi

# ── repo-scanner MCP 每用户 Bearer ───────────────────────────────
# 服务端 GIT_STATS_API_KEYS 与三个消费者 key 同一来源；Hermes 配置里的
# headers 也在启动前幂等补上（Hermes 只在启动时读配置）。
_hermes_auth_changed=false
_auth_helper="${REPO_ROOT}/scripts/ensure_issue_watcher_auth.py"
if [[ ! -f "${_auth_helper}" ]]; then
  echo "❌ 缺少 ${_auth_helper}；repo-scanner MCP 会在无认证状态运行，拒绝启动"
  echo "   从仓库完整检出后重试"
  exit 1
fi
# 认证是硬门槛：helper 失败或没写出 key map 就不启动。旧写法用 `|| true` +
# 未锚定的 grep，模板里的注释行 `# GIT_STATS_API_KEYS=...` 会让检查假绿，
# 结果 MCP 在整个 myopenclaw-net 上无认证裸奔（任何容器可读私有 issue）。
if ! _auth_out="$(python3 "${_auth_helper}" 2>&1)"; then
  echo "${_auth_out}"
  echo "❌ issue-watcher MCP 认证配置失败；拒绝启动（避免无认证暴露私有 issue）"
  exit 1
fi
echo "${_auth_out}"
if ! grep -qE '^GIT_STATS_API_KEYS=.' "${REPO_ROOT}/.env" 2>/dev/null; then
  echo "❌ .env 缺少 GIT_STATS_API_KEYS；拒绝启动（避免无认证暴露私有 issue）"
  exit 1
fi
# 天一用自己的 --env-file；容器 env 在创建时冻结。key 轮换后只改文件不重建，
# 天一仍拿旧 key，表现为工具调用 401（entrypoint 探 /health 是放行的，看不出来）。
# 重建必须放在主栈 up 之后：服务端先加载新 key map，天一再用新 key 连接。
_tianyi_key_changed=false
if docker inspect openclaw-tianyi --format '{{.State.Status}}' 2>/dev/null | grep -q running; then
  if grep -q "synced tianyi key" <<< "${_auth_out}"; then
    _tianyi_key_changed=true
  else
    # 自愈：上一次重建失败后，文件可能已同步但容器仍持旧 key。
    _tianyi_expected="$(grep -E '^TIANYI_BOT_REPO_SCANNER_KEY=' "${REPO_ROOT}/.env.tianyi-bot" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    _tianyi_actual="$(docker inspect openclaw-tianyi --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep '^TIANYI_BOT_REPO_SCANNER_KEY=' | tail -1 | cut -d= -f2- || true)"
    if [[ -n "${_tianyi_expected}" && "${_tianyi_expected}" != "${_tianyi_actual}" ]]; then
      _tianyi_key_changed=true
    fi
  fi
fi
_hermes_helper="${REPO_ROOT}/scripts/ensure_hermes_repo_scanner_auth.py"
if [[ ! -f "${_hermes_helper}" ]]; then
  echo "❌ 缺少 ${_hermes_helper}；Hermes 会因缺 Bearer header 调用 401，拒绝启动"
  exit 1
fi
if ! _hermes_auth_out="$(python3 "${_hermes_helper}" 2>&1)"; then
  echo "${_hermes_auth_out}"
  echo "❌ Hermes repo-scanner Bearer header 配置失败；拒绝启动"
  exit 1
fi
echo "${_hermes_auth_out}"
if grep -q "added repo-scanner Bearer header" <<< "${_hermes_auth_out}"; then
  _hermes_auth_changed=true
fi

echo "🚀 启动服务..."
if [[ -n "${BACKUP_ROOT:-}" ]]; then
  echo "   备份目录: ${BACKUP_ROOT}"
fi
# 记录 up -d 之前的容器启动时间：用来判断这次 up 是否**重建**了容器。
# 重建过的容器在启动时已经读过新配置，不需要再重启。这个判断不是洁癖 ——
# 2026-09-16 实测踩过：OpenClaw 2026.9.1 会在这个 state 目录上跑一次启动迁移，
# 迁移期间重启会把它打断、下次从头再来，表现是网关**看起来卡死**（既不绑端口、
# 也不报错，docker logs 只有几行），静置几分钟才自行恢复。
_openclaw_started_before="$(docker inspect openclaw-gateway --format '{{.State.StartedAt}}' 2>/dev/null || true)"
_hermes_started_before="$(docker inspect hermes --format '{{.State.StartedAt}}' 2>/dev/null || true)"
_coder_started_before="$(docker inspect hermes-coder --format '{{.State.StartedAt}}' 2>/dev/null || true)"
_daoyuan_started_before="$(docker inspect hermes-daoyuan --format '{{.State.StartedAt}}' 2>/dev/null || true)"

docker compose up -d ${BUILD_FLAG}
echo "✅ 服务已启动"

if [[ "${_tianyi_key_changed}" == "true" ]]; then
  echo "🔄 天一 key 已轮换，重建 openclaw-tianyi 使新 key 生效..."
  if "${REPO_ROOT}/scripts/start-tianyi-bot.sh" >/dev/null 2>&1; then
    echo "   ✅ 天一端已重建"
  else
    echo "   ⚠️  天一端重建失败；请手动运行 ./scripts/start-tianyi-bot.sh（否则天一 repo-scanner 调用会 401）"
  fi
fi

if [[ "${_hermes_auth_changed}" == "true" ]]; then
  echo "🔄 Hermes repo-scanner auth 已变更，重启 hermes 服务使其生效..."
  if docker compose restart hermes hermes-coder hermes-finance hermes-daoyuan >/dev/null 2>&1; then
    echo "   ✅ 已重启"
  else
    echo "   ⚠️  重启失败，请手动: docker compose restart hermes hermes-coder hermes-finance hermes-daoyuan"
  fi
fi

# server-monitoring MCP 配置变更不会热加载；容器若没在本次 up 中重建，需要重启一次。
# _hermes_auth_changed 已经重启过全部 hermes 容器时跳过，避免重复重启。
restart_hermes_for_mcp() {
  local container="$1" status="$2" before="$3" label="$4"
  [[ "${status}" == written* || "${status}" == removed* ]] || return 0
  local after
  after="$(docker inspect "${container}" --format '{{.State.StartedAt}}' 2>/dev/null || true)"
  if [[ "${before}" != "${after}" ]]; then
    echo "   ℹ️  ${container} 已在本次 up 中创建/重建，MCP 配置随启动生效"
    return 0
  fi
  echo "🔄 ${label} MCP 配置有变更，重启 ${container} 使其重新加载..."
  if docker compose restart "${container}" >/dev/null 2>&1; then
    echo "   ✅ ${container} 已重启"
  else
    echo "   ⚠️  ${container} 重启失败，请手动执行: docker compose restart ${container}"
  fi
}
if [[ "${_hermes_auth_changed}" != "true" ]]; then
  restart_hermes_for_mcp hermes "${DEFAULT_MCP_STATUS}" "${_hermes_started_before}" "爱玛士"
  restart_hermes_for_mcp hermes-coder "${CODER_MCP_STATUS}" "${_coder_started_before}" "爱码士"
fi

# 道元的 cron.wrap_response 是投递时读取的 profile 配置；容器没在本次 up 中重建时
# 重启一次以确保重新加载（与上面 MCP 配置同一套路）。
if [[ "${_hermes_auth_changed}" != "true" && "${DAOYUAN_CRON_STATUS}" == written* ]]; then
  _daoyuan_started_after="$(docker inspect hermes-daoyuan --format '{{.State.StartedAt}}' 2>/dev/null || true)"
  if [[ "${_daoyuan_started_before}" != "${_daoyuan_started_after}" ]]; then
    echo "   ℹ️  hermes-daoyuan 已在本次 up 中创建/重建，cron 配置随启动生效"
  else
    echo "🔄 道元 cron.wrap_response 有变更，重启 hermes-daoyuan 使其生效..."
    if docker compose restart hermes-daoyuan >/dev/null 2>&1; then
      echo "   ✅ hermes-daoyuan 已重启"
    else
      echo "   ⚠️  hermes-daoyuan 重启失败，请手动执行: docker compose restart hermes-daoyuan"
    fi
  fi
fi

# 道元 paper-queue：MCP 配置或 skill symlink 有变化时同样需要重启才生效。
_daoyuan_pq_changed=false
if [[ "${DAOYUAN_PAPER_QUEUE_STATUS}" == written* || "${DAOYUAN_PAPER_QUEUE_STATUS}" == removed* ]]; then
  _daoyuan_pq_changed=true
fi
if [[ "${PQ_SKILL_CHANGED}" == "true" ]]; then
  _daoyuan_pq_changed=true
fi
if [[ "${_hermes_auth_changed}" != "true" && "${_daoyuan_pq_changed}" == "true" ]]; then
  restart_hermes_for_mcp hermes-daoyuan "written:paper-queue" \
    "${_daoyuan_started_before}" "道元"
fi

# mcp.servers / plugins.load 的配置变更不会热加载，必须重启网关才生效
# （实测日志：config change requires gateway restart (plugins.load)）。
if [[ "${OPENCLAW_RESTART_NEEDED:-false}" == "true" ]]; then
  _openclaw_started_after="$(docker inspect openclaw-gateway --format '{{.State.StartedAt}}' 2>/dev/null || true)"
  # 不相等 = 容器在本次 up 里才出现（首次部署）或被重建 —— 两种情况启动时都已读到新配置。
  # 注意不能要求 before 非空：首次部署时它本来就是空的，早期版本这样写过，结果
  # 在"刚创建完立刻重启"，正好撞上 OpenClaw 的启动迁移。
  if [[ "${_openclaw_started_before}" != "${_openclaw_started_after}" ]]; then
    echo "   ℹ️  网关已在本次 up 中创建/重建，新配置已随启动生效，跳过多余重启"
  else
    echo "🔄 论文清单有变更（配置或代码），重启 OpenClaw 网关使其生效..."
    if docker compose restart openclaw-gateway >/dev/null 2>&1; then
      echo "   ✅ 网关已重启"
    else
      echo "   ⚠️  网关重启失败，请手动执行: docker compose restart openclaw-gateway"
    fi
  fi
fi

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

# 从 .env 读取 FEISHU_HOME_CHANNEL 和 LARK_USER_OPEN_ID（不能 source .env，cron 表达式会破坏 bash）
FEISHU_HOME_CHANNEL="${FEISHU_HOME_CHANNEL:-$(grep '^FEISHU_HOME_CHANNEL=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)}"
LARK_USER_OPEN_ID="${LARK_USER_OPEN_ID:-$(grep '^LARK_USER_OPEN_ID=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || true)}"

# ── 注册 Morning Triage v2 Cron Job ───────────────────────────
# 仅在 cron_mode=allow 时注册
if [[ -f "${HERMES_CONFIG}" ]] && grep -q 'cron_mode: allow' "${HERMES_CONFIG}" 2>/dev/null; then
  # 等待 Hermes 就绪
  for i in $(seq 1 15); do
    if docker compose ps hermes 2>/dev/null | grep -q 'Up'; then break; fi
    sleep 2
  done
  if docker compose ps hermes 2>/dev/null | grep -q 'Up'; then
    # 读取飞书私聊推送目标：优先 LARK_USER_OPEN_ID，fallback 到 FEISHU_HOME_CHANNEL
    FEISHU_OPEN_ID="${LARK_USER_OPEN_ID:-${FEISHU_HOME_CHANNEL:-}}"
    if [[ -n "${FEISHU_OPEN_ID:-}" ]]; then
      DELIVER="feishu:${FEISHU_OPEN_ID}"

      # ── Daily Command Center（TDAI 记忆 + 健康信号 + 活跃场景）──
      EXISTING=$(docker compose exec -T hermes "${HERMES_BIN}" cron list 2>/dev/null | grep -c "Daily Command Center" || true)
      if [ "${EXISTING:-0}" -lt 1 ]; then
        docker compose exec -T hermes "${HERMES_BIN}" cron create \
          "50 23 * * *" \
          "执行 morning-triage-v2 技能：查询 TDAI Memory Gateway (http://tdai-memory:8420) 获取昨日记忆和活跃场景，汇总系统健康信号，输出 Daily Command Center 晨间简报。" \
          --deliver "${DELIVER}" \
          --name "Daily Command Center" 2>/dev/null && \
          echo "   📋 Daily Command Center cron job 已注册 (每日 7:50 北京)" || \
          echo "   ⚠️  Daily Command Center cron job 注册失败"
      else
        echo "   📋 Daily Command Center cron job 已存在，跳过"
      fi

      # ── 工作日晨间简报（待办事务 + 未读邮件，周一至周六推送）────────
      # 日期预脚本：cron --script 只认 ~/.hermes/scripts/ 下的路径，stdout 每轮注入 prompt。
      # ⚠️ 只补缺、不覆盖 —— ~/.hermes 下是 agent 自维护的权威副本，仓库版只是版本源/种子
      #    （2026-10-12 symlink 覆盖事故、2026-10-13 日期误判事故的教训）。
      if [ ! -f "${HOME}/.hermes/scripts/cst_today.py" ]; then
        install -m 644 "${REPO_ROOT}/skills/morning-briefing/scripts/cst_today.py" \
          "${HOME}/.hermes/scripts/cst_today.py" && \
          echo "   📋 日期预脚本 cst_today.py 已就位 (~/.hermes/scripts/)" || \
          echo "   ⚠️  日期预脚本安装失败（job 的 --script 将找不到它）"
      fi
      EXISTING=$(docker compose exec -T hermes "${HERMES_BIN}" cron list 2>/dev/null | grep -c "工作日晨间简报" || true)
      if [ "${EXISTING:-0}" -lt 1 ]; then
        # prompt 全文种子（含「邮件铁律」与「第一步：先查日期」）——新部署的唯一来源；
        # 已存在的 job 不会被覆盖，线上 prompt 的权威副本在 ~/.hermes/cron/jobs.json。
        MORNING_BRIEFING_PROMPT=$(cat <<'MORNING_BRIEFING_PROMPT_EOF'
执行 morning-briefing 技能生成晨间简报。收集待办事务和未读邮件，按 SKILL.md 中的规则筛选和呈现。

⛔ 邮件铁律（用户 2026-09-15 明确要求，不可妥协）：
「邮件规整的时候读所有未读邮件，不要嫌麻烦，我不能错过任何一个未读的邮件。」

- 必须一次性拉全 DLUT + QQ + Outlook 三个邮箱的**所有未读**，用：
  python3 /opt/data/skills/devops/morning-briefing/scripts/unread_report.py
- 严禁用 `himalaya envelope list --page-size 15` 这类「最近N封」的窗口代替未读筛选——那跟已读/未读无关，会漏掉更早的未读邮件。
- 简报里先把三个邮箱各自的**未读总数**和合计报出来，再逐条列出全部「需关注」邮件（含历史积压，积压的也要列，可压缩但不能省略）。推送类按「发件人 × N」聚合即可。
- editorialmanager（审稿系统）的邮件不算推送，必须逐条列出。

⛔ 第一步（不可跳过，不可凭直觉）：确认北京时间今天几号、星期几

```bash
python3 /opt/data/skills/devops/morning-briefing/scripts/today.py
python3 /opt/data/skills/devops/morning-briefing/scripts/today.py --check-briefing-day   # 退出码 0=简报日（周一至周六）1=休息日（周日）
echo $?
```

- 退出码 **0** → 今天（北京时间）是简报日（周一至周六），**必须**正常采集并发送简报。
- 退出码 **1**（仅周日）→ 才回「今天是周末，没有晨间简报。好好休息！🌴」，不做数据采集。
- 简报标题的日期用脚本输出的北京时间日期；事务「已逾期/今日截止/本周」的分档也用它。
- 容器时区是 UTC，本任务在北京时间 07:30 触发（= 前一天 23:30 UTC）。**千万不要自己推算星期几。**
  2026-10-13 07:30 CST 那期就是代理没查日期、凭感觉断言「今天是周末」，把整期简报吞了，用户当天追问。
- 判定完把脚本输出原样贴一句作为凭据（例如「今天：2026年10月13日 星期二 ← 工作日」）。
MORNING_BRIEFING_PROMPT_EOF
)
        docker compose exec -T hermes "${HERMES_BIN}" cron create \
          "30 23 * * 0-5" \
          "${MORNING_BRIEFING_PROMPT}" \
          --script cst_today.py \
          --deliver "${DELIVER}" \
          --name "工作日晨间简报" 2>/dev/null && \
          echo "   📋 工作日晨间简报 cron job 已注册 (北京周一至周六 7:30)" || \
          echo "   ⚠️  工作日晨间简报 cron job 注册失败"
      else
        echo "   📋 工作日晨间简报 cron job 已存在，跳过"
      fi

      # ── daily-dev-report（研发贡献日报）────────────────────────────
      EXISTING=$(docker compose exec -T hermes "${HERMES_BIN}" cron list 2>/dev/null | grep -c "daily-dev-report" || true)
      if [ "${EXISTING:-0}" -lt 1 ]; then
        docker compose exec -T hermes "${HERMES_BIN}" cron create \
          "55 23 * * *" \
          "执行 daily-dev-report 技能：调用 MCP get_daily_report 获取昨日研发贡献数据，DeepSeek 深度分析，输出每日研发贡献报告。" \
          --deliver "${DELIVER}" \
          --name "daily-dev-report" 2>/dev/null && \
          echo "   📋 daily-dev-report cron job 已注册 (每日 7:55 北京)" || \
          echo "   ⚠️  daily-dev-report cron job 注册失败"
      else
        echo "   📋 daily-dev-report cron job 已存在，跳过"
      fi

      # ── server-monitoring-digest（服务器监控日报）──────────────────
      # 默认 profile 爱玛士 → 飞书私聊；coder profile 爱码士 → 预报系统群。
      # 两条 job 各自跑独立 agent 回合；08:30 北京 = 30 0 * * * UTC。
      MONITORING_URL_SET="$(grep -c '^SERVER_MONITORING_MCP_URL=.' "${REPO_ROOT}/.env" 2>/dev/null || true)"
      MONITORING_TOKEN_SET="$(grep -c '^SERVER_MONITORING_MCP_TOKEN=.' "${REPO_ROOT}/.env" 2>/dev/null || true)"
      MONITORING_DIGEST_PROMPT="执行 server-monitoring-digest 技能：用 server-monitoring MCP 汇总当前服务器整体情况（服务器状态与告警 / Prometheus 目标 / GFS 流水线 / 各项目运行时与最近构建），按证据纪律如实报告：UNKNOWN≠健康、缺失≠0；先给结论与需要关注的异常，再给细节，全文控制在 500 字内。只读 MCP，不要写文件或运行脚本；最终回复只包含日报正文，不要字数说明或工具调用说明。"
      if [[ "${MONITORING_URL_SET:-0}" -gt 0 && "${MONITORING_TOKEN_SET:-0}" -gt 0 ]]; then
        if [[ "${DEFAULT_MCP_STATUS}" != failed* && "${DEFAULT_MCP_STATUS}" != conflict* && "${DEFAULT_MCP_STATUS}" != removed* ]]; then
          EXISTING=$(docker compose exec -T hermes "${HERMES_BIN}" cron list 2>/dev/null | grep -c "server-monitoring-digest" || true)
          if [ "${EXISTING:-0}" -lt 1 ]; then
            docker compose exec -T hermes "${HERMES_BIN}" cron create \
              "30 0 * * *" \
              "${MONITORING_DIGEST_PROMPT}" \
              --deliver "${DELIVER}" \
              --name "server-monitoring-digest" 2>/dev/null && \
              echo "   📋 server-monitoring-digest cron job 已注册 (每日 8:30 北京，爱玛士私聊)" || \
              echo "   ⚠️  server-monitoring-digest cron job 注册失败"
          else
            echo "   📋 server-monitoring-digest cron job 已存在，跳过"
          fi
        else
          echo "   ⚠️  爱玛士 server-monitoring MCP 未就绪（${DEFAULT_MCP_STATUS}），跳过服务器日报注册"
        fi

        MONITORING_GROUP_CHAT_ID="$(grep '^MONITORING_DIGEST_GROUP_CHAT_ID=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' || true)"
        if [[ -n "${MONITORING_GROUP_CHAT_ID}" ]]; then
          if [[ "${CODER_MCP_STATUS}" != failed* && "${CODER_MCP_STATUS}" != conflict* && "${CODER_MCP_STATUS}" != removed* ]]; then
            EXISTING_CODER=$(docker compose exec -T hermes-coder "${HERMES_BIN}" -p coder cron list 2>/dev/null | grep -c "server-monitoring-digest" || true)
            if [ "${EXISTING_CODER:-0}" -lt 1 ]; then
              docker compose exec -T hermes-coder "${HERMES_BIN}" -p coder cron create \
                "30 0 * * *" \
                "${MONITORING_DIGEST_PROMPT}" \
                --deliver "feishu:${MONITORING_GROUP_CHAT_ID}" \
                --name "server-monitoring-digest" 2>/dev/null && \
                echo "   📋 server-monitoring-digest coder cron job 已注册 (每日 8:30 北京，爱码士→预报系统群)" || \
                echo "   ⚠️  server-monitoring-digest coder cron job 注册失败"
            else
              echo "   📋 server-monitoring-digest coder cron job 已存在，跳过"
            fi
          else
            echo "   ⚠️  爱码士 server-monitoring MCP 未就绪（${CODER_MCP_STATUS}），跳过群日报注册"
          fi
        else
          echo "   ⚠️  MONITORING_DIGEST_GROUP_CHAT_ID 未设置，跳过爱码士→预报系统群的服务器日报注册"
        fi
      else
        echo "   ⚠️  SERVER_MONITORING_MCP_URL/TOKEN 未配置，跳过 server-monitoring-digest cron job 注册"
      fi

      # ── yuque-daily-digest（语雀每日变更日报，可选）────────────────
      # 需 .env 配置：YUQUE_DAILY_PUSH_REPOS（跟踪的知识库显示名，逗号分隔）
      #             + YUQUE_MCP_URL + MCP_YUQUE_MCP_API_KEY（语雀 MCP 前置）
      # 注意：MCP key 必须放仓库根 .env 并经 compose 注入容器环境；
      #       ~/.hermes/.env 里的同名变量不参与 Hermes MCP 客户端 ${VAR} 展开。
      YUQUE_DAILY_PUSH_REPOS="$(grep '^YUQUE_DAILY_PUSH_REPOS=' "${REPO_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' || true)"
      YUQUE_MCP_URL_SET="$(grep -c '^YUQUE_MCP_URL=' "${REPO_ROOT}/.env" 2>/dev/null || true)"
      YUQUE_MCP_KEY_SET="$(grep -c '^MCP_YUQUE_MCP_API_KEY=' "${REPO_ROOT}/.env" 2>/dev/null || true)"
      if [[ -n "${YUQUE_DAILY_PUSH_REPOS}" && "${YUQUE_MCP_URL_SET:-0}" -gt 0 && "${YUQUE_MCP_KEY_SET:-0}" -gt 0 ]]; then
        # 模板前缀作为 prompt 版本签名：改动模板后重跑 start.sh 会触发 job 更新
        YUQUE_PROMPT_PREFIX="执行 yuque-daily-digest 技能：先用 MCP list_repos 解析知识库显示名对应的 namespace"
        YUQUE_PROMPT="${YUQUE_PROMPT_PREFIX}，再对知识库（${YUQUE_DAILY_PUSH_REPOS}）逐一调用 MCP get_change_summary 获取服务端最新变更报告，对重点变更文档读取正文做中文摘要，生成语雀变更日报输出到最终回复。若所有知识库均无变更则按技能规则静默。"
        EXISTING=$(docker compose exec -T hermes "${HERMES_BIN}" cron list 2>/dev/null | grep -c "yuque-daily-digest" || true)
        if [ "${EXISTING:-0}" -lt 1 ]; then
          docker compose exec -T hermes "${HERMES_BIN}" cron create \
            "10 0 * * *" \
            "${YUQUE_PROMPT}" \
            --deliver "${DELIVER}" \
            --name "yuque-daily-digest" 2>/dev/null && \
            echo "   📋 yuque-daily-digest cron job 已注册 (每日 8:10 北京)" || \
            echo "   ⚠️  yuque-daily-digest cron job 注册失败（可去掉 2>/dev/null 重试查看原因）"
        else
          # 已存在：比对 prompt 模板版本 + 知识库列表，任一变更则更新 job
          PROMPT_MATCHES=$(python3 -c "
import json, sys
try:
    data = json.load(open('${HOME}/.hermes/cron/jobs.json'))
except Exception:
    sys.exit(1)
job = next((j for j in data.get('jobs', []) if j.get('name') == 'yuque-daily-digest'), None)
p = job.get('prompt', '') if job else ''
print('ok' if p.startswith('${YUQUE_PROMPT_PREFIX}') and '${YUQUE_DAILY_PUSH_REPOS}' in p else 'stale')
" 2>/dev/null || echo stale)
          if [ "${PROMPT_MATCHES}" = "ok" ]; then
            echo "   📋 yuque-daily-digest cron job 已存在且配置一致，跳过"
          else
            JOB_ID=$(python3 -c "
import json
data = json.load(open('${HOME}/.hermes/cron/jobs.json'))
job = next((j for j in data.get('jobs', []) if j.get('name') == 'yuque-daily-digest'), None)
print(job.get('id', '') if job else '')
" 2>/dev/null || true)
            if [[ -n "${JOB_ID}" ]]; then
              docker compose exec -T hermes "${HERMES_BIN}" cron edit "${JOB_ID}" \
                --prompt "${YUQUE_PROMPT}" 2>/dev/null && \
                echo "   🔄 yuque-daily-digest cron job 已更新（知识库列表变更）" || \
                echo "   ⚠️  yuque-daily-digest cron job 更新失败"
            else
              echo "   ⚠️  找不到 yuque-daily-digest job id，跳过更新"
            fi
          fi
        fi
      elif [[ -z "${YUQUE_DAILY_PUSH_REPOS}" ]]; then
        echo "   ⚠️  YUQUE_DAILY_PUSH_REPOS 未设置，跳过语雀每日变更 cron job 注册（见 .env.example）"
      else
        echo "   ⚠️  YUQUE_MCP_URL 或 MCP_YUQUE_MCP_API_KEY 未在仓库根 .env 配置，跳过 yuque-daily-digest 注册（语雀 MCP 未就绪）"
      fi

    else
      echo "   ⚠️  LARK_USER_OPEN_ID 和 FEISHU_HOME_CHANNEL 都未设置，跳过 cron job 注册"
      echo "   在 .env 中至少设置一个（推荐 FEISHU_HOME_CHANNEL）"
    fi
  fi
else
  echo "   ⚠️  cron_mode 未启用，跳过 Hermes cron job 注册"
fi

# ── 道元 no-agent cron（dailyinfo 简报 / paper-queue 对账）────────────
# 两个 job 共用同一套幂等注册 + 漂移修正：schedule / script / no-agent / deliver
# 四要素任一漂移都会被改回。脚本 stdout 由 Hermes 原样投递，不经过 LLM；
# 空 stdout = 静默成功，非 0 退出 = 同一个 --deliver 目标收到 watchdog 告警。
# 07:30 / 07:20 北京 = 30 23 / 20 23 * * *（容器 UTC）。chat id 只放 .env。
DAOYUAN_DM_CHAT_ID="$(grep '^DAOYUAN_FEISHU_DM_CHAT_ID=' "${REPO_ROOT}/.env" 2>/dev/null | tail -1 | cut -d'=' -f2- | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' || true)"
DAILYINFO_URL_SET="$(grep -c '^DAILYINFO_MCP_URL=.' "${REPO_ROOT}/.env" 2>/dev/null || true)"
DAILYINFO_KEY_SET="$(grep -c '^DAILYINFO_MCP_KEY=.' "${REPO_ROOT}/.env" 2>/dev/null || true)"
PAPER_QUEUE_URL_SET="$(grep -c '^PAPER_QUEUE_MCP_URL=.' "${REPO_ROOT}/.env" 2>/dev/null || true)"

daoyuan_cron_job_id() {
  local name="$1"
  CRON_JOB_NAME="${name}" python3 - <<'PY' 2>/dev/null || true
import json, os
path = os.path.expanduser("~/.hermes/profiles/daoyuan/cron/jobs.json")
try:
    data = json.load(open(path))
except Exception:
    raise SystemExit(0)
job = next((j for j in data.get("jobs", []) if j.get("name") == os.environ["CRON_JOB_NAME"]), None)
print(job.get("id", "") if job else "")
PY
}

remove_daoyuan_script_cron() {
  local name="$1"
  if [[ -z "$(daoyuan_cron_job_id "${name}")" ]]; then
    return 0
  fi
  docker compose exec -T hermes-daoyuan "${HERMES_BIN}" -p daoyuan cron remove "${name}" 2>/dev/null && \
    echo "   🗑️  ${name} cron job 已移除（配置已停用）" || \
    echo "   ⚠️  ${name} cron job 移除失败"
}

ensure_daoyuan_script_cron() {
  local name="$1" schedule="$2" script="$3" chat_id="$4" label="$5"
  local job_id drift

  job_id="$(daoyuan_cron_job_id "${name}")"

  if [[ -z "${job_id}" ]]; then
    docker compose exec -T hermes-daoyuan "${HERMES_BIN}" -p daoyuan cron create \
      "${schedule}" \
      --script "${script}" \
      --no-agent \
      --deliver "feishu:${chat_id}" \
      --name "${name}" 2>/dev/null && \
      echo "   📋 ${name} cron job 已注册（${label}）" || \
      echo "   ⚠️  ${name} cron job 注册失败（可去掉 2>/dev/null 重试查看原因）"
    return
  fi

  # 已存在：比对 schedule / script / no-agent / deliver，漂移则修正。
  drift="$(CRON_JOB_NAME="${name}" CRON_SCHEDULE="${schedule}" \
    CRON_SCRIPT="${script}" CRON_DELIVER="feishu:${chat_id}" python3 - <<'PY' 2>/dev/null || echo unknown
import json, os
path = os.path.expanduser("~/.hermes/profiles/daoyuan/cron/jobs.json")
try:
    data = json.load(open(path))
except Exception:
    print("unknown")
    raise SystemExit(0)
job = next((j for j in data.get("jobs", []) if j.get("name") == os.environ["CRON_JOB_NAME"]), None)
if not job:
    print("missing")
    raise SystemExit(0)
schedule = job.get("schedule")
expr = schedule.get("expr") if isinstance(schedule, dict) else schedule
deliver = job.get("deliver")
deliveries = deliver if isinstance(deliver, list) else [deliver]
ok = (
    expr == os.environ["CRON_SCHEDULE"]
    and os.path.basename(str(job.get("script") or "")) == os.environ["CRON_SCRIPT"]
    and bool(job.get("no_agent"))
    and os.environ["CRON_DELIVER"] in deliveries
)
print("ok" if ok else "drift")
PY
)"

  case "${drift}" in
    ok)
      echo "   📋 ${name} cron job 已存在且配置一致，跳过" ;;
    drift)
      docker compose exec -T hermes-daoyuan "${HERMES_BIN}" -p daoyuan cron edit "${job_id}" \
        --schedule "${schedule}" \
        --script "${script}" \
        --no-agent \
        --deliver "feishu:${chat_id}" 2>/dev/null && \
        echo "   🔄 ${name} cron job 已修正（配置漂移）" || \
        echo "   ⚠️  ${name} cron job 修正失败" ;;
    *)
      echo "   ⚠️  ${name} cron job 状态未知（jobs.json 不可读），保留现状" ;;
  esac
}

DAILYINFO_CONFIGURED=false
if [[ "${DAILYINFO_URL_SET:-0}" -ge 1 && "${DAILYINFO_KEY_SET:-0}" -ge 1 ]]; then
  DAILYINFO_CONFIGURED=true
fi
PAPER_QUEUE_CONFIGURED=false
if [[ "${PAPER_QUEUE_URL_SET:-0}" -ge 1 ]]; then
  PAPER_QUEUE_CONFIGURED=true
fi

if [[ -z "${DAOYUAN_DM_CHAT_ID}" ]]; then
  echo "   ⚠️  DAOYUAN_FEISHU_DM_CHAT_ID 未设置，跳过道元 cron job 注册（见 .env.example）"
elif [[ ! "${DAOYUAN_DM_CHAT_ID}" =~ ^oc_[0-9a-f]{32}$ ]]; then
  echo "   ⚠️  DAOYUAN_FEISHU_DM_CHAT_ID 格式不对（应为 oc_ + 32 位十六进制），跳过道元 cron job 注册"
elif [[ "${DAILYINFO_CONFIGURED}" != "true" && "${PAPER_QUEUE_CONFIGURED}" != "true" ]]; then
  echo "   ⚠️  dailyinfo 与 paper-queue 均未配置，跳过道元 cron job 注册"
  remove_daoyuan_script_cron "paper-queue-sync"
else
  DAOYUAN_READY=false
  for _ in $(seq 1 15); do
    if docker compose ps hermes-daoyuan 2>/dev/null | grep -q 'Up'; then
      DAOYUAN_READY=true
      break
    fi
    sleep 2
  done
  if [[ "${DAOYUAN_READY}" != "true" ]]; then
    echo "   ⚠️  hermes-daoyuan 未就绪，跳过道元 cron job 注册"
  else
    if [[ "${DAILYINFO_CONFIGURED}" != "true" ]]; then
      echo "   ⚠️  DAILYINFO_MCP_URL/KEY 未配置，跳过 dailyinfo-digest 注册"
    else
      ensure_daoyuan_script_cron "dailyinfo-digest" "30 23 * * *" \
        dailyinfo_digest.py "${DAOYUAN_DM_CHAT_ID}" "每日 7:30 北京 → 道元私聊"
    fi
    if [[ "${PAPER_QUEUE_CONFIGURED}" != "true" ]]; then
      echo "   ⚠️  PAPER_QUEUE_MCP_URL 未配置，跳过 paper-queue-sync 注册"
      remove_daoyuan_script_cron "paper-queue-sync"
    else
      ensure_daoyuan_script_cron "paper-queue-sync" "20 23 * * *" \
        paper_queue_sync.py "${DAOYUAN_DM_CHAT_ID}" "每日 7:20 北京 → 道元私聊"
    fi
  fi
fi

# ── 幂等初始化 Uptime Kuma 监控项 ──────────────────────────────
if docker compose ps uptime-kuma 2>/dev/null | grep -q 'Up'; then
  "${REPO_ROOT}/scripts/setup-uptime-kuma.sh" --quiet || true
fi

docker compose ps
