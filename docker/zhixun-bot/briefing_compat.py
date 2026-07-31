"""Compatibility and routing guidance for briefing hydromodel tools."""

from __future__ import annotations

import sys
from typing import Any


def extract_hydromodels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten current and legacy hydromodel collection response shapes."""
    embedded = payload.get("_embedded")
    raw: Any = None
    if isinstance(embedded, dict):
        raw = embedded.get("hydromodels")
        if raw is None:
            raw = embedded.get("items")
    if raw is None:
        raw = payload.get("items")
    if raw is None and isinstance(payload.get("data"), list):
        raw = payload.get("data")
    if not isinstance(raw, list):
        return []

    models: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        models.append(data if isinstance(data, dict) else item)
    return models


def install(briefing_bridge: Any) -> None:
    """Patch the loaded briefing module before unified tool registration."""
    briefing_module = briefing_bridge.get_module()
    registry = sys.modules.get("forecast_registry")
    if registry is None:
        return

    def get_basin_models(basin_id: str) -> list[dict[str, Any]]:
        try:
            payload = registry._api_get(f"/basins/{basin_id}/hydromodels")
            return extract_hydromodels(payload)
        except Exception:
            return []

    def get_entry(stcd: str) -> dict[str, Any] | None:
        reservoir = registry._get_reservoir(stcd)
        if not reservoir:
            return None
        # In zhixun-core v2 a reservoir is also the basin outlet, so the
        # reservoir station code itself is the basin ID when no separate field
        # is present in the reservoir detail resource.
        basin = reservoir.get("basin")
        basin_id = (
            reservoir.get("basin_id")
            or (basin.get("id") if isinstance(basin, dict) else "")
            or str(stcd).strip()
        )
        models = get_basin_models(str(basin_id))
        model_names = [
            str(
                model.get("model_name")
                or model.get("model_code")
                or model.get("name")
                or ""
            ).strip()
            for model in models
        ]
        model_names = [name for name in model_names if name]
        plcd_list = [
            str(model.get("plcd") or "").strip()
            for model in models
            if str(model.get("plcd") or "").strip()
        ]
        return {
            "model_names": model_names,
            "model_names_norm": {name.upper() for name in model_names},
            "plcd_list": plcd_list,
            "plcd_list_norm": {name.upper() for name in plcd_list},
            "model_count": len(models),
        }

    registry._get_basin_models = get_basin_models
    registry.get_entry = get_entry
    # The briefing implementation imports get_entry into its module globals.
    briefing_module.get_entry = get_entry

    hydromodel_list = getattr(briefing_module, "hydromodel_list", None)
    if hydromodel_list is not None:
        existing_doc = hydromodel_list.__doc__ or ""
        hydromodel_list.__doc__ = (
            """仅用于已确认的简报创建/执行流程，为 item_add 获取 model_param_id。

不要用本工具回答“某流域有哪些水文模型”或“查询流域模型详情”；这类只读问题
必须使用 get_basin_hydromodel。只有用户明确要创建或执行简报，并已确认写操作后，
才能调用本工具。

"""
            + existing_doc
        )
