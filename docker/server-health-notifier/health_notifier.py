"""Consume confirmed health events, persist delivery intent, then send cards."""

from __future__ import annotations
import argparse
import asyncio
from datetime import datetime
import fnmatch
import json
import logging
import os
from pathlib import Path
import signal
from threading import Event
import time

from health_config import load_config
from health_card import build_card
from health_state import State
from notifier_delivery import FeishuSender

LOGGER = logging.getLogger("server-health-notifier")
STATES = {"HEALTHY", "DEGRADED", "UNHEALTHY", "UNKNOWN"}
WATCHER_STATES = {"CURRENT", "PARTIAL", "ERROR", "STOPPED", "UNINITIALIZED", "DISABLED"}


class ContractError(ValueError):
    pass


def _integer(value):
    return type(value) is int and 0 <= value <= 2**63 - 1


def _timestamp(value):
    if not isinstance(value, str):
        raise ContractError("INVALID_TIMESTAMP")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ContractError("INVALID_TIMESTAMP") from None
    if stamp.tzinfo is None:
        raise ContractError("INVALID_TIMESTAMP")


def validate_payload(payload, cursor):
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
    ):
        raise ContractError("UNSUPPORTED_SCHEMA")
    latest = payload.get("latest_event_id")
    if not _integer(latest):
        raise ContractError("MISSING_OR_INVALID_LATEST_EVENT_ID")
    following = payload.get("next_cursor")
    if not _integer(following):
        raise ContractError("MISSING_OR_INVALID_NEXT_CURSOR")
    rows, watcher = payload.get("events"), payload.get("watcher")
    if not isinstance(rows, list) or len(rows) > 100 or not isinstance(watcher, dict):
        raise ContractError("INVALID_PAGE")
    if (
        watcher.get("status") not in WATCHER_STATES
        or type(watcher.get("fresh")) is not bool
        or type(watcher.get("enabled")) is not bool
    ):
        raise ContractError("INVALID_WATCHER")
    if not _integer(watcher.get("entities")) or (
        watcher.get("failed_entities") is not None
        and not _integer(watcher["failed_entities"])
    ):
        raise ContractError("INVALID_WATCHER")
    if watcher.get("last_success_at") is not None:
        _timestamp(watcher["last_success_at"])
    if cursor is not None and (
        cursor > latest or watcher.get("cursor_ahead") or watcher.get("cursor_expired")
    ):
        raise ContractError("CURSOR_DISCONTINUITY_REQUIRES_RESET")
    if cursor is not None and cursor < latest and not rows:
        raise ContractError("EMPTY_PAGE_BEFORE_LATEST")
    previous, keys = cursor or 0, set()
    for event in rows:
        if (
            not isinstance(event, dict)
            or type(event.get("schema_version")) is not int
            or event["schema_version"] != 1
        ):
            raise ContractError("INVALID_EVENT_SCHEMA")
        identity, key, entity = (
            event.get("event_id"),
            event.get("event_key"),
            event.get("entity"),
        )
        if not _integer(identity) or not previous < identity <= latest:
            raise ContractError("INVALID_EVENT_ORDER")
        if not isinstance(key, str) or not 1 <= len(key) <= 1024 or key in keys:
            raise ContractError("INVALID_EVENT_KEY")
        keys.add(key)
        previous = identity
        if not isinstance(entity, dict):
            raise ContractError("INVALID_ENTITY")
        kind, entity_key = entity.get("kind"), entity.get("key")
        if (
            kind not in ("server", "service", "gfs")
            or not isinstance(entity_key, str)
            or not entity_key.startswith(kind + ":")
            or len(entity_key) > 512
            or not isinstance(entity.get("label"), str)
            or type(entity.get("required")) is not bool
        ):
            raise ContractError("INVALID_ENTITY")
        old, new, transition = (
            event.get("from_state"),
            event.get("to_state"),
            event.get("transition"),
        )
        if (
            old not in STATES
            or new not in STATES
            or (old == "HEALTHY") == (new == "HEALTHY")
        ):
            raise ContractError("INVALID_HEALTH_BOUNDARY")
        if transition != ("recovered" if new == "HEALTHY" else "degraded"):
            raise ContractError("INVALID_TRANSITION")
        _timestamp(event.get("observed_at"))
        _timestamp(event.get("detected_at"))
        if not isinstance(event.get("details"), dict):
            raise ContractError("INVALID_DETAILS")
    if following != (rows[-1]["event_id"] if rows else cursor or 0):
        raise ContractError("INVALID_NEXT_CURSOR")
    healthy = watcher["enabled"] and watcher["status"] == "CURRENT" and watcher["fresh"]
    if healthy:
        _timestamp(watcher.get("last_success_at"))
    return rows, latest, following, healthy


