"""Read-only SSE acceptance probe; never prints keys or business records."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client


async def verify(url, secret_file, username, skip_business):
    mapping = json.loads(Path(secret_file).read_text())
    key = next(key for key, user in mapping.items() if user == username)
    origin = url.rsplit("/", 1)[0]
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        for target, method in [(url, "GET"), (origin + "/messages/", "POST")]:
            for headers in [{}, {"Authorization": "Bearer invalid-probe"}, [
                ("Authorization", f"Bearer {key}"),
                ("Authorization", "Bearer invalid-probe"),
            ]]:
                result = await client.request(method, target, headers=headers)
                if result.status_code != 401:
                    raise RuntimeError(f"Expected 401 for {method}; got {result.status_code}")
    print("Missing, invalid and duplicate authorization rejected on GET and POST", flush=True)
    async with sse_client(url, headers={"Authorization": f"Bearer {key}"},
                          timeout=10, sse_read_timeout=150) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            if "list_available_stations" not in names:
                raise RuntimeError("Expected read-only acceptance tool is missing")
            print(f"Authenticated user={username}; initialize and tools/list passed; tools={len(names)}", flush=True)
            if not skip_business:
                result = await session.call_tool("list_available_stations", {})
                if result.isError:
                    raise RuntimeError("Read-only business call returned isError=true")
                for block in result.content:
                    if getattr(block, "type", None) == "text":
                        try:
                            value = json.loads(block.text)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(value, dict) and (value.get("error") or value.get("success") is False):
                            raise RuntimeError("Read-only business payload reported an error")
                print("Real list_available_stations call passed (records not displayed)", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18201/sse")
    parser.add_argument("--keys", default="/run/secrets/water-mcp-keys.json")
    parser.add_argument("--user", default="openclaw-zhixun")
    parser.add_argument("--skip-business", action="store_true")
    args = parser.parse_args()
    asyncio.run(verify(args.url, args.keys, args.user, args.skip_business))
