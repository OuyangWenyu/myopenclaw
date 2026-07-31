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
grep -q 'get_reservoir_page_url(page="detail")' openclaw-zhixun/workspace/AGENTS.md
grep -q 'related_page.url' openclaw-zhixun/workspace/AGENTS.md
grep -q "never construct or guess a URL" openclaw-zhixun/workspace/AGENTS.md
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
assert build["args"]["PYTHON_BASE_IMAGE"] == "docker.m.daocloud.io/library/python:3.12-slim"
assert build["args"]["PIP_INDEX_URL"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
assert mcp["working_dir"] == "/app/mcp_servers/water"
assert mcp["command"][:2] == ["python", "mcp_entrypoint.py"]
assert mcp["environment"]["ZHIXUN_CORE_BASE_URL"] == "https://ws.waterism.tech:8090/api/v2"
assert mcp["environment"]["ZHIXUN_MCP_STATION_INDEX_PATH"] == "/var/lib/zhixun-water-mcp/station-index.json"
assert mcp["environment"]["ZHIXUN_MCP_STATION_INDEX_TTL_SECONDS"] == "86400"
assert mcp["environment"]["ZHIXUN_MCP_STATION_INDEX_WORKERS"] == "12"
assert mcp["volumes"][0]["target"] == "/var/lib/zhixun-water-mcp"

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

python3 - <<'PY'
import importlib.util
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

module_path = Path("docker/zhixun-bot/zhixun_core_v2_compat.py")
spec = importlib.util.spec_from_file_location("zhixun_core_v2_compat", module_path)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)

reservoir_payload = {
    "_embedded": {
        "reservoirs": [
            {"data": {"stcd": "21100150", "stnm": "大伙房水库"}},
            {"data": {"stcd": "10310500", "stnm": "红花尔基"}},
        ]
    }
}
assert compat.extract_collection_items(reservoir_payload, "/reservoirs") == [
    {"stcd": "21100150", "stnm": "大伙房水库"},
    {"stcd": "10310500", "stnm": "红花尔基"},
]

calls = []
basin_payloads = {
    "/basins/21100150/stations": {
        "_embedded": {
            "stations": [
                {"data": {"stcd": "21103500", "stnm": "占贝", "sttype": "ZQ"}},
                {"data": {"stcd": "21120032", "stnm": "石庙子", "sttype": "PP"}},
                {"data": {"stcd": "21103250", "stnm": "四道河子", "sttype": "ZQ"}},
            ]
        }
    },
    "/basins/10310500/stations": {
        "_embedded": {
            "stations": [
                {"data": {"stcd": "21103257", "stnm": "四道河子", "sttype": "ZQ"}},
            ]
        }
    },
}

def fake_api_get(endpoint, params=None):
    calls.append((endpoint, params))
    if endpoint == "/reservoirs":
        query = str((params or {}).get("q") or "")
        if query:
            matches = [
                item
                for item in reservoir_payload["_embedded"]["reservoirs"]
                if query in item["data"]["stnm"]
            ]
            return {"_embedded": {"reservoirs": matches}}
        return reservoir_payload
    return basin_payloads[endpoint]

utils = SimpleNamespace(
    _CACHED_BY_TYPE={},
    _CACHED_NAME_TO_ID={},
    _CACHED_IDS_BY_NAME={},
    _CACHED_INFO_BY_ID={},
    _CACHE_INIT_ATTEMPTED=False,
    _STATION_TYPE_APIS={
        "水库站": "/reservoirs",
        "河道站": "/rivers",
        "雨量站": "/rainstations",
    },
    _api_get=fake_api_get,
    _require_non_empty=lambda name, value: str(value),
    get_station_id=lambda name, station_type=None: "21100150",
    logger=SimpleNamespace(
        info=lambda message: None,
        warning=lambda message: None,
    ),
)
with tempfile.TemporaryDirectory() as cache_dir:
    os.environ["ZHIXUN_MCP_STATION_INDEX_PATH"] = str(Path(cache_dir) / "stations.json")
    os.environ["ZHIXUN_MCP_STATION_INDEX_WORKERS"] = "2"
    compat.install(utils)
    utils._init_station_caches()
    assert calls == [("/reservoirs", {"page": 1, "size": 100})]
    assert utils._CACHED_BY_TYPE["水库站"]["大伙房水库"] == "21100150"

    assert utils.get_station_id("占贝", "河道站") == "21103500"
    assert utils.get_station_id("占贝河道站", "河道站") == "21103500"
    assert utils.get_station_id("占贝") == "21103500"
    assert utils.get_station_id("石庙子", "雨量站") == "21120032"
    assert utils.get_station_id("21103500", "河道站") == "21103500"
    assert Path(os.environ["ZHIXUN_MCP_STATION_INDEX_PATH"]).is_file()

    try:
        utils.get_station_id("四道河子", "河道站")
    except ValueError as exc:
        assert "匹配到多个站点" in str(exc)
        assert "21103250" in str(exc)
        assert "21103257" in str(exc)
    else:
        raise AssertionError("duplicate river name must be rejected")
PY
pass "zhixun-core v2 station-name index compatibility"

grep -q 'get_reservoir_profile_with_related_page' docker/zhixun-bot/mcp_entrypoint.py
grep -q 'get_reservoir_page_url' docker/zhixun-bot/mcp_entrypoint.py
grep -q '@wraps(_get_reservoir_profile)' docker/zhixun-bot/mcp_entrypoint.py
pass "reservoir profile includes verified detail page"

render() {
  local write_tools="$1"
  local output="$2"
  env \
    ZHIXUN_BOT_FEISHU_APP_ID=cli_test \
    ZHIXUN_BOT_FEISHU_APP_SECRET=test_secret \
    ZHIXUN_BOT_FEISHU_STREAMING=false \
    ZHIXUN_BOT_MODEL_API_KEY=model_secret \
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
assert feishu["allowFrom"] == ["*"]
assert "groups" not in feishu
assert feishu["requireMention"] is True
assert feishu["streaming"] is False
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
assert read_only["agents"]["defaults"]["model"]["primary"] == "zhipu/deepseek-chat"
assert read_only["models"]["providers"]["zhipu"]["apiKey"] == "model_secret"
PY
pass "rendered group binding and MCP tool policy"

echo "✅ zhixun bot static tests passed"
