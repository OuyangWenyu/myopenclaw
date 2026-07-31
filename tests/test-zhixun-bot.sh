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
grep -q 'Every successful reservoir, river-station, rainfall-station, or basin query' openclaw-zhixun/workspace/AGENTS.md
grep -q 'related_page.url' openclaw-zhixun/workspace/AGENTS.md
grep -q "never construct or guess a URL" openclaw-zhixun/workspace/AGENTS.md
grep -q 'cp "${source_file}" "${target_file}"' docker/zhixun-bot/entrypoint.sh
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

python3 - <<'PY'
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

module_path = Path("docker/zhixun-bot/related_page_compat.py")
spec = importlib.util.spec_from_file_location("related_page_compat", module_path)
related = importlib.util.module_from_spec(spec)
spec.loader.exec_module(related)

async def get_reservoir_profile(
    include, stcd="", reservoir_name="", storage_curve_info_only=False,
    warning_start_time="", warning_stop_time=""
):
    return {"stcd": stcd or "21100150", "stnm": reservoir_name or "大伙房水库"}

async def list_reservoirs(
    page=1, size=20, keyword="", region="", warning_type="",
    start_time="", stop_time=""
):
    return {
        "reservoirs": (
            [{"stcd": "21100150", "stnm": "大伙房水库"}] if keyword else []
        )
    }

async def get_river_station_detail(station_name):
    return {"stcd": "21103500", "stnm": station_name}

async def get_river_warning_status(station_name, start_time, stop_time, mode="both"):
    return {"stcd": "21103500", "station_name": station_name}

async def get_river_historical_comparison(
    station_name, year1, year2, start_date, stop_date, metric
):
    return {"stcd": "21103500", "station_name": station_name}

async def get_rainstation_detail(station_name):
    return {"stcd": "21120032", "stnm": station_name}

async def get_rainfall_statistics(
    scope, name, period_type, year, compare_year=None, include_average=True, months=None
):
    if scope == "basin":
        return {"scope": scope, "basin_id": "21100150", "basin_name": name}
    return {"scope": scope, "stcd": "21120032", "station_name": name}

async def get_basin_stations(basin_name, relation="all"):
    return {"basin_id": "21100150", "basin_name": basin_name}

async def get_basin_rainfall_summary(basin_name, start_time="", stop_time=""):
    return {"basin_id": "21100150", "basin_name": basin_name}

async def get_basin_rainfall_forecast(
    basin_name, start_time="", model="gfs", forecast_hours=120
):
    return {"basin_id": "21100150", "basin_name": basin_name}

async def get_basin_rainfall_complete(
    basin_name, start_time="", warmup_days=30, forecast_days=5,
    model="gfs", interval="3h"
):
    return {"basin_id": "21100150", "basin_name": basin_name}

async def get_basin_warning_status(basin_name, start_time, stop_time):
    return {"basin_id": "21100150", "basin_name": basin_name}

async def get_basin_rainfall_isoline(basin_name, analysis_date, force=False):
    return {"basin_id": "21100150", "basin_name": basin_name}

async def get_basin_rainfall_file(
    basin_name, file_format="nc", start_time="", model="gfs"
):
    return {"basin_id": "21100150", "basin_name": basin_name}

async def get_station_timeseries(
    station_type, station_name, start_time, stop_time, parameters="", mode="full",
    threshold=None, exceed_parameter="water_level"
):
    return {
        "station_type": station_type,
        "summary": {"stcd": "21103500", "station_name": station_name},
    }

async def get_station_latest_data(station_type, station_name):
    return {
        "station_type": station_type,
        "summary": {"stcd": "21120032", "station_name": station_name},
    }

mcp = SimpleNamespace(
    list_reservoirs=list_reservoirs,
    get_reservoir_profile=get_reservoir_profile,
    get_river_station_detail=get_river_station_detail,
    get_river_warning_status=get_river_warning_status,
    get_river_historical_comparison=get_river_historical_comparison,
    get_rainstation_detail=get_rainstation_detail,
    get_rainfall_statistics=get_rainfall_statistics,
    get_basin_stations=get_basin_stations,
    get_basin_rainfall_summary=get_basin_rainfall_summary,
    get_basin_rainfall_forecast=get_basin_rainfall_forecast,
    get_basin_rainfall_complete=get_basin_rainfall_complete,
    get_basin_warning_status=get_basin_warning_status,
    get_basin_rainfall_isoline=get_basin_rainfall_isoline,
    get_basin_rainfall_file=get_basin_rainfall_file,
    get_station_timeseries=get_station_timeseries,
    get_station_latest_data=get_station_latest_data,
)
url_calls = []

