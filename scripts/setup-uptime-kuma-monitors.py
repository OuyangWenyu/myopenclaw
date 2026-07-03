#!/usr/bin/env python3
"""
Setup Uptime Kuma monitors by auto-discovering Docker services.

Connects to Uptime Kuma via Socket.IO, reads docker-compose.yml,
discovers all services, and creates HTTP + Docker container monitors.

Idempotent: safe to re-run — skips monitors that already exist.

Usage:
  python3 scripts/setup-uptime-kuma-monitors.py

Environment variables (optional, will prompt if missing):
  UPK_URL      — Uptime Kuma base URL (default: http://localhost:3001)
  UPK_USER     — Uptime Kuma admin username
  UPK_PASS     — Uptime Kuma admin password
"""

import json
import os
import re
import sys
import threading
import getpass
from pathlib import Path

import socketio

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# ── Services with HTTP endpoints ──────────────────────────────
HTTP_SERVICE_MAP = {
    "openclaw-gateway": {
        "url": "http://openclaw-gateway:18789/healthz",
        "interval": 30,
        "name": "OpenClaw Gateway",
    },
    "hermes-dashboard": {
        "url": "http://hermes-dashboard:9119",
        "interval": 60,
        "name": "Hermes Dashboard",
    },
    "aisecretary": {
        "url": "http://aisecretary:8000/health",
        "interval": 60,
        "name": "aisecretary",
    },
}

# Hermes instances don't expose standard HTTP health endpoints.
# The gateway uses WebSocket for Feishu/Discord, and the webhook port
# only responds to specific webhook paths, not to GET /.
# Monitor them via Docker container status instead.
SKIP_HTTP = {"openclaw-cli", "backup-cron", "hermes", "hermes-coder", "hermes-finance"}
SKIP_DOCKER = {"openclaw-cli"}
HTTP_DEFAULT_PORTS = {}


def parse_compose_services():
    """Parse docker-compose.yml and return service info."""
    if not COMPOSE_FILE.exists():
        print(f"❌ docker-compose.yml not found at {COMPOSE_FILE}")
        sys.exit(1)

    content = COMPOSE_FILE.read_text()
    services = []
    current_service = None
    in_services = False

    for line in content.split("\n"):
        if line.startswith("services:"):
            in_services = True
            continue
        if in_services and line.startswith("networks:"):
            break

        if not in_services:
            continue

        if line.startswith("  ") and not line.startswith("    ") and ":" in line and not line.startswith("  #"):
            key = line.split(":")[0].strip()
            if key and not key.startswith("#"):
                current_service = {
                    "service_key": key,
                    "container_name": key,
                    "has_http": False,
                }
                services.append(current_service)
            continue

        if current_service is None:
            continue

        m = re.match(r"\s{4}container_name:\s*(\S+)", line)
        if m:
            current_service["container_name"] = m.group(1)
            continue

        if re.match(r'\s{4}ports:', line):
            current_service["has_http"] = True
            continue

    return services


def build_http_monitors(services):
    """Build HTTP monitor configs."""
    monitors = []
    for svc in services:
        key = svc["service_key"]
        if key in SKIP_HTTP:
            continue

        if key in HTTP_SERVICE_MAP:
            cfg = HTTP_SERVICE_MAP[key]
            monitors.append({
                "type": "http",
                "name": cfg["name"],
                "url": cfg["url"],
                "method": "GET",
                "interval": cfg["interval"],
                "maxretries": 3,
                "resendInterval": 0,
                "timeout": 48,
                "maxredirects": 10,
                "accepted_statuscodes": ["200-299", "300-399"],
                "ignoreTls": False,
            })
        elif key in HTTP_DEFAULT_PORTS:
            hostname, port, interval, name = HTTP_DEFAULT_PORTS[key]
            monitors.append({
                "type": "http",
                "name": name,
                "url": f"http://{hostname}:{port}",
                "method": "GET",
                "interval": interval,
                "maxretries": 3,
                "resendInterval": 0,
                "timeout": 48,
                "maxredirects": 10,
                "accepted_statuscodes": ["200-299", "300-399"],
                "ignoreTls": False,
            })

    return monitors


def build_docker_monitors(services):
    """Build Docker container monitor configs."""
    monitors = []
    for svc in services:
        key = svc["service_key"]
        if key in SKIP_DOCKER:
            continue
        monitors.append({
            "type": "docker",
            "name": f"Docker: {svc['container_name']}",
            "docker_host": 1,
            "docker_container": svc["container_name"],
            "interval": 60,
            "maxretries": 3,
            "accepted_statuscodes": ["200-299"],
        })
    return monitors


def sock_call(sio, event, data=None):
    """Emit a Socket.IO event and wait for the callback response.

    Uptime Kuma's Socket.IO API uses callback functions, not ack.
    We wrap it in a future to make it synchronous.
    """
    import threading
    result = []
    error = []
    event_done = threading.Event()

    def callback(*args):
        # python-socketio drops null args, so:
        #   callback(null, result) → (result,)
        #   callback("error")       → ("error",)
        resp = args[0] if len(args) > 0 else None
        if isinstance(resp, str):
            error.append(resp)
        elif isinstance(resp, dict) and resp.get("ok") is False:
            error.append(resp.get("msg", str(resp)))
        else:
            result.append(resp)
        event_done.set()

    if data is None:
        sio.emit(event, callback=callback)
    else:
        sio.emit(event, data, callback=callback)

    if not event_done.wait(timeout=15):
        raise RuntimeError(f"Timeout waiting for '{event}' response")

    if error:
        raise RuntimeError(f"'{event}' failed: {error[0]}")
    return result[0] if result else None


