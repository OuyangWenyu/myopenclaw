"""Start the upstream water MCP with local zhixun-core compatibility fixes."""

import runpy

import utils_xz
from zhixun_core_v2_compat import install


install(utils_xz)
runpy.run_module("mcp_server_unified", run_name="__main__")