async def reservoir_url(**kwargs):
    url_calls.append(("reservoir", kwargs))
    return {"URL": f"https://frontend.test/reservoir/{kwargs['page']}"}

async def river_url(**kwargs):
    url_calls.append(("river", kwargs))
    return {"URL": f"https://frontend.test/river/{kwargs['page']}"}

async def rain_url(**kwargs):
    url_calls.append(("rainfall", kwargs))
    return {"URL": "https://frontend.test/rainfall"}

async def basin_rain_url(**kwargs):
    url_calls.append(("basin-rain", kwargs))
    return {"URL": f"https://frontend.test/basin/{kwargs['page']}"}

async def basin_warning_url(**kwargs):
    url_calls.append(("basin-warning", kwargs))
    return {"URL": "https://frontend.test/basin/warning"}

urls = SimpleNamespace(
    get_reservoir_page_url=reservoir_url,
    get_river_page_url=river_url,
    get_rainstation_url=rain_url,
    get_basin_rain_page_url=basin_rain_url,
    get_basin_warning_status_url=basin_warning_url,
)
related.install(mcp, urls)

async def main():
    reservoir_search = await mcp.list_reservoirs(keyword="大伙房")
    assert reservoir_search["related_page"]["url"].endswith("/reservoir/detail")

    reservoir = await mcp.get_reservoir_profile(
        include="extra_info", reservoir_name="大伙房水库"
    )
    assert reservoir["related_page"]["url"].endswith("/reservoir/detail")

    river = await mcp.get_river_station_detail("占贝")
    assert river["related_page"]["url"].endswith("/river/monitor")

    comparison = await mcp.get_river_historical_comparison(
        "占贝", 2024, 2025, "07-01", "07-31", "水位"
    )
    assert comparison["related_page"]["url"].endswith("/river/comparison")
    assert url_calls[-1][1]["metric"] == "water_level"

    rainfall = await mcp.get_rainfall_statistics(
        "station", "石庙子", "month", 2025
    )
    assert rainfall["related_page"]["url"].endswith("/rainfall")
    assert url_calls[-1][1]["start_time"] == "2025-01-01T00:00:00+08:00"

    basin_overview = await mcp.get_basin_stations("大伙房水库")
    assert [page["label"] for page in basin_overview["related_pages"]] == [
        "流域雨情监测页面", "流域风险研判页面"
    ]
    assert "流域雨情监测页面" in basin_overview["response_requirement"]
    assert "流域风险研判页面" in basin_overview["response_requirement"]

    basin_summary = await mcp.get_basin_rainfall_summary(
        "大伙房水库", "2025-07-01T00:00:00Z", "2025-07-31T00:00:00Z"
    )
    assert basin_summary["related_page"]["url"].endswith("/basin/monitor")
    assert url_calls[-1][1]["start_time"] == "2025-07-01T08:00:00+08:00"

    basin_forecast = await mcp.get_basin_rainfall_forecast("大伙房水库")
    assert basin_forecast["related_page"]["url"].endswith("/basin/forecast")

    basin_rain_stats = await mcp.get_rainfall_statistics(
        "basin", "大伙房水库", "month", 2025, compare_year=2024
    )
    assert basin_rain_stats["related_page"]["url"].endswith("/basin/statistics")
    assert url_calls[-1][1]["compare_year"] == 2024

    basin_isoline = await mcp.get_basin_rainfall_isoline("大伙房水库", "2025-07-01")
    assert basin_isoline["related_page"]["url"].endswith("/basin/isoline")

    timeseries = await mcp.get_station_timeseries(
        "river", "占贝", "2025-07-01T00:00:00Z", "2025-07-31T00:00:00Z"
    )
    assert timeseries["related_page"]["url"].endswith("/river/monitor")
    assert url_calls[-1][1]["start_time"] == "2025-07-01T08:00:00+08:00"

    latest = await mcp.get_station_latest_data("rainfall", "石庙子")
    assert latest["related_page"]["url"].endswith("/rainfall")

    for result in (
        reservoir_search, reservoir, river, comparison, rainfall, basin_overview,
        basin_summary, basin_forecast, basin_rain_stats, basin_isoline, timeseries, latest
    ):
        assert "最终回复必须在正文末尾" in result["response_requirement"]

asyncio.run(main())
PY
grep -q 'related_page_compat.py' docker/zhixun-bot/Dockerfile.mcp
grep -q 'install_related_pages' docker/zhixun-bot/mcp_entrypoint.py
pass "station and basin queries include verified related pages"

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
assert read_only["agents"]["defaults"]["model"]["primary"] == "deepseek/deepseek-chat"
assert read_only["models"]["providers"]["deepseek"]["apiKey"] == "model_secret"
PY
pass "rendered group binding and MCP tool policy"

echo "✅ zhixun bot static tests passed"
