#!/usr/bin/env python3
"""
Tests for skills/repo-triage/tools/query_repo_data.py — deterministic SQLite data tool.

Usage:
    python3 -m pytest scripts/tests/test_query_repo_data.py -v
"""

import importlib.util
import json
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

# Load module via importlib (filename has hyphen in path but module is flat)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "skills" / "repo-triage" / "tools" / "query_repo_data.py"
)
# If module doesn't exist yet (RED phase), create a minimal stub for import
if not MODULE_PATH.exists():
    MODULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODULE_PATH.write_text("""
\"\"\"Stub — tests written first.\"\"\"
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = None  # patched by tests

def get_db_connection(db_path):
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def build_summary(date_str, db_path_override=None):
    return None  # stub

if __name__ == "__main__":
    pass
""")

spec = importlib.util.spec_from_file_location(
    "query_repo_data", str(MODULE_PATH)
)
query_repo_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query_repo_data)
sys.modules["query_repo_data"] = query_repo_data


# ============================================================================
# Helpers
# ============================================================================

def _make_test_db() -> tuple[sqlite3.Connection, str]:
    """Create an in-memory SQLite DB with test schema and data."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS commits (
            sha TEXT NOT NULL,
            repo_platform TEXT NOT NULL,
            repo_owner TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            author TEXT,
            date TEXT,
            message TEXT,
            url TEXT,
            collected_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (repo_platform, repo_owner, repo_name, sha)
        );

        CREATE TABLE IF NOT EXISTS issues (
            issue_number INTEGER NOT NULL,
            repo_platform TEXT NOT NULL,
            repo_owner TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            title TEXT,
            state TEXT,
            author TEXT,
            created_at TEXT,
            closed_at TEXT,
            is_new_24h INTEGER DEFAULT 0,
            is_closed_24h INTEGER DEFAULT 0,
            collected_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (repo_platform, repo_owner, repo_name, issue_number)
        );

        CREATE TABLE IF NOT EXISTS pull_requests (
            pr_number INTEGER NOT NULL,
            repo_platform TEXT NOT NULL,
            repo_owner TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            title TEXT,
            state TEXT,
            author TEXT,
            created_at TEXT,
            merged_at TEXT,
            is_new_24h INTEGER DEFAULT 0,
            is_merged_24h INTEGER DEFAULT 0,
            collected_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (repo_platform, repo_owner, repo_name, pr_number)
        );
    """)

    today = date.today().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO commits VALUES (?,?,?,?,?,?,?,?,?)",
        ("abc1234567abcdef", "github", "OuyangWenyu", "torchhydro",
         "owen", f"{today}T10:00:00Z", "fix: resolve memory leak", "",
         f"{today}T10:00:00Z"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO commits VALUES (?,?,?,?,?,?,?,?,?)",
        ("def5678901ghijkl", "github", "OuyangWenyu", "torchhydro",
         "owen", f"{today}T14:00:00Z", "feat: add transformer model", "",
         f"{today}T14:00:00Z"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (42, "github", "OuyangWenyu", "torchhydro",
         "Bug: crash on empty tensor", "open", "user1",
         f"{today}T08:00:00Z", None, 1, 0, f"{today}T08:00:00Z"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (40, "github", "OuyangWenyu", "torchhydro",
         "Fix NaN gradient", "closed", "user2",
         "2026-07-10T08:00:00Z", f"{today}T12:00:00Z", 0, 1,
         f"{today}T12:00:00Z"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO pull_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (101, "github", "iHeadWater", "HydroAgent",
         "Add multi-GPU support", "open", "contributor1",
         f"{today}T09:00:00Z", None, 1, 0, f"{today}T09:00:00Z"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO pull_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (100, "github", "iHeadWater", "HydroAgent",
         "Fix typo in README", "merged", "owen",
         "2026-07-11T09:00:00Z", f"{today}T15:00:00Z", 0, 1,
         f"{today}T15:00:00Z"),
    )
    conn.commit()
    return conn, tmp.name


# ============================================================================
# Tests
# ============================================================================

today_str = date.today().isoformat()


def test_db_connection_success():
    """Existing DB file returns a valid connection."""
    conn, db_path = _make_test_db()
    conn.close()
    db = query_repo_data.get_db_connection(Path(db_path))
    assert db is not None
    db.close()
    Path(db_path).unlink()


def test_db_connection_nonexistent():
    """Non-existent file returns None."""
    db = query_repo_data.get_db_connection(Path("/nonexistent/path.sqlite"))
    assert db is None


def test_build_summary_has_activity():
    """DB with data returns summary with has_activity=True."""
    conn, db_path = _make_test_db()
    conn.close()
    summary = query_repo_data.build_summary(today_str, Path(db_path))
    assert summary is not None
    assert summary["has_activity"] is True
    assert summary["date"] == today_str
    Path(db_path).unlink()


def test_build_summary_commits():
    """Commits are grouped correctly by repo."""
    conn, db_path = _make_test_db()
    conn.close()
    summary = query_repo_data.build_summary(today_str, Path(db_path))
    repos = {f"{r['platform']}/{r['owner']}/{r['repo']}": r for r in summary["repos"]}
    assert "github/OuyangWenyu/torchhydro" in repos
    assert len(repos["github/OuyangWenyu/torchhydro"]["commits"]) == 2
    Path(db_path).unlink()


def test_build_summary_issues():
    """New and closed issues are separated correctly."""
    conn, db_path = _make_test_db()
    conn.close()
    summary = query_repo_data.build_summary(today_str, Path(db_path))
    repos = {f"{r['platform']}/{r['owner']}/{r['repo']}": r for r in summary["repos"]}
    assert len(repos["github/OuyangWenyu/torchhydro"]["new_issues"]) == 1
    assert len(repos["github/OuyangWenyu/torchhydro"]["closed_issues"]) == 1
    Path(db_path).unlink()


def test_build_summary_prs():
    """New and merged PRs are separated correctly."""
    conn, db_path = _make_test_db()
    conn.close()
    summary = query_repo_data.build_summary(today_str, Path(db_path))
    repos = {f"{r['platform']}/{r['owner']}/{r['repo']}": r for r in summary["repos"]}
    assert len(repos["github/iHeadWater/HydroAgent"]["new_prs"]) == 1
    assert len(repos["github/iHeadWater/HydroAgent"]["merged_prs"]) == 1
    Path(db_path).unlink()


def test_build_summary_totals():
    """Totals reflect actual row counts."""
    conn, db_path = _make_test_db()
    conn.close()
    summary = query_repo_data.build_summary(today_str, Path(db_path))
    assert summary["totals"]["repos_scanned"] == 2
    assert summary["totals"]["total_commits"] == 2
    assert summary["totals"]["total_new_issues"] == 1
    assert summary["totals"]["total_closed_issues"] == 1
    assert summary["totals"]["total_new_prs"] == 1
    assert summary["totals"]["total_merged_prs"] == 1
    Path(db_path).unlink()


def test_build_summary_empty_db():
    """Empty DB returns has_activity=False."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS commits (
            sha TEXT, repo_platform TEXT, repo_owner TEXT, repo_name TEXT,
            author TEXT, date TEXT, message TEXT, url TEXT,
            collected_at TEXT, PRIMARY KEY (repo_platform, repo_owner, repo_name, sha)
        );
        CREATE TABLE IF NOT EXISTS issues (
            issue_number INTEGER, repo_platform TEXT, repo_owner TEXT, repo_name TEXT,
            title TEXT, state TEXT, author TEXT, created_at TEXT, closed_at TEXT,
            is_new_24h INTEGER DEFAULT 0, is_closed_24h INTEGER DEFAULT 0,
            collected_at TEXT, PRIMARY KEY (repo_platform, repo_owner, repo_name, issue_number)
        );
        CREATE TABLE IF NOT EXISTS pull_requests (
            pr_number INTEGER, repo_platform TEXT, repo_owner TEXT, repo_name TEXT,
            title TEXT, state TEXT, author TEXT, created_at TEXT, merged_at TEXT,
            is_new_24h INTEGER DEFAULT 0, is_merged_24h INTEGER DEFAULT 0,
            collected_at TEXT, PRIMARY KEY (repo_platform, repo_owner, repo_name, pr_number)
        );
    """)
    conn.commit()
    conn.close()
    summary = query_repo_data.build_summary(today_str, Path(tmp.name))
    assert summary is not None
    assert summary["has_activity"] is False
    Path(tmp.name).unlink()


def test_build_summary_nonexistent_db():
    """Non-existent DB file returns None."""
    summary = query_repo_data.build_summary(
        today_str, Path("/nonexistent/path.sqlite")
    )
    assert summary is None


def test_build_summary_sha_truncated():
    """Commit SHA is truncated to 7 characters."""
    conn, db_path = _make_test_db()
    conn.close()
    summary = query_repo_data.build_summary(today_str, Path(db_path))
    repos = {f"{r['platform']}/{r['owner']}/{r['repo']}": r for r in summary["repos"]}
    sha = repos["github/OuyangWenyu/torchhydro"]["commits"][0]["sha"]
    assert len(sha) == 7
    Path(db_path).unlink()


def test_build_summary_date_filtering():
    """Only records matching the requested date are returned."""
    conn, db_path = _make_test_db()
    conn.close()
    # Query a date far in the future — should have no data
    summary = query_repo_data.build_summary("2099-01-01", Path(db_path))
    assert summary is not None
    assert summary["has_activity"] is False
    Path(db_path).unlink()


def test_build_summary_json_output():
    """Summary dict is JSON-serializable."""
    conn, db_path = _make_test_db()
    conn.close()
    summary = query_repo_data.build_summary(today_str, Path(db_path))
    dumped = json.dumps(summary)
    reloaded = json.loads(dumped)
    assert reloaded["has_activity"] is True
    Path(db_path).unlink()
