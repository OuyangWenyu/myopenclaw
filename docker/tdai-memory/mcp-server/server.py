#!/usr/bin/env python3
"""
TDAI Memory MCP Server — CC飞总 接入 TencentDB Agent Memory Gateway.

Exposes 4 tools to Claude Code via MCP stdio protocol:
  - memory_search      → POST /search/memories (L1 atomic facts)
  - conversation_search → POST /search/conversations (L0 raw conversations)
  - read_scenario      → POST /recall (L2 scenario context)
  - read_core          → persona.md + checkpoint.json (L3 persona)

Env vars:
  TDAI_GATEWAY_URL  — Gateway HTTP address (default: http://tdai-memory:8420)
  TDAI_DATA_DIR     — Path to memory data for persona.md read (default: /home/node/.myagentdata/tdai-memory)
"""

import os
import json
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

GATEWAY_URL = os.environ.get("TDAI_GATEWAY_URL", "http://tdai-memory:8420")
DATA_DIR = os.environ.get("TDAI_DATA_DIR", "/home/node/.myagentdata/tdai-memory")

mcp = FastMCP("tdai-memory")


async def _gateway_post(endpoint: str, body: dict) -> dict:
    """Call a Gateway HTTP endpoint and return the JSON response."""
    url = f"{GATEWAY_URL}{endpoint}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def memory_search(query: str, limit: int = 5, type: str = "") -> str:
    """Search L1 atomic memories (structured facts, preferences, instructions).

    Args:
        query: Search query in natural language
        limit: Max results (default 5)
        type: Optional filter — 'fact', 'preference', or 'instruction'
    """
    body = {"query": query, "limit": limit}
    if type:
        body["type"] = type
    try:
        result = await _gateway_post("/search/memories", body)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error searching memories: {e}"


@mcp.tool()
async def conversation_search(query: str, limit: int = 5) -> str:
    """Search L0 raw conversation history across all sessions.

    Args:
        query: Search query in natural language
        limit: Max results (default 5)
    """
    try:
        result = await _gateway_post("/search/conversations", {"query": query, "limit": limit})
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error searching conversations: {e}"


@mcp.tool()
async def read_scenario(query: str, session_key: str = "personal_ccfeizong") -> str:
    """Recall composite memory context including L2 scenario blocks for a topic.

    Args:
        query: Topic or question to recall context for
        session_key: Session identifier (default: personal_ccfeizong)
    """
    try:
        result = await _gateway_post("/recall", {"query": query, "session_key": session_key})
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error recalling scenario: {e}"


@mcp.tool()
async def read_core() -> str:
    """Read L3 persona profile and core memories (persona.md + checkpoint.json).

    Returns the user's Golden Rules, preferences, and long-term profile.
    """
    parts = []

    persona_path = Path(DATA_DIR) / "persona.md"
    if persona_path.exists():
        parts.append(f"## Persona\n\n{persona_path.read_text()}")

    checkpoint_path = Path(DATA_DIR) / "checkpoint.json"
    if checkpoint_path.exists():
        try:
            cp = json.loads(checkpoint_path.read_text())
            parts.append(f"## Checkpoint\n\n{json.dumps(cp, ensure_ascii=False, indent=2)}")
        except (json.JSONDecodeError, OSError):
            parts.append("## Checkpoint\n\n(unable to parse checkpoint.json)")

    if not parts:
        return "No persona or core memories found yet. This will populate as agents interact with the memory system."

    return "\n\n---\n\n".join(parts)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
