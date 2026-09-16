"""Behavioral guards for scripts/check_paper_queue.py — the "no model involved" read path.

Run: uv run --with pytest pytest tests/test_paper_queue_checker.py -v

The checker is the only surface that answers "what's on the list" without a model or a
live 虾酱, so its exit codes and JSON fields are a contract for cron/monitoring. These
tests run it as a subprocess against deliberately-broken databases — the point is that
the invariants are enforced against data from *any* source, not just rows our own writer
would have produced.

Several scenarios build their table by hand (no CHECK constraints) because a DB written
by an older schema, or by a different tool, is exactly what the checker exists to catch.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "scripts" / "check_paper_queue.py"
SCHEMA = REPO_ROOT / "openclaw" / "skills" / "paper-queue" / "schema.sql"

# Columns the checker depends on. Hand-built "broken" DBs use this shape so the column
# check passes and the row-level checks are what actually fires.
LOOSE_TABLE = """
CREATE TABLE paper_requests (
  id INTEGER PRIMARY KEY,
  request_key TEXT NOT NULL, input_kind TEXT, raw_input TEXT,
  title TEXT, doi TEXT, doi_source TEXT, arxiv_id TEXT, url TEXT, note TEXT,
  requester TEXT, requester_name TEXT, channel_ref TEXT, message_ref TEXT,
  session_ref TEXT, attribution_source TEXT,
  attribution_ambiguous INTEGER DEFAULT 0,
  requested_at TEXT, cancelled_at TEXT, cancelled_by TEXT
)
"""

VALID_ROW = {
    "request_key": "title:aaa",
    "input_kind": "title",
    "raw_input": "Attention Is All You Need",
    "title": "Attention Is All You Need",
    "requester": "1297756995834609676",
    "requested_at": "2026-09-16T00:20:24Z",
}


def run_checker(db: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PAPER_QUEUE_DB": str(db)}
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def make_valid_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text())
    conn.commit()
    conn.close()


def make_loose_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(LOOSE_TABLE)
    conn.commit()
    return conn


def insert(conn: sqlite3.Connection, **overrides) -> None:
    row = {**VALID_ROW, **overrides}
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO paper_requests ({cols}) VALUES ({marks})", tuple(row.values()))
    conn.commit()


@pytest.fixture()
def jsonify():
    def parse(result: subprocess.CompletedProcess) -> dict:
        assert result.stdout.strip(), f"stdout 为空；stderr={result.stderr!r}"
        return json.loads(result.stdout)
    return parse


class TestExitCodes:
    """Exit codes are the cron/monitoring interface — 0 green, 1 findings, 2 script error."""

    def test_missing_db_is_not_an_error(self, tmp_path):
        """A queue nobody has written to yet is not a failure — it must not page anyone."""
        r = run_checker(tmp_path / "nope.sqlite", "--json")
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout)["status"] == "not_initialized"

    def test_empty_sqlite_file_is_not_an_error(self, tmp_path):
        db = tmp_path / "empty.sqlite"
        sqlite3.connect(db).close()
        assert run_checker(db, "--json").returncode == 0

    def test_healthy_db_exits_zero(self, tmp_path, jsonify):
        db = tmp_path / "q.sqlite"
        make_valid_db(db)
        r = run_checker(db, "--json")
        assert r.returncode == 0, r.stderr
        assert jsonify(r)["status"] == "ok"

    def test_findings_exit_one(self, tmp_path):
        db = tmp_path / "q.sqlite"
        conn = make_loose_db(db)
        insert(conn, title=None)  # 没有任何标识
        conn.close()
        assert run_checker(db, "--json").returncode == 1

    def test_unreadable_db_exits_two(self, tmp_path):
        """A non-SQLite file at the queue path is a script/config error, not a finding."""
        db = tmp_path / "q.sqlite"
        db.write_text("this is not a database\n")
        assert run_checker(db, "--json").returncode == 2


class TestRowInvariants:
    """Rows from any source are held to the contract's rules."""

    def test_flags_row_without_identifier(self, tmp_path, jsonify):
        db = tmp_path / "q.sqlite"
        conn = make_loose_db(db)
        insert(conn, title=None)
        conn.close()
        r = run_checker(db, "--json")
        kinds = {f["kind"] for f in jsonify(r)["findings"]}
        assert "no_identifier" in kinds

    def test_flags_non_canonical_timestamp(self, tmp_path, jsonify):
        db = tmp_path / "q.sqlite"
        conn = make_loose_db(db)
        insert(conn, requested_at="2026-09-16T00:20:24.123Z")
        conn.close()
        r = run_checker(db, "--json")
        kinds = {f["kind"] for f in jsonify(r)["findings"]}
        assert "bad_timestamp" in kinds

    def test_flags_cancel_mismatch(self, tmp_path, jsonify):
        db = tmp_path / "q.sqlite"
        conn = make_loose_db(db)
        insert(conn, cancelled_at="2026-09-16T01:00:00Z")  # 缺 cancelled_by
        conn.close()
        r = run_checker(db, "--json")
        kinds = {f["kind"] for f in jsonify(r)["findings"]}
        assert "cancel_mismatch" in kinds

    def test_flags_duplicate_active_key(self, tmp_path, jsonify):
        """Without the unique index (older DB), duplicates are possible — flag them."""
        db = tmp_path / "q.sqlite"
        conn = make_loose_db(db)
        insert(conn)
        insert(conn, title="同一篇，措辞不同")
        conn.close()
        r = run_checker(db, "--json")
        kinds = {f["kind"] for f in jsonify(r)["findings"]}
        assert "duplicate_active_key" in kinds

    def test_flags_wrong_database(self, tmp_path, jsonify):
        """Pointed at some other SQLite DB, the checker must not report a healthy empty queue."""
        db = tmp_path / "q.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
        conn.close()
        r = run_checker(db, "--json")
        assert r.returncode == 1
        assert jsonify(r)["status"] == "findings"

    def test_wrong_database_human_mode_does_not_crash(self, tmp_path):
        """人类可读分支也要能处理"计数未知"的情况，不能 KeyError（曾漏过）。"""
        db = tmp_path / "q.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
        conn.close()
        r = run_checker(db)
        assert r.returncode == 1, r.stderr
        assert "wrong_database" in r.stdout
        assert "Traceback" not in r.stderr


