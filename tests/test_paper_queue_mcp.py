"""Behavioral guards for openclaw/skills/paper-queue/mcp_server.py.

Run: uv run --with pytest pytest tests/test_paper_queue_mcp.py -v

Two layers:
  * unit   — key derivation / window math / tool functions against a temp DB
  * stdio  — spawn the real server and speak newline-delimited JSON-RPC, because that
             is exactly what OpenClaw does; a unit test that skips the framing would
             not catch a broken handshake.

The module is pure stdlib on purpose (the openclaw image is stock and cannot take pip
packages), so these tests run anywhere python3 does.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER = REPO_ROOT / "docker" / "paper-queue-mcp" / "server.py"

ACTOR = "1297756995834609676"          # Owen 的 Discord 用户 ID（雪花）
OTHER = "987654321098765432"           # 另一位群成员
SECRET = "test-secret"


def sig_for(module, tool: str, args: dict, secret: str = SECRET) -> str:
    """按服务端的规则生成归属签名。

    这里刻意用一份**独立实现**（而不是调用服务端的内部函数）—— 两边算出来的必须一样，
    不一样就说明签名材料对不齐，而这正是"每次写入都被拒"的根因。
    """
    import hashlib
    import hmac
    material = "|".join(str(args.get(f) or "") for f in module.ACTOR_SIGNED_FIELDS)
    material += "|" + module.signable_payload(tool, args)
    return hmac.new(secret.encode(), material.encode(), hashlib.sha256).hexdigest()


def signed(module, tool: str, args: dict, secret: str = SECRET) -> dict:
    """给工具入参补上签名（签名材料本身不含 sig 字段）。"""
    out = dict(args)
    out["actor_sig"] = sig_for(module, tool, out, secret)
    return out


def _load(tmp_path, monkeypatch, secret: str | None = None):
    """Import the server module fresh against a temp DB."""
    monkeypatch.setenv("PAPER_QUEUE_DB", str(tmp_path / "queue.sqlite"))
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    if secret is None:
        monkeypatch.delenv("PAPER_QUEUE_ACTOR_SECRET", raising=False)
    else:
        monkeypatch.setenv("PAPER_QUEUE_ACTOR_SECRET", secret)
    spec = importlib.util.spec_from_file_location(
        f"pq_mcp_server_{secret or 'nosecret'}", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def pq(tmp_path, monkeypatch):
    """配置了签名密钥的服务端 —— 也就是真实部署的形态。

    写路径必须带有效签名，所以走 `handle()` 的写测试要用 `signed()` 包装入参；
    直接调用 `add_items()/cancel_item()` 的测试不受影响（签名校验在 `_call_tool` 里）。
    """
    return _load(tmp_path, monkeypatch, secret=SECRET)


def actor(**overrides) -> dict:
    base = {"actor_id": ACTOR, "actor_name": "Owen"}
    base.update(overrides)
    return base


class TestDetectKind:
    """input_kind describes what the user actually stated — not what we inferred."""

    @pytest.mark.parametrize("raw", [
        "10.1038/s41586-021-03819-2",
        "doi:10.1038/s41586-021-03819-2",
        "https://doi.org/10.1038/s41586-021-03819-2",
    ])
    def test_doi_forms(self, pq, raw):
        assert pq.detect_kind(raw) == "doi"

    @pytest.mark.parametrize("raw", [
        "2605.00412", "arXiv:2401.12345", "arxiv:2401.12345v2",
    ])
    def test_arxiv_forms(self, pq, raw):
        assert pq.detect_kind(raw) == "arxiv"

    def test_url(self, pq):
        assert pq.detect_kind("https://example.org/paper.pdf") == "url"

    def test_title_is_the_fallback(self, pq):
        assert pq.detect_kind("Attention Is All You Need") == "title"


class TestCanonicalKey:
    """The key must come from what the USER said, never from an inferred DOI."""

    def test_doi_key_is_lowercased_and_unprefixed(self, pq):
        # kind 只可能来自 detect_kind / _resolve_item，一律小写 —— 断言的就是那个契约
        assert pq.canonical_key("doi", "10.1038/S41586") == "doi:10.1038/s41586"
        assert pq.canonical_key("doi", "https://doi.org/10.1038/X") == "doi:10.1038/x"

    def test_arxiv_key_drops_version(self, pq):
        assert pq.canonical_key("arxiv", "arXiv:2401.12345v2") == "arxiv:2401.12345"

    def test_url_key_is_hashed_and_fragment_insensitive(self, pq):
        a = pq.canonical_key("url", "https://e.org/p.pdf#page=2")
        b = pq.canonical_key("url", "https://e.org/p.pdf")
        assert a == b
        assert a.startswith("url:")

    def test_title_key_normalises_case_and_whitespace(self, pq):
        a = pq.canonical_key("title", "Attention  Is All\nYou Need")
        b = pq.canonical_key("title", "attention is all you need")
        assert a == b
        assert a.startswith("title:")

    def test_normalize_title_collapses_inner_whitespace(self, pq):
        assert pq.normalize_title("  A\t B \n C ") == "a b c"


class TestParseWindow:
    """Windows are computed server-side so the model never does date arithmetic."""

    def test_all_is_unbounded(self, pq):
        assert pq.parse_window("all") == (None, None)
        assert pq.parse_window(None) == (None, None)

    def test_today_uses_the_configured_timezone(self, pq):
        from datetime import datetime, timezone
        # 2026-09-16T00:30Z == 2026-09-16 08:30 Asia/Shanghai → 当地日界为 09-15T16:00Z
        now = datetime(2026, 9, 16, 0, 30, tzinfo=timezone.utc)
        start, end = pq.parse_window("today", now=now)
        assert start == "2026-09-15T16:00:00Z"
        assert end is None

    def test_last_24h(self, pq):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 16, 0, 30, tzinfo=timezone.utc)
        start, _ = pq.parse_window("24h", now=now)
        assert start == "2026-09-15T00:30:00Z"

    def test_explicit_bounds_win_over_window(self, pq):
        start, end = pq.parse_window("today", since="2026-01-01T00:00:00Z",
                                     until="2026-02-01T00:00:00Z")
        assert (start, end) == ("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")

    def test_unknown_window_raises(self, pq):
        with pytest.raises(ValueError):
            pq.parse_window("last-tuesday")

    def test_window_must_be_a_string(self, pq):
        """入参是模型生成的，形状错了只能拒绝，不能让 AttributeError 冒泡。"""
        with pytest.raises(ValueError):
            pq.parse_window(7)

    @pytest.mark.parametrize("bad", [
        "2026-9-16T00:00:00Z",          # 月份没补零 —— 字典序比较会静默返回空列表
        "2026-09-16T08:00:00+08:00",    # 带偏移，非 UTC
        "2026-09-16",
        "yesterday",
    ])
    def test_rejects_malformed_since_until(self, pq, bad):
        """写侧由 DB CHECK 兜底，读侧必须自己挡 —— 否则给的是**静默错误**的结果。"""
        with pytest.raises(ValueError):
            pq.parse_window(since=bad)
        with pytest.raises(ValueError):
            pq.parse_window(until=bad)

    def test_accepts_strict_since_until(self, pq):
        assert pq.parse_window(since="2026-09-16T00:00:00Z") == ("2026-09-16T00:00:00Z", None)


class TestAddItems:
    """Partial failure must not roll back the batch."""

    def test_queues_title_only(self, pq):
        conn = pq.connect()
        result = pq.add_items(conn, [{"title": "Attention Is All You Need"}], actor())
        assert result[0]["status"] == "queued"
        row = conn.execute("SELECT * FROM paper_requests").fetchone()
        assert row["title"] == "Attention Is All You Need"
        assert row["doi"] is None
        assert row["input_kind"] == "title"
        assert row["requester"] == ACTOR

    def test_user_supplied_doi_becomes_the_kind_and_the_key(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Some Paper", "doi": "10.1038/ABC",
                             "doi_source": "user"}], actor())
        row = conn.execute("SELECT * FROM paper_requests").fetchone()
        assert row["input_kind"] == "doi"
        assert row["request_key"] == "doi:10.1038/abc"
        assert row["doi_source"] == "user"

    def test_inferred_doi_does_not_become_the_key(self, pq):
        """A hallucinated DOI must never mint a unique key."""
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Attention Is All You Need",
                             "doi": "10.9999/made-up", "doi_source": "inferred"}], actor())
        row = conn.execute("SELECT * FROM paper_requests").fetchone()
        assert row["input_kind"] == "title"
        assert row["request_key"].startswith("title:")
        assert row["doi"] == "10.9999/made-up"
        assert row["doi_source"] == "inferred"

    def test_duplicate_is_reported_with_existing_row(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Attention Is All You Need"}], actor())
        again = pq.add_items(conn, [{"title": "attention  is all you need"}], actor())
        assert again[0]["status"] == "duplicate"
        assert again[0]["existing_id"]
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 1

    def test_item_without_any_identifier_is_invalid(self, pq):
        conn = pq.connect()
        result = pq.add_items(conn, [{"note": "要正文"}], actor())
        assert result[0]["status"] == "invalid"
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0

    def test_item_with_only_an_inferred_doi_is_invalid(self, pq):
        """Nothing the user actually said ⇒ nothing to key on."""
        conn = pq.connect()
        result = pq.add_items(conn, [{"doi": "10.1/x", "doi_source": "inferred"}], actor())
        assert result[0]["status"] == "invalid"

    def test_batch_keeps_good_items_when_one_is_bad(self, pq):
        conn = pq.connect()
        result = pq.add_items(conn, [
            {"title": "Good One"},
            {"note": "no identifier"},
            {"title": "Good Two"},
        ], actor())
        assert [r["status"] for r in result] == ["queued", "invalid", "queued"]
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 2

    def test_doi_pasted_into_the_title_field_is_reclassified(self, pq):
        """模型很可能把用户说的 DOI 塞进 title —— 别拿它去算标题哈希（那会让去重失效）。"""
        conn = pq.connect()
        pq.add_items(conn, [{"title": "10.1038/s41586-021-03819-2"}], actor())
        row = conn.execute("SELECT * FROM paper_requests").fetchone()
        assert row["input_kind"] == "doi"
        assert row["request_key"] == "doi:10.1038/s41586-021-03819-2"
        assert row["doi_source"] == "user"
        assert row["title"] is None

    def test_real_title_is_not_reclassified(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Attention Is All You Need"}], actor())
        assert conn.execute("SELECT input_kind FROM paper_requests").fetchone()[0] == "title"

    def test_check_violation_is_not_reported_as_duplicate(self, pq):
        """**回归测试**：非唯一索引的完整性错误（CHECK 等）曾被报成 duplicate ——
        SKILL.md 会让模型回答「这篇已经在清单里了」，而实际一行都没写，请求静默丢失。"""
        conn = pq.connect()
        result = pq.add_items(conn, [{"title": "X"}], actor(attribution_source="bogus"))
        assert result[0]["status"] == "invalid", result
        assert "拒绝" in result[0]["reason"]
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0

    def test_batch_of_five(self, pq):
        """The PRD's headline: ≥5 papers in a single message."""
        conn = pq.connect()
        items = [{"title": f"Paper {i}"} for i in range(5)]
        result = pq.add_items(conn, items, actor())
        assert all(r["status"] == "queued" for r in result)
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 5


