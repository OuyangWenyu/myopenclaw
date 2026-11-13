"""Behavior tests for a Feishu-scanned Hermes agent.

The tests talk to the public functions in ``scripts/bind_agent.py`` and to
the committed defaults file. They do not start a Hermes container.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = REPO_ROOT / "agents" / "hermes" / "bind-defaults.yaml"

FORBIDDEN_ENV_KEYS = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "MCP_YUQUE_MCP_API_KEY",
        "LARK_CLI_APP_SECRET",
        "MEMORY_TENCENTDB_GATEWAY_HOST",
        "MEMORY_TENCENTDB_GATEWAY_PORT",
    }
)

CHAT_SCOPES = [
    "im:message.p2p_msg:readonly",
    "im:message.group_at_msg:readonly",
    "im:message:send_as_bot",
    "im:resource",
]


def test_bind_defaults_keep_chat_scopes_and_drop_personal_keys() -> None:
    """A scanned bot may chat and call the shared model, and nothing else."""
    defaults = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    feishu = defaults["feishu"]
    assert feishu["create_only"] is True
    assert feishu["preset"] is False
    assert feishu["scopes"] == CHAT_SCOPES
    assert feishu["events"] == ["im.message.receive_v1"]
    assert feishu["user_scopes"] == []
    assert feishu["callbacks"] == []

    env_keys = set(defaults["container"]["env_keys"])
    assert env_keys.isdisjoint(FORBIDDEN_ENV_KEYS)
    assert "DEEPSEEK_API_KEY" in env_keys
    assert "GLM_API_KEY" in env_keys
    assert "FEISHU_APP_SECRET" in env_keys
    assert defaults["container"]["memory"] == "2G"
    assert defaults["container"]["cpus"] == "1.0"


def _load():
    """Load the bind module by path, matching the rest of this test suite."""
    spec = importlib.util.spec_from_file_location(
        "bind_agent_under_test",
        REPO_ROOT / "scripts" / "bind_agent.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _credential(**overrides: str):
    bind_agent = _load()
    values = {
        "client_id": "cli_test",
        "client_secret": "secret-value",
        "open_id": "ou_scanner",
        "tenant_brand": "feishu",
    }
    values.update(overrides)
    return bind_agent.Credential(**values)


def test_materialize_writes_local_memory_and_hides_the_secret(tmp_path: Path) -> None:
    """The agent home keeps local memory, and the secret stays out of bind.json."""
    bind_agent = _load()
    result = bind_agent.materialize(
        tmp_path,
        _credential(),
        model_env={"DEEPSEEK_API_KEY": "sk-test", "GLM_API_KEY": "glm-test"},
    )

    config = yaml.safe_load(
        (result.home / "profiles" / "owner" / "config.yaml").read_text(encoding="utf-8")
    )
    assert config["memory"]["provider"] == "local"
    assert config["memory"]["memory_enabled"] is True
    assert config["model"]["default"] == "deepseek-flash"

    env_path = result.home / ".env"
    env_text = env_path.read_text(encoding="utf-8")
    assert "FEISHU_APP_SECRET=secret-value" in env_text
    assert "FEISHU_ALLOWED_USERS=ou_scanner" in env_text
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

    bind_text = (result.home / "bind.json").read_text(encoding="utf-8")
    assert "secret-value" not in bind_text
    assert "cli_test" in bind_text
    assert result.home.name != ".hermes"


def test_materialize_refuses_a_missing_open_id(tmp_path: Path) -> None:
    """No open_id means no directory. Pairing mode is not a fallback."""
    bind_agent = _load()
    with pytest.raises(bind_agent.BindError):
        bind_agent.materialize(
            tmp_path,
            _credential(open_id=""),
            model_env={"DEEPSEEK_API_KEY": "sk-test", "GLM_API_KEY": "glm-test"},
        )
    assert list(tmp_path.iterdir()) == []


def test_materialize_does_not_create_a_second_home_for_the_same_app(
    tmp_path: Path,
) -> None:
    """One Feishu app id maps to one agent directory."""
    bind_agent = _load()
    model_env = {"DEEPSEEK_API_KEY": "sk-test", "GLM_API_KEY": "glm-test"}
    first = bind_agent.materialize(tmp_path, _credential(), model_env=model_env)
    second = bind_agent.materialize(tmp_path, _credential(), model_env=model_env)
    homes = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert homes == [first.home]
    assert second.home == first.home
    assert second.created is False


def test_compose_mounts_only_the_agent_home_and_publishes_no_port(
    tmp_path: Path,
) -> None:
    """The generated file cannot attach the personal Hermes home or a host port."""
    bind_agent = _load()
    result = bind_agent.materialize(
        tmp_path,
        _credential(),
        model_env={"DEEPSEEK_API_KEY": "sk-test", "GLM_API_KEY": "glm-test"},
    )
    document = yaml.safe_load(result.compose_text)
    assert document["name"] == "hermes-bind"
    service = document["services"][f"hermes-bind-{result.agent_id}"]
    assert "ports" not in service
    assert "ports" not in document
    assert service["deploy"]["resources"]["limits"]["memory"] == "2G"
    assert service["volumes"] == [
        f"{result.home}:/opt/data",
        "email-config-none:/opt/data/.config/himalaya",
        "email-tokens-none:/opt/data/.config/ortie",
    ]
    assert "secret-value" not in result.compose_text
    assert ".hermes:" not in result.compose_text
    assert document["networks"]["myopenclaw-net"]["external"] is True
    assert document["networks"]["myopenclaw-net"]["name"] == "myopenclaw_myopenclaw-net"
    assert (tmp_path / "compose.yml").read_text(encoding="utf-8") == result.compose_text


def test_register_app_options_are_create_only_and_chat_only() -> None:
    """The printed options are what registerApp would receive. No SDK call."""
    completed = subprocess.run(
        ["node", "scripts/feishu_register_app.mjs", "--print-options"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    options = json.loads(completed.stdout)
    assert options["createOnly"] is True
    assert options["addons"]["preset"] is False
    assert options["addons"]["scopes"]["tenant"] == CHAT_SCOPES
    assert options["addons"]["scopes"]["user"] == []
    assert options["addons"]["events"]["items"]["tenant"] == ["im.message.receive_v1"]
    assert options["addons"]["callbacks"]["items"] == []
    rendered = completed.stdout + completed.stderr
    for forbidden in ("docx", "drive", "contact", "client_secret"):
        assert forbidden not in rendered

    package = json.loads(
        (REPO_ROOT / "services" / "agent-bind" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    assert package["dependencies"]["@larksuiteoapi/node-sdk"] == "1.72.0"


def test_bind_page_refuses_to_listen_on_every_interface() -> None:
    """A public bind page could attach the shared model key to someone else's app."""
    bind_agent = _load()
    with pytest.raises(bind_agent.BindError):
        bind_agent.serve("0.0.0.0")
    assert "client_secret" not in bind_agent.page_html()


