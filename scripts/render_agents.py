#!/usr/bin/env python3
"""Render ``agents/hermes/*.yaml`` into the hermes region of docker-compose.yml.

The compose file stays fully expanded. Several guards read it with PyYAML or
by splitting on service keys, and they never run ``docker compose config``, so
an ``extends`` block would make their negative assertions pass for the wrong
reason. This script is a dev-time tool: ``scripts/start.sh`` does not call it.

Environment order is fixed here (base, then selected bundles, then selected
MCP entries, then derived keys, then the agent's own keys). Compose's service
config hash ignores that order. Mount order is the agent's listed order, and
that order does change the hash. The same run writes ``agents/hermes/mcp.tsv``,
which ``scripts/start.sh`` reads; start.sh does not call this script.

Exit codes follow the repo convention: 0 ok, 1 the compose region drifted
(``--check``), 2 the manifests themselves are invalid.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents" / "hermes"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
MCP_TABLE_PATH = AGENTS_DIR / "mcp.tsv"
_MCP_PHASES = frozenset({"early", "late"})
_CONFIG_VARS = frozenset({"HERMES_DEFAULT_CONFIG", "CODER_CONFIG", "DAOYUAN_CONFIG"})
_SCRIPT_RE = re.compile(r"ensure_hermes_[a-z0-9_]+\.py\Z")
_STATUS_VAR_RE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_MCP_ID_RE = re.compile(r"[a-z][a-z0-9-]*\Z")

BEGIN = (
    "  # >>> GENERATED: hermes agents. Source: agents/hermes/. "
    "Regenerate: uv run --with pyyaml scripts/render_agents.py"
)
END_LINE = "  # <<< GENERATED\n"

# Kept out of the manifest schema on purpose: env order does not affect the
# config hash, so a hand-written order only makes an agent file repeat base.
_STALE_KEYS = ("fragments", "env_order")
_PROFILE_RE = re.compile(r"[a-z][a-z0-9-]*\Z")
_FEISHU_PREFIX_RE = re.compile(r"[A-Z][A-Z0-9_]*\Z")


class ManifestError(ValueError):
    """Raised when a manifest cannot be rendered without guessing."""


def load(agents_dir: Path | None = None) -> tuple[dict, list[tuple[str, dict]]]:
    """Load ``base.yaml`` and the agent files named by ``base.order``.

    Args:
        agents_dir: Directory containing the manifests. Defaults to
            ``agents/hermes`` in this repo.

    Returns:
        The base mapping and ``(file stem, agent mapping)`` pairs in order.

    Raises:
        ManifestError: A file is missing, an unlisted agent file exists, or
            the base mapping contradicts itself.
    """
    agents_dir = agents_dir or AGENTS_DIR
    base_path = agents_dir / "base.yaml"
    if not base_path.is_file():
        raise ManifestError(f"missing {base_path}")
    base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    _validate_base(base)

    agents: list[tuple[str, dict]] = []
    for name in base["order"]:
        path = agents_dir / f"{name}.yaml"
        if not path.is_file():
            raise ManifestError(f"base.order lists {name}, but {path.name} is missing")
        agent = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(agent, dict) or "service" not in agent:
            raise ManifestError(f"{path.name} must be a mapping with a service name")
        agents.append((name, agent))

    listed = set(base["order"])
    extra = sorted(
        p.stem for p in agents_dir.glob("*.yaml") if p.stem not in listed | {"base"}
    )
    if extra:
        raise ManifestError(f"agent files not listed in base.order: {extra}")

    services = [agent["service"] for _, agent in agents]
    if len(services) != len(set(services)):
        raise ManifestError(f"duplicate service names: {services}")
    _validate_mcp(base, agents)
    return base, agents


def _validate_base(base: dict) -> None:
    """Reject a base file whose names or keys overlap."""
    if not isinstance(base, dict):
        raise ManifestError("base.yaml must be a mapping")
    stale = [key for key in _STALE_KEYS if key in base]
    if stale:
        raise ManifestError(
            "base.yaml: env order is fixed by the renderer; " f"remove {stale}"
        )
    env = base.get("env") or {}
    bundles = base.get("bundles") or {}
    mounts = base.get("mounts") or {}
    groups = base.get("mount_groups") or {}

    owned: dict[str, str] = {}
    for name, mapping in bundles.items():
        if name in env:
            raise ManifestError(f"bundle name collides with a base env key: {name}")
        if not isinstance(mapping, dict):
            raise ManifestError(f"bundle {name} must be a mapping of env keys")
        for key in mapping:
            if key in env:
                raise ManifestError(f"bundle {name} duplicates base env key {key}")
            if key in owned:
                raise ManifestError(
                    f"env key {key} is in both bundles {owned[key]} and {name}"
                )
            owned[key] = name

    for name in groups:
        if name in mounts:
            raise ManifestError(f"mount group collides with mount id {name}")


def _validate_mcp(base: dict, agents: list[tuple[str, dict]]) -> None:
    """Reject an MCP catalog that does not match the agent selections.

    ``on`` is the shell wiring start.sh already consumes. It has to name
    exactly the services whose manifests list that MCP id, so neither side
    can be updated alone.
    """
    catalog = base.get("mcp") or {}
    if not isinstance(catalog, dict):
        raise ManifestError("base.yaml: mcp must be a mapping")
    env = base.get("env") or {}
    bundles = base.get("bundles") or {}
    bundle_keys = {key for mapping in bundles.values() for key in mapping}
    owned: dict[str, str] = {}
    for name, spec in catalog.items():
        if not _MCP_ID_RE.fullmatch(name):
            raise ManifestError(f"mcp id must be a lowercase name, got {name!r}")
        if not isinstance(spec, dict):
            raise ManifestError(f"mcp {name} must be a mapping")
        if name in env or name in bundles:
            raise ManifestError(f"mcp name collides with a bundle or env key: {name}")
        if spec.get("phase") not in _MCP_PHASES:
            raise ManifestError(f"mcp {name}: phase must be early or late")
        script = spec.get("script")
        if not isinstance(script, str) or not _SCRIPT_RE.fullmatch(script):
            raise ManifestError(
                f"mcp {name}: script must be an ensure_hermes_*.py file name"
            )
        mapping = spec.get("env")
        if not isinstance(mapping, dict) or not mapping:
            raise ManifestError(f"mcp {name}: env must be a non-empty mapping")
        for key, value in mapping.items():
            if not isinstance(value, str):
                raise ManifestError(
                    f"mcp {name}: {key} must be a YAML string, got {value!r}"
                )
            if key in owned:
                raise ManifestError(
                    f"env key {key} is in both mcp {owned[key]} and {name}"
                )
            if key in env or key in bundle_keys:
                raise ManifestError(f"mcp {name} duplicates env key {key}")
            owned[key] = name
        if True in spec:
            raise ManifestError(
                f"mcp {name}: quote the on key; bare on is boolean true in YAML 1.1"
            )
        on = spec.get("on")
        if not isinstance(on, dict) or not on:
            raise ManifestError(f"mcp {name}: on must list at least one service")
        for service, wiring in on.items():
            if not isinstance(wiring, dict):
                raise ManifestError(f"mcp {name}: on.{service} must be a mapping")
            config_var = wiring.get("config_var")
            status_var = wiring.get("status_var")
            if config_var not in _CONFIG_VARS:
                raise ManifestError(
                    f"mcp {name}: config_var {config_var!r} is not defined in start.sh"
                )
            if not isinstance(status_var, str) or not _STATUS_VAR_RE.fullmatch(
                status_var
            ):
                raise ManifestError(
                    f"mcp {name}: status_var must be an uppercase shell name, "
                    f"got {status_var!r}"
                )

    selected_by_service: dict[str, list[str]] = {}
    for _, agent in agents:
        service = agent["service"]
        raw = agent.get("mcp") or []
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise ManifestError(f"{service}: mcp must be a list of names")
        if len(raw) != len(set(raw)):
            raise ManifestError(f"{service}: duplicate mcp entry")
        unknown = [item for item in raw if item not in catalog]
        if unknown:
            raise ManifestError(f"{service}: unknown mcp {unknown[0]!r}")
        selected_by_service[service] = list(raw)

    for name, spec in catalog.items():
        declared = {
            service for service, items in selected_by_service.items() if name in items
        }
        wired = set(spec["on"])
        if declared != wired:
            raise ManifestError(
                f"mcp {name}: on lists {sorted(wired)}, "
                f"but agents select it on {sorted(declared)}"
            )
    status_vars = [
        wiring["status_var"]
        for spec in catalog.values()
        for wiring in spec["on"].values()
    ]
    if len(status_vars) != len(set(status_vars)):
        raise ManifestError("mcp status_var values must be unique")


def resolve_env(base: dict, agent: dict) -> list[tuple[str, str]]:
    """Resolve one agent's environment in renderer order.

    Order is ``base.env``, then each selected bundle, then each selected MCP
    entry, then keys derived from ``profile`` and ``feishu_env_prefix``, then
    the agent's own ``env``. A key
    that arrives from two of those sources is an error. A key that exists only
    in a bundle the agent does not select may still be set by the agent: the
    default profile's ``RCLONE_CONFIG`` differs from the shared-rclone bundle.

    Args:
        base: Loaded ``base.yaml``.
        agent: One agent mapping.

    Returns:
        ``(KEY, value)`` pairs. Values are strings, already in the form that
        should appear after ``=``.

    Raises:
        ManifestError: An unknown bundle, a duplicate key, a stale ordering
            field, or a non-string value.
    """
    service = agent["service"]
    stale = [key for key in _STALE_KEYS if key in agent]
    if stale:
        raise ManifestError(
            f"{service}: {stale} are not used; list bundles and set env directly"
        )

    base_env = base.get("env") or {}
    bundles = base.get("bundles") or {}
    agent_env = agent.get("env") or {}
    if not isinstance(agent_env, dict):
        raise ManifestError(f"{service}: env must be a mapping")
    selected = _selected_bundles(service, agent, bundles)

    resolved: list[tuple[str, str]] = []
    seen: set[str] = set()

    def append(key: str, value: object, origin: str) -> None:
        if key in seen:
            raise ManifestError(
                f"{service}: env key {key} is set more than once ({origin})"
            )
        seen.add(key)
        resolved.append((key, _as_text(service, key, value)))

    for key, value in base_env.items():
        append(key, value, "base env")
    for name in selected:
        for key, value in bundles[name].items():
            append(key, value, f"bundle {name}")
    catalog = base.get("mcp") or {}
    for name in _selected_mcp(service, agent, catalog):
        for key, value in catalog[name]["env"].items():
            append(key, value, f"mcp {name}")
    for key, value in _derived_env(service, agent):
        origin = "profile" if key == "HERMES_PROFILE" else "feishu_env_prefix"
        append(key, value, origin)
    for key, value in agent_env.items():
        append(key, value, "agent env")
    return resolved


def _selected_mcp(service: str, agent: dict, catalog: dict) -> list[str]:
    raw = agent.get("mcp") or []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ManifestError(f"{service}: mcp must be a list of names")
    unknown = [name for name in raw if name not in catalog]
    if unknown:
        raise ManifestError(f"{service}: unknown mcp {unknown[0]!r}")
    return raw


def mcp_rows(base: dict, agents: list[tuple[str, dict]]) -> list[tuple[str, ...]]:
    """Return TSV rows in agent order, then each agent's ``mcp`` list order."""
    catalog = base.get("mcp") or {}
    rows: list[tuple[str, ...]] = []
    for _, agent in agents:
        service = agent["service"]
        for name in _selected_mcp(service, agent, catalog):
            spec = catalog[name]
            wiring = spec["on"][service]
            rows.append(
                (
                    spec["phase"],
                    service,
                    wiring["config_var"],
                    spec["script"],
                    wiring["status_var"],
                    name,
                )
            )
    return rows