class TestIdentityIsMandatory:
    """fail closed: no host-injected actor ⇒ refuse to write, never fall back to the model."""

    def test_add_without_actor_is_rejected(self, pq):
        conn = pq.connect()
        with pytest.raises(ValueError, match="actor_id"):
            pq.add_items(conn, [{"title": "X"}], {})
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0

    def test_add_with_blank_actor_is_rejected(self, pq):
        conn = pq.connect()
        with pytest.raises(ValueError, match="actor_id"):
            pq.add_items(conn, [{"title": "X"}], {"actor_id": ""})

    def test_malformed_actor_id_is_rejected(self, pq):
        """actor_id 必须是 Discord 雪花 —— 实测传对象会落库成 "{'x': 1}" 这种垃圾。"""
        conn = pq.connect()
        with pytest.raises(ValueError, match="格式"):
            pq.add_items(conn, [{"title": "X"}], {"actor_id": "not-a-snowflake"})
        with pytest.raises(ValueError, match="格式"):
            pq.add_items(conn, [{"title": "X"}], {"actor_id": {"x": 1}})

    def test_empty_batch_is_rejected(self, pq):
        conn = pq.connect()
        with pytest.raises(ValueError):
            pq.add_items(conn, [], actor())

    def test_write_tools_have_no_requester_parameter(self, pq):
        """归属只认宿主注入的 `actor_id`。

        更强的保证来自**函数签名**：写入工具连 `requester` 参数都没有，模型想伪造都
        无处可传（FastMCP 会拒绝未知参数）。读工具保留 `requester` 是**筛选**用途，
        伪造它写不进任何东西。
        """
        import inspect
        for tool in (pq.paper_queue_add, pq.paper_queue_cancel):
            assert "requester" not in inspect.signature(tool).parameters, (
                f"{tool.__name__} 暴露 requester 参数 = 给模型留了伪造归属的口子"
            )
        assert "requester" in inspect.signature(pq.paper_queue_list).parameters


