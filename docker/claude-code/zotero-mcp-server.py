#!/usr/bin/env python3
"""
Zotero Web API MCP Server -- CC飞总 接入 Zotero 文献库.

Exposes 4 tools to Claude Code via MCP stdio protocol (JSON-RPC over stdin/stdout):
  - zotero_search              -> Search the Zotero library
  - zotero_get_item            -> Get full metadata for a specific item
  - zotero_get_recent          -> Get recently added items
  - zotero_get_collection_items -> Get items in a collection

Uses the Zotero Web API (https://api.zotero.org) -- no Zotero Desktop needed.
Python 3 stdlib only -- no pip dependencies.

Env vars (set in docker-compose, inherited by the MCP process):
  ZOTERO_API_KEY       -- Zotero Web API key
  ZOTERO_LIBRARY_ID    -- User or group library ID
  ZOTERO_LIBRARY_TYPE  -- "user" (default) or "group"
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.zotero.org"
API_KEY = os.environ.get("ZOTERO_API_KEY", "")
LIBRARY_ID = os.environ.get("ZOTERO_LIBRARY_ID", "")
LIBRARY_TYPE = os.environ.get("ZOTERO_LIBRARY_TYPE", "user")

# Zotero Web API has a rate limit of 1 req/sec per API key.
# A small delay ensures we stay well under it.
RATE_LIMIT_DELAY = 0.3  # seconds

_last_request_time = 0.0


def _rate_limit():
    """Ensure at least RATE_LIMIT_DELAY seconds between API calls."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)
    _last_request_time = time.monotonic()


def _api_url(path):
    """Build the full Zotero API URL for a path.

    User libraries:   /users/{id}/...
    Group libraries:  /groups/{id}/...
    """
    prefix = "users" if LIBRARY_TYPE == "user" else "groups"
    return f"{API_BASE}/{prefix}/{LIBRARY_ID}{path}"


def _api_get(path):
    """Make a GET request to the Zotero API. Returns parsed JSON or an error dict."""
    _rate_limit()
    url = _api_url(path)
    req = urllib.request.Request(url, headers={"Zotero-API-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw.strip():
                return []
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {
            "error": True,
            "status": e.code,
            "message": f"Zotero API HTTP {e.code}: {body[:500]}",
        }
    except urllib.error.URLError as e:
        return {
            "error": True,
            "message": f"Network error connecting to Zotero API: {e.reason}",
        }
    except OSError as e:
        return {
            "error": True,
            "message": f"I/O error contacting Zotero API: {e}",
        }
    except json.JSONDecodeError as e:
        return {
            "error": True,
            "message": f"Invalid JSON response from Zotero API: {e}",
        }


def _format_item(item):
    """Extract key fields from a Zotero item for concise, readable output.

    Handles both the list-item envelope (top-level key + data sub-object)
    and the raw data dict (from single-item endpoints that unwrap data).
    """
    data = item.get("data", item)
    creators = data.get("creators", [])
    creator_names = []
    for c in creators:
        name = " ".join(filter(None, [c.get("firstName", ""), c.get("lastName", "")]))
        if not name:
            name = c.get("name", "")
        if name:
            creator_names.append(name)

    result = {
        "key": data.get("key", item.get("key", "")),
        "title": data.get("title", ""),
        "itemType": data.get("itemType", ""),
        "creators": creator_names,
        "date": data.get("date", ""),
        "DOI": data.get("DOI", ""),
    }

    abstract = data.get("abstractNote", "")
    if abstract:
        result["abstractSnippet"] = abstract[:300] + ("..." if len(abstract) > 300 else "")

    url = data.get("url", "")
    if url:
        result["url"] = url

    return result


# ── Helpers ───────────────────────────────────────────────────────

def _parse_int(args, key, default):
    """Parse an integer parameter with a clear error if the value is not an int."""
    value = args.get(key, default)
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValueError(f"{key} must be an integer, got: {value!r}") from None


# ── Tool implementations ──────────────────────────────────────────

def tool_zotero_search(args):
    """Search the Zotero library by keyword."""
    query = args.get("query", "")
    limit = _parse_int(args, "limit", 10)
    params = urllib.parse.urlencode({"q": query, "limit": limit})
    result = _api_get(f"/items?{params}")

    if isinstance(result, dict) and result.get("error"):
        return json.dumps(result, ensure_ascii=False)

    items = [_format_item(item) for item in (result or [])]
    return json.dumps({
        "total": len(items),
        "query": query,
        "items": items,
    }, ensure_ascii=False, indent=2)


def tool_zotero_get_item(args):
    """Get full metadata for a specific Zotero item by key."""
    item_key = args.get("item_key", "")
    if not item_key:
        return json.dumps({"error": True, "message": "item_key is required"}, ensure_ascii=False)

    result = _api_get(f"/items/{item_key}")
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_zotero_get_recent(args):
    """Get recently added items, sorted by dateAdded descending."""
    limit = _parse_int(args, "limit", 10)
    since = args.get("since", "")
    params = {"sort": "dateAdded", "direction": "desc", "limit": limit}
    if since:
        params["since"] = since
    result = _api_get(f"/items?{urllib.parse.urlencode(params)}")

    if isinstance(result, dict) and result.get("error"):
        return json.dumps(result, ensure_ascii=False)

    items = [_format_item(item) for item in (result or [])]
    return json.dumps({
        "total": len(items),
        "items": items,
    }, ensure_ascii=False, indent=2)


def tool_zotero_get_collection_items(args):
    """Get all items in a specific Zotero collection."""
    collection_key = args.get("collection_key", "")
    if not collection_key:
        return json.dumps({
            "error": True,
            "message": "collection_key is required",
        }, ensure_ascii=False)

    limit = _parse_int(args, "limit", 20)
    result = _api_get(f"/collections/{collection_key}/items?limit={limit}")

    if isinstance(result, dict) and result.get("error"):
        return json.dumps(result, ensure_ascii=False)

    items = [_format_item(item) for item in (result or [])]
    return json.dumps({
        "total": len(items),
        "collectionKey": collection_key,
        "items": items,
    }, ensure_ascii=False, indent=2)


# ── Tool schema definitions (MCP tools/list response) ─────────────

TOOLS = [
    {
        "name": "zotero_search",
        "description": (
            "Search the Zotero library by keyword. "
            "Returns matching items with title, creators, date, DOI, and abstract snippet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (natural language keyword search)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "zotero_get_item",
        "description": (
            "Get full metadata for a specific Zotero item by its item key. "
            "Returns the complete item JSON with all fields."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_key": {
                    "type": "string",
                    "description": "Zotero item key (e.g., 'ABCD1234')",
                },
            },
            "required": ["item_key"],
        },
    },
    {
        "name": "zotero_get_recent",
        "description": (
            "Get recently added items from the Zotero library, "
            "sorted by date added (newest first)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 10)",
                },
                "since": {
                    "type": "string",
                    "description": (
                        "ISO date string to filter items added after "
                        "(e.g., '2026-01-01')"
                    ),
                },
            },
        },
    },
    {
        "name": "zotero_get_collection_items",
        "description": "Get all items in a specific Zotero collection by collection key.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_key": {
                    "type": "string",
                    "description": "Zotero collection key (e.g., 'EFGH5678')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 20)",
                },
            },
            "required": ["collection_key"],
        },
    },
]

