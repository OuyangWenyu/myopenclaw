#!/usr/bin/env bash
# Wire a deployed Yuque MCP SSE service into a local Hermes installation.
#
# This script does not deploy the Yuque MCP server and does not require
# YUQUE_TOKEN on the agent machine. The server-side deployment owns YUQUE_TOKEN;
# Hermes only needs the remote SSE URL and the MCP API key used for Bearer auth.
#
# Required for enable:
#   YUQUE_MCP_URL=https://your-yuque-mcp.example.com/sse
#   MCP_API_KEY=your_remote_mcp_api_key
#
# Optional:
#   HERMES_HOME=$HOME/.hermes
#
# Usage:
#   YUQUE_MCP_URL=https://your-server/sse MCP_API_KEY=xxx ./scripts/bootstrap_hermes.sh
#   ./scripts/bootstrap_hermes.sh --url https://your-server/sse --api-key xxx
#   ./scripts/bootstrap_hermes.sh --dry-run
#   ./scripts/bootstrap_hermes.sh --disable

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME_ARG=""
ENV_HERMES_HOME="${HERMES_HOME:-}"
SERVER_NAME="yuque-mcp"
URL_ARG=""
API_KEY_ARG=""
DISABLE=false
DRY_RUN=false
FORCE=false
INSTALL_SKILL=true
ENV_FILE="${REPO_DIR}/.env"

