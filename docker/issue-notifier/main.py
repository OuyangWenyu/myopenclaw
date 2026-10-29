"""Feishu issue notifier: poll repo-scanner MCP events → group cards.

Deterministic path: no LLM, no issue/PR data ownership. The watcher in
git-contribution-stats decides what happened; this process decides who to
tell and delivers the card.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from feishu import FeishuSender, build_card
from mapping import matched_people
from state import State, snapshot

from config import Config, ConfigError, load_config

logger = logging.getLogger("issue-notifier")

DEFAULT_CONFIG = Path(os.environ.get("ISSUE_NOTIFIER_CONFIG", "/data/config.toml"))
DEFAULT_STATE = Path(os.environ.get("ISSUE_NOTIFIER_STATE", "/data/state.sqlite"))
DEFAULT_STATUS = Path(os.environ.get("ISSUE_NOTIFIER_STATUS", "/data/status.json"))

# Keep retrying transient Feishu/outage failures for a day before dead-lettering;
# a poisoned card must not silently drop a 10-minute outage.
MAX_RETRY_AGE_SECONDS = 24 * 3600
# Do not let one poll block for minutes if Feishu is blackholed.
FLUSH_BUDGET_SECONDS = 20
# Dead cards contain private titles; purge them after the documented 7 days
# even if nobody runs --purge-dead manually.
PURGE_DEAD_AFTER_DAYS = 7
PURGE_INTERVAL_SECONDS = 24 * 3600


def _age_seconds(created_at: str | None) -> float:
    # Missing/unparseable timestamps are treated as age 0 (retry forever)
    # rather than dead-lettering a card whose clock data was lost.
    if not created_at:
        return 0.0
    try:
        moment = datetime.fromisoformat(created_at)
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (datetime.now(UTC) - moment).total_seconds()

_REDACTORS = (
    (re.compile(r"(access_token|private_token|token|authorization)=([^&\s]+)", re.IGNORECASE), r"\1=[REDACTED]"),
    (re.compile(r"(Bearer\s+)[^\s]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(/bot/v2/hook/)[^\s]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"\b(?:oc|ou)_[0-9a-f]{32}\b"), "[REDACTED]"),
)


def redact_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for pattern, replacement in _REDACTORS:
        text = pattern.sub(replacement, text)
    return text[:500]


async def _fetch_events(
    url: str, since_id: int, limit: int = 100, token: str | None = None
) -> dict[str, Any]:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Bound every poll: a restarted MCP server must not leave the notifier
    # stuck in an open session (the container would go unhealthy silently).
    async with asyncio.timeout(30):
        async with httpx.AsyncClient(headers=headers, timeout=30.0) as http_client:
            async with (
                streamable_http_client(url, http_client=http_client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool(
                    "get_issue_events", {"since_id": since_id, "limit": limit}
                )
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        text = "\n".join(getattr(block, "text", "") for block in result.content)
        payload = json.loads(text)
    if not isinstance(payload, dict) or "events" not in payload:
        raise RuntimeError(f"unexpected get_issue_events response: {payload!r}")
    return payload


def fetch_events(
    url: str, since_id: int, limit: int = 100, token: str | None = None
) -> dict[str, Any]:
    return asyncio.run(_fetch_events(url, since_id, limit, token))


def _write_status(path: Path, **values: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # A manual --once can race the daemon; a shared .tmp path would let one
    # process replace the file out from under the other.
    tmp = Path(f"{path}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_failure_status(path: Path, error: str) -> None:
    """Preserve the last good counters (dead/pending/cursor) on poll failure."""
    try:
        existing = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, ValueError):
        existing = {}
    existing.update(
        last_poll_epoch=int(time.time()),
        last_success_epoch=0,
        last_error=error,
    )
    _write_status(path, **existing)


def process_payload(
    config: Config,
    state: State,
    payload: dict[str, Any],
    *,
    dry_run: bool = False,
    counters: dict[str, int] | None = None,
) -> tuple[int, int]:
    """Enqueue matched events; return (seen, queued)."""
    cursor = state.get_cursor()
    latest_raw = payload.get("latest_event_id")
    if latest_raw is None:
        # Contract drift: never silently baseline to 0 and flood history.
        raise RuntimeError("get_issue_events response missing latest_event_id")
    latest = int(latest_raw)

    if cursor is not None and cursor > latest:
        # The stats DB was restored/recreated behind our cursor. All new
        # events have ids <= cursor, so polling forever would be silent.
        logger.error(
            "cursor %s > latest_event_id %s (DB restored?); resetting", cursor, latest
        )
        if not dry_run:
            state.set_cursor(latest)
        return 0, 0

    if cursor is None and not config.notify_existing:
        if not dry_run:
            state.set_cursor(latest)
        logger.info("first run: baseline cursor=%s (notify_existing=false)", latest)
        return 0, 0

    events = payload.get("events")
    if not isinstance(events, list):
        # {"events": null} or a contract change must not look like "no events"
        # while the cursor advances.
        raise RuntimeError(f"get_issue_events events must be a list, got {type(events).__name__}")

    seen = 0
    queued = 0
    for event in events:
        seen += 1
        repo_key = (
            f"{event.get('platform')}/{event.get('owner')}/{event.get('repo')}".casefold()
        )
        if config.allowed_repos and repo_key not in config.allowed_repos:
            if counters is not None:
                counters["filtered_repo"] += 1
            continue
        event_key = event.get("event_key") or ""
        if event_key and state.is_known(event_key):
            continue
        people = matched_people(config.people, event)
        if not people:
            if counters is not None:
                counters["unmatched"] += 1
            continue
        card = build_card(event, people)
        if dry_run:
            print(json.dumps(card, ensure_ascii=False, indent=2))
        else:
            state.enqueue(int(event["event_id"]), event_key, json.dumps(card, ensure_ascii=False))
        queued += 1

    if not dry_run:
        state.set_cursor(int(payload.get("next_cursor") or cursor or 0))
    return seen, queued


def flush_outbox(state: State, sender: FeishuSender) -> tuple[int, int]:
    """Send pending cards with a per-poll budget and a 24h retry window."""
    sent = 0
    failed = 0
    started = time.time()
    for row in state.pending():
        if time.time() - started > FLUSH_BUDGET_SECONDS:
            logger.warning("flush budget reached; remaining cards retry next poll")
            break

        event_id = int(row["event_id"])
        age = _age_seconds(row["created_at"])
        if age > MAX_RETRY_AGE_SECONDS:
            error = f"retry age exceeded ({int(age)}s): {row['last_error'] or ''}"
            state.dead_letter(event_id, error)
            failed += 1
            logger.error("dead-lettered event %s after %ds: %s", event_id, age, error)
            continue

        try:
            sender.send(json.loads(row["card_json"]))
            state.mark_sent(event_id, row["event_key"])
            sent += 1
            continue
        except Exception as exc:  # noqa: BLE001 - keep the card for retry
            failed += 1
            error = redact_error(exc)
            state.mark_failed(event_id, error)
            # Per-card detail at DEBUG; one summary per poll avoids log growth
            # during a long Feishu outage.
            logger.debug(
                "send failed for event %s (attempt %d, age %ds): %s",
                event_id, int(row["attempts"]) + 1, int(age), error,
            )
    if failed:
        logger.warning(
            "flush: %d card(s) failed; will retry (pending=%d)",
            failed, state.pending_count(),
        )
    return sent, failed


def run_once(
    config: Config,
    state: State,
    sender: FeishuSender,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    now = int(time.time())
    cursor = state.get_cursor() or 0
    payload = fetch_events(config.mcp_url, cursor, token=config.mcp_token)
    watcher = payload.get("watcher") or {}
    if watcher.get("error_repos"):
        logger.warning(
            "watcher reports %s repo(s) with errors; oldest success: %s",
            watcher.get("error_repos"), watcher.get("oldest_success_at"),
        )
    counters = {"filtered_repo": 0, "unmatched": 0}
    seen, queued = process_payload(
        config, state, payload, dry_run=dry_run, counters=counters
    )
    sent, failed = (0, 0) if dry_run else flush_outbox(state, sender)
    snap = snapshot(state)
    status = {
        "last_poll_epoch": now,
        "last_success_epoch": int(time.time()),
        "events_seen": seen,
        "events_queued": queued,
        "events_filtered_repo": counters["filtered_repo"],
        "events_unmatched": counters["unmatched"],
        "placeholder_config": config.placeholder_people,
        "events_sent": sent,
        "send_failed": failed,
        "cursor": snap["cursor"],
        "pending": snap["pending"],
        "oldest_pending_age": snap["oldest_pending_age"],
        "dead": snap["dead"],
        "last_send_error": state.last_pending_error() if failed else "",
        "watcher": watcher,
        "last_error": f"{failed} send failure(s)" if failed else "",
    }
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Feishu issue notifier")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--requeue-dead", action="store_true")
    parser.add_argument("--purge-dead", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # The MCP/httpx clients log ~6 INFO lines per 60s poll; keep the notifier
    # log focused (project has a log-volume incident history).
    for noisy in ("httpx", "mcp", "mcp.client.streamable_http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        return 2

    state = State(args.state)
    sender = FeishuSender(config)

    if args.requeue_dead:
        count = state.requeue_dead()
        logger.info("requeued %d dead-lettered card(s)", count)
        state.close()
        return 0

    if args.purge_dead:
        count = state.purge_dead(older_than_days=PURGE_DEAD_AFTER_DAYS)
        logger.info(
            "purged %d dead card(s) older than %d days",
            count,
            PURGE_DEAD_AFTER_DAYS,
        )
        state.close()
        return 0

    if args.once or args.dry_run:
        try:
            status = run_once(config, state, sender, dry_run=args.dry_run)
            # --dry-run must not overwrite the daemon's health signal.
            if not args.dry_run:
                _write_status(args.status, **status)
            logger.info("run complete: %s", status)
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.error("run failed: %s", redact_error(exc))
            if not args.dry_run:
                _write_failure_status(args.status, redact_error(exc))
            return 1
        finally:
            state.close()

    last_purge = 0.0
    try:
        while True:
            try:
                status = run_once(config, state, sender)
                _write_status(args.status, **status)
                logger.info(
                    "poll: seen=%d queued=%d sent=%d cursor=%s",
                    status["events_seen"], status["events_queued"],
                    status["events_sent"], status["cursor"],
                )
            except Exception as exc:  # noqa: BLE001 - keep polling
                logger.error("poll failed: %s", redact_error(exc))
                _write_failure_status(args.status, redact_error(exc))
            if time.time() - last_purge >= PURGE_INTERVAL_SECONDS:
                try:
                    count = state.purge_dead(older_than_days=PURGE_DEAD_AFTER_DAYS)
                    if count:
                        logger.info(
                            "purged %d dead card(s) older than %d days",
                            count,
                            PURGE_DEAD_AFTER_DAYS,
                        )
                except Exception:  # noqa: BLE001 - purge must not stop polling
                    logger.exception("dead-card purge failed")
                last_purge = time.time()
            time.sleep(config.poll_seconds)
    finally:
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())