class TestCounts:
    """The counts are what a human reads to decide whether to act."""

    def test_counts_active_cancelled_and_windows(self, tmp_path, jsonify):
        db = tmp_path / "q.sqlite"
        make_valid_db(db)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO paper_requests (request_key, input_kind, raw_input, title, requester,"
            " requested_at) VALUES (?,?,?,?,?,?)",
            ("k1", "title", "a", "A", "u1", "2026-09-16T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO paper_requests (request_key, input_kind, raw_input, title, requester,"
            " requested_at, cancelled_at, cancelled_by) VALUES (?,?,?,?,?,?,?,?)",
            ("k2", "title", "b", "B", "u1", "2026-09-16T00:00:00Z",
             "2026-09-16T01:00:00Z", "u1"),
        )
        conn.commit()
        conn.close()

        data = jsonify(run_checker(db, "--json"))
        assert data["counts"]["total"] == 2
        assert data["counts"]["active"] == 1
        assert data["counts"]["cancelled"] == 1

    def test_reports_ambiguous_attribution_without_failing(self, tmp_path, jsonify):
        """Ambiguity is a known, accepted condition — surfaced loudly, but not a violation."""
        db = tmp_path / "q.sqlite"
        make_valid_db(db)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO paper_requests (request_key, input_kind, raw_input, title, requester,"
            " requested_at, attribution_ambiguous) VALUES (?,?,?,?,?,?,?)",
            ("k1", "title", "a", "A", "u1", "2026-09-16T00:00:00Z", 1),
        )
        conn.commit()
        conn.close()

        r = run_checker(db, "--json")
        assert r.returncode == 0
        assert jsonify(r)["counts"]["attribution_ambiguous"] == 1

        human = run_checker(db)
        assert "歧义" in human.stdout, "人类可读输出也必须显示歧义计数，不能只在 --json 里"


class TestOutputShape:
    """Both output modes must be usable: JSON for machines, emoji+indent for humans."""

    def test_json_fields_are_stable(self, tmp_path, jsonify):
        db = tmp_path / "q.sqlite"
        make_valid_db(db)
        data = jsonify(run_checker(db, "--json"))
        assert set(data) >= {"status", "db", "counts", "findings"}
        assert set(data["counts"]) >= {"total", "active", "cancelled", "attribution_ambiguous"}

    def test_human_mode_has_no_json(self, tmp_path):
        db = tmp_path / "q.sqlite"
        make_valid_db(db)
        r = run_checker(db)
        assert r.returncode == 0
        with pytest.raises(json.JSONDecodeError):
            json.loads(r.stdout)