def process_payload(config, state, payload, *, dry_run=False, preview=True):
    cursor = state.cursor()
    rows, latest, following, healthy = validate_payload(payload, cursor)
    result = {
        "seen": 0,
        "queued": 0,
        "filtered": 0,
        "baselined": False,
        "source_healthy": healthy,
    }
    if cursor is None:
        if not healthy:
            raise ContractError("CANNOT_BASELINE_UNHEALTHY_SOURCE")
        if not dry_run:
            state.baseline(latest)
        result["baselined"] = True
        return result
    selected = []
    for event in rows:
        result["seen"] += 1
        entity = event["entity"]
        if (not config.include_auxiliary and not entity["required"]) or not any(
            fnmatch.fnmatchcase(entity["key"], p) for p in config.entity_patterns
        ):
            result["filtered"] += 1
            continue
        card = build_card(event)
        if dry_run:
            if preview:
                print(json.dumps(card, ensure_ascii=False))
        else:
            selected.append((event, json.dumps(card, ensure_ascii=False)))
    if not dry_run:
        result["queued"] = state.enqueue_page(selected, following)
    return result


async def _fetch_events(url, cursor, limit, token):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    # Do not let proxy env redirect private traffic or leak bearer credentials.
    async with asyncio.timeout(30):
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + token},
            timeout=30,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            async with streamable_http_client(url, http_client=client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "get_health_events", {"since_id": cursor, "limit": limit}
                    )
    if getattr(result, "isError", False):
        raise ContractError("MCP_TOOL_ERROR")
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        payload = json.loads("\n".join(getattr(v, "text", "") for v in result.content))
    return payload


def fetch_events(url, cursor, limit, token):
    return asyncio.run(_fetch_events(url, cursor, limit, token))


def flush_outbox(state, sender, *, stopping=None):
    sent = failed = 0
    started = time.monotonic()
    for row in state.pending():
        if (
            stopping is not None and stopping.is_set()
        ) or time.monotonic() - started >= 20:
            break
        if time.time() - row["created_at"] > 86400:
            state.dead_letter(row["message_key"])
            failed += 1
            continue
        try:
            sender.send(json.loads(row["card_json"]), message_key=row["message_key"])
            state.mark_sent(row["message_key"])
            sent += 1
        except Exception:
            state.mark_failed(row["message_key"], "FEISHU_SEND_FAILED")
            failed += 1
    return sent, failed


def run_once(
    config,
    state,
    sender,
    *,
    fetch=fetch_events,
    dry_run=False,
    stopping=None,
    preview=True,
):
    state.bind_source(config.mcp_url)
    if not dry_run:
        state.bind_destination(
            config.feishu_mode
            + "|"
            + config.app_id
            + "|"
            + config.chat_id
            + "|"
            + config.webhook_url
        )
    status = {
        "last_poll_epoch": int(time.time()),
        "poll_seconds": config.poll_seconds,
        "source_healthy": False,
        "last_error": "",
        "watcher": {},
        "events_seen": 0,
        "events_queued": 0,
        "events_filtered": 0,
        "latest_event_id": None,
        "backlog": False,
    }
    try:
        for _ in range(config.max_pages):
            if stopping is not None and stopping.is_set():
                break
            payload = fetch(config.mcp_url, state.cursor() or 0, 100, config.mcp_token)
            result = process_payload(
                config, state, payload, dry_run=dry_run, preview=preview
            )
            # Only persist safe metadata, never upstream raw errors or payloads.
            watcher = payload["watcher"]
            status.update(
                source_healthy=result["source_healthy"],
                latest_event_id=payload["latest_event_id"],
                watcher={
                    k: watcher.get(k)
                    for k in (
                        "status",
                        "fresh",
                        "entities",
                        "last_success_at",
                        "failed_entities",
                    )
                },
            )
            for target, key in (
                ("events_seen", "seen"),
                ("events_queued", "queued"),
                ("events_filtered", "filtered"),
            ):
                status[target] += result[key]
            status["backlog"] = (state.cursor() or 0) < payload["latest_event_id"]
            if (
                result["baselined"]
                or dry_run
                or not status["backlog"]
                or not payload["events"]
            ):
                break
        status["last_success_epoch"] = int(time.time())
        if not status["source_healthy"]:
            status["last_error"] = "WATCHER_NOT_CURRENT"
    except Exception as exc:
        status.update(
            source_healthy=False,
            last_success_epoch=0,
            last_error=str(exc)
            if isinstance(exc, ContractError)
            else "MCP_POLL_FAILED",
        )
    sent, failed = (0, 0) if dry_run else flush_outbox(state, sender, stopping=stopping)
    status.update(
        events_sent=sent,
        send_failed=failed,
        cursor=state.cursor(),
        pending=state.pending_count(),
        oldest_pending_age=state.oldest_pending_age(),
        dead=state.dead_count(),
    )
    return status