def render_mcp_table(base: dict, agents: list[tuple[str, dict]]) -> str:
    """Render ``agents/hermes/mcp.tsv``, including its comment and header."""
    lines = [
        "# Generated from agents/hermes/. "
        "Regenerate: uv run --with pyyaml scripts/render_agents.py",
        "phase\tservice\tconfig_var\tscript\tstatus_var\tserver",
    ]
    lines.extend("\t".join(row) for row in mcp_rows(base, agents))
    return "\n".join(lines) + "\n"


def _selected_bundles(service: str, agent: dict, bundles: dict) -> list[str]:
    raw = agent.get("bundles") or []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ManifestError(f"{service}: bundles must be a list of bundle names")
    if len(raw) != len(set(raw)):
        raise ManifestError(f"{service}: duplicate bundle in bundles")
    unknown = [name for name in raw if name not in bundles]
    if unknown:
        raise ManifestError(f"{service}: unknown bundle {unknown[0]!r}")
    return raw


def _derived_env(service: str, agent: dict) -> list[tuple[str, str]]:
    """Keys implied by ``profile`` and ``feishu_env_prefix``.

    ``profile`` also selects the ``--profile`` flag in the command. The Feishu
    prefix is the env-var stem: ``CODER_FEISHU`` becomes
    ``FEISHU_APP_ID=${CODER_FEISHU_APP_ID:-}``.
    """
    derived: list[tuple[str, str]] = []
    profile = agent.get("profile")
    if profile is not None:
        if not isinstance(profile, str) or not _PROFILE_RE.fullmatch(profile):
            raise ManifestError(
                f"{service}: profile must be a lowercase name, got {profile!r}"
            )
        derived.append(("HERMES_PROFILE", profile))
    prefix = agent.get("feishu_env_prefix")
    if prefix is not None:
        if not isinstance(prefix, str) or not _FEISHU_PREFIX_RE.fullmatch(prefix):
            raise ManifestError(
                f"{service}: feishu_env_prefix must be an uppercase env prefix, "
                f"got {prefix!r}"
            )
        derived.append(("FEISHU_APP_ID", f"${{{prefix}_APP_ID:-}}"))
        derived.append(("FEISHU_APP_SECRET", f"${{{prefix}_APP_SECRET:-}}"))
    return derived


