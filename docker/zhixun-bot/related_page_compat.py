"""Attach verified Zhixun frontend pages to water-query tool results.

The upstream Water MCP exposes data tools and URL tools separately.  A model
can therefore answer from the first tool result without making the second URL
call.  This compatibility layer combines those two observations for
reservoir, river, rainfall-station and basin queries.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Awaitable, Callable, Optional, Tuple


_BJ_TIMEZONE = timezone(timedelta(hours=8))
_STATION_TYPE_ALIASES = {
    "reservoir": "reservoir",
    "水库": "reservoir",
    "水库站": "reservoir",
    "river": "river",
    "河道": "river",
    "河道站": "river",
    "rainfall": "rainfall",
    "rain": "rainfall",
    "雨量": "rainfall",
    "雨量站": "rainfall",
}
_PAGE_LABELS = {
    "reservoir-detail": "水库详情页面",
    "reservoir-monitor": "水库监测页面",
    "reservoir-warning": "水库告警页面",
    "river-monitor": "河道站监测页面",
    "river-comparison": "河道站历史对比页面",
    "rainfall": "雨量站分析页面",
    "basin-monitor": "流域雨情监测页面",
    "basin-warning": "流域风险研判页面",
    "basin-statistics": "流域雨情统计页面",
    "basin-forecast": "流域降雨预报页面",
    "basin-isoline": "流域等雨量线页面",
}


def _as_bj_iso(value: Any) -> str:
    """Convert a Water MCP ISO value to the URL tools' required +08:00 form."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_BJ_TIMEZONE)
    return parsed.astimezone(_BJ_TIMEZONE).isoformat(timespec="seconds")


def _station_identity(
    arguments: dict[str, Any],
    result: dict[str, Any],
    *argument_names: str,
) -> str:
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    candidates = [
        result.get("stnm"),
        result.get("station_name"),
        result.get("stcd"),
        summary.get("station_name"),
        summary.get("stnm"),
        summary.get("stcd"),
    ]
    for name in argument_names:
        candidates.extend((arguments.get(name), query.get(name)))
    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _basin_identity(arguments: dict[str, Any], result: dict[str, Any]) -> str:
    """Find the basin name or ID returned by a basin tool."""
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    for candidate in (
        result.get("basin_name"),
        result.get("basin_id"),
        result.get("stcd"),
        summary.get("basin_name"),
        summary.get("basin_id"),
        arguments.get("basin_name"),
        query.get("basin_name"),
    ):
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return ""


def _metric_for_url(metric: Any) -> str:
    value = str(metric or "flow").strip().lower()
    return {
        "水位": "water_level",
        "流量": "flow",
        "level": "water_level",
    }.get(value, value)


def _attach_pages(
    result: Any,
    resolved_pages: Any,
) -> Any:
    if not isinstance(result, dict):
        return result
    if isinstance(resolved_pages, tuple):
        resolved_pages = [resolved_pages]
    if not isinstance(resolved_pages, list):
        return result
    pages = []
    for page, label_key in resolved_pages:
        url = page.get("URL") if isinstance(page, dict) else None
        if url and label_key in _PAGE_LABELS:
            pages.append({"label": _PAGE_LABELS[label_key], "url": url})
    if not pages:
        return result
    # Keep the singular field for existing clients while allowing a generic
    # basin query to expose both monitoring and risk-analysis pages.
    result["related_page"] = pages[0]
    if len(pages) > 1:
        result["related_pages"] = pages
    links = "；".join(
        f"相关页面：[{item['label']}]({item['url']})" for item in pages
    )
    result["response_requirement"] = (
        "最终回复必须在正文末尾另起一行，按顺序原样写“"
        + links
        + "”；链接不得省略、改写或自行构造。"
    )
    return result


PageResolver = Callable[
    [dict[str, Any], dict[str, Any]],
    Awaitable[Optional[Tuple[Any, str]]],
]