def healthcheck(status, now=None):
    now = time.time() if now is None else now
    interval = status.get("poll_seconds", 60)
    return (
        status.get("source_healthy") is True
        and 0 <= now - status.get("last_success_epoch", 0) < max(300, 3 * interval)
        and status.get("send_failed", 0) == 0
        and (status.get("oldest_pending_age") or 0) < max(900, 10 * interval)
    )


def write_status(path, status):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(status, handle, ensure_ascii=False)
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/data/config.toml"))
    parser.add_argument("--state", type=Path, default=Path("/data/state.sqlite"))
    parser.add_argument("--status", type=Path, default=Path("/data/status.json"))
    modes = parser.add_mutually_exclusive_group()
    for option in (
        "once",
        "dry-run",
        "observe-only",
        "healthcheck",
        "reset-cursor",
        "requeue-dead",
    ):
        modes.add_argument("--" + option, action="store_true")
    args = parser.parse_args(argv)
    if args.healthcheck:
        try:
            return (
                0
                if healthcheck(json.loads(args.status.read_text(encoding="utf-8")))
                else 1
            )
        except (OSError, ValueError, TypeError):
            return 1
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    for name in ("httpx", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)
    stopping = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    try:
        if args.requeue_dead:
            with State(args.state) as state:
                LOGGER.info("requeued=%d", state.requeue_dead())
            return 0
        config = load_config(
            args.config,
            require_delivery=not (
                args.dry_run or args.observe_only or args.reset_cursor
            ),
        )
        readonly = args.dry_run or args.observe_only
        with State(args.state, readonly=readonly) as state:
            state.bind_source(config.mcp_url)
            if args.reset_cursor:
                payload = fetch_events(config.mcp_url, 0, 100, config.mcp_token)
                _, latest, _, healthy = validate_payload(payload, None)
                if not healthy:
                    raise ContractError("CANNOT_RESET_UNHEALTHY_SOURCE")
                state.baseline(latest, reset=True)
                LOGGER.warning(
                    "cursor reset; generation advanced; existing outbox retained"
                )
                return 0
            sender = None if readonly else FeishuSender(config)
            while not stopping.is_set():
                status = run_once(
                    config,
                    state,
                    sender,
                    dry_run=readonly,
                    stopping=stopping,
                    preview=not args.observe_only,
                )
                status.update(
                    mode="observe-only"
                    if args.observe_only
                    else "dry-run"
                    if args.dry_run
                    else "delivery",
                    delivery_enabled=not readonly,
                )
                if not args.dry_run:
                    write_status(args.status, status)
                LOGGER.info(
                    "poll source_healthy=%s queued=%s sent=%s pending=%s error_category=%s",
                    status["source_healthy"],
                    status["events_queued"],
                    status["events_sent"],
                    status["pending"],
                    status["last_error"],
                )
                if args.dry_run or args.once:
                    return 0 if healthcheck(status) else 1
                if not readonly:
                    state.purge_dead()
                stopping.wait(config.poll_seconds)
    except Exception:
        LOGGER.error("notifier stopped error_category=CONFIG_OR_STATE_UNAVAILABLE")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
