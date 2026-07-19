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

# ── gh hosts.yml 自动同步 ───────────────────────────────────
# 每次容器启动时，用 .env 里的 GH_TOKEN 更新 hosts.yml 中的 oauth_token
# 这样用户只需要改 .env 一个地方，重启即可生效
if [ -n "${GITHUB_TOKEN:-}" ]; then
  mkdir -p /opt/gh-config
  if [ -f /opt/gh-config/hosts.yml ]; then
    sed -i "s/oauth_token:.*/oauth_token: ${GITHUB_TOKEN}/" /opt/gh-config/hosts.yml
    echo "   🔑 gh hosts.yml 已同步当前 GITHUB_TOKEN"
  else
    cat > /opt/gh-config/hosts.yml << HOSTSEOF
github.com:
    git_protocol: https
    users:
        OuyangWenyu:
            oauth_token: ${GITHUB_TOKEN}
    user: OuyangWenyu
HOSTSEOF
    chmod 600 /opt/gh-config/hosts.yml
    echo "   🔑 gh hosts.yml 已创建并写入 GITHUB_TOKEN"
  fi
fi

# cc-connect reads config from $HOME/.cc-connect/
rm -rf /home/node/.cc-connect /root/.cc-connect
ln -sf /opt/cc-config /home/node/.cc-connect
ln -sf /opt/cc-config /root/.cc-connect

# cc-connect run/ directory must be on a local (non-macOS) filesystem
# because chmod on Unix sockets fails across the macOS→Linux boundary
mkdir -p /tmp/cc-connect-run
rm -rf /opt/cc-config/run
ln -sf /tmp/cc-connect-run /opt/cc-config/run

# gitcode-cli reads config from $HOME/.gitcode/
rm -rf /home/node/.gitcode /root/.gitcode
ln -sf /opt/gitcode-config /home/node/.gitcode
ln -sf /opt/gitcode-config /root/.gitcode

# ── dailyinfo skills -> Claude Code skills ────────────────────
# Mounted at /opt/dailyinfo-skills (ro), symlinked after .claude is set up
mkdir -p /home/node/.claude/skills
for skill_dir in /opt/dailyinfo-skills/*/; do
    skill_name=$(basename "$skill_dir")
    target="/home/node/.claude/skills/${skill_name}"
    rm -rf "$target"
    ln -sf "$skill_dir" "$target"
done

# ── myloop skills -> Claude Code skills ───────────────────────
# myloop 是独立的 loop 设计项目，通过 volume 挂载到 /home/node/code/myloop
# 如果存在则 symlink 其 skills/ 到 CC飞总 skills 目录（不复制、不分叉）
if [ -d "/home/node/code/myloop/skills" ]; then
    for skill_dir in /home/node/code/myloop/skills/*/; do
        skill_name=$(basename "$skill_dir")
        target="/home/node/.claude/skills/${skill_name}"
        rm -rf "$target"
        ln -sf "$skill_dir" "$target"
    done
    echo "📎 myloop skills: $(ls /home/node/code/myloop/skills/ 2>/dev/null | tr '\n' ' ')"
else
    echo "ℹ️  myloop 未挂载，跳过 skill 安装（clone myloop 到 ~/code/myloop 即可自动加载）"
fi

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

# ── dailyinfo install（后台 pip install -e，deps 已在镜像中）─────
(
    if [ -d "/home/node/code/dailyinfo" ] && ! python3 -c "import dailyinfo" 2>/dev/null; then
        echo "📦 安装 dailyinfo..."
        timeout 30 python3 -m pip install -e /home/node/code/dailyinfo --quiet 2>/dev/null && \
            echo "✅ dailyinfo 安装完成" || true
    fi
) &

# ── Code 目录骨架（卷挂载后创建）─────────────────────────────
mkdir -p /home/node/code/opensource /home/node/code/OuyangWenyu /home/node/code/iHeadWater 2>/dev/null || true

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

