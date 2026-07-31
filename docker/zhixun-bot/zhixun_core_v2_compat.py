"""Runtime compatibility for the zhixun-core v2 reservoir collection API.

The current water MCP expects flat ``items`` responses and requests 200 rows
per page. zhixun-core v2 returns HAL resources under
``_embedded.reservoirs[].data`` and limits page size to 100.
"""

from __future__ import annotations

from typing import Any


def _collection_name(endpoint: str) -> str:
    return endpoint.strip("/").split("/")[-1]


def extract_collection_items(payload: dict[str, Any], endpoint: str) -> list[dict[str, Any]]:
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


def install(utils: Any) -> None:
    """Patch the loaded zhixun-agent ``utils_xz`` module in memory."""

    def search_station_api(name: str, endpoint: str) -> list[dict[str, Any]]:
        try:
            payload = utils._api_get(endpoint, params={"q": name, "page": 1, "size": 20})
        except Exception:
            return []
        return extract_collection_items(payload, endpoint)

    def init_station_caches() -> None:
        if utils._CACHED_NAME_TO_ID or utils._CACHE_INIT_ATTEMPTED:
            return
        utils._CACHE_INIT_ATTEMPTED = True

        for station_type, endpoint in utils._STATION_TYPE_APIS.items():
            by_name: dict[str, str] = {}
            if endpoint != "/reservoirs":
                utils._CACHED_BY_TYPE[station_type] = by_name
                utils.logger.info(
                    f"站点缓存: {station_type} 未提供 v2 列表接口，跳过预加载"
                )
                continue
            page = 1
            while True:
                try:
                    payload = utils._api_get(
                        endpoint,
                        params={"page": page, "size": 100},
                    )
                except Exception:
                    break

                items = extract_collection_items(payload, endpoint)
                if not items:
                    break

                for item in items:
                    stcd = str(item.get("stcd") or item.get("id") or "").strip()
                    stnm = str(item.get("stnm") or item.get("name") or "").strip()
                    if not stcd or not stnm:
                        continue
                    by_name[stnm] = stcd
                    utils._CACHED_NAME_TO_ID[stnm] = stcd
                    coordinates = item.get("outlet_coordinates")
                    if not isinstance(coordinates, dict):
                        coordinates = {}
                    utils._CACHED_INFO_BY_ID[stcd] = {
                        "name": stnm,
                        "type": "",
                        "category": station_type,
                        "lon": item.get("lon") or coordinates.get("longitude", ""),
                        "lat": item.get("lat") or coordinates.get("latitude", ""),
                        "basin_name": item.get("basin_name", ""),
                    }
                    utils._CACHED_IDS_BY_NAME.setdefault(stnm, []).append(
                        (stcd, station_type)
                    )

                pagination = payload.get("metadata", {}).get("pagination", {})
                total_pages = int(pagination.get("pages") or 0)
                if (total_pages and page >= total_pages) or len(items) < 100:
                    break
                page += 1

            utils._CACHED_BY_TYPE[station_type] = by_name
            utils.logger.info(f"站点缓存: {station_type} {len(by_name)} 个")

        utils.logger.info(
            f"站点缓存初始化完成: 共 {len(utils._CACHED_NAME_TO_ID)} 个"
        )

    utils._search_station_api = search_station_api
    utils._init_station_caches = init_station_caches
    utils.logger.info("已加载 zhixun-core v2 水库名称查询兼容层")
