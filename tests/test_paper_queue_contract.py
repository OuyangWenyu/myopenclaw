"""Behavioral guards for the paper-queue contract (docker/paper-queue-mcp/schema.sql).

Run: uv run --with pytest pytest tests/test_paper_queue_contract.py -v

These do NOT string-match the schema — they apply it to a real SQLite file and assert
that bad rows are actually rejected. The whole point of the contract is that the DB
enforces it, so a test that only greps text would pass on a schema that accepts garbage.

Note: the DB must be a FILE, not :memory:. `PRAGMA journal_mode = WAL` is a no-op on
in-memory databases, which would make the WAL guard vacuous.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = REPO_ROOT / "docker" / "paper-queue-mcp" / "schema.sql"

# The contract's public surface. Renaming/removing one of these breaks consumers,
# so it is pinned here rather than left to whoever edits the schema next.
REQUIRED_COLUMNS = {
    "id",
    "request_key",
    "input_kind",
    "raw_input",
    "title",
    "doi",
    "doi_source",
    "arxiv_id",
    "url",
    "note",
    "requester",
    "requester_name",
    "channel_ref",
    "message_ref",
    "session_ref",
    "attribution_source",
    "attribution_ambiguous",
    "requested_at",
    "cancelled_at",
    "cancelled_by",
}

VALID_ROW = {
    "request_key": "title:6f1a…",
    "input_kind": "title",
    "raw_input": "Attention Is All You Need",
    "title": "Attention Is All You Need",
    "requester": "111111111111111111",
    "requested_at": "2026-09-16T00:20:24Z",
}


@pytest.fixture()
def conn(tmp_path):
    """A real file-backed DB with the contract applied."""
    db = tmp_path / "queue.sqlite"
    c = sqlite3.connect(db)
    c.executescript(SCHEMA.read_text())
    yield c
    c.close()


def insert(conn, **overrides):
    row = {**VALID_ROW, **overrides}
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO paper_requests ({cols}) VALUES ({marks})", tuple(row.values()))


class TestShape:
    """The columns the consumers read must exist — a rename is a contract break."""

    def test_all_required_columns_present(self, conn):
        actual = {r[1] for r in conn.execute("PRAGMA table_info(paper_requests)")}
        assert REQUIRED_COLUMNS <= actual, f"缺少列: {sorted(REQUIRED_COLUMNS - actual)}"

    def test_connection_pragmas_applied(self, conn):
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    def test_journal_mode_is_wal(self, conn):
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


class TestEnumEnforcement:
    """Enums are enforced by the DB, not by caller discipline."""

    @pytest.mark.parametrize("bad", ["paper", "DOI", "", "pdf"])
    def test_rejects_unknown_input_kind(self, conn, bad):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, input_kind=bad)

    @pytest.mark.parametrize("bad", ["guessed", "USER", ""])
    def test_rejects_unknown_doi_source(self, conn, bad):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, doi="10.1038/x", doi_source=bad)

    @pytest.mark.parametrize("bad", ["model", "plugin", ""])
    def test_rejects_unknown_attribution_source(self, conn, bad):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, attribution_source=bad)

    @pytest.mark.parametrize("bad", [2, -1, 0.5])
    def test_rejects_out_of_range_ambiguous_flag(self, conn, bad):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, attribution_ambiguous=bad)

    @pytest.mark.parametrize("source", ["single", "batched", None])
    def test_accepts_documented_attribution_sources(self, conn, source):
        insert(conn, attribution_source=source)
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 1

    def test_rejects_the_retired_session_latest_label(self, conn):
        """v3 起 `session_latest` 已退役 —— 非用户触发的回合不再产生记录，
        插件不绑定身份、写入被拒，没有"退回会话最近发言者"这条路。"""
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, attribution_source="session_latest")

    def test_accepts_documented_enum_values(self, conn):
        insert(conn, input_kind="doi", doi="10.1038/x", doi_source="user",
               attribution_source="single", attribution_ambiguous=0)
        insert(conn, request_key="k2", input_kind="arxiv", arxiv_id="2605.00412",
               title=None, attribution_source="batched", attribution_ambiguous=1)
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 2


class TestIdentifierRequired:
    """A row must carry at least one way to identify the paper."""

    def test_rejects_row_without_any_identifier(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, title=None)

    @pytest.mark.parametrize("field", ["doi", "arxiv_id", "url"])
    def test_each_identifier_alone_is_accepted(self, conn, field):
        insert(conn, title=None, **{field: "x"})
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 1


class TestCancelConsistency:
    """cancelled_at and cancelled_by travel together — one without the other is a bug."""

    def test_rejects_cancelled_at_without_by(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, cancelled_at="2026-09-16T01:00:00Z")

    def test_rejects_cancelled_by_without_at(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, cancelled_by="111111111111111111")

    def test_accepts_both_together(self, conn):
        insert(conn, cancelled_at="2026-09-16T01:00:00Z", cancelled_by="111111111111111111")
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 1


class TestTimestampFormat:
    """Range queries compare requested_at as TEXT, so a non-ISO value silently breaks them."""

    @pytest.mark.parametrize("bad", [
        "2026-09-16 00:00:00",      # 空格分隔
        "16/09/2026",
        "yesterday",
        "",
        "2026-09-16T00:00:00",      # 缺 Z
        "2026-09-16T00:00:00+08:00",  # 带偏移（非 UTC）
        # 带毫秒：看着像 ISO，但与 '…:24Z' 的字典序比较会被 '.' < 'Z' 判反 —— 静默漏记录
        "2026-09-16T00:20:24.123Z",
    ])
    def test_rejects_non_canonical_requested_at(self, conn, bad):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, requested_at=bad)

    @pytest.mark.parametrize("bad", ["2026/09/16 01:00", "2026-09-16T01:00:00",
                                     "2026-09-16T01:00:00.500Z"])
    def test_rejects_non_canonical_cancelled_at(self, conn, bad):
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, cancelled_at=bad, cancelled_by="u1")


class TestActiveKeyDedup:
    """Dedup applies to the active (non-cancelled) set; cancelling frees the key."""

    def test_rejects_second_active_row_with_same_key(self, conn):
        insert(conn)
        with pytest.raises(sqlite3.IntegrityError):
            insert(conn, title="同一篇，措辞不同")

    def test_allows_same_key_after_cancel(self, conn):
        insert(conn, cancelled_at="2026-09-16T01:00:00Z", cancelled_by="u1")
        insert(conn)  # 重新请求
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 2

    def test_allows_other_keys_concurrently(self, conn):
        insert(conn)
        insert(conn, request_key="title:other", title="另一篇")
        assert conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 2