# ── 确保 marketplace + plugin 注册（延迟执行，等 cc-connect 完成 settings.json 初始化）──
(
    sleep 10
    node -e '
const fs = require("fs");
const path = "/home/node/.claude/settings.json";
let settings = {};
try { settings = JSON.parse(fs.readFileSync(path, "utf8")); } catch(e) { console.error("ERROR reading settings.json:", e.message); }
let changed = false;

try {

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

// Permissions (cc-connect needs auto-accept for Bash + MCP tools)
if (!settings.permissions) {
    settings.permissions = {};
}
if (!settings.permissions.allow) {
    settings.permissions.allow = [
        "Bash(*)",
        "mcp__codegraph__codegraph_search",
        "mcp__codegraph__codegraph_context",
        "mcp__codegraph__codegraph_callers",
        "mcp__codegraph__codegraph_callees",
        "mcp__codegraph__codegraph_impact",
        "mcp__codegraph__codegraph_node",
        "mcp__codegraph__codegraph_status",
        "mcp__playwright__*",
        "mcp__zotero__*"
    ];
    changed = true;
}
if (!settings.permissions.allow.includes("mcp__zotero__*")) {
    settings.permissions.allow.push("mcp__zotero__*");
    changed = true;
}

// Model defaults (deepseek-v4-pro 主模型，防止 cc-connect 重写后丢失)
if (!settings.env) {
    settings.env = {};
}
if (!settings.env.ANTHROPIC_BASE_URL || settings.env.ANTHROPIC_BASE_URL.includes("bigmodel")) {
    settings.env.ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic";
    changed = true;
}
if (!settings.env.ANTHROPIC_MODEL || settings.env.ANTHROPIC_MODEL.includes("glm")) {
    settings.env.ANTHROPIC_MODEL = "deepseek-v4-pro[1M]";
    changed = true;
}
if (!settings.env.ANTHROPIC_DEFAULT_HAIKU_MODEL || settings.env.ANTHROPIC_DEFAULT_HAIKU_MODEL.includes("glm")) {
    settings.env.ANTHROPIC_DEFAULT_HAIKU_MODEL = "deepseek-v4-flash";
    changed = true;
}
if (!settings.env.ANTHROPIC_DEFAULT_SONNET_MODEL || settings.env.ANTHROPIC_DEFAULT_SONNET_MODEL.includes("glm")) {
    settings.env.ANTHROPIC_DEFAULT_SONNET_MODEL = "deepseek-v4-pro[1M]";
    changed = true;
}
if (!settings.env.ANTHROPIC_DEFAULT_OPUS_MODEL || settings.env.ANTHROPIC_DEFAULT_OPUS_MODEL.includes("glm")) {
    settings.env.ANTHROPIC_DEFAULT_OPUS_MODEL = "deepseek-v4-pro[1M]";
    changed = true;
}
if (!settings.env.ANTHROPIC_DEFAULT_FABLE_MODEL || settings.env.ANTHROPIC_DEFAULT_FABLE_MODEL.includes("glm")) {
    settings.env.ANTHROPIC_DEFAULT_FABLE_MODEL = "deepseek-v4-pro[1M]";
    changed = true;
}
if (!settings.env.API_TIMEOUT_MS) {
    settings.env.API_TIMEOUT_MS = "3000000";
    changed = true;
}
if (!settings.env.CLAUDE_CODE_EFFORT_LEVEL) {
    settings.env.CLAUDE_CODE_EFFORT_LEVEL = "max";
    changed = true;
}

// MCP servers
if (!settings.mcpServers) {
    settings.mcpServers = {};
}
if (!settings.mcpServers.codegraph) {
    settings.mcpServers.codegraph = {
        command: "codegraph",
        args: ["serve", "--mcp"]
    };
    changed = true;
}
	if (!settings.mcpServers["tdai-memory"]) {
	    settings.mcpServers["tdai-memory"] = {
	        command: "python3",
	        args: ["/opt/tdai-mcp-server.py"],
	        env: {
	            TDAI_GATEWAY_URL: "http://tdai-memory:8420",
	            TDAI_DATA_DIR: "/home/node/.myagentdata/tdai-memory"
	        }
	    };
	    changed = true;
	}
		if (!settings.mcpServers["zotero"]) {
		    settings.mcpServers["zotero"] = {
		        command: "python3",
		        args: ["/opt/zotero-mcp-server.py"]
		    };
		    changed = true;
		}

	// Stop hook: capture CC飞总 conversations into TDAI Memory Gateway
	// (writes L0 → enables bidirectional cross-agent memory sharing).
	if (!settings.hooks) {
	    settings.hooks = {};
	}
	if (!settings.hooks.Stop) {
	    settings.hooks.Stop = [{
	        hooks: [{
	            type: "command",
	            command: "python3 /opt/capture-to-gateway.py"
	        }]
	    }];
	    changed = true;
	}


if (changed) {
    fs.writeFileSync(path, JSON.stringify(settings, null, 2) + "\n");
    console.log("🔧 settings.json 已恢复: deepseek-v4-pro 主模型 + ECC/pm-skills marketplace + 9 plugins + permissions + codegraph MCP");
}

} catch(e) {
    console.error("🔧 settings.json restore failed:", e.message);
}
'
) &

# ── Auto-register weekly AI News cron jobs ──────────────────────
(
    for i in $(seq 1 30); do
        if [ -S /root/.cc-connect/run/api.sock ]; then break; fi
        sleep 2
    done
    if [ -S /root/.cc-connect/run/api.sock ]; then
        EXISTING=$(CC_SESSION_KEY=s1 cc-connect cron list 2>/dev/null | grep -c "AI News" || true)
        if [ "$EXISTING" -lt 2 ]; then
            echo "📋 Registering weekly AI News cron jobs..."
            CC_SESSION_KEY=s1 cc-connect cron add \
                --cron "0 8 * * 0" \
                --exec "bash /opt/claude-code/weekly-ai-news-generate.sh" \
                --desc "AI News 周报生成" 2>/dev/null || true
            CC_SESSION_KEY=s1 cc-connect cron add \
                --cron "10 8 * * 0" \
                --prompt "执行 ai-news-weekly-polish 润色任务：1）读取 /home/node/.myagentdata/dailyinfo/briefings/weekly/weekly_recap_\$(date +%Y-%m-%d).md 2）深度润色（导读用具体数字切入、跨日事件体现演化、冷门实体加背景、消除AI套话）3）保存润色版并作为回复返回" \
                --session-mode new-per-run \
                --desc "AI News 周报润色+飞书推送" 2>/dev/null || true
            echo "✅ weekly AI News cron jobs registered"
        fi
    fi
) &

exec cc-connect