usage() {
  cat <<'EOF'
Usage:
  bootstrap_hermes.sh [options]

Options:
  --url URL              Remote Yuque MCP SSE URL, e.g. https://host/sse
  --api-key KEY          MCP API key for Bearer auth. Not printed by the script.
  --hermes-home PATH     Hermes home directory. Default: auto-detect ~/.hermes first,
                         then $HERMES_HOME if it contains config.yaml.
  --server-name NAME     Hermes MCP server name. Default: yuque-mcp
  --env-file PATH        Optional env file to read YUQUE_MCP_URL/MCP_API_KEY from.
                         Default: <repo>/.env. The file is parsed, not sourced.
  --no-skill             Only update Hermes MCP config; do not install the skill.
  --disable              Remove the managed yuque-mcp config and managed env key.
  --force                Replace an existing unmanaged MCP server entry with the same name.
                         With --disable, also remove an unmanaged entry with the same name.
  --dry-run              Show what would change without writing files.
  -h, --help             Show this help.

Environment:
  YUQUE_MCP_URL          Remote SSE endpoint. Required unless --disable.
  MCP_API_KEY            Preferred input key variable.
  YUQUE_MCP_API_KEY      Also accepted for compatibility.
  MCP_YUQUE_MCP_API_KEY  Also accepted; this is what Hermes .env stores.
  HERMES_HOME            Hermes home directory.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      [[ $# -ge 2 ]] || { echo "Missing value for --url" >&2; exit 2; }
      URL_ARG="$2"
      shift 2
      ;;
    --api-key)
      [[ $# -ge 2 ]] || { echo "Missing value for --api-key" >&2; exit 2; }
      API_KEY_ARG="$2"
      shift 2
      ;;
    --hermes-home)
      [[ $# -ge 2 ]] || { echo "Missing value for --hermes-home" >&2; exit 2; }
      HERMES_HOME_ARG="$2"
      shift 2
      ;;
    --server-name)
      [[ $# -ge 2 ]] || { echo "Missing value for --server-name" >&2; exit 2; }
      SERVER_NAME="$2"
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || { echo "Missing value for --env-file" >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    --no-skill)
      INSTALL_SKILL=false
      shift
      ;;
    --disable)
      DISABLE=true
      shift
      ;;
    --force)
      FORCE=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

read_env_value() {
  local name="$1"
  local file="$2"
  if [[ ! -f "$file" ]]; then
    return 0
  fi
  awk -F= -v key="$name" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      sub(/^[^=]*=/, "")
      gsub(/^[[:space:]]+|[[:space:]]+$/, "")
      print
      exit
    }
  ' "$file"
}

resolve_hermes_home() {
  local default_home="${HOME}/.hermes"

  if [[ -n "$HERMES_HOME_ARG" ]]; then
    echo "$HERMES_HOME_ARG"
    return 0
  fi

  if [[ -f "${default_home}/config.yaml" ]]; then
    echo "$default_home"
    return 0
  fi

  if [[ -n "$ENV_HERMES_HOME" && -f "${ENV_HERMES_HOME}/config.yaml" ]]; then
    echo "$ENV_HERMES_HOME"
    return 0
  fi

  if [[ -d "$default_home" ]]; then
    echo "$default_home"
    return 0
  fi

  if [[ -n "$ENV_HERMES_HOME" ]]; then
    echo "$ENV_HERMES_HOME"
    return 0
  fi

  echo "$default_home"
}

HERMES_HOME="$(resolve_hermes_home)"

YUQUE_MCP_URL="${URL_ARG:-${YUQUE_MCP_URL:-$(read_env_value YUQUE_MCP_URL "$ENV_FILE")}}"
MCP_INPUT_KEY="${API_KEY_ARG:-${MCP_API_KEY:-${YUQUE_MCP_API_KEY:-${MCP_YUQUE_MCP_API_KEY:-$(read_env_value MCP_API_KEY "$ENV_FILE")}}}}"
if [[ -z "${MCP_INPUT_KEY:-}" ]]; then
  MCP_INPUT_KEY="$(read_env_value YUQUE_MCP_API_KEY "$ENV_FILE")"
fi
if [[ -z "${MCP_INPUT_KEY:-}" ]]; then
  MCP_INPUT_KEY="$(read_env_value MCP_YUQUE_MCP_API_KEY "$ENV_FILE")"
fi

export BOOTSTRAP_REPO_DIR="$REPO_DIR"
export BOOTSTRAP_HERMES_HOME="$HERMES_HOME"
export BOOTSTRAP_SERVER_NAME="$SERVER_NAME"
export BOOTSTRAP_YUQUE_MCP_URL="$YUQUE_MCP_URL"
export BOOTSTRAP_MCP_API_KEY="$MCP_INPUT_KEY"
export BOOTSTRAP_DISABLE="$DISABLE"
export BOOTSTRAP_DRY_RUN="$DRY_RUN"
export BOOTSTRAP_FORCE="$FORCE"
export BOOTSTRAP_INSTALL_SKILL="$INSTALL_SKILL"

python_works() {
  local candidate="$1"
  command -v "$candidate" >/dev/null 2>&1 || return 1
  "$candidate" -c 'print("ok")' >/dev/null 2>&1
}

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -n "$PYTHON_BIN" ]]; then
  if ! python_works "$PYTHON_BIN"; then
    echo "Configured PYTHON_BIN is not usable: $PYTHON_BIN" >&2
    exit 1
  fi
elif python_works python; then
  PYTHON_BIN=python
elif python_works python3; then
  PYTHON_BIN=python3
else
  echo "Python is required to update Hermes config.yaml safely." >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

try:
    import yaml
except ImportError:
    print(
        "PyYAML is required to update Hermes config.yaml safely. "
        "Install it with: python -m pip install PyYAML",
        file=sys.stderr,
    )
    raise SystemExit(1)


MANAGED_BY = "yuque_mcp_server"
ENV_NAME = "MCP_YUQUE_MCP_API_KEY"


def truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"


repo_dir = Path(os.environ["BOOTSTRAP_REPO_DIR"])
hermes_home = Path(os.environ["BOOTSTRAP_HERMES_HOME"]).expanduser()
server_name = os.environ["BOOTSTRAP_SERVER_NAME"]
url = os.environ.get("BOOTSTRAP_YUQUE_MCP_URL", "").strip()
api_key = os.environ.get("BOOTSTRAP_MCP_API_KEY", "").strip()
disable = truthy("BOOTSTRAP_DISABLE")
dry_run = truthy("BOOTSTRAP_DRY_RUN")
force = truthy("BOOTSTRAP_FORCE")
install_skill = truthy("BOOTSTRAP_INSTALL_SKILL")

config_path = hermes_home / "config.yaml"
env_path = hermes_home / ".env"
skill_source = repo_dir / "skills" / "yuque-knowledge"
skill_dest = hermes_home / "skills" / "yuque-knowledge"
skill_marker = skill_dest / ".installed-by-yuque-mcp-server"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def action(message: str) -> None:
    prefix = "DRY-RUN" if dry_run else "OK"
    print(f"{prefix}: {message}")


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    if dry_run:
        action(f"would write {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.chmod(mode)
    temp_path.replace(path)


def backup_file(path: Path) -> None:
    if dry_run or not path.exists():
        return
    stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup)
    backup.chmod(stat.S_IRUSR | stat.S_IWUSR)
    action(f"backed up {path} -> {backup.name}")


def load_config() -> dict:
    if not config_path.exists():
        fail(f"Hermes config not found: {config_path}. Is Hermes initialized?")
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        fail("Hermes config.yaml must contain a top-level mapping")
    return loaded


def render_config(config: dict) -> str:
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


def update_env(content: str, value: str | None) -> str:
    lines = []
    found = False
    for line in content.splitlines():
        if line.startswith(f"{ENV_NAME}="):
            if value is not None and not found:
                lines.append(f"{ENV_NAME}={value}")
                found = True
            continue
        lines.append(line)
    if value is not None and not found:
        lines.append(f"{ENV_NAME}={value}")
    return "\n".join(lines) + ("\n" if lines else "")


def configure_server(config: dict) -> bool:
    if not url:
        fail("YUQUE_MCP_URL is required unless --disable is used")
    if not api_key:
        fail("MCP_API_KEY is required unless --disable is used")

    servers = config.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        fail("Hermes config field mcp_servers must be a mapping")

    existing = servers.get(server_name)
    if existing is not None and (
        not isinstance(existing, dict) or existing.get("managed_by") != MANAGED_BY
    ) and not force:
        fail(
            f"mcp_servers.{server_name} already exists and is not managed by {MANAGED_BY}. "
            "Use --force only if replacing it is intended."
        )

    servers[server_name] = {
        "url": url,
        "transport": "sse",
        "timeout": 900,
        "headers": {"Authorization": f"Bearer ${{{ENV_NAME}}}"},
        "enabled": True,
        "managed_by": MANAGED_BY,
    }
    return True


def disable_server(config: dict) -> bool:
    changed = False
    servers = config.get("mcp_servers")
    if isinstance(servers, dict) and server_name in servers:
        existing = servers[server_name]
        if isinstance(existing, dict) and existing.get("managed_by") == MANAGED_BY or force:
            del servers[server_name]
            changed = True
            if not servers:
                config.pop("mcp_servers", None)
        else:
            fail(
                f"mcp_servers.{server_name} exists but is not managed by {MANAGED_BY}. "
                "Use --force only if removing it is intended."
            )
    return changed


def install_skill_files() -> None:
    if not install_skill:
        action("skipped skill installation")
        return
    if dry_run:
        action(f"would copy {skill_source} -> {skill_dest}")
        return
    shutil.copytree(skill_source, skill_dest, dirs_exist_ok=True)
    skill_marker.write_text(f"managed_by={MANAGED_BY}\nsource={skill_source}\n", encoding="utf-8")
    action(f"installed skill to {skill_dest}")


def remove_existing_skill_target() -> None:
    try:
        if skill_dest.is_symlink() or skill_dest.is_file():
            if dry_run:
                action(f"would remove existing skill path {skill_dest}")
                return
            skill_dest.unlink()
            return
        if skill_dest.is_dir():
            if dry_run:
                action(f"would remove existing skill directory {skill_dest}")
                return
            shutil.rmtree(skill_dest)
            return
    except OSError as exc:
        fail(
            f"Existing skill path is not accessible: {skill_dest}. "
            f"Remove it manually or fix its target, then rerun. Details: {exc}"
        )

    fail(
        f"Existing skill path is not a regular file, symlink, or accessible directory: {skill_dest}. "
        "Remove it manually or fix its target, then rerun."
    )


def skill_target_exists() -> bool:
    try:
        return skill_dest.exists() or skill_dest.is_symlink()
    except OSError as exc:
        fail(
            f"Existing skill path is not accessible: {skill_dest}. "
            f"Remove it manually or fix its target, then rerun. Details: {exc}"
        )


def prepare_skill_install() -> None:
    if not install_skill:
        return
    if not skill_source.is_dir():
        fail(f"Skill source directory not found: {skill_source}")
    if not skill_target_exists():
        return
    if skill_marker.exists():
        return
    if not force:
        fail(
            f"Skill target already exists and is not managed by {MANAGED_BY}: {skill_dest}. "
            "Use --force only if replacing it is intended."
        )
    remove_existing_skill_target()


def remove_managed_skill() -> None:
    if not install_skill or not skill_dest.exists():
        return
    if skill_marker.exists() or force:
        if dry_run:
            action(f"would remove managed skill {skill_dest}")
            return
        shutil.rmtree(skill_dest)
        action(f"removed managed skill {skill_dest}")


config = load_config()
env_current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""

if disable:
    config_changed = disable_server(config)
    env_next = update_env(env_current, None)
    if config_changed:
        backup_file(config_path)
        atomic_write(config_path, render_config(config))
        action(f"removed Hermes MCP server '{server_name}'")
    else:
        action(f"Hermes MCP server '{server_name}' was not present")
    if env_next != env_current:
        backup_file(env_path)
        atomic_write(env_path, env_next)
        action(f"removed {ENV_NAME} from Hermes .env")
    remove_managed_skill()
else:
    configure_server(config)
    prepare_skill_install()
    backup_file(config_path)
    atomic_write(config_path, render_config(config))
    env_next = update_env(env_current, api_key)
    backup_file(env_path)
    atomic_write(env_path, env_next)
    install_skill_files()
    action(f"configured Hermes MCP server '{server_name}'")
    action(f"url: {url}")
    action("transport: sse")
    action("timeout: 900")
    action(f"Bearer key stored in {env_path} as {ENV_NAME}")
PY
