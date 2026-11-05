"""Health notification contracts: no network or real messages."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docker" / "issue-notifier"))
sys.path.insert(0, str(ROOT / "docker" / "server-health-notifier"))
from health_state import State  # noqa: E402
from health_config import Config, load_config  # noqa: E402
from health_card import build_card  # noqa: E402
from health_notifier import process_payload, run_once, flush_outbox, ContractError  # noqa: E402


def event(i=1, **changes):
    result = {
        "schema_version": 1,
        "event_id": i,
        "event_key": f"health-{i}",
        "entity": {
            "key": "server:weather",
            "kind": "server",
            "label": "weather",
            "required": True,
        },
        "from_state": "HEALTHY",
        "to_state": "UNKNOWN",
        "transition": "degraded",
        "observed_at": "2026-11-02T00:00:00Z",
        "detected_at": "2026-11-02T00:05:00Z",
        "details": {"reason_codes": ["DATA_STALE"]},
    }
    result.update(changes)
    return result


def page(events=None, latest=1, **watcher):
    rows = [] if events is None else events
    return {
        "schema_version": 1,
        "events": rows,
        "latest_event_id": latest,
        "next_cursor": rows[-1]["event_id"] if rows else 0,
        "watcher": {
            "enabled": True,
            "status": "CURRENT",
            "fresh": True,
            "last_success_at": "2026-11-02T00:05:00Z",
            "entities": 64,
            "cursor_ahead": False,
            "cursor_expired": False,
            **watcher,
        },
    }


def config(**changes):
    return Config(
        **{
            "mcp_url": "https://mcp.example.com:8765/mcp",
            "mcp_token": "test-key",
            **changes,
        }
    )


def test_first_run_is_silent_and_dry_run_does_not_create_state(tmp_path):
    path = tmp_path / "missing" / "state.sqlite"
    state = State(path, readonly=True)
    result = process_payload(config(), state, page([event()]), dry_run=True)
    assert result["baselined"] and state.cursor() is None and not path.parent.exists()
    state.close()
    with State(path) as state:
        assert process_payload(config(), state, page([event()]))["baselined"]
        assert state.cursor() == 1 and state.pending_count() == 0


def test_queue_and_cursor_are_atomic_and_replay_deduplicates(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        rows = page([event()])
        assert process_payload(config(), state, rows)["queued"] == 1
        assert state.cursor() == 1 and state.pending_count() == 1
        # A replay after a cursor rollback cannot queue the same event twice.
        state.conn.execute("UPDATE kv SET value='0' WHERE key='cursor'")
        state.conn.commit()
        assert process_payload(config(), state, rows)["queued"] == 0
        state.conn.execute(
            "CREATE TRIGGER reject_cursor BEFORE UPDATE ON kv WHEN NEW.key='cursor' BEGIN SELECT RAISE(ABORT,'test'); END"
        )
        with pytest.raises(Exception):
            process_payload(config(), state, page([event(2)], latest=2))
        assert state.cursor() == 1 and state.pending_count() == 1


@pytest.mark.parametrize("broken", ["latest_event_id", "next_cursor", "schema_version"])
def test_missing_contract_fields_do_not_advance_cursor(tmp_path, broken):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        payload = page([event()])
        del payload[broken]
        with pytest.raises(ContractError):
            process_payload(config(), state, payload)
        assert state.cursor() == 0 and state.pending_count() == 0


@pytest.mark.parametrize(
    "change",
    [
        {"event_id": True},
        {"event_key": ""},
        {"schema_version": 2},
        {"transition": "recovered"},
        {"to_state": "HEALTHY"},
        {
            "entity": {
                "key": "server:weather",
                "kind": "service",
                "label": "bad",
                "required": True,
            }
        },
    ],
)
def test_invalid_events_fail_whole_page(tmp_path, change):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        with pytest.raises(ContractError):
            process_payload(config(), state, page([event(**change)]))
        assert state.cursor() == 0 and state.pending_count() == 0


def test_filtered_events_still_advance_cursor(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        result = process_payload(
            config(entity_patterns=("gfs:*",)), state, page([event()])
        )
        assert (
            result["filtered"] == 1
            and state.cursor() == 1
            and not state.pending_count()
        )


def test_watcher_fault_does_not_hide_persisted_events_or_baseline_blindly(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        with pytest.raises(ContractError):
            process_payload(config(), state, page(status="UNINITIALIZED", fresh=False))
        assert state.cursor() is None
        state.baseline(0)
        result = process_payload(
            config(), state, page([event()], status="PARTIAL", fresh=False)
        )
        assert result["queued"] == 1 and not result["source_healthy"]


@pytest.mark.parametrize(
    "payload", [page(latest=0, cursor_ahead=True), page([event()], cursor_expired=True)]
)
def test_cursor_discontinuity_requires_explicit_reset(tmp_path, payload):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(2)
        with pytest.raises(ContractError):
            process_payload(config(), state, payload)
        assert state.cursor() == 2


def test_reset_creates_new_generation_without_id_collision(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        process_payload(config(), state, page([event()]))
        first = state.pending()[0]["message_key"]
        state.baseline(0, reset=True)
        process_payload(config(), state, page([event()]))
        assert state.pending_count() == 2
        assert state.pending()[1]["message_key"] != first


def test_poll_error_does_not_block_queued_delivery(tmp_path):
    class Sender:
        def send(self, card, *, message_key):
            self.called = True

    sender = Sender()

    def unavailable(*args):
        raise RuntimeError("Bearer sensitive-canary")

    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        process_payload(config(), state, page([event()]))
        status = run_once(config(), state, sender, fetch=unavailable)
        assert sender.called and status["events_sent"] == 1 and status["pending"] == 0
        assert not status["source_healthy"] and "sensitive-canary" not in json.dumps(
            status
        )


def test_pagination_drains_backlog_in_one_poll(tmp_path):
    cursors = []

    def fetch(url, cursor, limit, token):
        cursors.append(cursor)
        return page([event(cursor + 1)], latest=3)

    class Sender:
        def send(self, *args, **kwargs):
            pass

    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        status = run_once(config(), state, Sender(), fetch=fetch)
        assert (
            cursors == [0, 1, 2]
            and status["cursor"] == 3
            and status["events_sent"] == 3
        )


def test_send_failure_is_retried_then_dead_lettered(tmp_path):
    class Bad:
        def send(self, *args, **kwargs):
            raise RuntimeError("secret")

    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        process_payload(config(), state, page([event()]))
        assert flush_outbox(state, Bad()) == (0, 1)
        assert (
            state.pending_count() == 1
            and "secret" not in state.pending()[0]["last_error"]
        )
        state.conn.execute("UPDATE outbox SET created_at=0")
        state.conn.commit()
        assert flush_outbox(state, Bad()) == (0, 1)
        assert state.pending_count() == 0 and state.dead_count() == 1
        assert state.requeue_dead() == 1 and state.pending_count() == 1


def test_only_one_writer_and_readonly_can_run_alongside(tmp_path):
    path = tmp_path / "state.sqlite"
    with State(path):
        with pytest.raises(RuntimeError):
            State(path)
        with State(path, readonly=True) as reader:
            assert reader.cursor() is None
    with State(path):
        pass


def test_card_uses_plain_text_and_preserves_unknown():
    card = build_card(
        event(
            entity={
                "key": "server:weather",
                "kind": "server",
                "label": "<at id=all>",
                "required": True,
            }
        )
    )
    text = json.dumps(card, ensure_ascii=False)
    assert "UNKNOWN" in text and "<at id=all>" in text
    assert (
        "lark_md" not in text
        and "url" not in text
        and card["header"]["template"] == "orange"
    )
    recovered = build_card(
        event(from_state="UNKNOWN", to_state="HEALTHY", transition="recovered")
    )
    assert recovered["header"]["template"] == "green"


def test_config_dry_run_does_not_require_feishu_and_never_accepts_url_credentials(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HEALTH_NOTIFIER_MCP_TOKEN", "test-key")
    monkeypatch.setenv("HEALTH_NOTIFIER_MCP_URL", "https://example.com/mcp")
    cfg = load_config(tmp_path / "absent.toml", require_delivery=False)
    assert cfg.poll_seconds == 60
    monkeypatch.setenv(
        "HEALTH_NOTIFIER_MCP_URL", "https://user:password@example.com/mcp"
    )
    with pytest.raises(ValueError):
        load_config(tmp_path / "absent.toml", require_delivery=False)


def test_feishu_app_uses_stable_request_uuid_without_changing_legacy_calls(monkeypatch):
    import notifier_delivery

    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"code":0}'

    def send(request, timeout):
        captured.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr(notifier_delivery.urllib.request, "urlopen", send)
    sender = notifier_delivery.FeishuSender(config(chat_id="test-chat"))
    sender._token = "fake-token"
    sender._expires_at = 10**12
    sender.send({}, message_key="event-a")
    sender.send({}, message_key="event-a")
    sender.send({})
    assert captured[0]["uuid"] == captured[1]["uuid"]
    assert len(captured[0]["uuid"]) == 36 and "uuid" not in captured[2]


def test_invalid_later_event_does_not_enqueue_earlier_event(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        with pytest.raises(ContractError):
            process_payload(
                config(),
                state,
                page([event(), event(2, transition="invalid")], latest=2),
            )
        assert state.cursor() == 0 and state.pending_count() == 0


def test_dry_run_with_existing_state_is_bytewise_readonly(tmp_path, capsys):
    path = tmp_path / "state.sqlite"
    with State(path) as state:
        state.baseline(0)
    before = path.read_bytes()
    with State(path, readonly=True) as state:
        process_payload(config(), state, page([event()]), dry_run=True)
        assert state.cursor() == 0 and state.pending_count() == 0
    assert before == path.read_bytes()
    assert "UNKNOWN" in capsys.readouterr().out


def test_unexpected_empty_page_cannot_silently_stall(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        with pytest.raises(ContractError):
            process_payload(config(), state, page(latest=5))


def test_wrong_source_cannot_reuse_cursor_or_send_backlog(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        state.bind_source("https://example.com/mcp")
        with pytest.raises(ValueError):
            state.bind_source("https://other.example.com/mcp")


def test_single_nonhealthy_to_nonhealthy_event_is_rejected(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        with pytest.raises(ContractError):
            process_payload(
                config(),
                state,
                page([event(from_state="DEGRADED", to_state="UNHEALTHY")]),
            )


def test_healthcheck_distinguishes_poll_success_from_source_health():
    from health_notifier import healthcheck

    status = {
        "source_healthy": True,
        "last_success_epoch": 1000,
        "poll_seconds": 60,
        "send_failed": 0,
    }
    assert healthcheck(status, 1001)
    assert not healthcheck({**status, "source_healthy": False}, 1001)
    assert not healthcheck(status, 2000)
    assert not healthcheck({**status, "send_failed": 1}, 1001)


def test_agentops_optional_consumer_does_not_alarm_until_enabled(tmp_path):
    sys.path.insert(0, str(ROOT))
    from scripts.collect_agentops import check_server_health_notifier

    path = tmp_path / "absent.json"
    assert check_server_health_notifier(path, enabled=False) == []
    assert (
        check_server_health_notifier(path, enabled=True)[0]["title"]
        == "健康通知状态缺失"
    )


def test_agentops_detects_stale_source_send_failure_and_dead_cards(tmp_path):
    from datetime import datetime
    from scripts.collect_agentops import check_server_health_notifier

    path = tmp_path / "status.json"
    sample = datetime(2026, 11, 2, 10, 0, 0)
    path.write_text(
        json.dumps(
            {
                "last_success_epoch": sample.timestamp(),
                "source_healthy": False,
                "send_failed": 1,
                "pending": 2,
                "oldest_pending_age": 3600,
                "dead": 1,
            }
        ),
        encoding="utf-8",
    )
    rows = check_server_health_notifier(path, now=sample)
    assert len(rows) == 4


def test_destination_binding_prevents_accidental_retargeting(tmp_path):
    with State(tmp_path / "state.sqlite") as state:
        state.bind_destination("app|application|test-group")
        state.bind_destination("app|application|test-group")
        with pytest.raises(ValueError):
            state.bind_destination("app|application|other-group")
        assert "test-group" not in state.get("destination")


def test_bounded_pagination_reports_remaining_backlog(tmp_path):
    def fetch(url, cursor, limit, token):
        return page([event(cursor + 1)], latest=10)

    class Sender:
        def send(self, *args, **kwargs):
            pass

    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        status = run_once(config(max_pages=2), state, Sender(), fetch=fetch)
        assert (
            status["cursor"] == 2 and status["backlog"] and status["events_sent"] == 2
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"schema_version": True},
        {"latest_event_id": True},
        {
            "watcher": {
                "enabled": True,
                "fresh": True,
                "status": "CURRENT",
                "entities": {"token": "canary"},
            }
        },
    ],
)
def test_wrong_typed_metadata_never_advances_or_leaks(tmp_path, bad):
    with State(tmp_path / "state.sqlite") as state:
        state.baseline(0)
        payload = page([event()])
        payload.update(bad)
        with pytest.raises(ContractError):
            process_payload(config(), state, payload)
        assert state.cursor() == 0 and state.pending_count() == 0


def test_mcp_adapter_calls_health_tool_and_rejects_protocol_errors(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from health_notifier import _fetch_events

    called = []
    result = SimpleNamespace(isError=False, structuredContent=page(latest=0))

    @asynccontextmanager
    async def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert kwargs["headers"]["Authorization"] == "Bearer fake-key"
        yield object()

    @asynccontextmanager
    async def stream(url, **kwargs):
        assert url == "https://example.com/mcp"
        yield None, None, None

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            called.append("initialize")

        async def call_tool(self, name, arguments):
            called.append((name, arguments))
            return result

    # Pure adapter test: no installed SDK, sockets, or remote service required.
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=client))
    monkeypatch.setitem(sys.modules, "mcp", SimpleNamespace(ClientSession=Session))
    monkeypatch.setitem(sys.modules, "mcp.client", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "mcp.client.streamable_http",
        SimpleNamespace(streamable_http_client=stream),
    )
    assert (
        asyncio.run(_fetch_events("https://example.com/mcp", 0, 100, "fake-key"))[
            "latest_event_id"
        ]
        == 0
    )
    assert called == [
        "initialize",
        ("get_health_events", {"since_id": 0, "limit": 100}),
    ]
    result.isError = True
    with pytest.raises(ContractError):
        asyncio.run(_fetch_events("https://example.com/mcp", 0, 100, "fake-key"))


def test_observe_only_is_quiet_readonly_and_never_sends(tmp_path, capsys):
    class NoSending:
        def send(self, *args, **kwargs):
            raise AssertionError("must not send")

    path = tmp_path / "state.sqlite"
    with State(path) as state:
        state.baseline(0)
    before = path.read_bytes()
    with State(path, readonly=True) as state:
        status = run_once(
            config(),
            state,
            NoSending(),
            fetch=lambda *args: page([event()]),
            dry_run=True,
            preview=False,
        )
    assert status["source_healthy"] and status["events_sent"] == 0
    assert path.read_bytes() == before and not capsys.readouterr().out


def test_observe_only_daemon_writes_status_without_creating_cursor(
    tmp_path, monkeypatch
):
    import health_notifier as module

    class Stop:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, *args):
            self.set()

    def load(*args, **kwargs):
        assert kwargs["require_delivery"] is False
        return config()

    def poll(cfg, state, sender, **kwargs):
        assert (
            state.readonly
            and sender is None
            and kwargs["dry_run"]
            and not kwargs["preview"]
        )
        return {
            "source_healthy": True,
            "events_queued": 0,
            "events_sent": 0,
            "pending": 0,
            "last_error": "",
        }

    monkeypatch.setattr(module, "Event", Stop)
    monkeypatch.setattr(module, "load_config", load)
    monkeypatch.setattr(module, "run_once", poll)
    status = tmp_path / "status.json"
    state = tmp_path / "state.sqlite"
    assert (
        module.main(["--observe-only", "--state", str(state), "--status", str(status)])
        == 0
    )
    data = json.loads(status.read_text(encoding="utf-8"))
    assert (
        data["mode"] == "observe-only"
        and data["delivery_enabled"] is False
        and not state.exists()
    )
