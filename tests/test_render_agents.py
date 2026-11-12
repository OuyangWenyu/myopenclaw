"""Tests for the hermes agent manifest renderer.

Run: uv run --with pytest --with pyyaml pytest tests/test_render_agents.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"


def _load():
    """Load the renderer by path, matching the rest of this test suite."""
    spec = importlib.util.spec_from_file_location(
        "render_agents_under_test",
        REPO_ROOT / "scripts" / "render_agents.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


render_agents = _load()
ManifestError = render_agents.ManifestError


def _write(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


BASE = """\
order: [one]
service_defaults:
  image: img
  restart: unless-stopped
  shm_size: 1g
  memory: 4G
  cpus: "2.0"
  network: net
env:
  SHARED: shared
bundles:
  extra:
    EXTRA: extra
  other:
    OTHER: other
mounts:
  data: /data:/data
  sock: /sock:/sock
mount_groups:
  both: [data, sock]
"""

AGENT = """\
service: one
port: "1:1"
bundles: [extra]
env: {}
mounts: [both]
"""


def test_mount_group_expands_in_listed_order(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", BASE)
    _write(tmp_path, "one.yaml", AGENT)
    base, agents = render_agents.load(tmp_path)
    assert render_agents.expand_mounts(base, agents[0][1]["mounts"], "one") == [
        "/data:/data",
        "/sock:/sock",
    ]


def test_unknown_bundle_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", BASE)
    _write(
        tmp_path, "one.yaml", AGENT.replace("bundles: [extra]", "bundles: [missing]")
    )
    base, agents = render_agents.load(tmp_path)
    with pytest.raises(ManifestError, match="unknown bundle"):
        render_agents.resolve_env(base, agents[0][1])


def test_agent_env_cannot_repeat_a_base_key(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", BASE)
    _write(
        tmp_path,
        "one.yaml",
        """\
service: one
port: "1:1"
bundles: []
env:
  SHARED: other
mounts: [data]
""",
    )
    base, agents = render_agents.load(tmp_path)
    with pytest.raises(ManifestError, match="set more than once"):
        render_agents.resolve_env(base, agents[0][1])


def test_selected_bundle_cannot_override_agent_env(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", BASE)
    _write(
        tmp_path,
        "one.yaml",
        """\
service: one
port: "1:1"
bundles: [extra]
env:
  EXTRA: other
mounts: [data]
""",
    )
    base, agents = render_agents.load(tmp_path)
    with pytest.raises(ManifestError, match="set more than once"):
        render_agents.resolve_env(base, agents[0][1])


def test_agent_env_can_set_a_key_from_an_unselected_bundle(tmp_path: Path) -> None:
    """The default profile's RCLONE_CONFIG differs from shared-rclone."""
    _write(tmp_path, "base.yaml", BASE)
    _write(
        tmp_path,
        "one.yaml",
        """\
service: one
port: "1:1"
bundles: [extra]
env:
  OTHER: mine
mounts: [data]
""",
    )
    base, agents = render_agents.load(tmp_path)
    env = dict(render_agents.resolve_env(base, agents[0][1]))
    assert env["OTHER"] == "mine"
    assert env["EXTRA"] == "extra"


def test_overlapping_bundles_are_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "base.yaml",
        BASE.replace(
            "  other:\n    OTHER: other\n",
            "  other:\n    EXTRA: other\n",
        ),
    )
    _write(tmp_path, "one.yaml", AGENT)
    with pytest.raises(ManifestError, match="both bundles"):
        render_agents.load(tmp_path)


def test_stale_env_order_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", BASE)
    _write(tmp_path, "one.yaml", AGENT + "env_order: [extra]\n")
    base, agents = render_agents.load(tmp_path)
    with pytest.raises(ManifestError, match="not used"):
        render_agents.resolve_env(base, agents[0][1])


