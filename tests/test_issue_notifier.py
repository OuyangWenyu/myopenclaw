"""Unit tests for docker/issue-notifier (no network, no Feishu)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

NOTIFIER_DIR = Path(__file__).resolve().parent.parent / "docker" / "issue-notifier"
sys.path.insert(0, str(NOTIFIER_DIR))

import config as notifier_config  # noqa: E402
import feishu as notifier_feishu  # noqa: E402
import main as notifier_main  # noqa: E402
import mapping  # noqa: E402
from feishu import build_card  # noqa: E402
from state import State  # noqa: E402


@pytest.fixture(autouse=True)
def _notifier_env(monkeypatch):
    """Keep config tests deterministic: webhook mode by default."""
    monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_MODE", "webhook")
    monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_WEBHOOK", "https://example.com/hook")
    monkeypatch.delenv("ISSUE_NOTIFIER_FEISHU_CHAT_ID", raising=False)


def _person(name="示例成员", gitcode="example-gitcode", github="example-github"):
    return notifier_config.Person(name=name, gitcode=gitcode, github=github)


def _event(**kwargs):
    base = {
        "event_id": 1,
        "event_key": "k1",
        "event_type": "assigned",
        "platform": "gitcode",
        "owner": "dlut-water",
        "repo": "server-monitoring",
        "kind": "issue",
        "number": 5,
        "title": "示例 issue",
        "url": "https://gitcode.com/x/y/issues/5",
        "subject": {"login": "example-gitcode", "name": "示例成员"},
        "assignees": [{"login": "example-gitcode", "name": "示例成员"}],
        "old_value": "absent",
        "new_value": "present",
    }
    base.update(kwargs)
    return base


class TestMapping:
    def test_subject_matches_platform_username(self):
        assert mapping.matched_people((_person(),), _event()) == [_person()]

    def test_subject_does_not_match_other_platform(self):
        event = _event(platform="github", subject={"login": "example-gitcode"})
        assert mapping.matched_people((_person(),), event) == []

    def test_state_event_matches_any_assignee(self):
        event = _event(
            event_type="closed",
            subject=None,
            assignees=[{"login": "example-gitcode", "name": "示例成员"}],
        )
        assert mapping.matched_people((_person(),), event) == [_person()]

    def test_unmapped_person_is_ignored(self):
        event = _event(subject={"login": "someone-else"})
        assert mapping.matched_people((_person(),), event) == []


class TestCard:
    def test_card_contains_repo_event_and_link(self):
        card = build_card(_event(), [_person()])
        text = json.dumps(card, ensure_ascii=False)
        assert "GitCode" in text
        assert "server-monitoring" in text
        assert "#5" in text
        assert "新分配" in text
        assert "示例成员" in text
        assert "https://gitcode.com/x/y/issues/5" in text

    def test_state_card_uses_assignee_names(self):
        card = build_card(
            _event(event_type="merged", subject=None, assignees=[{"login": "example-gitcode", "name": "示例成员"}]),
            [_person()],
        )
        assert "已合并" in json.dumps(card, ensure_ascii=False)

    def test_review_event_label(self):
        card = build_card(
            _event(event_type="review_requested", subject={"login": "example-gitcode"}),
            [_person()],
        )
        assert "请求 review" in json.dumps(card, ensure_ascii=False)

    def test_untrusted_title_is_plain_text_and_foreign_url_is_dropped(self):
        card = build_card(
            _event(
                title="<at id=all></at> [x](https://evil.example)",
                url="https://evil.example/issue",
            ),
            [_person()],
        )
        text = json.dumps(card, ensure_ascii=False)
        assert '"tag": "plain_text"' in text
        assert "https://evil.example/issue" not in text
        assert '"tag": "action"' not in text


class TestState:
    def test_cursor_and_outbox_dedup(self, tmp_path):
        state = State(tmp_path / "state.sqlite")
        assert state.get_cursor() is None
        state.set_cursor(10)
        assert state.get_cursor() == 10
        state.enqueue(11, "k11", "{}")
        state.enqueue(11, "k11", "{}")  # idempotent
        assert len(state.pending()) == 1
        state.mark_sent(11, "k11")
        assert state.is_sent("k11")
        assert state.pending() == []
        state.close()


class TestProcessPayload:
    def _config(self, tmp_path, notify_existing=False):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[[people]]\nname = "示例成员"\ngitcode = "example-gitcode"\ngithub = "example-github"\n',
            encoding="utf-8",
        )
        return notifier_config.load_config(cfg_path)

    def test_first_run_baselines_without_sending(self, tmp_path):
        state = State(tmp_path / "state.sqlite")
        config = self._config(tmp_path)
        payload = {"events": [_event()], "next_cursor": 5, "latest_event_id": 5}
        seen, queued = notifier_main.process_payload(config, state, payload)
        assert (seen, queued) == (0, 0)
        assert state.get_cursor() == 5
        assert state.pending() == []
        state.close()

    def test_mapped_event_is_enqueued_once(self, tmp_path):
        state = State(tmp_path / "state.sqlite")
        state.set_cursor(0)
        config = self._config(tmp_path)
        payload = {"events": [_event()], "next_cursor": 1, "latest_event_id": 1}
        assert notifier_main.process_payload(config, state, payload) == (1, 1)
        assert len(state.pending()) == 1
        assert notifier_main.process_payload(config, state, payload) == (1, 0)
        assert len(state.pending()) == 1
        state.close()

    def test_flush_marks_sent_and_retries_on_failure(self, tmp_path):
        class Sender:
            def __init__(self, fail=False):
                self.fail = fail
                self.cards = []

            def send(self, card):
                if self.fail:
                    raise RuntimeError("boom")
                self.cards.append(card)

        state = State(tmp_path / "state.sqlite")
        state.enqueue(1, "k1", json.dumps({"x": 1}))
        sender = Sender(fail=True)
        assert notifier_main.flush_outbox(state, sender) == (0, 1)
        assert len(state.pending()) == 1
        assert state.pending()[0]["attempts"] == 1

        sender.fail = False
        assert notifier_main.flush_outbox(state, sender) == (1, 0)
        assert state.pending() == []
        assert sender.cards == [{"x": 1}]
        state.close()


class TestConfigValidation:
    def test_missing_people_fails_loud(self, tmp_path):
        with pytest.raises(notifier_config.ConfigError):
            notifier_config.load_config(tmp_path / "missing.toml")

    def test_app_mode_requires_chat_id(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.toml"
        cfg.write_text('[[people]]\nname = "x"\ngitcode = "x"\n', encoding="utf-8")
        monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_MODE", "app")
        monkeypatch.delenv("ISSUE_NOTIFIER_FEISHU_CHAT_ID", raising=False)
        monkeypatch.setenv("CODER_FEISHU_APP_ID", "cli_x")
        monkeypatch.setenv("CODER_FEISHU_APP_SECRET", "secret")
        with pytest.raises(notifier_config.ConfigError):
            notifier_config.load_config(cfg)

    def _webhook_config(self, tmp_path, people):
        cfg = tmp_path / "config.toml"
        cfg.write_text(people, encoding="utf-8")
        return notifier_config.load_config(cfg)

    def test_example_people_are_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_MODE", "webhook")
        monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_WEBHOOK", "https://example/hook")
        config = self._webhook_config(
            tmp_path,
            '[[people]]\nname = "示例成员"\ngitcode = "example-gitcode"\n',
        )
        assert config.placeholder_people is True

    def test_real_people_are_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_MODE", "webhook")
        monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_WEBHOOK", "https://example/hook")
        config = self._webhook_config(
            tmp_path,
            '[[people]]\nname = "真实成员"\ngitcode = "real-user"\n',
        )
        assert config.placeholder_people is False


class TestCursorValidation:
    def _config(self, tmp_path, notify_existing=False):
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(
            '[[people]]\nname = "示例成员"\ngitcode = "example-gitcode"\n',
            encoding="utf-8",
        )
        return notifier_config.load_config(cfg_path)

    def test_cursor_ahead_of_latest_resets(self, tmp_path):
        state = State(tmp_path / "state.sqlite")
        state.set_cursor(10)
        config = self._config(tmp_path)
        payload = {"events": [], "next_cursor": 10, "latest_event_id": 5}
        assert notifier_main.process_payload(config, state, payload) == (0, 0)
        assert state.get_cursor() == 5
        state.close()

    def test_missing_latest_event_id_fails_loud(self, tmp_path):
        state = State(tmp_path / "state.sqlite")
        state.set_cursor(0)
        config = self._config(tmp_path)
        payload = {"events": [], "next_cursor": 0}
        with pytest.raises(RuntimeError):
            notifier_main.process_payload(config, state, payload)
        state.close()

    def test_null_events_does_not_advance_cursor(self, tmp_path):
        state = State(tmp_path / "state.sqlite")
        state.set_cursor(3)
        config = self._config(tmp_path)
        payload = {"events": None, "next_cursor": 9, "latest_event_id": 9}
        with pytest.raises(RuntimeError):
            notifier_main.process_payload(config, state, payload)
        assert state.get_cursor() == 3
        state.close()


class TestFeishuSender:
    class Resp:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def _config(self, tmp_path, mode="webhook"):
        return notifier_config.Config(
            mcp_url="http://example/mcp",
            mcp_token=None,
            poll_seconds=60,
            feishu_mode=mode,
            chat_id="oc_" + "a" * 32 if mode == "app" else None,
            webhook_url="https://example/hook" if mode == "webhook" else None,
            app_id="cli_x" if mode == "app" else None,
            app_secret="secret" if mode == "app" else None,
            people=(_person(),),
            notify_existing=False,
        )

    def test_webhook_accepts_statuscode_shape(self, tmp_path, monkeypatch):
        sender = notifier_feishu.FeishuSender(self._config(tmp_path))
        monkeypatch.setattr(
            notifier_feishu.urllib.request,
            "urlopen",
            lambda req, timeout: self.Resp({"StatusCode": 0, "StatusMessage": "success"}),
        )
        sender.send({"x": 1})  # must not raise

    def test_app_mode_rejects_error_without_echoing_ids(self, tmp_path, monkeypatch):
        sender = notifier_feishu.FeishuSender(self._config(tmp_path, mode="app"))

        def fake(req, timeout):
            if req.full_url == notifier_feishu.AUTH_URL:
                return self.Resp({"code": 0, "tenant_access_token": "t", "expire": 7200})
            return self.Resp({"code": 99991663, "msg": "bad oc_" + "b" * 32})

        monkeypatch.setattr(notifier_feishu.urllib.request, "urlopen", fake)
        with pytest.raises(RuntimeError) as excinfo:
            sender.send({"x": 1})
        assert "oc_" + "b" * 32 not in str(excinfo.value)
        # Business auth errors (HTTP 200) must drop the cached token too.
        assert sender._token is None

    def test_http_error_body_is_not_echoed(self, tmp_path, monkeypatch):
        import urllib.error

        sender = notifier_feishu.FeishuSender(self._config(tmp_path))

        def fake(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 400, "bad", None, None)

        monkeypatch.setattr(notifier_feishu.urllib.request, "urlopen", fake)
        with pytest.raises(RuntimeError) as excinfo:
            sender.send({"x": 1})
        assert "feishu HTTP 400" in str(excinfo.value)
        assert "hook" not in str(excinfo.value)


class TestRedactionAndDeadLetter:
    def test_redact_error_strips_ids_and_secrets(self):
        secret_id = "oc_" + "c" * 32
        text = notifier_main.redact_error(
            RuntimeError(f"https://x/bot/v2/hook/SECRET access_token=ABC {secret_id}")
        )
        assert secret_id not in text
        assert "SECRET" not in text
        assert "ABC" not in text

    def test_poisoned_card_is_dead_lettered_after_retry_window(self, tmp_path):
        class Sender:
            def send(self, card):
                raise RuntimeError("always fails")

        state = State(tmp_path / "state.sqlite")
        state.enqueue(1, "k1", "{}")
        state.conn.execute(
            "UPDATE outbox SET created_at='2020-01-01 00:00:00' WHERE event_id=1"
        )
        state.conn.commit()
        assert notifier_main.flush_outbox(state, Sender()) == (0, 1)
        assert state.pending_count() == 0
        assert state.dead_count() == 1

        assert state.requeue_dead() == 1
        assert state.pending_count() == 1
        assert state.dead_count() == 0
        state.close()


def test_mcp_token_loaded(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[[people]]\nname = "x"\ngitcode = "x"\n', encoding="utf-8")
    monkeypatch.setenv("ISSUE_NOTIFIER_MCP_TOKEN", "mcp-token")
    monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_MODE", "webhook")
    monkeypatch.setenv("ISSUE_NOTIFIER_FEISHU_WEBHOOK", "https://example/hook")
    assert notifier_config.load_config(cfg).mcp_token == "mcp-token"


class TestAllowedRepos:
    def _config(self, tmp_path, allowed):
        cfg = tmp_path / "config.toml"
        lines = ['[[people]]', 'name = "示例成员"', 'gitcode = "example-gitcode"']
        if allowed:
            lines.append("allowed_repos = [" + ", ".join(f'"{r}"' for r in allowed) + "]")
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return notifier_config.load_config(cfg)

    def test_empty_allowlist_means_all_repos(self, tmp_path):
        config = self._config(tmp_path, [])
        state = State(tmp_path / "state.sqlite")
        state.set_cursor(0)
        event = _event(platform="gitcode", owner="o", repo="r")
        assert notifier_main.process_payload(
            config, state, {"events": [event], "next_cursor": 1, "latest_event_id": 1}
        ) == (1, 1)
        state.close()

    def test_allowlist_filters_repos(self, tmp_path):
        config = self._config(tmp_path, ["gitcode/dlut-water/server-monitoring"])
        state = State(tmp_path / "state.sqlite")
        state.set_cursor(0)
        other = _event(platform="github", owner="o", repo="r")
        allowed = _event()
        assert notifier_main.process_payload(
            config,
            state,
            {"events": [other, allowed], "next_cursor": 2, "latest_event_id": 2},
        ) == (2, 1)
        assert len(state.pending()) == 1
        state.close()

    def test_allowlist_is_case_insensitive(self, tmp_path):
        config = self._config(tmp_path, ["GitCode/DLUT-Water/Server-Monitoring"])
        state = State(tmp_path / "state.sqlite")
        state.set_cursor(0)
        assert notifier_main.process_payload(
            config,
            state,
            {"events": [_event()], "next_cursor": 1, "latest_event_id": 1},
        ) == (1, 1)
        state.close()


def test_purge_dead_removes_only_old_cards(tmp_path):
    state = State(tmp_path / "state.sqlite")
    state.enqueue(1, "old", "{}")
    state.mark_failed(1, "boom")
    state.dead_letter(1, "boom")
    state.conn.execute("UPDATE dead SET dead_at='2020-01-01 00:00:00' WHERE event_id=1")
    state.conn.commit()
    state.enqueue(2, "new", "{}")
    state.mark_failed(2, "boom")
    state.dead_letter(2, "boom")

    assert state.purge_dead(7) == 1
    assert state.dead_count() == 1
    state.close()
