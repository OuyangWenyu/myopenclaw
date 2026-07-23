#!/usr/bin/env python3
"""Verify Yuque MCP SSE authentication and its public tool inventory."""

from __future__ import annotations

import argparse
import asyncio

from mcp import ClientSession
from mcp.client.sse import sse_client


EXPECTED_TOOLS = {
    "list_docs",
    "get_doc_content",
    "get_repo_toc",
    "search_docs",
    "backup_repo",
    "collect_and_get_change_summary",
}


async def discover(url: str, key: str) -> set[str]:
    headers = {"Authorization": f"Bearer {key}"}
    async with asyncio.timeout(20):
        async with sse_client(url, headers=headers) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                result = await session.list_tools()
                return {tool.name for tool in result.tools}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--expect", choices=("success", "unauthorized"), required=True)
    args = parser.parse_args()

    try:
        tools = await discover(args.url, args.key)
    except Exception:
        if args.expect == "unauthorized":
            print("unauthorized connection rejected")
            return 0
        raise

    if args.expect == "unauthorized":
        raise SystemExit("invalid credential unexpectedly connected")
    if tools != EXPECTED_TOOLS:
        raise SystemExit(f"unexpected tools: {sorted(tools)}")
    print(f"authenticated discovery returned {len(tools)} expected tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
