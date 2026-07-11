#!/usr/bin/env bash
# =============================================================
# scripts/setup-openclaw-memory.sh
# 幂等安装虾酱 OpenClaw memory plugin（local 模式，独立 DB）。
#
# 虾酱 (Discord bot) 面向多用户 → 使用独立 SQLite 库
# ~/.openclaw/memory-tdai/（与个人体系 ~/.myagentdata/tdai-memory/ 物理隔离）
#
# 用法:
#   ./scripts/setup-openclaw-memory.sh           # 交互式
#   ./scripts/setup-openclaw-memory.sh --quiet   # 静默模式
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
QUIET="${1:-}"
OPENCLAW_JSON="${HOME}/.openclaw/openclaw.json"

log() {
    [[ "${QUIET}" == "--quiet" ]] && return
    echo "$@"
}

# ── 检查 OpenClaw JSON 是否存在 ───────────────────────────────
if [[ ! -f "${OPENCLAW_JSON}" ]]; then
    echo "❌ ${OPENCLAW_JSON} 不存在，请先初始化 OpenClaw" >&2
    exit 1
fi

# ── 幂等安装 plugin ──────────────────────────────────────────
log "📦 安装 TencentDB Agent Memory plugin..."

# 在 Docker 容器内运行 openclaw plugins install（遵守安全规则）
# local 模式：进程内 sqlite-vec，无需 Gateway
INSTALL_OUTPUT=$(docker compose run --rm --entrypoint "node" openclaw-gateway \
    openclaw.mjs plugins install @tencentdb-agent-memory/memory-tencentdb 2>&1) || true

if echo "${INSTALL_OUTPUT}" | grep -q 'already installed'; then
    log "   ✅ plugin 已安装，跳过"
else
    log "   📦 plugin 安装完成"
fi

# ── 配置 openclaw.json — 启用 local 模式 ─────────────────────
log ""
log "🔧 配置 openclaw.json..."

# 使用 python3 安全地修改 JSON（避免 sed 破坏 token 特殊字符）
python3 - "$OPENCLAW_JSON" << 'PYEOF'
import json, sys, os

path = sys.argv[1]

with open(path) as f:
    cfg = json.load(f)

changed = False

# Ensure plugins section exists
if "plugins" not in cfg:
    cfg["plugins"] = {}

# Configure memory-tencentdb plugin in local mode
plugin_key = "@tencentdb-agent-memory/memory-tencentdb"
if plugin_key not in cfg["plugins"]:
    cfg["plugins"][plugin_key] = {
        "enabled": True,
        "mode": "local",
        "dataDir": os.path.expanduser("~/.openclaw/memory-tdai")
    }
    changed = True
elif not cfg["plugins"][plugin_key].get("enabled", False):
    cfg["plugins"][plugin_key]["enabled"] = True
    changed = True

if changed:
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f"   ✅ plugin 已启用: {plugin_key}")
    print(f"   📂 数据目录: ~/.openclaw/memory-tdai/")
    print(f"   🔒 模式: local (sqlite-vec, 无外部依赖)")
    print(f"   🚫 与个人体系 (~/.myagentdata/tdai-memory/) 物理隔离")
else:
    print("   ✅ plugin 已配置，跳过")
PYEOF

log ""
log "✅ 虾酱 OpenClaw memory plugin 配置完成"
log ""
log "🔔 如需重启 OpenClaw 以加载新 plugin:"
log "   docker compose restart openclaw-gateway"
