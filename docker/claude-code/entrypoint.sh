#!/usr/bin/env bash
# =============================================================
# Claude Code + cc-connect entrypoint
# Sets up config symlinks then launches cc-connect as main process.
# cc-connect manages Claude Code sessions internally.
# =============================================================
set -euo pipefail

# ── Symlink config dirs to host-mounted volumes ──────────────
# Claude Code reads config from $HOME/.claude/
mkdir -p /home/node/.config /root/.config
rm -rf /home/node/.claude /root/.claude
ln -sf /opt/claude-config /home/node/.claude
ln -sf /opt/claude-config /root/.claude

# gh CLI reads config from $HOME/.config/gh/
ln -sf /opt/gh-config /home/node/.config/gh
ln -sf /opt/gh-config /root/.config/gh

# cc-connect reads config from $HOME/.cc-connect/
rm -rf /home/node/.cc-connect /root/.cc-connect
ln -sf /opt/cc-config /home/node/.cc-connect
ln -sf /opt/cc-config /root/.cc-connect

# ── API key mapping: DeepSeek → Anthropic ─────────────────────
# Claude Code reads ANTHROPIC_API_KEY; map DEEPSEEK_API_KEY → ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY="${DEEPSEEK_API_KEY:-${ANTHROPIC_API_KEY:-}}"
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://api.deepseek.com/anthropic}"

# ── Git credential helper for private repos ──────────────────
if [ -n "${GITHUB_TOKEN:-}" ]; then
    cat > /opt/claude-code/git-credential-helper.sh << CREDEOF
#!/bin/bash
echo "username=oauth2"
echo "password=${GITHUB_TOKEN}"
CREDEOF
    chmod 700 /opt/claude-code/git-credential-helper.sh
    git config --global credential.helper /opt/claude-code/git-credential-helper.sh
fi

# ── uv self-update ───────────────────────────────────────────
uv self update --quiet 2>/dev/null || true

# ── Code 目录骨架（卷挂载后创建）─────────────────────────────
mkdir -p /home/node/code/opensource /home/node/code/OuyangWenyu /home/node/code/iHeadWater
chown -R node:node /home/node/code 2>/dev/null || true

# ── Launch cc-connect ─────────────────────────────────────────
# cc-connect is the main process; it manages Claude Code sessions
# and bridges to Feishu/DingTalk/Telegram etc.
echo "🚀 claude-code 容器启动"
echo "   📎 Claude Code 配置: /opt/claude-config"
echo "   📎 cc-connect 配置:  /opt/cc-config"
echo "   📎 gh CLI 配置:      /opt/gh-config"
echo "   🐍 Python:           $(python3 --version 2>/dev/null || echo 'N/A')"
echo "   📦 uv:               $(uv --version 2>/dev/null || echo 'N/A')"

# ── ECC bootstrap（首次运行自动安装，后续跳过）─────────────────
if [ ! -f /home/node/.claude/ecc/install-state.json ]; then
    echo "🔧 首次运行：安装 ECC (developer profile)..."
    cd /opt/ecc-seed \
        && node scripts/install-apply.js --target claude --profile developer
    echo "✅ ECC 安装完成"
fi

# ── 确保 marketplace + plugin 注册（幂等）────────────────────
node -e '
const fs = require("fs");
const path = "/home/node/.claude/settings.json";
let settings = {};
try { settings = JSON.parse(fs.readFileSync(path, "utf8")); } catch(e) {}
let changed = false;
if (!settings.extraKnownMarketplaces) {
    settings.extraKnownMarketplaces = {
        ecc: { source: { source: "github", repo: "affaan-m/everything-claude-code" } }
    };
    changed = true;
}
if (!settings.enabledPlugins) {
    settings.enabledPlugins = { "ecc@ecc": true };
    changed = true;
}
if (changed) {
    fs.writeFileSync(path, JSON.stringify(settings, null, 2) + "\n");
    console.log("🔧 settings.json: 已注册 ECC marketplace + plugin");
}
'

exec cc-connect
