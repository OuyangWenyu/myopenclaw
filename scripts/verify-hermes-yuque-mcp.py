#!/usr/bin/env python3
"""Use Hermes' real MCP loader to verify the isolated Yuque SSE server."""

from __future__ import annotations

import os
import argparse
from pathlib import Path
import yaml

from hermes_cli.mcp_config import _probe_single_server, _resolve_mcp_server_config


EXPECTED = {
    "list_docs",
    "get_doc_content",
    "get_repo_toc",
    "search_docs",
    "backup_repo",
    "collect_and_get_change_summary",
}


parser = argparse.ArgumentParser()
parser.add_argument("--expect", choices=("present", "absent"), required=True)
args = parser.parse_args()
home = Path(os.environ.get("HOME", "/opt/data"))
config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
server = config.get("mcp_servers", {}).get("yuque-mcp")

if args.expect == "absent":
    if server is not None:
        raise SystemExit("managed Yuque server still present after disable")
    if "MCP_YUQUE_MCP_API_KEY=" in (home / ".env").read_text(encoding="utf-8"):
        raise SystemExit("managed Yuque credential still present after disable")
    print("Hermes Yuque config absent after disable")
    raise SystemExit(0)

if not isinstance(server, dict) or server.get("managed_by") != "myopenclaw":
    raise SystemExit("helper-generated managed Yuque config missing")
if server.get("headers", {}).get("Authorization") != "Bearer ${MCP_YUQUE_MCP_API_KEY}":
    raise SystemExit("helper-generated Authorization template missing")
resolved = _resolve_mcp_server_config(server)
if "${" in resolved["headers"]["Authorization"]:
    raise SystemExit("Hermes did not interpolate helper-generated credential")
tools = {name for name, _ in _probe_single_server("yuque-mcp", server)}
if tools != EXPECTED:
    raise SystemExit(f"unexpected Hermes tool inventory: {sorted(tools)}")
print("Hermes consumed helper config and discovered 6 expected Yuque tools")
