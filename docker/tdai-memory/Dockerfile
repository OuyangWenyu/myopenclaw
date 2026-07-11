# =============================================================
# TDAI Memory Gateway — Agent 长期记忆（L0→L3 分层管线）
# Base: ubuntu:24.04 + Node.js 22
# =============================================================
FROM ubuntu:24.04

# ── 系统包：只装 Node.js 运行时依赖（native addon 有 prebuilt binary）──
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 22（tarball，同 claude-code Dockerfile 模式）─────────
ENV NODE_VERSION=22.22.3
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSLO --compressed "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" && \
    tar -xJf "node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" -C /usr/local --strip-components=1 --no-same-owner && \
    rm "node-v${NODE_VERSION}-linux-${ARCH}.tar.xz"

# ── Gateway 包（tsx 是依赖，自动安装）────────────────────────────
RUN npm install -g @tencentdb-agent-memory/memory-tencentdb@0.3.6

# ── Entrypoint ─────────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── 数据目录 ──────────────────────────────────────────────────
RUN mkdir -p /opt/data/tdai-memory

# ── 默认配置（可在 docker-compose 中覆盖）─────────────────────
ENV TDAI_DATA_DIR=/opt/data/tdai-memory
ENV TDAI_GATEWAY_HOST=0.0.0.0

EXPOSE 8420
ENTRYPOINT ["/entrypoint.sh"]