def load_env_file(env_path: Path):
    """Load KEY=VALUE pairs from a .env file into os.environ (if not already set)."""
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def main():
    # Load .env from repo root so UPK_USER/UPK_PASS are auto-resolved
    load_env_file(REPO_ROOT / ".env")

    base_url = os.environ.get("UPK_URL", "http://localhost:3001").rstrip("/")
    username = os.environ.get("UPK_USER")
    password = os.environ.get("UPK_PASS")

    if not username:
        username = input("Uptime Kuma username: ").strip()
    if not password:
        password = getpass.getpass("Uptime Kuma password: ").strip()
    if not username or not password:
        print("❌ Username and password are required")
        sys.exit(1)

    # ── Connect & Login ──────────────────────────────────────
    print(f"🔌 Connecting to {base_url} ...")
    sio = socketio.Client()

    try:
        sio.connect(base_url, wait_timeout=10)
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    print("   ✅ Connected")

    try:
        print(f"🔑 Logging in as {username} ...")
        sock_call(sio, "login", {"username": username, "password": password, "token": ""})
        print("   ✅ Authenticated")
    except RuntimeError as e:
        print(f"❌ Login failed: {e}")
        sio.disconnect()
        sys.exit(1)

    # ── Discover services ────────────────────────────────────
    print(f"\n📋 Reading {COMPOSE_FILE.name} ...")
    services = parse_compose_services()
    print(f"   Found {len(services)} services:")
    for svc in services:
        extras = []
        if svc["has_http"]:
            extras.append("HTTP")
        extra_str = f" ({', '.join(extras)})" if extras else ""
        print(f"     • {svc['service_key']}{extra_str}")

    # ── Build monitor configs ────────────────────────────────
    http_monitors = build_http_monitors(services)
    docker_monitors = build_docker_monitors(services)

    print(f"\n🎯 Monitors to create:")
    print(f"   HTTP:   {len(http_monitors)}")
    for m in http_monitors:
        print(f"     • {m['name']} → {m['url']}")
    print(f"   Docker: {len(docker_monitors)}")
    for m in docker_monitors:
        print(f"     • {m['name']}")

    # ── Get existing monitors ────────────────────────────────
    # getMonitorList response comes as a separate "monitorList" event,
    # not in the callback (which only returns {ok: true}).
    print(f"\n🔍 Checking existing monitors ...")
    existing_data = {}

    def on_monitor_list(data):
        nonlocal existing_data
        if isinstance(data, dict):
            existing_data = data
        monitor_list_received.set()

    monitor_list_received = threading.Event()
    sio.on("monitorList", on_monitor_list)

    try:
        sock_call(sio, "getMonitorList")
        if not monitor_list_received.wait(timeout=10):
            print(f"   ⚠️  Timeout waiting for monitor list")
    except RuntimeError as e:
        print(f"   ⚠️  Could not request monitor list: {e}")

    # Note: can't easily detach event handler in python-socketio,
    # but we're done listening at this point
    existing = existing_data

    # getMonitorList returns {monitorID: monitor_data, ...}
    if isinstance(existing, dict):
        existing_list = list(existing.values())
    else:
        existing_list = existing if isinstance(existing, list) else []

    existing_names = {m.get("name", "") for m in existing_list if isinstance(m, dict)}
    existing_urls = {m.get("url", "") for m in existing_list if isinstance(m, dict) and m.get("url")}
    existing_containers = {
        m.get("docker_container", "") for m in existing_list
        if isinstance(m, dict) and m.get("docker_container")
    }

    # ── Create monitors ─────────────────────────────────────
    created = 0
    skipped = 0

    for monitor in http_monitors:
        if monitor["name"] in existing_names or monitor["url"] in existing_urls:
            print(f"   ⏭️  {monitor['name']} — already exists, skipping")
            skipped += 1
            continue
        try:
            sock_call(sio, "add", monitor)
            print(f"   ✅ {monitor['name']}")
            created += 1
        except RuntimeError as e:
            print(f"   ❌ {monitor['name']}: {e}")

    for monitor in docker_monitors:
        if monitor["name"] in existing_names or monitor["docker_container"] in existing_containers:
            print(f"   ⏭️  {monitor['name']} — already exists, skipping")
            skipped += 1
            continue
        try:
            sock_call(sio, "add", monitor)
            print(f"   ✅ {monitor['name']}")
            created += 1
        except RuntimeError as e:
            print(f"   ❌ {monitor['name']}: {e}")

    sio.disconnect()

    # ── Summary ──────────────────────────────────────────────
    total = len(http_monitors) + len(docker_monitors)
    print(f"\n{'='*60}")
    print(f"📊 Summary: {created} created, {skipped} skipped, {total} total")
    if created > 0:
        print(f"🌐 View: {base_url}/dashboard")
    print(f"{'='*60}")

    if created == 0 and skipped == total:
        print("\n✨ All monitors already configured — nothing to do.")
    elif created > 0:
        print(f"\n💡 Next: configure Feishu notification in Settings → Notifications")


if __name__ == "__main__":
    main()