def _as_text(service: str, key: str, value: object) -> str:
    if not isinstance(value, str):
        raise ManifestError(
            f"{service}: {key} must be a YAML string "
            f"(quote numbers and booleans), got {value!r}"
        )
    return value


def expand_mounts(base: dict, names: list[str], service: str) -> list[str]:
    """Expand mount ids and groups, preserving the listed order.

    Args:
        base: Loaded ``base.yaml``.
        names: Mount ids and group names, groups expanded in place.
        service: Service name, used in error messages.

    Returns:
        Volume strings in the order they must appear in compose.

    Raises:
        ManifestError: An unknown id, or a group cycle.
    """
    mounts = base.get("mounts") or {}
    groups = base.get("mount_groups") or {}
    expanded: list[str] = []

    def walk(name: str, stack: tuple[str, ...]) -> None:
        if name in stack:
            raise ManifestError(
                f"{service}: mount group cycle {' -> '.join((*stack, name))}"
            )
        if name in groups:
            for child in groups[name]:
                walk(child, (*stack, name))
            return
        if name in mounts:
            expanded.append(_as_text(service, name, mounts[name]))
            return
        raise ManifestError(f"{service}: unknown mount {name!r}")

    for name in names:
        walk(name, ())
    return expanded


def render_service(base: dict, agent: dict) -> str:
    """Render one compose service, fully expanded."""
    defaults = base["service_defaults"]
    name = agent["service"]
    lines = [f"  {name}:"]

    build = agent.get("build")
    if build:
        lines.append("    build:")
        lines.append(f"      context: {build['context']}")
        lines.append(f"      dockerfile: {build['dockerfile']}")
        lines.append("      args:")
        for arg in build.get("args") or []:
            lines.append(f"        - {arg}")

    lines.append(f"    image: {defaults['image']}")
    lines.append(f"    container_name: {agent.get('container_name', name)}")
    lines.append(f"    restart: {defaults['restart']}")

    command = agent.get("command", "gateway run")
    if agent.get("profile"):
        command = f"{command} --profile {agent['profile']}"
    if agent.get("command_extra"):
        command = f"{command} {agent['command_extra']}"
    lines.append(f"    command: {command}")

    lines.append("    ports:")
    lines.append(f'      - "{agent["port"]}"')

    lines.append("    volumes:")
    for volume in expand_mounts(base, list(agent.get("mounts") or []), name):
        lines.append(f"      - {volume}")

    lines.append("    environment:")
    for key, value in resolve_env(base, agent):
        lines.append(f"      - {key}={value}")

    if agent.get("extra_hosts"):
        lines.append("    extra_hosts:")
        for host in agent["extra_hosts"]:
            lines.append(f'      - "{host}"')

    lines.append(f'    shm_size: "{defaults["shm_size"]}"')
    lines.append("    deploy:")
    lines.append("      resources:")
    lines.append("        limits:")
    lines.append(f"          memory: {defaults['memory']}")
    lines.append(f'          cpus: "{defaults["cpus"]}"')
    lines.append("    networks:")
    lines.append(f"      - {defaults['network']}")
    return "\n".join(lines)


