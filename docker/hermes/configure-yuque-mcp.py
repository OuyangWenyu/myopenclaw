#!/usr/bin/env python3
"""Idempotently provision the local Yuque MCP connection for Hermes."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import argparse

import yaml


HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/opt/data"))
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"
ENV_NAME = "MCP_YUQUE_MCP_API_KEY"
MANAGED_BY = "myopenclaw"
SKILL_SOURCE = Path(os.environ.get("YUQUE_SKILL_SOURCE", "/opt/hermes-skills/yuque-knowledge"))
SKILL_LINK = HERMES_HOME / "skills" / "yuque-knowledge"
MANAGE_SKILL_LINK = os.environ.get("YUQUE_MANAGE_SKILL_LINK", "true").lower() == "true"


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.chmod(mode)
    temp_path.replace(path)


def update_env(content: str, value: str) -> str:
    lines = content.splitlines()
    replacement = f"{ENV_NAME}={value}"
    updated: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{ENV_NAME}="):
            if not found:
                updated.append(replacement)
                found = True
        else:
            updated.append(line)
    if not found:
        updated.append(replacement)
    return "\n".join(updated) + "\n"


def remove_env(content: str) -> str:
    lines = [line for line in content.splitlines() if not line.startswith(f"{ENV_NAME}=")]
    return "\n".join(lines) + ("\n" if lines else "")


def install_skill_link() -> None:
    if not MANAGE_SKILL_LINK:
        return
    if not SKILL_SOURCE.is_dir():
        raise SystemExit("Yuque Skill source directory is unavailable")
    if SKILL_LINK.is_symlink():
        if SKILL_LINK.resolve() != SKILL_SOURCE.resolve():
            return
        return
    if SKILL_LINK.exists():
        return
    SKILL_LINK.parent.mkdir(parents=True, exist_ok=True)
    SKILL_LINK.symlink_to(SKILL_SOURCE, target_is_directory=True)


def remove_managed_skill_link() -> None:
    if not MANAGE_SKILL_LINK:
        return
    if SKILL_LINK.is_symlink() and SKILL_LINK.resolve() == SKILL_SOURCE.resolve():
        SKILL_LINK.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()

    if CONFIG_PATH.exists():
        loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
        if loaded is None:
            config: dict = {}
        elif isinstance(loaded, dict):
            config = loaded
        else:
            raise SystemExit("Hermes config.yaml must contain a mapping")
    else:
        config = {}

    servers = config.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise SystemExit("Hermes mcp_servers must contain a mapping")
    existing = servers.get("yuque-mcp")
    if args.disable:
        if not isinstance(existing, dict) or existing.get("managed_by") != MANAGED_BY:
            remove_managed_skill_link()
            return
        del servers["yuque-mcp"]
        if not servers:
            config.pop("mcp_servers", None)
        rendered_config = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        current_env = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
        atomic_write(CONFIG_PATH, rendered_config)
        if ENV_PATH.exists():
            atomic_write(ENV_PATH, remove_env(current_env))
        remove_managed_skill_link()
        return

    key = os.environ.get(ENV_NAME, "")
    if not key:
        raise SystemExit(f"{ENV_NAME} is required when Yuque MCP is enabled")
    if existing is not None and (
        not isinstance(existing, dict) or existing.get("managed_by") != MANAGED_BY
    ):
        raise SystemExit("Hermes yuque-mcp config exists and is not managed by myopenclaw")
    server = servers.setdefault("yuque-mcp", {})
    if not isinstance(server, dict):
        raise SystemExit("Hermes yuque-mcp config must contain a mapping")
    server.update(
        {
            "url": "http://yuque-mcp:18000/sse",
            "transport": "sse",
            "timeout": 900,
            "headers": {"Authorization": f"Bearer ${{{ENV_NAME}}}"},
            "enabled": True,
            "managed_by": MANAGED_BY,
        }
    )

    rendered_config = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    current_env = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    rendered_env = update_env(current_env, key)
    atomic_write(CONFIG_PATH, rendered_config)
    atomic_write(ENV_PATH, rendered_env)
    install_skill_link()


if __name__ == "__main__":
    main()
