# =============================================================
# Claude Code + cc-connect — 独立编码 Agent 容器
# Base: ubuntu:24.04 (Python 3.12 自带)
# =============================================================
FROM ubuntu:24.04

# ── 系统包：git + 编译工具 + Python 3.12 + Node.js 运行时依赖 ──
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git wget ca-certificates curl xz-utils \
        python3 python3-dev python3-pip python3-venv \
        build-essential \
        libgeos-dev libproj-dev \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python

# ── Node.js 22（tarball 直接下载，仿 node:22-slim 官方做法）─────
ENV NODE_VERSION=22.22.3
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSLO --compressed "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" && \
    tar -xJf "node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" -C /usr/local --strip-components=1 --no-same-owner && \
    rm "node-v${NODE_VERSION}-linux-${ARCH}.tar.xz"

# ── uv（静态二进制，复用 Hermes Dockerfile 模式）─────────────────
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ── Python deps for dailyinfo (weekly_summary.py) ────────────────
RUN pip install --break-system-packages --no-cache-dir \
    requests scikit-learn click python-dotenv

# ── TDAI Memory MCP server deps ──────────────────────────────
RUN pip install --break-system-packages --no-cache-dir \
    "mcp>=1.0.0" "httpx>=0.27.0"

# ── GitHub CLI（direct binary）───────────────────────────────────
RUN ARCH=$(dpkg --print-architecture) && \
    GH_VERSION="2.92.0" && \
    wget -qO /tmp/gh.tar.gz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${ARCH}.tar.gz" && \
    tar -xzf /tmp/gh.tar.gz -C /tmp && \
    cp /tmp/gh_${GH_VERSION}_linux_${ARCH}/bin/gh /usr/local/bin/gh && \
    rm -rf /tmp/gh*

# ── Claude Code CLI + cc-connect ─────────────────────────────────
RUN npm install -g @anthropic-ai/claude-code cc-connect @stupidloud/codegraph gitcode-cli

# ── Playwright 浏览器（Chromium）供 MCP browser 工具使用 ──────────
RUN npx playwright install --with-deps chromium

# ── 创建 node 用户（UID 1000，兼容现有卷挂载）────────────────────
RUN id -u node 2>/dev/null || useradd -m -s /bin/bash node

# ── Config directories for host-side persistence ──────────────────
RUN mkdir -p /opt/claude-config /opt/gh-config /opt/cc-config /opt/gitcode-config && \
    chown -R node:node /opt/claude-config /opt/gh-config /opt/cc-config /opt/gitcode-config

# ── Code 目录（子目录由 entrypoint 在卷挂载后创建）───────────────
RUN mkdir -p /home/node/code && chown node:node /home/node/code

# ── ECC seed（pre-seeded for first-run auto-install to volume）────
RUN git clone --depth 1 https://github.com/affaan-m/everything-claude-code.git /opt/ecc-seed \
    && cd /opt/ecc-seed && npm install --no-audit --no-fund --loglevel=error \
    && chown -R node:node /opt/ecc-seed

# ── Zotero Web API MCP server (stdlib Python, no npm dependency) ──
COPY zotero-mcp-server.py /opt/zotero-mcp-server.py

# ── Weekly AI News generate script ────────────────────────────────
COPY weekly-ai-news-generate.sh /opt/claude-code/weekly-ai-news-generate.sh
RUN chmod +x /opt/claude-code/weekly-ai-news-generate.sh

# ── Entrypoint wrapper ────────────────────────────────────────────
COPY entrypoint.sh /opt/claude-code/entrypoint.sh
RUN chmod +x /opt/claude-code/entrypoint.sh

ENTRYPOINT ["/opt/claude-code/entrypoint.sh"]