def install(mcp_server: Any, url_server: Any) -> None:
    """Wrap supported upstream query functions before unified registration."""

    def wrap(tool_name: str, resolver: PageResolver) -> None:
        original = getattr(mcp_server, tool_name, None)
        if original is None or getattr(original, "_zhixun_related_page_wrapped", False):
            return
        signature = inspect.signature(original)

        @wraps(original)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = await original(*args, **kwargs)
            if not isinstance(result, dict):
                return result
            try:
                bound = signature.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                resolved = await resolver(dict(bound.arguments), result)
                if resolved:
                    return _attach_pages(result, resolved)
            except Exception as exc:
                result["related_page_error"] = str(exc)
            return result

        wrapped._zhixun_related_page_wrapped = True
        setattr(mcp_server, tool_name, wrapped)

    async def reservoir_profile(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        station = _station_identity(arguments, result, "reservoir_name", "stcd")
        if not station:
            return None
        include = str(arguments.get("include") or "").strip().lower()
        if include == "warning_status":
            page_name = "warning"
            label_key = "reservoir-warning"
        else:
            page_name = "detail"
            label_key = "reservoir-detail"
        page = await url_server.get_reservoir_page_url(
            reservoir_name=station,
            page=page_name,
            start_time=_as_bj_iso(arguments.get("warning_start_time")),
            end_time=_as_bj_iso(arguments.get("warning_stop_time")),
        )
        return page, label_key

    async def reservoir_list(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        # A keyword lookup is often the model's first and only call for a
        # singular reservoir question. Attach a page only when it resolves to
        # exactly one row; a multi-row list has no unambiguous target page.
        if not str(arguments.get("keyword") or "").strip():
            return None
        reservoirs = result.get("reservoirs")
        if not isinstance(reservoirs, list) or len(reservoirs) != 1:
            return None
        item = reservoirs[0] if isinstance(reservoirs[0], dict) else {}
        station = str(
            item.get("stnm")
            or item.get("name")
            or item.get("stcd")
            or ""
        ).strip()
        if not station:
            return None
        warning_type = str(arguments.get("warning_type") or "").strip()
        page_name = "warning" if warning_type else "detail"
        label_key = "reservoir-warning" if warning_type else "reservoir-detail"
        page = await url_server.get_reservoir_page_url(
            reservoir_name=station,
            page=page_name,
            start_time=_as_bj_iso(arguments.get("start_time")),
            end_time=_as_bj_iso(arguments.get("stop_time")),
        )
        return page, label_key

    async def river_detail(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        station = _station_identity(arguments, result, "station_name")
        if not station:
            return None
        page = await url_server.get_river_page_url(
            station_name=station,
            page="monitor",
        )
        return page, "river-monitor"

    async def river_warning(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        station = _station_identity(arguments, result, "station_name")
        if not station:
            return None
        page = await url_server.get_river_page_url(
            station_name=station,
            page="monitor",
            start_time=_as_bj_iso(arguments.get("start_time")),
            end_time=_as_bj_iso(arguments.get("stop_time")),
        )
        return page, "river-monitor"

    async def river_comparison(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        station = _station_identity(arguments, result, "station_name")
        if not station:
            return None
        page = await url_server.get_river_page_url(
            station_name=station,
            page="comparison",
            year1=arguments.get("year1"),
            year2=arguments.get("year2"),
            start_date=str(arguments.get("start_date") or ""),
            stop_date=str(arguments.get("stop_date") or ""),
            metric=_metric_for_url(arguments.get("metric")),
        )
        return page, "river-comparison"

    async def rainfall_detail(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        station = _station_identity(arguments, result, "station_name")
        if not station:
            return None
        page = await url_server.get_rainstation_url(rainstation_name=station)
        return page, "rainfall"

    async def rainfall_statistics(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        if str(arguments.get("scope") or "").strip().lower() != "station":
            return None
        station = _station_identity(arguments, result, "name")
        if not station:
            return None
        year = int(arguments.get("year"))
        page = await url_server.get_rainstation_url(
            rainstation_name=station,
            start_time=f"{year:04d}-01-01T00:00:00+08:00",
            end_time=f"{year + 1:04d}-01-01T00:00:00+08:00",
        )
        return page, "rainfall"

    async def basin_overview(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> list[tuple[Any, str]] | None:
        basin = _basin_identity(arguments, result)
        if not basin:
            return None
        monitor = await url_server.get_basin_rain_page_url(
            basin_name=basin,
            page="monitor",
        )
        warning = await url_server.get_basin_warning_status_url(basin_name=basin)
        return [(monitor, "basin-monitor"), (warning, "basin-warning")]

    async def basin_rainfall_summary(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        basin = _basin_identity(arguments, result)
        if not basin:
            return None
        page = await url_server.get_basin_rain_page_url(
            basin_name=basin,
            page="monitor",
            start_time=_as_bj_iso(arguments.get("start_time")),
            end_time=_as_bj_iso(arguments.get("stop_time")),
        )
        return page, "basin-monitor"

    async def basin_forecast(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        basin = _basin_identity(arguments, result)
        if not basin:
            return None
        page = await url_server.get_basin_rain_page_url(
            basin_name=basin,
            page="forecast",
            start_time=_as_bj_iso(arguments.get("start_time")),
        )
        return page, "basin-forecast"

    async def basin_warning(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        basin = _basin_identity(arguments, result)
        if not basin:
            return None
        page = await url_server.get_basin_warning_status_url(
            basin_name=basin,
            start_time=_as_bj_iso(arguments.get("start_time")),
            end_time=_as_bj_iso(arguments.get("stop_time")),
        )
        return page, "basin-warning"

    async def basin_statistics(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        if str(arguments.get("scope") or "").strip().lower() != "basin":
            return None
        basin = _basin_identity(arguments, result)
        if not basin:
            return None
        page = await url_server.get_basin_rain_page_url(
            basin_name=basin,
            page="statistics",
            year=int(arguments.get("year")),
            compare_year=arguments.get("compare_year"),
            period_type=str(arguments.get("period_type") or "month"),
        )
        return page, "basin-statistics"

    async def basin_isoline(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        basin = _basin_identity(arguments, result)
        if not basin:
            return None
        page = await url_server.get_basin_rain_page_url(
            basin_name=basin,
            page="isoline",
            date=str(arguments.get("analysis_date") or ""),
        )
        return page, "basin-isoline"

    async def station_timeseries(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        station_type = _STATION_TYPE_ALIASES.get(
            str(arguments.get("station_type") or "").strip().lower()
        )
        station = _station_identity(arguments, result, "station_name")
        if not station_type or not station:
            return None
        start_time = _as_bj_iso(arguments.get("start_time"))
        end_time = _as_bj_iso(arguments.get("stop_time"))
        if station_type == "reservoir":
            page = await url_server.get_reservoir_page_url(
                reservoir_name=station,
                page="monitor",
                start_time=start_time,
                end_time=end_time,
            )
            return page, "reservoir-monitor"
        if station_type == "river":
            page = await url_server.get_river_page_url(
                station_name=station,
                page="monitor",
                start_time=start_time,
                end_time=end_time,
            )
            return page, "river-monitor"
        page = await url_server.get_rainstation_url(
            rainstation_name=station,
            start_time=start_time,
            end_time=end_time,
        )
        return page, "rainfall"

    async def station_latest(
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[Any, str] | None:
        station_type = _STATION_TYPE_ALIASES.get(
            str(arguments.get("station_type") or "").strip().lower()
        )
        station = _station_identity(arguments, result, "station_name")
        if not station_type or not station:
            return None
        if station_type == "reservoir":
            page = await url_server.get_reservoir_page_url(
                reservoir_name=station,
                page="monitor",
            )
            return page, "reservoir-monitor"
        if station_type == "river":
            page = await url_server.get_river_page_url(
                station_name=station,
                page="monitor",
            )
            return page, "river-monitor"
        page = await url_server.get_rainstation_url(rainstation_name=station)
        return page, "rainfall"

    wrap("list_reservoirs", reservoir_list)
    wrap("get_reservoir_profile", reservoir_profile)
    wrap("get_river_station_detail", river_detail)
    wrap("get_river_warning_status", river_warning)
    wrap("get_river_historical_comparison", river_comparison)
    wrap("get_rainstation_detail", rainfall_detail)
    wrap("get_rainfall_statistics", rainfall_statistics)
    wrap("get_basin_stations", basin_overview)
    wrap("get_basin_rainfall_summary", basin_rainfall_summary)
    wrap("get_basin_rainfall_forecast", basin_forecast)
    wrap("get_basin_rainfall_complete", basin_forecast)
    wrap("get_basin_warning_status", basin_warning)
    wrap("get_basin_rainfall_isoline", basin_isoline)
    wrap("get_basin_rainfall_file", basin_forecast)
    # This second wrapper replaces the station resolver above only for
    # basin-scoped rainfall statistics.
    original_rainfall_statistics = getattr(mcp_server, "get_rainfall_statistics", None)
    if original_rainfall_statistics is not None:
        # The original function is already wrapped. Compose rather than lose
        # the station URL behavior.
        signature = inspect.signature(original_rainfall_statistics)

        @wraps(original_rainfall_statistics)
        async def rainfall_statistics_with_basin_page(*args: Any, **kwargs: Any) -> Any:
            result = await original_rainfall_statistics(*args, **kwargs)
            if not isinstance(result, dict):
                return result
            try:
                bound = signature.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                resolved = await basin_statistics(dict(bound.arguments), result)
                if resolved:
                    return _attach_pages(result, resolved)
            except Exception as exc:
                result["related_page_error"] = str(exc)
            return result

        rainfall_statistics_with_basin_page._zhixun_related_page_wrapped = True
        setattr(mcp_server, "get_rainfall_statistics", rainfall_statistics_with_basin_page)
    wrap("get_station_timeseries", station_timeseries)
    wrap("get_station_latest_data", station_latest)