class TestInputLimits:
    """入参来自模型：没有上限就等于允许它把几 MB 文本灌进库 —— 而那还会被热备上云、
    被消费方读走，再被 `list` 原样塞回模型上下文。"""

    def test_too_many_items_is_rejected(self, pq):
        conn = pq.connect()
        with pytest.raises(ValueError, match="最多"):
            pq.add_items(conn, [{"title": f"P{i}"} for i in range(pq.MAX_ITEMS + 1)], actor())
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0

    def test_exactly_max_items_is_accepted(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": f"P{i}"} for i in range(pq.MAX_ITEMS)], actor())
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == pq.MAX_ITEMS

    def test_overlong_field_is_truncated_not_rejected(self, pq):
        """模型把整段摘要塞进 note 是常见操作，不该因此丢掉整条请求。"""
        conn = pq.connect()
        pq.add_items(conn, [{"title": "T", "note": "x" * 100000}], actor())
        stored = conn.execute("SELECT note FROM paper_requests").fetchone()[0]
        assert len(stored) == pq.MAX_FIELD_CHARS


class TestListItems:
    def test_defaults_to_the_callers_own_list(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Mine"}], actor())
        pq.add_items(conn, [{"title": "Theirs"}], actor(actor_id=OTHER, actor_name="ringo"))
        mine = pq.list_items(conn, actor())
        assert [r["title"] for r in mine] == ["Mine"]

    def test_explicit_requester_queries_someone_else(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Theirs"}], actor(actor_id=OTHER, actor_name="ringo"))
        rows = pq.list_items(conn, actor(), requester=OTHER)
        assert [r["title"] for r in rows] == ["Theirs"]

    def test_window_filters_by_time(self, pq, pq_insert_at):
        conn = pq.connect()
        pq_insert_at(conn, "Old", "2026-09-01T00:00:00Z", ACTOR)
        pq_insert_at(conn, "New", "2026-09-16T00:00:00Z", ACTOR)
        rows = pq.list_items(conn, actor(), window="7d",
                             now=__import__("datetime").datetime(
                                 2026, 9, 16, 12, 0, tzinfo=__import__("datetime").timezone.utc))
        assert [r["title"] for r in rows] == ["New"]

    def test_cancelled_hidden_by_default_but_visible_on_request(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Dropped"}], actor())
        row_id = conn.execute("SELECT id FROM paper_requests").fetchone()[0]
        pq.cancel_item(conn, actor(), conn.execute(
            "SELECT request_key FROM paper_requests").fetchone()[0])
        assert pq.list_items(conn, actor()) == []
        assert len(pq.list_items(conn, actor(), include_cancelled=True)) == 1
        assert row_id


@pytest.fixture()
def pq_insert_at():
    """Insert a row with an explicit timestamp (bypasses 'now')."""
    def _insert(conn, title, requested_at, requester):
        conn.execute(
            "INSERT INTO paper_requests (request_key, input_kind, raw_input, title,"
            " requester, requested_at) VALUES (?,?,?,?,?,?)",
            (f"title:{title}", "title", title, title, requester, requested_at),
        )
        conn.commit()
    return _insert


class TestCancel:
    def test_cancel_sets_both_fields(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "X"}], actor())
        key = conn.execute("SELECT request_key FROM paper_requests").fetchone()[0]
        out = pq.cancel_item(conn, actor(), key)
        assert out["status"] == "cancelled"
        row = conn.execute("SELECT * FROM paper_requests").fetchone()
        assert row["cancelled_at"] and row["cancelled_by"] == ACTOR

    def test_cancel_is_idempotent(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "X"}], actor())
        key = conn.execute("SELECT request_key FROM paper_requests").fetchone()[0]
        pq.cancel_item(conn, actor(), key)
        assert pq.cancel_item(conn, actor(), key)["status"] == "already_cancelled"

    def test_unknown_key_is_reported(self, pq):
        conn = pq.connect()
        assert pq.cancel_item(conn, actor(), "title:nope")["status"] == "not_found"

    def test_cannot_cancel_someone_elses_request(self, pq):
        """清单可查别人的（群友能拿到 request_key），但撤销只能撤自己的 ——
        否则一句「把那条去掉」就能悄悄撤掉别人的请求，当事人毫不知情。"""
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Theirs"}], actor(actor_id=OTHER, actor_name="ringo"))
        key = conn.execute("SELECT request_key FROM paper_requests").fetchone()[0]

        out = pq.cancel_item(conn, actor(), key)
        assert out["status"] == "not_owner"
        assert conn.execute(
            "SELECT cancelled_at FROM paper_requests").fetchone()[0] is None

    def test_owner_can_cancel_their_own(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Mine"}], actor())
        key = conn.execute("SELECT request_key FROM paper_requests").fetchone()[0]
        assert pq.cancel_item(conn, actor(), key)["status"] == "cancelled"

    def test_same_paper_can_be_requested_again_after_cancel(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "X"}], actor())
        key = conn.execute("SELECT request_key FROM paper_requests").fetchone()[0]
        pq.cancel_item(conn, actor(), key)
        assert pq.add_items(conn, [{"title": "X"}], actor())[0]["status"] == "queued"