def render_section(base: dict, agents: list[tuple[str, dict]]) -> str:
    """Render the marked region, including its start and end markers."""
    blocks = [render_service(base, agent) for _, agent in agents]
    body = "\n\n".join(blocks)
    return f"{BEGIN}\n{body}\n{END_LINE}"


def extract_generated(text: str) -> str:
    """Return the marked region currently stored in a compose file.

    Raises:
        ManifestError: Either marker is missing.
    """
    start = text.find(BEGIN)
    if start < 0:
        raise ManifestError("generated region start marker is missing")
    end = text.find(END_LINE, start)
    if end < 0:
        raise ManifestError("generated region end marker is missing")
    return text[start : end + len(END_LINE)]


def splice(text: str, section: str) -> str:
    """Replace the marked region, or the hand-written hermes services on first use.

    The first run replaces everything from the banner above ``hermes`` through
    the line before the ``hermes-dashboard`` banner. Later runs replace only
    the marked region, so the dashboard service stays hand-written.
    """
    if not section.endswith("\n"):
        section += "\n"
    if BEGIN in text:
        start = text.find(BEGIN)
        end = text.find(END_LINE, start)
        if end < 0:
            raise ManifestError("generated region end marker is missing")
        end += len(END_LINE)
        return text[:start] + section + text[end:]

    lines = text.splitlines(keepends=True)
    start_i = next(
        (i for i, line in enumerate(lines) if line.startswith("  hermes:")),
        None,
    )
    end_i = next(
        (i for i, line in enumerate(lines) if line.startswith("  hermes-dashboard:")),
        None,
    )
    if start_i is None or end_i is None:
        raise ManifestError("could not find the hermes or hermes-dashboard service")
    if start_i > 0 and lines[start_i - 1].startswith("  #"):
        start_i -= 1
    if lines[end_i - 1].startswith("  #"):
        end_i -= 1
    if not section.endswith("\n\n"):
        section += "\n"
    return "".join(lines[:start_i]) + section + "".join(lines[end_i:])


def main(argv: list[str] | None = None) -> int:
    """Write the compose region, or report drift with ``--check``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when the compose region or mcp.tsv does not match",
    )
    args = parser.parse_args(argv)
    try:
        base, agents = load()
        section = render_section(base, agents)
        table = render_mcp_table(base, agents)
        text = COMPOSE_PATH.read_text(encoding="utf-8")
        current = extract_generated(text)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    current_table = (
        MCP_TABLE_PATH.read_text(encoding="utf-8") if MCP_TABLE_PATH.is_file() else ""
    )
    if args.check:
        drifted = []
        if current != section:
            drifted.append("docker-compose.yml hermes region")
        if current_table != table:
            drifted.append("agents/hermes/mcp.tsv")
        if drifted:
            print(
                "does not match agents/hermes/: " + ", ".join(drifted), file=sys.stderr
            )
            print(
                "Regenerate: uv run --with pyyaml scripts/render_agents.py",
                file=sys.stderr,
            )
            return 1
        return 0
    COMPOSE_PATH.write_text(splice(text, section), encoding="utf-8")
    MCP_TABLE_PATH.write_text(table, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
