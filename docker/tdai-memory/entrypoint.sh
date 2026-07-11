#!/usr/bin/env bash
# =============================================================
# TDAI Memory Gateway entrypoint
# cd 到 npm 全局安装的包目录，启动 Gateway server
# =============================================================
set -euo pipefail

PKG_DIR="/usr/local/lib/node_modules/@tencentdb-agent-memory/memory-tencentdb"

cd "${PKG_DIR}"
echo "🚀 TDAI Memory Gateway starting..."
echo "   📦 Package: ${PKG_DIR}"
echo "   🔌 Port:    ${TDAI_GATEWAY_PORT:-8420}"
echo "   💾 Data:    ${TDAI_DATA_DIR:-/opt/data/tdai-memory}"
echo "   🤖 LLM:     ${TDAI_LLM_MODEL:-gpt-4o} @ ${TDAI_LLM_BASE_URL:-https://api.openai.com/v1}"

exec node --import tsx/esm src/gateway/server.ts
