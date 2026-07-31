#!/usr/bin/env bash
# Static tests only: this script never starts containers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

pass() {
  echo "✅ $1"
}

cd "${REPO_ROOT}"

bash -n scripts/start-zhixun-bot.sh
sh -n docker/zhixun-bot/entrypoint.sh
node --check docker/zhixun-bot/render-config.mjs
pass "shell and Node syntax"

docker compose \
  --env-file .env.zhixun-bot.example \
  -f docker-compose.zhixun-bot.yml \
  config --format json > "${TMP_DIR}/compose.json"

python3 - "${TMP_DIR}/compose.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)

services = config["services"]
assert set(services) == {"openclaw-zhixun", "zhixun-water-mcp"}
assert set(config["networks"]) == {"zhixun-bot-net"}

mcp = services["zhixun-water-mcp"]
build = mcp["build"]
assert build["context"].endswith("/docker/zhixun-bot")
assert build["dockerfile"] == "Dockerfile.mcp"
assert build["additional_contexts"]["zhixun_src"].endswith("/zhixun-agent")
assert build["args"]["PYTHON_BASE_IMAGE"] == "python:3.12-slim"
assert build["args"]["PIP_INDEX_URL"] == "https://pypi.org/simple"
assert mcp["working_dir"] == "/app/mcp_servers/water"
assert mcp["command"][:2] == ["python", "mcp_server_unified.py"]

for service in services.values():
    assert "ports" not in service
    assert set(service["networks"]) == {"zhixun-bot-net"}

mounts = services["openclaw-zhixun"]["volumes"]
sources = {mount["source"] for mount in mounts}
assert any(source.endswith(".openclaw-zhixun") for source in sources)
assert all(not source.endswith("/.openclaw") for source in sources)
assert all("docker.sock" not in source for source in sources)
PY
pass "Compose build source and service isolation"

render() {
  local write_tools="$1"
  local output="$2"
  env \
    ZHIXUN_BOT_FEISHU_APP_ID=cli_test \
    ZHIXUN_BOT_FEISHU_APP_SECRET=test_secret \
    ZHIXUN_BOT_MODEL_ID=deepseek-chat \
    ZHIXUN_BOT_MODEL_BASE_URL=https://api.deepseek.com \
    ZHIXUN_BOT_ENABLE_WRITE_TOOLS="${write_tools}" \
    node docker/zhixun-bot/render-config.mjs \
      docker/zhixun-bot/openclaw.json.template \
      "${output}"
}

render false "${TMP_DIR}/read-only.json"
render true "${TMP_DIR}/write-enabled.json"

python3 - "${TMP_DIR}/read-only.json" "${TMP_DIR}/write-enabled.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    read_only = json.load(stream)
with open(sys.argv[2], encoding="utf-8") as stream:
    write_enabled = json.load(stream)

agent = read_only["agents"]["list"][0]
assert agent["id"] == "zhixun-water"
assert agent["tools"]["allow"] == ["bundle-mcp"]
assert read_only["tools"]["profile"] == "messaging"

feishu = read_only["channels"]["feishu"]
assert feishu["dmPolicy"] == "open"
assert feishu["groupPolicy"] == "open"
assert "allowFrom" not in feishu
assert "groups" not in feishu
assert feishu["requireMention"] is True
assert all(enabled is False for enabled in feishu["tools"].values())

binding = read_only["bindings"][0]
assert binding["agentId"] == "zhixun-water"
assert binding["match"] == {"channel": "feishu"}

server = read_only["mcp"]["servers"]["water_unified"]
assert server["url"] == "http://zhixun-water-mcp:18201/sse"
assert "dispatch_task_execute" in server["toolFilter"]["exclude"]
assert "toolFilter" not in write_enabled["mcp"]["servers"]["water_unified"]

serialized = json.dumps(read_only)
assert "__FEISHU_" not in serialized
assert "__MODEL_" not in serialized
assert read_only["agents"]["defaults"]["model"]["primary"] == "deepseek/deepseek-chat"
PY
pass "rendered group binding and MCP tool policy"

echo "✅ zhixun bot static tests passed"
