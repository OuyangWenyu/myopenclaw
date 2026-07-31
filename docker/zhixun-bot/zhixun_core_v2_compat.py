"""Runtime compatibility for zhixun-core v2 station collections.

zhixun-core v2 exposes a searchable reservoir collection, but river and
rainfall stations are only listed inside each basin.  This module adapts the
HAL response shape and lazily builds a persistent name-to-station index from
``/basins/{basin_id}/stations`` without changing zhixun-core.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


_INDEX_VERSION = 1
_ENDPOINT_TO_TYPE = {
    "/reservoirs": "水库站",
    "/rivers": "河道站",
    "/rainstations": "雨量站",
}
_STTYPE_TO_TYPE = {
    "RR": "水库站",
    "ZZ": "河道站",
    "ZQ": "河道站",
    "PP": "雨量站",
}


def _collection_name(endpoint: str) -> str:
    return endpoint.strip("/").split("/")[-1]


def extract_collection_items(
    payload: dict[str, Any],
    endpoint: str,
) -> list[dict[str, Any]]:
    """Return flat station records from supported HAL and legacy list shapes."""
    embedded = payload.get("_embedded")
    raw_items: Any = None
    if isinstance(embedded, dict):
        raw_items = embedded.get(_collection_name(endpoint))
        if raw_items is None:
            raw_items = embedded.get("items")
    if raw_items is None:
        raw_items = payload.get("items")
    if raw_items is None and isinstance(payload.get("data"), list):
        raw_items = payload["data"]
    if not isinstance(raw_items, list):
        return []

    items: list[dict[str, Any]] = []
    for resource in raw_items:
        if not isinstance(resource, dict):
            continue
        nested = resource.get("data")
        items.append(nested if isinstance(nested, dict) else resource)
    return items


def _normalize_station_name(value: str) -> str:
    normalized = re.sub(r"[\s\"'“”‘’]+", "", str(value)).casefold()
    for suffix in ("河道水文站", "雨量测站", "河道站", "雨量站", "水库站", "测站"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def install(utils: Any) -> None:
    """Patch the loaded zhixun-agent ``utils_xz`` module in memory."""
    original_get_station_id = utils.get_station_id
    index_path = Path(
        os.environ.get(
            "ZHIXUN_MCP_STATION_INDEX_PATH",
            "/var/lib/zhixun-water-mcp/station-index.json",
        )
    )
    try:
        index_ttl = max(
            60,
            int(os.environ.get("ZHIXUN_MCP_STATION_INDEX_TTL_SECONDS", "86400")),
        )
    except ValueError:
        index_ttl = 86400
    try:
        max_workers = min(
            32,
            max(1, int(os.environ.get("ZHIXUN_MCP_STATION_INDEX_WORKERS", "12"))),
        )
    except ValueError:
        max_workers = 12

    index_lock = threading.Lock()
    state: dict[str, Any] = {
        "records": [],
        "full_ready": False,
        "refresh_attempted": False,
    }

    def log(level: str, message: str) -> None:
        method = getattr(utils.logger, level, None) or getattr(utils.logger, "info")
        method(message)

    def make_record(
        item: dict[str, Any],
        station_type: str,
        basin_id: str = "",
        basin_name: str = "",
    ) -> dict[str, Any] | None:
        stcd = str(item.get("stcd") or item.get("id") or "").strip()
        stnm = str(item.get("stnm") or item.get("name") or "").strip()
        if not stcd or not stnm:
            return None
        location = item.get("location")
        if not isinstance(location, dict):
            location = item.get("outlet_coordinates")
        if not isinstance(location, dict):
            location = {}
        return {
            "stcd": stcd,
            "stnm": stnm,
            "station_type": station_type,
            "sttype": str(item.get("sttype") or "").strip(),
            "lon": item.get("lon")
            or location.get("longitude")
            or location.get("lon")
            or "",
            "lat": item.get("lat")
            or location.get("latitude")
            or location.get("lat")
            or "",
            "basin_id": str(item.get("basin_id") or basin_id or "").strip(),
            "basin_name": str(item.get("basin_name") or basin_name or "").strip(),
        }

    def deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for record in records:
            key = (record["station_type"], record["stcd"])
            if key not in unique:
                unique[key] = record
                continue
            current = unique[key]
            if not current.get("basin_id") and record.get("basin_id"):
                current["basin_id"] = record["basin_id"]
                current["basin_name"] = record.get("basin_name", "")
        return list(unique.values())

    def populate_caches(records: list[dict[str, Any]]) -> None:
        utils._CACHED_BY_TYPE.clear()
        utils._CACHED_NAME_TO_ID.clear()
        utils._CACHED_IDS_BY_NAME.clear()
        utils._CACHED_INFO_BY_ID.clear()
        for station_type in utils._STATION_TYPE_APIS:
            utils._CACHED_BY_TYPE[station_type] = {}

        for record in records:
            station_type = record["station_type"]
            if station_type not in utils._CACHED_BY_TYPE:
                continue
            stcd = record["stcd"]
            stnm = record["stnm"]
            utils._CACHED_BY_TYPE[station_type][stnm] = stcd
            utils._CACHED_NAME_TO_ID[stnm] = stcd
            pair = (stcd, station_type)
            ids = utils._CACHED_IDS_BY_NAME.setdefault(stnm, [])
            if pair not in ids:
                ids.append(pair)
            utils._CACHED_INFO_BY_ID[stcd] = {
                "name": stnm,
                "type": record.get("sttype", ""),
                "category": station_type,
                "lon": record.get("lon", ""),
                "lat": record.get("lat", ""),
                "basin_id": record.get("basin_id", ""),
                "basin_name": record.get("basin_name", ""),
            }

        counts = {
            station_type: len(utils._CACHED_BY_TYPE.get(station_type, {}))
            for station_type in utils._STATION_TYPE_APIS
        }
        log(
            "info",
            "站点索引已加载: "
            + ", ".join(f"{station_type} {count} 个" for station_type, count in counts.items()),
        )

    def load_disk_index() -> tuple[list[dict[str, Any]], bool]:
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            if payload.get("version") != _INDEX_VERSION:
                return [], False
            records = payload.get("stations")
            if not isinstance(records, list) or not records:
                return [], False
            generated_at = float(payload.get("generated_at_epoch") or 0)
            fresh = time.time() - generated_at <= index_ttl
            return [record for record in records if isinstance(record, dict)], fresh
        except FileNotFoundError:
            return [], False
        except Exception as exc:
            log("warning", f"读取站点索引缓存失败: {exc}")
            return [], False

    def write_disk_index(
        records: list[dict[str, Any]],
        successful_basins: int,
        failed_basins: int,
    ) -> None:
        payload = {
            "version": _INDEX_VERSION,
            "generated_at_epoch": time.time(),
            "successful_basins": successful_basins,
            "failed_basins": failed_basins,
            "stations": records,
        }
        temporary_path = index_path.with_suffix(f"{index_path.suffix}.tmp")
        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_path, index_path)
        except Exception as exc:
            log("warning", f"写入站点索引缓存失败: {exc}")

    def fetch_reservoir_records() -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        while True:
            try:
                payload = utils._api_get(
                    "/reservoirs",
                    params={"page": page, "size": 100},
                )
            except Exception:
                break
            items = extract_collection_items(payload, "/reservoirs")
            if not items:
                break
            for item in items:
                record = make_record(
                    item,
                    "水库站",
                    basin_id=str(item.get("stcd") or ""),
                    basin_name=str(item.get("stnm") or ""),
                )
                if record:
                    records.append(record)
            pagination = payload.get("metadata", {}).get("pagination", {})
            total_pages = int(pagination.get("pages") or 0)
            if (total_pages and page >= total_pages) or len(items) < 100:
                break
            page += 1
        return records

    def fetch_basin_station_records(
        reservoir: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        basin_id = reservoir["stcd"]
        basin_name = reservoir["stnm"]
        try:
            payload = utils._api_get(f"/basins/{basin_id}/stations")
        except Exception:
            return [], False
        resources = payload.get("_embedded", {}).get("stations", [])
        records: list[dict[str, Any]] = []
        for resource in resources if isinstance(resources, list) else []:
            if not isinstance(resource, dict):
                continue
            item = resource.get("data")
            if not isinstance(item, dict):
                continue
            station_type = _STTYPE_TO_TYPE.get(str(item.get("sttype") or "").upper())
            if station_type not in {"河道站", "雨量站"}:
                continue
            record = make_record(item, station_type, basin_id, basin_name)
            if record:
                records.append(record)
        return records, True

    def build_full_index() -> bool:
        reservoir_records = [
            record
            for record in state["records"]
            if record.get("station_type") == "水库站"
        ]
        if not reservoir_records:
            reservoir_records = fetch_reservoir_records()
        if not reservoir_records:
            log("warning", "无法构建站点索引: 未获取到水库/流域列表")
            return False

        station_records: list[dict[str, Any]] = []
        successful_basins = 0
        failed_basins = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(fetch_basin_station_records, reservoir)
                for reservoir in reservoir_records
            ]
            for future in as_completed(futures):
                records, success = future.result()
                if success:
                    successful_basins += 1
                    station_records.extend(records)
                else:
                    failed_basins += 1

        if successful_basins == 0:
            log("warning", "无法构建站点索引: 所有流域测站请求均失败")
            return False

        records = deduplicate(reservoir_records + station_records)
        previous_non_reservoirs = sum(
            record.get("station_type") != "水库站" for record in state["records"]
        )
        new_non_reservoirs = sum(
            record.get("station_type") != "水库站" for record in records
        )
        if previous_non_reservoirs and new_non_reservoirs < previous_non_reservoirs * 0.8:
            log(
                "warning",
                "新站点索引明显少于旧缓存，保留上一次成功缓存",
            )
            return False

        state["records"] = records
        state["full_ready"] = True
        populate_caches(records)
        write_disk_index(records, successful_basins, failed_basins)
        log(
            "info",
            f"站点索引刷新完成: 成功流域 {successful_basins}，失败流域 {failed_basins}",
        )
        return True

    def ensure_full_index() -> None:
        if state["full_ready"] or state["refresh_attempted"]:
            return
        with index_lock:
            if state["full_ready"] or state["refresh_attempted"]:
                return
            state["refresh_attempted"] = True
            if not build_full_index() and state["records"]:
                state["full_ready"] = True
                populate_caches(state["records"])
                log("warning", "站点索引刷新失败，继续使用旧缓存")

    def search_local_index(name: str, station_type: str) -> list[dict[str, Any]]:
        ensure_full_index()
        query = _normalize_station_name(name)
        candidates = [
            record
            for record in state["records"]
            if record.get("station_type") == station_type
        ]
        exact = [
            record
            for record in candidates
            if _normalize_station_name(record.get("stnm", "")) == query
        ]
        if exact:
            return exact
        return [
            record
            for record in candidates
            if query and query in _normalize_station_name(record.get("stnm", ""))
        ]

    def search_station_api(name: str, endpoint: str) -> list[dict[str, Any]]:
        station_type = _ENDPOINT_TO_TYPE.get(endpoint)
        if station_type in {"河道站", "雨量站"}:
            return search_local_index(name, station_type)
        try:
            payload = utils._api_get(
                endpoint,
                params={"q": name, "page": 1, "size": 20},
            )
        except Exception:
            return []
        return extract_collection_items(payload, endpoint)

    def get_station_id(station_name: str, station_type: str = None) -> str:
        value = utils._require_non_empty("station_name", station_name).strip()
        is_id_like = bool(re.fullmatch(r"[A-Za-z0-9_-]+", value)) and any(
            char.isdigit() for char in value
        )
        if is_id_like:
            return value
        if station_type == "水库站":
            return original_get_station_id(value, station_type)
        if station_type is not None and station_type not in utils._STATION_TYPE_APIS:
            return original_get_station_id(value, station_type)

        station_types = (
            [station_type]
            if station_type in {"河道站", "雨量站"}
            else list(utils._STATION_TYPE_APIS)
        )
        matches: list[dict[str, Any]] = []
        for current_type in station_types:
            endpoint = utils._STATION_TYPE_APIS[current_type]
            for item in search_station_api(value, endpoint):
                candidate = dict(item)
                candidate.setdefault("station_type", current_type)
                matches.append(candidate)
        unique = {
            (str(item.get("station_type") or ""), str(item.get("stcd") or "")): item
            for item in matches
            if item.get("stcd")
        }
        if len(unique) == 1:
            return next(iter(unique))[1]
        if len(unique) > 1:
            candidates = "; ".join(
                (
                    f"{item.get('stnm')}({stcd})，类型: {current_type}"
                    f"，流域: {item.get('basin_name') or '未知'}"
                )
                for (current_type, stcd), item in sorted(unique.items())
            )
            raise ValueError(
                f"站名'{value}'匹配到多个站点: {candidates}。"
                "请指定站码或所属流域。"
            )
        raise ValueError(
            f"未找到匹配{station_type or '站点'}: '{value}'。"
            "站点索引来自各流域测站列表，请确认名称或直接使用站码(stcd)。"
        )

    def init_station_caches() -> None:
        if utils._CACHE_INIT_ATTEMPTED:
            return
        utils._CACHE_INIT_ATTEMPTED = True
        cached_records, fresh = load_disk_index()
        if cached_records:
            state["records"] = deduplicate(cached_records)
            state["full_ready"] = fresh
            populate_caches(state["records"])
            log(
                "info",
                f"已读取{'有效' if fresh else '过期'}站点索引缓存: {index_path}",
            )
            return

        reservoir_records = deduplicate(fetch_reservoir_records())
        state["records"] = reservoir_records
        populate_caches(reservoir_records)
        log("info", "河道站/雨量站索引将在首次名称查询时构建")

    utils._search_station_api = search_station_api
    utils._init_station_caches = init_station_caches
    utils.get_station_id = get_station_id
    log("info", "已加载 zhixun-core v2 站点名称索引兼容层")
