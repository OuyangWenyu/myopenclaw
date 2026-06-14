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

# gitcode-cli reads config from $HOME/.gitcode/
rm -rf /home/node/.gitcode /root/.gitcode
ln -sf /opt/gitcode-config /home/node/.gitcode
ln -sf /opt/gitcode-config /root/.gitcode

# ── API key mapping: DeepSeek → Anthropic ─────────────────────
# Claude Code reads ANTHROPIC_API_KEY; map DEEPSEEK_API_KEY → ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY="${DEEPSEEK_API_KEY:-${ANTHROPIC_API_KEY:-}}"
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://api.deepseek.com/anthropic}"

# ── Git credential helpers for private repos ─────────────────
if [ -n "${GITHUB_TOKEN:-}" ]; then
    cat > /opt/claude-code/git-credential-github.sh << CREDEOF
#!/bin/bash
echo "username=oauth2"
echo "password=${GITHUB_TOKEN}"
CREDEOF
    chmod 700 /opt/claude-code/git-credential-github.sh
    git config --global credential.https://github.com.helper /opt/claude-code/git-credential-github.sh
fi

if [ -n "${GITCODE_TOKEN:-}" ]; then
    cat > /opt/claude-code/git-credential-gitcode.sh << CREDEOF
#!/bin/bash
echo "username=oauth2"
echo "password=${GITCODE_TOKEN}"
CREDEOF
    chmod 700 /opt/claude-code/git-credential-gitcode.sh
    git config --global credential.https://gitcode.com.helper /opt/claude-code/git-credential-gitcode.sh

    # gitcode-cli config（与 git credential helper 共用 GITCODE_TOKEN）
    mkdir -p /opt/gitcode-config
    cat > /opt/gitcode-config/config.json << GITCODEEOF
{
  "host": "gitcode.com",
  "token": "${GITCODE_TOKEN}"
}
GITCODEEOF
    chmod 600 /opt/gitcode-config/config.json
fi

# ── gitcode-cli skill（从 npm 全局安装路径同步到 Claude Code skills）──
mkdir -p /opt/claude-config/skills/gitcode
cp -r /usr/local/lib/node_modules/gitcode-cli/skills/gitcode-cli/* /opt/claude-config/skills/gitcode/

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
echo "   📎 gitcode-cli 配置:  /opt/gitcode-config"
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

// Extra marketplaces
if (!settings.extraKnownMarketplaces) {
    settings.extraKnownMarketplaces = {};
}
if (!settings.extraKnownMarketplaces.ecc) {
    settings.extraKnownMarketplaces.ecc = {
        source: { source: "github", repo: "affaan-m/everything-claude-code" }
    };
    changed = true;
}
if (!settings.extraKnownMarketplaces["pm-skills"]) {
    settings.extraKnownMarketplaces["pm-skills"] = {
        source: { source: "github", repo: "phuryn/pm-skills" }
    };
    changed = true;
}

// Enabled plugins
if (!settings.enabledPlugins) {
    settings.enabledPlugins = {};
}
if (!settings.enabledPlugins["ecc@ecc"]) {
    settings.enabledPlugins["ecc@ecc"] = true;
    changed = true;
}

const pmPlugins = [
    "pm-toolkit",
    "pm-product-strategy",
    "pm-product-discovery",
    "pm-market-research",
    "pm-data-analytics",
    "pm-marketing-growth",
    "pm-go-to-market",
    "pm-execution",
    "pm-ai-shipping"
];
for (const p of pmPlugins) {
    const key = p + "@pm-skills";
    if (!settings.enabledPlugins[key]) {
        settings.enabledPlugins[key] = true;
        changed = true;
    }
}

if (changed) {
    fs.writeFileSync(path, JSON.stringify(settings, null, 2) + "\n");
    console.log("🔧 settings.json: 已注册 ECC + pm-skills marketplace + 9 plugins");
}
'

exec cc-connect
