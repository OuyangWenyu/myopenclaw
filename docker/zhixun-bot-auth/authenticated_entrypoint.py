"""Apply shared Bearer authentication before the existing compatibility entrypoint."""

import runpy
import sys

from mcp.server.fastmcp import FastMCP
from mcp_auth import BearerAuthenticationMiddleware, load_key_user_map


def install_authentication() -> None:
    keys = load_key_user_map()
    original_sse_app = FastMCP.sse_app

    def sse_app(self, *args, **kwargs):
        app = original_sse_app(self, *args, **kwargs)
        # Preserve Starlette's API for the old entrypoint's CORS setup.
        app.add_middleware(BearerAuthenticationMiddleware, key_user_map=keys)
        return app

    FastMCP.sse_app = sse_app


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "sse":
        raise RuntimeError("This compatibility entrypoint requires SSE transport")
    install_authentication()
    sys.path.insert(0, "/app/mcp_servers/water")
    runpy.run_path("/app/mcp_servers/water/mcp_entrypoint.py", run_name="__main__")