def test_hermes_backup_keeps_bound_agents_and_drops_env(tmp_path: Path) -> None:
    """A missing personal ~/.hermes must not skip the scanned-agent snapshot."""
    home = tmp_path / "home"
    agent = home / ".hermes-agents" / "aabbccdd"
    config = agent / "profiles" / "owner" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("memory:\n  provider: local\n", encoding="utf-8")
    (agent / ".env").write_text(
        "FEISHU_APP_SECRET=super-secret-value\n",
        encoding="utf-8",
    )
    secret_file = agent / "secrets" / "deepseek-api-key"
    secret_file.parent.mkdir()
    secret_file.write_text("sk-live-model-key", encoding="utf-8")
    (agent / "bind.json").write_text('{"client_id": "cli_test"}\n', encoding="utf-8")
    backup_root = tmp_path / "backups"
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["BACKUP_ROOT"] = str(backup_root)
    env["BACKUP_KEEP_DAYS"] = "30"
    env["BACKUP_SKIP_PRUNE"] = "1"
    completed = subprocess.run(
        ["bash", "hermes/scripts/backup.sh", "2026-11-13_151500"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    snapshot = backup_root / "hermes-agents" / "2026-11-13_151500" / "aabbccdd"
    assert (snapshot / "profiles" / "owner" / "config.yaml").is_file()
    assert (snapshot / "bind.json").is_file()
    assert not (snapshot / ".env").exists()
    copied = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in snapshot.rglob("*")
        if path.is_file()
    )
    assert "super-secret-value" not in copied
    assert not (snapshot / "secrets" / "deepseek-api-key").exists()
    assert "sk-live-model-key" not in copied

    old = backup_root / "hermes-agents" / "2020-01-01_000000" / "gone"
    old.parent.mkdir(parents=True)
    old.write_text("stale", encoding="utf-8")
    kept_day = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    kept = backup_root / "hermes-agents" / f"{kept_day}_100000" / "kept"
    kept.parent.mkdir(parents=True)
    kept.write_text("recent", encoding="utf-8")
    env.pop("BACKUP_SKIP_PRUNE", None)
    pruned = subprocess.run(
        ["bash", "hermes/scripts/backup.sh", "2026-11-13_151501"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert pruned.returncode == 0, pruned.stderr + pruned.stdout
    assert not old.parent.exists()
    assert kept.is_file()
    assert (
        backup_root / "hermes-agents" / "2026-11-13_151501" / "aabbccdd" / "bind.json"
    ).is_file()

    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    mount = "${HOME}/.hermes-agents:/root/.hermes-agents:ro"
    assert compose.count(mount) == 1
    assert compose.index(mount) > compose.index("\n  backup-cron:")
