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

import json
import logging
import os
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("tdai-memory-mcp")

GATEWAY_URL = os.environ.get("TDAI_GATEWAY_URL", "http://tdai-memory:8420")
DATA_DIR = os.environ.get("TDAI_DATA_DIR", "/home/node/.myagentdata/tdai-memory")

mcp = FastMCP("tdai-memory")


async def _gateway_post(endpoint: str, body: dict) -> dict:
    """Call a Gateway HTTP endpoint and return the JSON response.

    Raises specific exception types so callers can distinguish
    transient failures from permanent errors.
    """
    url = f"{GATEWAY_URL}{endpoint}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, json=body)
        except httpx.ConnectError:
            logger.error("Gateway unreachable at %s (%s)", url, endpoint)
            raise RuntimeError(f"Gateway unreachable at {url}") from None
        except httpx.TimeoutException:
            logger.error("Gateway timeout at %s (%s)", url, endpoint)
            raise RuntimeError(f"Gateway timeout at {url}") from None

        if resp.status_code >= 500:
            logger.error("Gateway server error %d for %s", resp.status_code, endpoint)
            raise RuntimeError(
                f"Gateway returned {resp.status_code} for {endpoint}"
            )

        try:
            return resp.json()
        except json.JSONDecodeError:
            ct = resp.headers.get("content-type", "unknown")
            logger.error(
                "Gateway returned non-JSON for %s (content-type=%s, body=%.200s)",
                endpoint, ct, resp.text,
            )
            raise RuntimeError(
                f"Gateway returned non-JSON response for {endpoint}"
            ) from None


def _format_tool_error(context: str, exc: Exception) -> str:
    """Return a structured error string suitable for the LLM to interpret."""
    logger.warning("%s failed: %s", context, exc)
    return json.dumps({
        "error": True,
        "context": context,
        "message": str(exc),
        "suggestion": (
            "Check that the TDAI Memory Gateway is running "
            "(docker compose ps tdai-memory) and reachable at " + GATEWAY_URL
        ) if "unreachable" in str(exc).lower() or "timeout" in str(exc).lower() else "See logs for details",
    }, ensure_ascii=False)


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
        if not result or not isinstance(result, dict):
            logger.warning("Empty or unexpected memory_search result: %s", result)
            return json.dumps({"results": "No matching memories found.", "total": 0})
        return json.dumps(result, ensure_ascii=False, indent=2)
    except RuntimeError as e:
        return _format_tool_error("memory_search", e)
    except Exception:
        logger.exception("Unexpected error in memory_search")
        return _format_tool_error("memory_search", RuntimeError("Unexpected internal error"))


@mcp.tool()
async def conversation_search(query: str, limit: int = 5) -> str:
    """Search L0 raw conversation history across all sessions.

    Args:
        query: Search query in natural language
        limit: Max results (default 5)
    """
    try:
        result = await _gateway_post("/search/conversations", {"query": query, "limit": limit})
        if not result or not isinstance(result, dict):
            logger.warning("Empty or unexpected conversation_search result: %s", result)
            return json.dumps({"results": "No matching conversation messages found.", "total": 0})
        return json.dumps(result, ensure_ascii=False, indent=2)
    except RuntimeError as e:
        return _format_tool_error("conversation_search", e)
    except Exception:
        logger.exception("Unexpected error in conversation_search")
        return _format_tool_error("conversation_search", RuntimeError("Unexpected internal error"))


@mcp.tool()
async def read_scenario(query: str, session_key: str = "personal_ccfeizong") -> str:
    """Recall composite memory context including L2 scenario blocks for a topic.

    Args:
        query: Topic or question to recall context for
        session_key: Session identifier (default: personal_ccfeizong)
    """
    try:
        result = await _gateway_post("/recall", {"query": query, "session_key": session_key})
        if not result or not isinstance(result, dict):
            logger.warning("Empty or unexpected read_scenario result: %s", result)
            return json.dumps({"context": "", "memory_count": 0})
        return json.dumps(result, ensure_ascii=False, indent=2)
    except RuntimeError as e:
        return _format_tool_error("read_scenario", e)
    except Exception:
        logger.exception("Unexpected error in read_scenario")
        return _format_tool_error("read_scenario", RuntimeError("Unexpected internal error"))


@mcp.tool()
async def read_core() -> str:
    """Read L3 persona profile and core memories (persona.md + checkpoint.json).

    Returns the user's Golden Rules, preferences, and long-term profile.
    """
    parts = []

    persona_path = Path(DATA_DIR) / "persona.md"
    if persona_path.exists():
        try:
            parts.append(f"## Persona\n\n{persona_path.read_text()}")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Failed to read persona.md: %s", e)
            parts.append(f"## Persona\n\n(unable to read persona.md: {e})")

    checkpoint_path = Path(DATA_DIR) / "checkpoint.json"
    if checkpoint_path.exists():
        try:
            cp = json.loads(checkpoint_path.read_text())
            parts.append(f"## Checkpoint\n\n{json.dumps(cp, ensure_ascii=False, indent=2)}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read checkpoint.json: %s", e)
            parts.append("## Checkpoint\n\n(unable to parse checkpoint.json)")

    if not parts:
        return "No persona or core memories found yet. This will populate as agents interact with the memory system."

    return "\n\n---\n\n".join(parts)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