def test_env_order_is_base_then_bundles_then_derived_then_agent(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", BASE)
    _write(
        tmp_path,
        "one.yaml",
        """\
service: one
profile: coder
feishu_env_prefix: CODER_FEISHU
port: "1:1"
bundles: [other, extra]
env:
  ONLY: mine
mounts: [data]
""",
    )
    base, agents = render_agents.load(tmp_path)
    agent = agents[0][1]
    assert [key for key, _ in render_agents.resolve_env(base, agent)] == [
        "SHARED",
        "OTHER",
        "EXTRA",
        "HERMES_PROFILE",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "ONLY",
    ]
    env = dict(render_agents.resolve_env(base, agent))
    assert env["HERMES_PROFILE"] == "coder"
    assert env["FEISHU_APP_ID"] == "${CODER_FEISHU_APP_ID:-}"
    assert env["FEISHU_APP_SECRET"] == "${CODER_FEISHU_APP_SECRET:-}"
    rendered = render_agents.render_service(base, agent)
    assert "command: gateway run --profile coder" in rendered


def test_unknown_mount_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "base.yaml", BASE)
    _write(tmp_path, "one.yaml", AGENT.replace("mounts: [both]", "mounts: [nope]"))
    base, agents = render_agents.load(tmp_path)
    with pytest.raises(ManifestError, match="unknown mount"):
        render_agents.expand_mounts(base, agents[0][1]["mounts"], "one")


def test_real_manifests_list_the_four_services_once() -> None:
    base, agents = render_agents.load()
    assert list(base["bundles"]) == [
        "kanban-off",
        "gitcode",
        "xiaomi",
        "zotero",
        "server-monitoring",
        "shared-rclone",
    ]
    assert "GDRIVE_PAPERS_LOCAL_PATH" in base["bundles"]["zotero"]
    assert "fragments" not in base
    services = [agent["service"] for _, agent in agents]
    assert services == ["hermes", "hermes-coder", "hermes-finance", "hermes-daoyuan"]
    ports = [agent["port"] for _, agent in agents]
    assert len(ports) == len(set(ports))
    app_ids = []
    for _, agent in agents:
        assert "env_order" not in agent
        assert "fragments" not in agent
        env = dict(render_agents.resolve_env(base, agent))
        app_ids.append(env["FEISHU_APP_ID"])
        assert "FEISHU_APP_ID" not in (agent.get("env") or {})
        assert "HERMES_PROFILE" not in (agent.get("env") or {})
    assert app_ids == [
        "${FEISHU_APP_ID:-}",
        "${CODER_FEISHU_APP_ID:-}",
        "${FINANCE_FEISHU_APP_ID:-}",
        "${DAOYUAN_FEISHU_APP_ID:-}",
    ]
    by_service = {agent["service"]: agent for _, agent in agents}
    assert "profile" not in by_service["hermes"]
    assert by_service["hermes-coder"]["profile"] == "coder"
    assert "kanban-off" not in by_service["hermes-coder"]["bundles"]
    coder_env = dict(render_agents.resolve_env(base, by_service["hermes-coder"]))
    assert "HERMES_KANBAN_DISPATCH_IN_GATEWAY" not in coder_env


def test_compose_region_matches_manifests() -> None:
    section = render_agents.render_section(*render_agents.load())
    assert (
        render_agents.extract_generated(COMPOSE.read_text(encoding="utf-8")) == section
    )


def test_rendered_environment_is_a_string_list() -> None:
    """Guards index environment entries as 'KEY=value' strings."""
    text = "services:\n" + render_agents.render_section(*render_agents.load())
    services = yaml.safe_load(text)["services"]
    env = services["hermes"]["environment"]
    assert isinstance(env, list)
    assert all(isinstance(item, str) and "=" in item for item in env)
    assert "GLM_API_KEY=${GLM_API_KEY:-}" in env
    assert "HERMES_KANBAN_DISPATCH_IN_GATEWAY=0" in env
    assert (
        "HERMES_KANBAN_DISPATCH_IN_GATEWAY=0"
        not in services["hermes-coder"]["environment"]
    )
