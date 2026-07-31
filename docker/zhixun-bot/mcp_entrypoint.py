"""Start the upstream water MCP with local zhixun-core compatibility fixes."""

import runpy
from functools import wraps

import utils_xz
from zhixun_core_v2_compat import install


install(utils_xz)

# The model may decide not to make a second URL-tool call after obtaining the
# profile.  Enrich the profile response itself, so the verified detail page is
# present in the same MCP observation as the factual data.
import get_url_server
import mcp_server_xz


_get_reservoir_profile = mcp_server_xz.get_reservoir_profile


@wraps(_get_reservoir_profile)
async def get_reservoir_profile_with_related_page(*args, **kwargs):
    result = await _get_reservoir_profile(*args, **kwargs)
    if not isinstance(result, dict):
        return result

    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    reservoir = (
        result.get("stnm")
        or result.get("stcd")
        or query.get("reservoir_name")
        or query.get("stcd")
    )
    if not reservoir:
        return result

    try:
        page = await get_url_server.get_reservoir_page_url(
            reservoir_name=str(reservoir),
            page="detail",
        )
        url = page.get("URL") if isinstance(page, dict) else None
        if url:
            result["related_page"] = {
                "label": "水库详情页面",
                "url": url,
            }
    except Exception as exc:
        result["related_page_error"] = str(exc)
    return result


mcp_server_xz.get_reservoir_profile = get_reservoir_profile_with_related_page
runpy.run_module("mcp_server_unified", run_name="__main__")
