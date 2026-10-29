"""Local delivery state: cursor, sent-event de-duplication, retry outbox.

This is channel state, not a copy of issue data. Rollback journal (not WAL)
so the backup pipeline can hot-copy it with ``-readonly``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sent (
    event_key TEXT PRIMARY KEY,
    event_id INTEGER NOT NULL,
    sent_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS outbox (
    event_id INTEGER PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    card_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dead (
    event_id INTEGER PRIMARY KEY,
    event_key TEXT NOT NULL,
    card_json TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    last_error TEXT,
    dead_at TEXT DEFAULT (datetime('now'))
);
"""


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.executescript(DDL)
        self.conn.commit()

    def get_cursor(self) -> int | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key='cursor'").fetchone()
        return int(row[0]) if row else None

    def set_cursor(self, value: int) -> None:
        self.conn.execute(
            "INSERT INTO kv(key, value) VALUES ('cursor', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(int(value)),),
        )
        self.conn.commit()

    def is_sent(self, event_key: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM sent WHERE event_key=? LIMIT 1", (event_key,)
            ).fetchone()
            is not None
        )

    def is_known(self, event_key: str) -> bool:
        """Sent, waiting in the outbox, or dead-lettered."""
        return (
            self.conn.execute(
                "SELECT 1 FROM sent WHERE event_key=? "
                "UNION ALL SELECT 1 FROM outbox WHERE event_key=? "
                "UNION ALL SELECT 1 FROM dead WHERE event_key=? LIMIT 1",
                (event_key, event_key, event_key),
            ).fetchone()
            is not None
        )

    def enqueue(self, event_id: int, event_key: str, card_json: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO outbox(event_id, event_key, card_json) "
            "VALUES (?, ?, ?)",
            (int(event_id), event_key, card_json),
        )
        self.conn.commit()

    def pending(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT event_id, event_key, card_json, attempts, last_error, created_at "
                "FROM outbox ORDER BY event_id LIMIT 50"
            )
        )

    def mark_sent(self, event_id: int, event_key: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO sent(event_key, event_id) VALUES (?, ?)",
                (event_key, int(event_id)),
            )
            self.conn.execute("DELETE FROM outbox WHERE event_id=?", (int(event_id),))

    def mark_failed(self, event_id: int, error: str) -> None:
        self.conn.execute(
            "UPDATE outbox SET attempts=attempts+1, last_error=? WHERE event_id=?",
            (error[:500], int(event_id)),
        )
        self.conn.commit()

    def pending_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def dead_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM dead").fetchone()[0])

    def dead_letter(self, event_id: int, error: str) -> None:
        """Move a permanently failing card out of the retry path."""
        row = self.conn.execute(
            "SELECT event_key, card_json, attempts FROM outbox WHERE event_id=?",
            (int(event_id),),
        ).fetchone()
        if row is None:
            return
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO dead(event_id, event_key, card_json, attempts, last_error) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(event_id), row[0], row[1], row[2], error[:500]),
            )
            self.conn.execute("DELETE FROM outbox WHERE event_id=?", (int(event_id),))

    def requeue_dead(self) -> int:
        """Move dead-lettered cards back to the outbox (operator recovery)."""
        rows = list(
            self.conn.execute("SELECT event_id, event_key, card_json FROM dead")
        )
        if not rows:
            return 0
        with self.conn:
            for event_id, event_key, card_json in rows:
                self.conn.execute(
                    "INSERT OR IGNORE INTO outbox"
                    "(event_id, event_key, card_json, attempts, created_at) "
                    "VALUES (?, ?, ?, 0, datetime('now'))",
                    (event_id, event_key, card_json),
                )
                self.conn.execute("DELETE FROM dead WHERE event_id=?", (event_id,))
        return len(rows)

    def purge_dead(self, older_than_days: int = 7) -> int:
        """Drop dead cards older than N days (card_json contains private titles)."""
        cursor = self.conn.execute(
            "DELETE FROM dead WHERE dead_at < datetime('now', ?)",
            (f"-{int(older_than_days)} days",),
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def close(self) -> None:
        self.conn.close()


def snapshot(state: State) -> dict[str, Any]:
    return {
        "cursor": state.get_cursor(),
        "pending": state.pending_count(),
        "dead": state.dead_count(),
    }