TOOL_MAP = {
    "zotero_search": tool_zotero_search,
    "zotero_get_item": tool_zotero_get_item,
    "zotero_get_recent": tool_zotero_get_recent,
    "zotero_get_collection_items": tool_zotero_get_collection_items,
}


# ── MCP stdio JSON-RPC handler ─────────────────────────────────────

def _write_response(msg_id, result):
    """Write a JSON-RPC success response to stdout."""
    response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _write_error(msg_id, code, message):
    """Write a JSON-RPC error response to stdout."""
    response = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle_request(request):
    """Dispatch a single JSON-RPC request to the appropriate handler."""
    method = request.get("method", "")
    msg_id = request.get("id", None)
    params = request.get("params", {})

    # --- Notification (no id) ---
    if msg_id is None:
        return  # notifications need no response

    # --- initialize ---
    if method == "initialize":
        return _write_response(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "zotero-web-api",
                "version": "1.0.0",
            },
        })

    # --- tools/list ---
    if method == "tools/list":
        return _write_response(msg_id, {"tools": TOOLS})

    # --- tools/call ---
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name not in TOOL_MAP:
            return _write_error(msg_id, -32601, f"Unknown tool: {tool_name}")

        try:
            result_text = TOOL_MAP[tool_name](tool_args)
            is_error = False
            # If the tool returned a JSON error dict, check if it's an error
            try:
                parsed = json.loads(result_text)
                if isinstance(parsed, dict) and parsed.get("error"):
                    is_error = True
            except (json.JSONDecodeError, TypeError):
                pass
            return _write_response(msg_id, {
                "content": [{"type": "text", "text": result_text}],
                "isError": is_error,
            })
        except Exception as e:
            error_text = json.dumps({
                "error": True,
                "message": f"Tool execution error: {e}",
            }, ensure_ascii=False)
            return _write_response(msg_id, {
                "content": [{"type": "text", "text": error_text}],
                "isError": True,
            })

    # --- Unknown method ---
    return _write_error(msg_id, -32601, f"Unknown method: {method}")


def main():
    """Read JSON-RPC requests from stdin, write responses to stdout."""
    if not API_KEY:
        sys.stderr.write("[zotero-mcp] WARNING: ZOTERO_API_KEY is not set\n")
        sys.stderr.flush()
    if not LIBRARY_ID:
        sys.stderr.write("[zotero-mcp] WARNING: ZOTERO_LIBRARY_ID is not set\n")
        sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            _handle_request(request)
        except json.JSONDecodeError:
            sys.stderr.write(f"[zotero-mcp] Invalid JSON input: {line[:200]}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
