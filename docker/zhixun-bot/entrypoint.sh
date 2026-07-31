#!/bin/sh
set -eu

STATE_DIR="/home/node/.openclaw"
CONFIG_FILE="${STATE_DIR}/openclaw.json"
PLUGIN_MARKER="${STATE_DIR}/.feishu-plugin-installed"
WORKSPACE_DIR="${STATE_DIR}/workspace"

mkdir -p "${STATE_DIR}" "${WORKSPACE_DIR}"

for source_file in /opt/zhixun-workspace/*; do
  target_file="${WORKSPACE_DIR}/$(basename "${source_file}")"
  if [ ! -e "${target_file}" ]; then
    cp "${source_file}" "${target_file}"
  fi
done

# 飞书是 OpenClaw 官方外部插件。安装记录保存在独立 state 目录中，
# marker 避免容器重启时重复访问插件仓库。
if [ ! -f "${PLUGIN_MARKER}" ]; then
  echo "📦 首次启动：安装 OpenClaw 飞书插件..."
  node /app/openclaw.mjs plugins install @openclaw/feishu
  touch "${PLUGIN_MARKER}"
fi

node /opt/zhixun-bot/render-config.mjs \
  /opt/zhixun-bot/openclaw.json.template \
  "${CONFIG_FILE}"

echo "🔍 校验独立 OpenClaw 配置..."
node /app/openclaw.mjs config validate

echo "🚀 启动 zhixun 飞书机器人..."
exec node /app/openclaw.mjs gateway --allow-unconfigured
