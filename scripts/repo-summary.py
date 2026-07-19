#!/usr/bin/env python3
"""
仓库摘要生成器 — 从 SQLite 读取采集数据，输出结构化 JSON 供 Hermes LLM 生成自然语言摘要。

数据流:
  SQLite (repos.sqlite)  →  JSON 摘要  →  Hermes LLM  →  自然语言  →  ledger  →  Feishu

用法:
  python3 scripts/repo-summary.py                  # 今日汇总，纯文本
  python3 scripts/repo-summary.py --json           # 今日汇总，JSON 输出
  python3 scripts/repo-summary.py --date 2024-01-15  # 指定日期
  python3 scripts/repo-summary.py --date 2024-01-15 --json
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_DIR = Path.home() / ".myagentdata" / "repo-scanner"
DB_PATH = DB_DIR / "repos.sqlite"


# =============================================================
# 1. DB helpers
# =============================================================

def get_db_connection(db_path=DB_PATH):
    """Open a read-only connection to the repo-scanner SQLite database.

    Args:
        db_path: Path to the SQLite database.

    Returns:
        sqlite3.Connection or None if database does not exist.
    """
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================
# 2. Query helpers
# =============================================================

def get_new_commits(conn, date_str):
    """Get commits collected since the given date.

    Args:
        conn: sqlite3.Connection.
        date_str: YYYY-MM-DD date string.

    Returns:
        list of sqlite3.Row objects.
    """
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


def get_issues_activity(conn, date_str):
    """Get issue activity for the given date: new issues and closed issues.

    Args:
        conn: sqlite3.Connection.
        date_str: YYYY-MM-DD date string.

    Returns:
        tuple: (new_issues, closed_issues) as lists of sqlite3.Row.
    """
    cursor = conn.cursor()

    # New issues (created on this date)
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

    # Closed issues (closed on this date)
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


def get_pr_activity(conn, date_str):
    """Get PR activity for the given date: new PRs and merged PRs.

    Args:
        conn: sqlite3.Connection.
        date_str: YYYY-MM-DD date string.

    Returns:
        tuple: (new_prs, merged_prs) as lists of sqlite3.Row.
    """
    cursor = conn.cursor()

    # New PRs (created on this date)
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

    # Merged PRs (merged on this date)
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


def get_all_active_repos(conn):
    """Get all repo configs that have been scanned at least once.

    Args:
        conn: sqlite3.Connection.

    Returns:
        list of (platform, owner, repo) tuples.
    """
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


# =============================================================
# 3. Summary builder
# =============================================================

def build_summary(date_str):
    """Build a structured summary for the given date.

    Args:
        date_str: YYYY-MM-DD date string.

    Returns:
        dict with per-repo summary data, or None if DB doesn't exist.
    """
    conn = get_db_connection()
    if conn is None:
        return None

    try:
        repos = get_all_active_repos(conn)
        commits = get_new_commits(conn, date_str)
        new_issues, closed_issues = get_issues_activity(conn, date_str)
        new_prs, merged_prs = get_pr_activity(conn, date_str)

        # Group by repo
        repo_map = {}
        for platform, owner, repo_name in repos:
            key = f"{platform}/{owner}/{repo_name}"
            repo_map[key] = {
                "platform": platform,
                "owner": owner,
                "repo": repo_name,
                "commits": [],
                "new_issues": [],
                "closed_issues": [],
                "new_prs": [],
                "merged_prs": [],
            }

        # Group commits
        for row in commits:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["commits"].append({
                "sha": row["sha"][:7],
                "author": row["author"],
                "message": row["message"],
                "date": row["date"],
            })

        # Group issues
        for row in new_issues:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["new_issues"].append({
                "number": row["issue_number"],
                "title": row["title"],
                "author": row["author"],
            })

        for row in closed_issues:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["closed_issues"].append({
                "number": row["issue_number"],
                "title": row["title"],
                "author": row["author"],
            })

        # Group PRs
        for row in new_prs:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["new_prs"].append({
                "number": row["pr_number"],
                "title": row["title"],
                "author": row["author"],
            })

        for row in merged_prs:
            key = f"{row['repo_platform']}/{row['repo_owner']}/{row['repo_name']}"
            if key not in repo_map:
                repo_map[key] = _make_empty_repo(row["repo_platform"], row["repo_owner"], row["repo_name"])
            repo_map[key]["merged_prs"].append({
                "number": row["pr_number"],
                "title": row["title"],
                "author": row["author"],
            })

        # Build totals
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


def _make_empty_repo(platform, owner, repo_name):
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


# =============================================================
# 4. Formatters
# =============================================================

def format_text_summary(summary):
    """Format the summary as human-readable text.

    Args:
        summary: dict from build_summary().

    Returns:
        str: formatted text.
    """
    if summary is None:
        return "❌ 数据库不存在 — 请先运行 collect-repos.py"

    lines = []
    t = summary["totals"]

    lines.append(f"仓库进展扫描 — {summary['date']}")
    lines.append(f"扫描时间: {summary['scanned_at']}")
    lines.append(f"扫描仓库: {t['repos_scanned']} | "
                 f"Commits: {t['total_commits']} | "
                 f"Issues: {t['total_new_issues']} 新建 / {t['total_closed_issues']} 关闭 | "
                 f"PRs: {t['total_new_prs']} 新建 / {t['total_merged_prs']} 合并")
    lines.append("")

    if not summary["has_activity"]:
        lines.append("📭 今日无仓库活动")
        return "\n".join(lines)

    for repo in summary["repos"]:
        activity = []
        if repo["commits"]:
            activity.append(f"{len(repo['commits'])} commits")
        if repo["new_issues"]:
            activity.append(f"{len(repo['new_issues'])} new issues")
        if repo["closed_issues"]:
            activity.append(f"{len(repo['closed_issues'])} closed issues")
        if repo["new_prs"]:
            activity.append(f"{len(repo['new_prs'])} new PRs")
        if repo["merged_prs"]:
            activity.append(f"{len(repo['merged_prs'])} merged PRs")

        if not activity:
            continue

        key = f"{repo['platform']}/{repo['owner']}/{repo['repo']}"
        lines.append(f"📦 {key}: {', '.join(activity)}")

        for c in repo["commits"]:
            lines.append(f"     commit {c['sha']} — {c['message']} ({c['author']})")

        for i in repo["new_issues"]:
            lines.append(f"     issue #{i['number']} — {i['title']} ({i['author']})")

        for i in repo["closed_issues"]:
            lines.append(f"     closed #{i['number']} — {i['title']} ({i['author']})")

        for pr in repo["new_prs"]:
            lines.append(f"     PR #{pr['number']} — {pr['title']} ({pr['author']})")

        for pr in repo["merged_prs"]:
            lines.append(f"     merged #{pr['number']} — {pr['title']} ({pr['author']})")

        lines.append("")

    return "\n".join(lines)


def format_json_summary(summary):
    """Format the summary as JSON.

    Args:
        summary: dict from build_summary().

    Returns:
        str: JSON string.
    """
    if summary is None:
        return json.dumps({"error": "database not found", "action": "run collect-repos.py first"})
    return json.dumps(summary, ensure_ascii=False, indent=2)


# =============================================================
# 5. Main
# =============================================================

def main():
    # Parse arguments
    json_output = "--json" in sys.argv

    date_str = None
    for i, arg in enumerate(sys.argv):
        if arg == "--date" and i + 1 < len(sys.argv):
            date_str = sys.argv[i + 1]
            break

    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build summary
    summary = build_summary(date_str)

    # Output
    if json_output:
        print(format_json_summary(summary))
    else:
        print(format_text_summary(summary))


if __name__ == "__main__":
    main()