class TestSchemaMigration:
    """SQLite 改不了 CHECK 约束 —— 枚举一变就只能重建表，且必须在**装机那一刻**自动完成。

    漏掉迁移的后果不是报错，而是：老库拒绝新枚举值 → add_items 把它归成 invalid →
    用户以为记下了、库里一行都没有。这是"部署即坏"级别的缺陷。
    """

    V1_SCHEMA = """
        CREATE TABLE paper_requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          request_key TEXT NOT NULL, input_kind TEXT NOT NULL, raw_input TEXT NOT NULL,
          title TEXT, doi TEXT, doi_source TEXT, arxiv_id TEXT, url TEXT, note TEXT,
          requester TEXT NOT NULL, requester_name TEXT, channel_ref TEXT,
          message_ref TEXT, session_ref TEXT,
          attribution_source TEXT CHECK (attribution_source IS NULL
              OR attribution_source IN ('message_id','session_fallback')),
          attribution_ambiguous INTEGER NOT NULL DEFAULT 0,
          requested_at TEXT NOT NULL, cancelled_at TEXT, cancelled_by TEXT
        );
        PRAGMA user_version = 0;
    """

    # v2 的 CHECK 允许三档（含后来退役的 session_latest）
    V2_SCHEMA = V1_SCHEMA.replace(
        "('message_id','session_fallback')", "('single','batched','session_latest')")

    def test_version_in_schema_sql_matches_the_module(self, pq):
        """两处版本号必须一起改 —— 不一致的话老库永远不会被迁移。"""
        import re as _re
        text = pq.SCHEMA_PATH.read_text()
        declared = int(_re.search(r"PRAGMA user_version = (\d+)", text).group(1))
        assert declared == pq.SCHEMA_VERSION

    def test_migrates_v1_rows_without_losing_them(self, tmp_path, monkeypatch):
        db = tmp_path / "old.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(self.V1_SCHEMA)
        conn.execute(
            "INSERT INTO paper_requests (request_key,input_kind,raw_input,title,requester,"
            "requested_at,attribution_source) VALUES (?,?,?,?,?,?,?)",
            ("k1", "title", "t", "T", "1", "2026-09-16T00:00:00Z", "message_id"))
        conn.commit()
        conn.close()

        module = _load(tmp_path, monkeypatch)      # 注意：_load 指向的是另一个库
        conn = sqlite3.connect(db)
        module.ensure_schema(conn)

        assert list(conn.execute(
            "SELECT request_key, attribution_source FROM paper_requests")) == \
            [("k1", None)], (
                "v1 的标签是幌子（生产里恒为常量），v3 起枚举只剩 single/batched，"
                "映射不过去就如实置 NULL = 未记录，不假装知道当时有多可信"
            )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == module.SCHEMA_VERSION
        # 迁移后必须能写新枚举 —— 这正是迁移存在的理由
        conn.execute(
            "INSERT INTO paper_requests (request_key,input_kind,raw_input,title,requester,"
            "requested_at,attribution_source) VALUES (?,?,?,?,?,?,?)",
            ("k2", "title", "t", "T", "1", "2026-09-16T00:00:00Z", "single"))
        conn.close()

    def test_migrates_v2_session_latest_to_null(self, tmp_path, monkeypatch):
        """v2 → v3：`session_latest` 退役，存量行如实置 NULL。"""
        db = tmp_path / "v2.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(self.V2_SCHEMA)
        conn.execute("PRAGMA user_version = 2")
        conn.execute(
            "INSERT INTO paper_requests (request_key,input_kind,raw_input,title,requester,"
            "requested_at,attribution_source) VALUES (?,?,?,?,?,?,?)",
            ("k1", "title", "t", "T", "1", "2026-09-16T00:00:00Z", "session_latest"))
        conn.commit()
        conn.close()

        module = _load(tmp_path, monkeypatch)
        conn = sqlite3.connect(db)
        module.ensure_schema(conn)
        assert list(conn.execute(
            "SELECT attribution_source FROM paper_requests")) == [(None,)]
        conn.close()

    def test_fresh_db_is_created_at_current_version(self, pq):
        conn = pq.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == pq.SCHEMA_VERSION

    def test_migration_is_idempotent(self, pq):
        conn = pq.connect()
        pq.ensure_schema(conn)
        pq.ensure_schema(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == pq.SCHEMA_VERSION


class TestActorSignature:
    """插件一旦缺席，服务端必须**拒绝写入**，而不是静默采信模型自填的身份。

    密钥写在 openclaw.json 里（模型读得到），这一层防的不是"读"而是"算"：
    模型算不出对 (actor_id, session_ref, actor_message) 的 HMAC。所以伪造在
    "插件没加载"这个失败模式下会变成响亮的报错，而不是库里多一行看不出真假的数据。
    """

    SESSION = "agent:main:discord:channel:1"
    MESSAGE = "1549589564275032150"

    @pytest.fixture()
    def signer(self, tmp_path, monkeypatch):
        return _load(tmp_path, monkeypatch, secret=SECRET)

    @staticmethod
    def _sign(module, actor_id, session_ref, message_ref, secret=None):
        """对 `add` 的默认入参签名（内容与 `_call` 构造的一致）。"""
        args = {"items": [{"title": "X"}], "actor_id": actor_id,
                "session_ref": session_ref, "actor_message": message_ref}
        return sig_for(module, "paper_queue_add", args, secret or SECRET)

    def _call(self, module, conn, actor_id, sig=None, key="paper_queue_add"):
        args = {"items": [{"title": "X"}], "actor_id": actor_id,
                "session_ref": self.SESSION, "actor_message": self.MESSAGE}
        if key == "paper_queue_cancel":
            args = {"request_key": "title:whatever", "actor_id": actor_id,
                    "session_ref": self.SESSION, "actor_message": self.MESSAGE}
        if sig is not None:
            args["actor_sig"] = sig
        return module.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": key, "arguments": args}}, conn)

    def test_valid_signature_is_accepted(self, signer):
        conn = signer.connect()
        sig = self._sign(signer, ACTOR, self.SESSION, self.MESSAGE)
        assert self._call(signer, conn, ACTOR, sig)["result"]["isError"] is False
        assert conn.execute("SELECT requester FROM paper_requests").fetchone()[0] == ACTOR

    def test_missing_signature_is_refused(self, signer):
        conn = signer.connect()
        resp = self._call(signer, conn, ACTOR, sig=None)
        assert resp["result"]["isError"] is True
        assert "插件" in resp["result"]["content"][0]["text"]
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0

    def test_wrong_signature_is_refused(self, signer):
        """伪造者读得到密钥也算不出 HMAC —— 签名不对就拒。"""
        conn = signer.connect()
        forged = self._sign(signer, ACTOR, self.SESSION, self.MESSAGE, secret="wrong-secret")
        assert self._call(signer, conn, ACTOR, forged)["result"]["isError"] is True
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0

    def test_signature_bound_to_the_identity_it_covers(self, signer):
        """拿别人的签名换一个 actor_id 也没用（签名覆盖 actor_id）。"""
        conn = signer.connect()
        sig_for_other = self._sign(signer, "999999", self.SESSION, self.MESSAGE)
        assert self._call(signer, conn, ACTOR, sig_for_other)["result"]["isError"] is True

    def test_cancel_also_requires_a_signature(self, signer):
        conn = signer.connect()
        assert self._call(signer, conn, ACTOR, sig=None,
                          key="paper_queue_cancel")["result"]["isError"] is True

    def test_list_does_not_require_a_signature(self, signer):
        """读不验签：requester 本就是可显式传的公开参数，伪造它写不进任何东西。"""
        conn = signer.connect()
        resp = signer.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "paper_queue_list",
                                         "arguments": {"actor_id": ACTOR}}}, conn)
        assert resp["result"]["isError"] is False

    def test_missing_secret_refuses_writes_instead_of_skipping(self, tmp_path, monkeypatch):
        """**未配置密钥时拒绝写入**，而不是静默跳过校验。

        静默跳过等于 fail open：任何一次"env 被删或改名"都会让防护无声消失，
        而插件那头还在签名 —— 表面上一切正常。
        """
        module = _load(tmp_path, monkeypatch, secret=None)
        conn = module.connect()
        resp = self._call(module, conn, ACTOR, sig="whatever")
        assert resp["result"]["isError"] is True
        assert "密钥" in resp["result"]["content"][0]["text"]
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0


