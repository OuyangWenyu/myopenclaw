"""Single writer, independent channel DB; enqueue and cursor commit atomically."""

from __future__ import annotations
import hashlib
import os
from pathlib import Path
import sqlite3
import time

DDL = """
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(message_key TEXT PRIMARY KEY,event_key TEXT NOT NULL,
 event_id INTEGER NOT NULL,card_json TEXT NOT NULL,created_at REAL NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT);
CREATE TABLE IF NOT EXISTS sent(message_key TEXT PRIMARY KEY,sent_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS dead(message_key TEXT PRIMARY KEY,event_key TEXT NOT NULL,
 event_id INTEGER NOT NULL,card_json TEXT NOT NULL,created_at REAL NOT NULL,
 attempts INTEGER NOT NULL,last_error TEXT,dead_at REAL NOT NULL);
"""


class State:
    def __init__(self, path: Path, *, readonly: bool = False):
        self.path, self.readonly, self.lock = Path(path), readonly, None
        if readonly:
            self.conn = (
                sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
                if self.path.exists()
                else sqlite3.connect(":memory:")
            )
            if not self.path.exists():
                self.conn.executescript(DDL)
            self.conn.execute("PRAGMA query_only=ON")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink():
                raise ValueError("State path must not be a symlink")
            lock_path = Path(str(self.path) + ".lock")
            fd = os.open(
                lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            self.lock = os.fdopen(fd, "r+b")
            try:
                if os.fstat(fd).st_size == 0:
                    self.lock.write(b"0")
                    self.lock.flush()
                self.lock.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fd_db = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(fd_db)
                self.conn = sqlite3.connect(self.path, timeout=5)
                self.conn.execute("PRAGMA journal_mode=DELETE")
                self.conn.executescript(DDL)
            except BaseException:
                if hasattr(self, "conn"):
                    self.conn.close()
                self.lock.close()
                self.lock = None
                raise RuntimeError(
                    "Health notification state unavailable or another writer is running"
                ) from None
        self.conn.row_factory = sqlite3.Row

    def get(self, key, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def _set(self, key, value):
        self.conn.execute(
            "INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def bind_source(self, source):
        known = self.get("source")
        if known and known != source:
            raise ValueError(
                "State belongs to another MCP endpoint; use a separate state directory"
            )
        if not known and not self.readonly:
            with self.conn:
                self._set("source", source)

    def bind_destination(self, destination):
        fingerprint = hashlib.sha256(destination.encode()).hexdigest()
        known = self.get("destination")
        if known and known != fingerprint:
            raise ValueError(
                "State belongs to another delivery destination; use a separate state directory"
            )
        if not known and not self.readonly:
            with self.conn:
                self._set("destination", fingerprint)

    def cursor(self):
        value = self.get("cursor")
        return int(value) if value is not None else None

    def baseline(self, cursor, *, reset=False):
        with self.conn:
            if reset:
                self._set("generation", int(self.get("generation", "0")) + 1)
            self._set("cursor", cursor)

    def enqueue_page(self, rows, cursor):
        queued = 0
        with self.conn:
            generation = self.get("generation", "0")
            for event, card_json in rows:
                key = hashlib.sha256(
                    (
                        self.get("source", "server-monitoring")
                        + "|"
                        + generation
                        + "|"
                        + event["event_key"]
                    ).encode()
                ).hexdigest()
                if self.conn.execute(
                    "SELECT 1 FROM sent WHERE message_key=? UNION ALL SELECT 1 FROM dead WHERE message_key=?",
                    (key, key),
                ).fetchone():
                    continue
                queued += self.conn.execute(
                    "INSERT OR IGNORE INTO outbox(message_key,event_key,event_id,card_json,created_at) VALUES (?,?,?,?,?)",
                    (
                        key,
                        event["event_key"],
                        event["event_id"],
                        card_json,
                        time.time(),
                    ),
                ).rowcount
            self._set("cursor", cursor)
        return queued

    def pending(self):
        return list(
            self.conn.execute(
                "SELECT * FROM outbox ORDER BY created_at,event_id LIMIT 50"
            )
        )

    def pending_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def dead_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM dead").fetchone()[0]

    def oldest_pending_age(self):
        stamp = self.conn.execute("SELECT MIN(created_at) FROM outbox").fetchone()[0]
        return None if stamp is None else max(0, int(time.time() - stamp))

    def mark_sent(self, key):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO sent VALUES (?,?)", (key, time.time())
            )
            self.conn.execute("DELETE FROM outbox WHERE message_key=?", (key,))

    def mark_failed(self, key, code):
        with self.conn:
            self.conn.execute(
                "UPDATE outbox SET attempts=attempts+1,last_error=? WHERE message_key=?",
                (code, key),
            )

    def dead_letter(self, key):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO dead SELECT *,? FROM outbox WHERE message_key=?",
                (time.time(), key),
            )
            self.conn.execute("DELETE FROM outbox WHERE message_key=?", (key,))

    def requeue_dead(self):
        with self.conn:
            rows = list(self.conn.execute("SELECT * FROM dead"))
            for row in rows:
                self.conn.execute(
                    "INSERT OR IGNORE INTO outbox VALUES (?,?,?,?,?,0,NULL)",
                    (
                        row["message_key"],
                        row["event_key"],
                        row["event_id"],
                        row["card_json"],
                        time.time(),
                    ),
                )
            self.conn.execute("DELETE FROM dead")
        return len(rows)

    def purge_dead(self):
        with self.conn:
            self.conn.execute(
                "DELETE FROM dead WHERE dead_at < ?", (time.time() - 7 * 86400,)
            )

    def close(self):
        try:
            self.conn.close()
        finally:
            if self.lock is not None:
                self.lock.close()
                self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
