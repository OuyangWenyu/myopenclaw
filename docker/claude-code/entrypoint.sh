#!/usr/bin/env bash
# =============================================================
# Claude Code + cc-connect entrypoint
# Sets up config symlinks then launches cc-connect as main process.
# cc-connect manages Claude Code sessions internally.
# =============================================================
set -euo pipefail
umask 077

# ── Symlink config dirs to host-mounted volumes ──────────────
# Claude Code reads config from $HOME/.claude/
mkdir -p /home/node/.config /root/.config
rm -rf /home/node/.claude /root/.claude
ln -sf /opt/claude-config /home/node/.claude
ln -sf /opt/claude-config /root/.claude

# gh CLI reads config from $HOME/.config/gh/
# 必须先 rm -rf，否则如果目标已存在为目录，ln 会把链接建在目录里面而非替换它。
rm -rf /home/node/.config/gh /root/.config/gh
ln -sf /opt/gh-config /home/node/.config/gh
ln -sf /opt/gh-config /root/.config/gh

# ── gh hosts.yml 自动同步 ───────────────────────────────────
# 每次容器启动时，用 .env 里的 GH_TOKEN（传入为 GITHUB_TOKEN）更新
# hosts.yml 中的 oauth_token。始终用 heredoc 重新生成（不用 sed），
# 避免 token 出现在 /proc/pid/cmdline 中。
#
# 使用 legacy 单账户格式（不加 users: 键），避免触发 gh 2.96+ 的
# 多账户迁移路径——该路径需要 dbus secret service，在 Docker 容器里不可用。
if [ -n "${GITHUB_TOKEN:-}" ]; then
  mkdir -p /opt/gh-config
  # 清理旧格式 config.yml（触发多账户迁移的源头）
  rm -f /opt/gh-config/config.yml
  cat > /opt/gh-config/hosts.yml << HOSTSEOF
github.com:
    user: OuyangWenyu
    oauth_token: ${GITHUB_TOKEN}
    git_protocol: https
HOSTSEOF
  echo "   🔑 gh hosts.yml 已同步当前 GITHUB_TOKEN"
fi

# 确保权限正确（放在 if 块外面：覆盖 token 更新和已有文件两种情况）
[ -f /opt/gh-config/hosts.yml ] && {
  chmod 600 /opt/gh-config/hosts.yml
  chown node:node /opt/gh-config/hosts.yml 2>/dev/null || true
}

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
        "mcp__playwright__*"
    ];
    changed = true;
}
if (!settings.permissions.allow.includes("mcp__playwright__*")) {
    settings.permissions.allow.push("mcp__playwright__*");
    changed = true;
}

// Model defaults (deepseek-flash 主模型，防止 cc-connect 重写后丢失)
if (!settings.env) {
    settings.env = {};
}
if (!settings.env.ANTHROPIC_BASE_URL || settings.env.ANTHROPIC_BASE_URL.includes("bigmodel")) {
    settings.env.ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic";
    changed = true;
}
// 需要改写的 model 值：为空、早期 glm 默认值、或任何**非 canonical 的 DeepSeek V 系列 id**。
// 只看"为空 or 含 glm"是不够的 —— cc-connect 与历史配置留下的旧 V 系列 id 两者都不满足，
// 会永久绕过重写，重建容器也修不好（线上 Opus tier 曾长期停在一个已下线的 id 上，
// 守卫见 tests/test-cc-model-migration.sh）。
// 用模式而非名单：下线名单会过期，而 `deepseek-v<数字>…` 这个形状天然覆盖各代
// 已下线 id 及其后续版本（canonical 的 deepseek-flash 无版本段，不匹配）。
function shouldSetModel(current) {
    if (!current) return true;                                  // 未设置
    const s = String(current);
    if (s.includes("glm")) return true;                         // 早期默认值
    return /^deepseek-v\d/.test(s.replace(/\[1M\]$/i, ""));      // 非 canonical 的 V 系列
}
const TIER_MODELS = {
    ANTHROPIC_MODEL: "deepseek-flash[1M]",
    ANTHROPIC_DEFAULT_HAIKU_MODEL: "deepseek-flash",
    ANTHROPIC_DEFAULT_SONNET_MODEL: "deepseek-flash[1M]",
    ANTHROPIC_DEFAULT_OPUS_MODEL: "deepseek-flash[1M]",
    ANTHROPIC_DEFAULT_FABLE_MODEL: "deepseek-flash[1M]"
};
for (const [key, value] of Object.entries(TIER_MODELS)) {
    if (shouldSetModel(settings.env[key])) {
        settings.env[key] = value;
        changed = true;
    }
    // cc-connect 会写入配套的 *_MODEL_NAME 作展示名；只改 *_MODEL 会留下
    // 一个仍然指向旧 id 的显示名，所以既有的一并纠正（不存在则不新建）。
    const nameKey = key + "_NAME";
    if (settings.env[nameKey] !== undefined && shouldSetModel(settings.env[nameKey])) {
        settings.env[nameKey] = value.replace("[1M]", "");
        changed = true;
    }
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
		        type: "http",
		        url: "http://zotero-mcp:8002/mcp"
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
	            command: "test -f /opt/capture-to-gateway.py && python3 /opt/capture-to-gateway.py || true"
	        }]
	    }];
	    changed = true;
	}


if (changed) {
    fs.writeFileSync(path, JSON.stringify(settings, null, 2) + "\n");
    console.log("🔧 settings.json 已恢复: deepseek-flash 主模型 + ECC/pm-skills marketplace + 9 plugins + permissions + codegraph MCP");
}

} catch(e) {
    console.error("🔧 settings.json restore failed:", e.message);
}
'
) &

exec cc-connect
