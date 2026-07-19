#!/usr/bin/env python3
"""
Deterministic data-access tool for the repo-triage Hermes skill.

Reads the repo-scanner SQLite database and outputs structured JSON to stdout.
No LLM calls, no Feishu calls — pure data access. Designed to be executed by
Hermes via its terminal tool.

Usage:
    python3 query_repo_data.py                    # Today's data (UTC)
    python3 query_repo_data.py --date 2026-07-19 # Specific date
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Container mount path (set by docker-compose), with host fallback for testing
_CONTAINER_DB = Path("/opt/myagentdata/repo-scanner/repos.sqlite")
_HOST_DB = Path.home() / ".myagentdata" / "repo-scanner" / "repos.sqlite"


def get_db_connection(db_path: Path) -> sqlite3.Connection | None:
    """Open a read-only connection to the repo-scanner SQLite database."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_new_commits(conn: sqlite3.Connection, date_str: str) -> list[sqlite3.Row]:
    """Get commits collected since the given date."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT repo_platform, repo_owner, repo_name, sha, author, date, message, url
        FROM commits
        WHERE date >= ? AND date < ?
        ORDER BY repo_platform, repo_owner, repo_name, date DESC
        """,
        (f"{date_str}T00:00:00Z", f"{date_str}T23:59:59Z"),
    )
    return cursor.fetchall()


def get_issues_activity(
    conn: sqlite3.Connection, date_str: str
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Get issue activity for the given date: new issues and closed issues."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT repo_platform, repo_owner, repo_name, issue_number, title, state, author
        FROM issues
        WHERE date(created_at) = ?
        ORDER BY repo_platform, repo_owner, repo_name, issue_number
        """,
        (date_str,),
    )
    new_issues = cursor.fetchall()
    cursor.execute(
        """
        SELECT repo_platform, repo_owner, repo_name, issue_number, title, state, author
        FROM issues
        WHERE date(closed_at) = ?
        ORDER BY repo_platform, repo_owner, repo_name, issue_number
        """,
        (date_str,),
    )
    closed_issues = cursor.fetchall()
    return new_issues, closed_issues


def get_pr_activity(
    conn: sqlite3.Connection, date_str: str
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Get PR activity for the given date: new PRs and merged PRs."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT repo_platform, repo_owner, repo_name, pr_number, title, state, author
        FROM pull_requests
        WHERE date(created_at) = ?
        ORDER BY repo_platform, repo_owner, repo_name, pr_number
        """,
        (date_str,),
    )
    new_prs = cursor.fetchall()
    cursor.execute(
        """
        SELECT repo_platform, repo_owner, repo_name, pr_number, title, state, author
        FROM pull_requests
        WHERE date(merged_at) = ?
        ORDER BY repo_platform, repo_owner, repo_name, pr_number
        """,
        (date_str,),
    )
    merged_prs = cursor.fetchall()
    return new_prs, merged_prs


def get_all_active_repos(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Get all repo configs that have been scanned at least once."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT repo_platform, repo_owner, repo_name
        FROM commits
        UNION ALL
        SELECT DISTINCT repo_platform, repo_owner, repo_name
        FROM issues
        UNION ALL
        SELECT DISTINCT repo_platform, repo_owner, repo_name
        FROM pull_requests
        ORDER BY repo_platform, repo_owner, repo_name
        """
    )
    return cursor.fetchall()


def _make_empty_repo(platform: str, owner: str, repo_name: str) -> dict:
    """Create an empty repo entry for the summary."""
    return {
        "platform": platform,
        "owner": owner,
        "repo": repo_name,
        "commits": [],
        "new_issues": [],
        "closed_issues": [],
        "new_prs": [],
        "merged_prs": [],
    }


def build_summary(date_str: str, db_path_override: Path | None = None) -> dict | None:
    """Build a structured summary for the given date.

    Args:
        date_str: YYYY-MM-DD date string.
        db_path_override: Override DB path (for testing). If None, uses
            the container mount path with host fallback.

    Returns:
        dict with per-repo summary data, or None if DB doesn't exist.
    """
    if db_path_override is not None:
        db_path = db_path_override
    elif _CONTAINER_DB.exists():
        db_path = _CONTAINER_DB
    else:
        db_path = _HOST_DB

    conn = get_db_connection(db_path)
    if conn is None:
        return None

    try:
        repos = get_all_active_repos(conn)
        commits = get_new_commits(conn, date_str)
        new_issues, closed_issues = get_issues_activity(conn, date_str)
        new_prs, merged_prs = get_pr_activity(conn, date_str)

        repo_map: dict[str, dict] = {}
        for platform, owner, repo_name in repos:
            key = f"{platform}/{owner}/{repo_name}"
            repo_map[key] = _make_empty_repo(platform, owner, repo_name)

        for row in commits:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["commits"].append({
                "sha": row["sha"][:7], "author": row["author"],
                "message": row["message"], "date": row["date"],
            })

        for row in new_issues:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["new_issues"].append({
                "number": row["issue_number"], "title": row["title"], "author": row["author"],
            })

        for row in closed_issues:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["closed_issues"].append({
                "number": row["issue_number"], "title": row["title"], "author": row["author"],
            })

        for row in new_prs:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["new_prs"].append({
                "number": row["pr_number"], "title": row["title"], "author": row["author"],
            })

        for row in merged_prs:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["merged_prs"].append({
                "number": row["pr_number"], "title": row["title"], "author": row["author"],
            })

        total_commits = sum(len(r["commits"]) for r in repo_map.values())
        total_new_issues = sum(len(r["new_issues"]) for r in repo_map.values())
        total_closed_issues = sum(len(r["closed_issues"]) for r in repo_map.values())
        total_new_prs = sum(len(r["new_prs"]) for r in repo_map.values())
        total_merged_prs = sum(len(r["merged_prs"]) for r in repo_map.values())
        has_any_activity = any(
            len(r["commits"]) + len(r["new_issues"]) + len(r["closed_issues"]) +
            len(r["new_prs"]) + len(r["merged_prs"]) > 0
            for r in repo_map.values()
        )

        return {
            "date": date_str,
            "scanned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repos": list(repo_map.values()),
            "totals": {
                "repos_scanned": len(repo_map),
                "total_commits": total_commits,
                "total_new_issues": total_new_issues,
                "total_closed_issues": total_closed_issues,
                "total_new_prs": total_new_prs,
                "total_merged_prs": total_merged_prs,
            },
            "has_activity": has_any_activity,
        }
    finally:
        conn.close()


def main() -> None:
    date_str = None
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--date" and i + 1 < len(args):
            date_str = args[i + 1]
            break
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    summary = build_summary(date_str)
    if summary is None:
        print("Error: database not found", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