class TestInitSentinel:
    """库被删必须能被查出来：server 首次建库时在库旁边留哨兵，检查器靠它区分
    「被删」与「从没用过」—— 两者在此之前都是"打不开"，删库因此不会告警。"""

    def test_connect_leaves_a_sentinel(self, pq, tmp_path):
        conn = pq.connect()
        conn.close()
        assert Path(str(tmp_path / "queue.sqlite") + pq.SENTINEL_SUFFIX).exists()

    def test_sentinel_is_idempotent(self, pq, tmp_path):
        pq.connect().close()
        pq.connect().close()
        assert Path(str(tmp_path / "queue.sqlite") + pq.SENTINEL_SUFFIX).exists()


class TestResponseCap:
    """`list` 是唯一会把大量数据塞回**模型上下文**的地方 —— 行数与字段各有上限，
    但相乘仍可能到约 1MB（既费钱又挤占上下文），所以整体也得封顶。"""

    def test_oversized_list_is_truncated_and_flagged(self, pq):
        pq.MAX_RESPONSE_CHARS = 2000          # 临时收紧，便于构造
        conn = pq.connect()
        pq.add_items(conn, [{"title": f"Paper {i:02d}", "note": "x" * 300}
                            for i in range(20)], actor())
        payload = pq.capped_list_payload(pq.list_items(conn, actor()))
        assert payload["truncated"] is True
        assert payload["total"] == 20
        assert 1 <= payload["count"] < payload["total"]
        assert "缩小 window" in payload["note"]

    def test_small_list_is_not_flagged(self, pq):
        conn = pq.connect()
        pq.add_items(conn, [{"title": "One"}], actor())
        payload = pq.capped_list_payload(pq.list_items(conn, actor()))
        assert "truncated" not in payload
        assert payload["count"] == payload["total"] == 1

    def test_always_returns_at_least_one_row(self, pq):
        """单行就超限时也不能返回空 —— 那会让调用方以为"清单是空的"。"""
        pq.MAX_RESPONSE_CHARS = 10
        conn = pq.connect()
        pq.add_items(conn, [{"title": "Big", "note": "y" * 500}], actor())
        payload = pq.capped_list_payload(pq.list_items(conn, actor()))
        assert payload["count"] == 1
        assert payload["truncated"] is True

