"""Behavior tests for the bind script's closing UX.

These tests never talk to a real Docker daemon: the ``docker`` binary on PATH
is a small shell script that records its argv and answers ``logs`` according
to ``FAKE_DOCKER_MODE``.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "feishu_register_app.mjs"

FAKE_DOCKER = """#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
if [ "$1" = "logs" ]; then
  case "${FAKE_DOCKER_MODE:-silent}" in
    connected)
      echo "[Lark] connected to wss://example.invalid/ws"
      exit 0
      ;;
    crash)
      echo "container is not running" >&2
      exit 1
      ;;
    *)
      exit 0
      ;;
  esac
fi
exit 0
"""


@pytest.fixture()
def fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    """Put a recording ``docker`` stub on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    calls = tmp_path / "calls.log"
    calls.write_text("", encoding="utf-8")
    return bin_dir, calls


def _run_node(
    args: list[str],
    fake_docker: tuple[Path, Path],
    mode: str,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    bin_dir, calls = fake_docker
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_DOCKER_CALLS"] = str(calls)
    env["FAKE_DOCKER_MODE"] = mode
    return subprocess.run(
        ["node", str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def test_wait_connection_succeeds_when_logs_show_the_websocket(
    fake_docker: tuple[Path, Path],
) -> None:
    completed = _run_node(
        [
            "--wait-connection",
            "hermes-bind-test",
            "--timeout-ms",
            "2000",
            "--interval-ms",
            "50",
        ],
        fake_docker,
        "connected",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    _, calls = fake_docker
    assert "logs --tail 200 hermes-bind-test" in calls.read_text(encoding="utf-8")


def test_wait_connection_reports_timeout_with_exit_code_three(
    fake_docker: tuple[Path, Path],
) -> None:
    completed = _run_node(
        [
            "--wait-connection",
            "hermes-bind-test",
            "--timeout-ms",
            "300",
            "--interval-ms",
            "50",
        ],
        fake_docker,
        "silent",
    )
    assert completed.returncode == 3, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["reason"]


def test_wait_connection_reports_a_docker_log_failure(
    fake_docker: tuple[Path, Path],
) -> None:
    completed = _run_node(
        [
            "--wait-connection",
            "hermes-bind-test",
            "--timeout-ms",
            "200",
            "--interval-ms",
            "50",
        ],
        fake_docker,
        "crash",
    )
    assert completed.returncode == 3, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert "docker logs failed" in payload["reason"]


def test_serve_check_refuses_an_occupied_port(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        completed = subprocess.run(
            [
                "node",
                str(SCRIPT),
                "--serve-check",
                "https://example.invalid/verify?code=abc",
                "--port",
                str(port),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    assert completed.returncode == 1, completed.stderr
    assert str(port) in completed.stderr
    assert "lsof" in completed.stderr
    assert "client_secret" not in completed.stdout + completed.stderr


def test_mark_failed_keeps_identity_and_records_the_reason(tmp_path: Path) -> None:
    home = tmp_path / "agents" / "aabbccdd"
    home.mkdir(parents=True)
    record = {
        "agent_id": "aabbccdd",
        "client_id": "cli_test",
        "open_id": "ou_scanner",
        "tenant_brand": "feishu",
        "container_name": "hermes-bind-aabbccdd",
        "created_at": "2026-11-13T00:00:00Z",
        "status": "ready",
    }
    (home / "bind.json").write_text(json.dumps(record), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/bind_agent.py",
            "--mark-failed",
            "--root",
            str(tmp_path / "agents"),
            "--agent-id",
            "aabbccdd",
            "--reason",
            "no websocket",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    updated = json.loads((home / "bind.json").read_text(encoding="utf-8"))
    assert updated["status"] == "failed"
    assert updated["failure_reason"] == "no websocket"
    assert updated["failed_at"].endswith("Z")
    assert updated["client_id"] == "cli_test"
    assert updated["open_id"] == "ou_scanner"
    assert not (home / "bind.json.tmp").exists()


def test_mark_failed_reports_a_missing_record_without_a_traceback(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/bind_agent.py",
            "--mark-failed",
            "--root",
            str(tmp_path / "agents"),
            "--agent-id",
            "deadbeef",
            "--reason",
            "gone",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "bind.json not found" in completed.stderr
    assert "Traceback" not in completed.stderr
