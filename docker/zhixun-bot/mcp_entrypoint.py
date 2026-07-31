"""Start the upstream Water MCP with local compatibility fixes."""

import runpy

import utils_xz
from related_page_compat import install as install_related_pages
from zhixun_core_v2_compat import install


install(utils_xz)

import get_url_server
import mcp_server_xz

# Patch functions before mcp_server_unified imports and registers them.
install_related_pages(mcp_server_xz, get_url_server)
runpy.run_module("mcp_server_unified", run_name="__main__")