class TestToolSurface:
    """工具面本身的结构。传输层交给 FastMCP，靠真机的 `mcp doctor --probe` + 一次真实
    调用覆盖 —— 这里只钉住"名字"和"插件注入的参数必须被工具接收"这两件会静默失效的事。"""

    def test_exposes_exactly_the_three_tools(self, pq):
        assert [t.__name__ for t in pq.TOOLS] == [
            "paper_queue_add", "paper_queue_list", "paper_queue_cancel"]

    def test_every_tool_accepts_the_injected_actor_params(self, pq):
        """插件注入的每个 actor_* 都必须是工具的**具名参数** —— 少一个，注入就被丢掉，
        而表现是"归属校验失败"（还看得出）或归属丢失（看不出来）。"""
        import inspect
        for tool in pq.TOOLS:
            params = inspect.signature(tool).parameters
            for name in pq._ACTOR_PROPS:
                assert name in params, f"{tool.__name__} 缺少注入参数 {name}"

    def test_reading_does_not_need_a_signature(self, pq):
        """读不验签：requester 本就是可显式传的公开参数，伪造它写不进任何东西。"""
        assert json.loads(pq.paper_queue_list(actor_id=ACTOR))["count"] == 0


class TestActorSignature:
    """插件一旦缺席，服务端必须**拒绝写入**，而不是静默采信模型自填的身份。

    密钥写在配置里（模型读得到），这一层防的不是"读"而是"算"：模型算不出 HMAC。
    签名还必须覆盖**请求内容** —— 注入的 actor_* 会进模型可见的会话记录，只签身份
    的话它就是一枚可重放的 bearer 值。
    """

    SESSION = "agent:main:discord:channel:1"
    MESSAGE = "1549589564275032150"
    ADD = {"items": [{"title": "X"}], "actor_id": ACTOR, "actor_name": "Owen"}

    def _args(self, **over):
        return {**self.ADD, "session_ref": self.SESSION, "actor_message": self.MESSAGE, **over}

    def test_valid_signature_is_accepted(self, pq):
        pq.paper_queue_add(**signed(pq, "paper_queue_add", self._args()))
        row = pq.connect().execute("SELECT requester FROM paper_requests").fetchone()
        assert row["requester"] == ACTOR

    @pytest.mark.parametrize("sig", [None, "deadbeef"])
    def test_missing_or_wrong_signature_is_refused(self, pq, sig):
        args = self._args()
        if sig:
            args["actor_sig"] = sig
        with pytest.raises(ValueError):
            pq.paper_queue_add(**args)
        assert pq.connect().execute(
            "SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0

    def test_signature_is_bound_to_the_payload(self, pq):
        """换掉 items 复用同一个签名必须失败 —— 否则签名只是可重放的 bearer 值。"""
        args = signed(pq, "paper_queue_add", self._args())
        args["items"] = [{"title": "SOMETHING ELSE ENTIRELY"}]
        with pytest.raises(ValueError, match="签名"):
            pq.paper_queue_add(**args)

    def test_signature_is_bound_to_the_identity(self, pq):
        args = signed(pq, "paper_queue_add", self._args())
        args["actor_id"] = OTHER
        with pytest.raises(ValueError, match="签名"):
            pq.paper_queue_add(**args)

    def test_cancel_also_requires_a_signature(self, pq):
        with pytest.raises(ValueError):
            pq.paper_queue_cancel(request_key="title:x", actor_id=ACTOR,
                                  session_ref=self.SESSION, actor_message=self.MESSAGE)

    def test_missing_secret_refuses_writes_instead_of_skipping(self, tmp_path, monkeypatch):
        """**未配置密钥时拒绝写入**，而不是静默跳过校验 —— 静默跳过等于 fail open：
        env 被删或改名都会让防护无声消失，而插件那头还在签名。"""
        module = _load(tmp_path, monkeypatch, secret=None)
        with pytest.raises(ValueError, match="密钥"):
            module.paper_queue_add(**self._args())
