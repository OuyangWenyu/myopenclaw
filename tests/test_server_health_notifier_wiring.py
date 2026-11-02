"""Opt-in deployment and privacy wiring; never reads real env files."""

from pathlib import Path
import yaml
import tomllib

ROOT = Path(__file__).resolve().parent.parent


def test_consumer_is_opt_in_and_does_not_depend_on_local_repo_scanner():
    services = yaml.safe_load(
        (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )["services"]
    service = services["server-health-notifier"]
    assert service["profiles"] == ["server-health-notifier"]
    assert service["build"] == {
        "context": "./docker",
        "dockerfile": "server-health-notifier/Dockerfile",
    }
    assert "depends_on" not in service and "ports" not in service
    assert service["read_only"] and service["cap_drop"] == ["ALL"]
    assert "10001" in service["user"]
    assert any("server-health-notifier:/data" in v for v in service["volumes"])
    assert not any("issue-notifier:/data" in v for v in service["volumes"])
    assert all(e.startswith("HEALTH_NOTIFIER_") for e in service["environment"])
    assert services["issue-notifier"]["build"]["context"] == "./docker/issue-notifier"


def test_shared_transport_is_copied_into_both_images():
    issue = (ROOT / "docker/issue-notifier/Dockerfile").read_text(encoding="utf-8")
    health = (ROOT / "docker/server-health-notifier/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert (
        "notifier_delivery.py" in issue
        and "issue-notifier/notifier_delivery.py" in health
    )
    assert "mcp==1.28.1" in health and "--healthcheck" in health


def test_health_state_backup_and_docs_are_registered():
    assert "server-health-notifier/state.sqlite" in (
        ROOT / "scripts/backup-data.sh"
    ).read_text(encoding="utf-8")
    assert "check_server_health_notifier()" in (
        ROOT / "scripts/collect_agentops.py"
    ).read_text(encoding="utf-8")
    assert "server-health-notifier.md" in (ROOT / "mkdocs.yml").read_text(
        encoding="utf-8"
    )
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "HEALTH_NOTIFIER_MCP_TOKEN=\n" in example
    assert "HEALTH_NOTIFIER_FEISHU_APP_SECRET=\n" in example
    config = (ROOT / "config/server-health-notifier.example.toml").read_text(
        encoding="utf-8"
    )
    assert "include_auxiliary = true" in config and "people" not in tomllib.loads(config)
