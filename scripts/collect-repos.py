#!/usr/bin/env python3
"""
仓库进展扫描器 — 每日采集 GitHub/GitCode 仓库的 commits、issues、PRs 并存入 SQLite。

数据流:
  configs/repos.toml  →  API 采集  →  SQLite (~/.myagentdata/repo-scanner/repos.sqlite)
                                 →  供 Hermes repo-summary.py 读取
                                 →  morning-triage 生成自然语言摘要

用法:
  python3 scripts/collect-repos.py                  # 默认采集昨天 00:00 UTC 至今
  python3 scripts/collect-repos.py --dry-run        # 预览模式，不写 DB
  python3 scripts/collect-repos.py --since 2024-01-15  # 指定起始日期
"""

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── TOML parsing: tomllib (3.11+) with fallback ──────────────────────────
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        print("Error: tomllib (Python 3.11+) or tomli package required", file=sys.stderr)
        sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "configs" / "repos.toml"
DB_DIR = Path.home() / ".myagentdata" / "repo-scanner"
DB_PATH = DB_DIR / "repos.sqlite"
ENV_FILE = REPO_ROOT / ".env"

# ── Constants ─────────────────────────────────────────────────────────────
GITHUB_API_BASE = "https://api.github.com"
GITCODE_API_BASE = "https://gitcode.com/api/v5"
MAX_WORKERS = 5
PER_PAGE = 100
WINDOW_HOURS = 24


# =============================================================
# 1. Config loading
# =============================================================

def parse_repos_config(config_path=CONFIG_FILE):
    """Parse repos.toml into a dict with 'github' and 'gitcode' keys.

    Args:
        config_path: Path to the TOML config file.

    Returns:
        dict: {"github": [{"owner": ..., "repo": ...}, ...],
               "gitcode": [{"owner": ..., "repo": ...}, ...]}

    Raises:
        FileNotFoundError: if config file does not exist.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    repos = {"github": [], "gitcode": []}
    for platform in ("github", "gitcode"):
        for entry in data.get(platform, []):
            if "owner" in entry and "repo" in entry:
                repos[platform].append({
                    "owner": entry["owner"],
                    "repo": entry["repo"],
                })
    return repos


# =============================================================
# 2. Token loading
# =============================================================

def load_github_token(env_file=ENV_FILE):
    """Read GITHUB_TOKEN from environment or .env file.

    Priority:
      1. GITHUB_TOKEN environment variable
      2. GH_TOKEN in .env file (mapped to GITHUB_TOKEN in Docker)

    Args:
        env_file: Path to .env file.

    Returns:
        str or None: the token if found.
    """
    # Check env var first
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    # Fall back to .env file
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith("GH_TOKEN="):
                    return stripped.split("=", 1)[1].strip()
        except OSError:
            pass

    return None


def load_gitcode_token():
    """Read GitCode token from environment.

    Returns:
        str or None: the token if found.
    """
    return os.environ.get("GITCODE_TOKEN")


# =============================================================
# 3. Date / time helpers
# =============================================================

def get_since_date(since_str=None):
    """Compute the since date for API queries.

    Args:
        since_str: ISO 8601 or YYYY-MM-DD string, or None for default (24h ago).

    Returns:
        str: ISO 8601 datetime string.
    """
    if since_str:
        # If already ISO format with 'T', return as-is
        if "T" in since_str:
            return since_str
        # YYYY-MM-DD: convert to start of day UTC
        return f"{since_str}T00:00:00Z"
    # Default: 24 hours ago
    dt = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_within_window(date_str, window=timedelta(hours=WINDOW_HOURS)):
    """Check if a date string is within the given time window from now.

    Args:
        date_str: ISO 8601 datetime string or YYYY-MM-DD.
        window: timedelta representing the window (default 24h).

    Returns:
        bool: True if the date is within the window.
    """
    if not date_str:
        return False

    try:
        # Handle Z suffix and +00:00
        normalized = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt
        return age <= window
    except (ValueError, TypeError):
        return False


# =============================================================
# 4. SQLite initialization
# =============================================================

def init_db(db_path):
    """Initialize the SQLite database with tables and WAL mode.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        sqlite3.Connection: open connection in WAL mode.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

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

    conn.commit()
    return conn


# =============================================================
# 5. UPSERT operations
# =============================================================

def upsert_commits(conn, commits):
    """Insert or replace commit records.

    Args:
        conn: sqlite3.Connection.
        commits: list of commit dicts with keys matching the table columns.

    Returns:
        int: number of rows affected.
    """
    if not commits:
        return 0

    sql = """
        INSERT INTO commits
            (sha, repo_platform, repo_owner, repo_name, author, date, message, url, collected_at)
        VALUES
            (:sha, :repo_platform, :repo_owner, :repo_name, :author, :date, :message, :url, datetime('now'))
        ON CONFLICT(repo_platform, repo_owner, repo_name, sha) DO UPDATE SET
            author = excluded.author,
            date = excluded.date,
            message = excluded.message,
            url = excluded.url,
            collected_at = datetime('now')
    """
    cursor = conn.cursor()
    cursor.executemany(sql, commits)
    conn.commit()
    return cursor.rowcount


def upsert_issues(conn, issues):
    """Insert or replace issue records.

    Args:
        conn: sqlite3.Connection.
        issues: list of issue dicts.

    Returns:
        int: number of rows affected.
    """
    if not issues:
        return 0

    sql = """
        INSERT INTO issues
            (issue_number, repo_platform, repo_owner, repo_name,
             title, state, author, created_at, closed_at,
             is_new_24h, is_closed_24h, collected_at)
        VALUES
            (:issue_number, :repo_platform, :repo_owner, :repo_name,
             :title, :state, :author, :created_at, :closed_at,
             :is_new_24h, :is_closed_24h, datetime('now'))
        ON CONFLICT(repo_platform, repo_owner, repo_name, issue_number) DO UPDATE SET
            title = excluded.title,
            state = excluded.state,
            author = excluded.author,
            created_at = excluded.created_at,
            closed_at = excluded.closed_at,
            is_new_24h = excluded.is_new_24h,
            is_closed_24h = excluded.is_closed_24h,
            collected_at = datetime('now')
    """
    cursor = conn.cursor()
    cursor.executemany(sql, issues)
    conn.commit()
    return cursor.rowcount


def upsert_prs(conn, prs):
    """Insert or replace pull request records.

    Args:
        conn: sqlite3.Connection.
        prs: list of PR dicts.

    Returns:
        int: number of rows affected.
    """
    if not prs:
        return 0

    sql = """
        INSERT INTO pull_requests
            (pr_number, repo_platform, repo_owner, repo_name,
             title, state, author, created_at, merged_at,
             is_new_24h, is_merged_24h, collected_at)
        VALUES
            (:pr_number, :repo_platform, :repo_owner, :repo_name,
             :title, :state, :author, :created_at, :merged_at,
             :is_new_24h, :is_merged_24h, datetime('now'))
        ON CONFLICT(repo_platform, repo_owner, repo_name, pr_number) DO UPDATE SET
            title = excluded.title,
            state = excluded.state,
            author = excluded.author,
            created_at = excluded.created_at,
            merged_at = excluded.merged_at,
            is_new_24h = excluded.is_new_24h,
            is_merged_24h = excluded.is_merged_24h,
            collected_at = datetime('now')
    """
    cursor = conn.cursor()
    cursor.executemany(sql, prs)
    conn.commit()
    return cursor.rowcount


# =============================================================
# 6. HTTP helper
# =============================================================

def api_get(url, token=None, max_retries=3):
    """Make an API GET request with retry and exponential backoff.

    Args:
        url: Full API URL.
        token: Optional Bearer token for Authorization header.
        max_retries: Number of retries on 403/429 (default 3).

    Returns:
        Parsed JSON (dict or list), or None if all retries exhausted.
    """
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "myopenclaw-repo-scanner/1.0")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    time.sleep(wait)
                    continue
            # Non-retryable error or exhausted retries
            print(f"  ⚠️  HTTP {e.code} for {url}", file=sys.stderr)
            return None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            print(f"  ⚠️  Request failed for {url}: {e}", file=sys.stderr)
            return None

    return None


# =============================================================
# 7. 24h flag helpers
# =============================================================

def determine_issue_flags(created_at, closed_at, state):
    """Determine is_new_24h and is_closed_24h flags for an issue.

    Args:
        created_at: ISO datetime string.
        closed_at: ISO datetime string or None.
        state: 'open' or 'closed'.

    Returns:
        dict: {"is_new_24h": int, "is_closed_24h": int}
    """
    is_new = 1 if is_within_window(created_at) else 0
    is_closed = 1 if (state == "closed" and is_within_window(closed_at)) else 0
    return {"is_new_24h": is_new, "is_closed_24h": is_closed}


def determine_pr_flags(created_at, merged_at, state):
    """Determine is_new_24h and is_merged_24h flags for a PR.

    Args:
        created_at: ISO datetime string.
        merged_at: ISO datetime string or None.
        state: 'open', 'closed', or 'merged'.

    Returns:
        dict: {"is_new_24h": int, "is_merged_24h": int}
    """
    is_new = 1 if is_within_window(created_at) else 0
    is_merged = 1 if (state == "merged" and is_within_window(merged_at)) else 0
    return {"is_new_24h": is_new, "is_merged_24h": is_merged}


# =============================================================
# 8. GitHub response parsers
# =============================================================

def parse_github_commit(raw, platform, owner, repo):
    """Parse a GitHub commit API response into a normalized dict.

    Args:
        raw: dict from GitHub commits API.
        platform: 'github'.
        owner: repo owner.
        repo: repo name.

    Returns:
        dict with keys: sha, repo_platform, repo_owner, repo_name,
                        author, date, message, url.
    """
    commit_data = raw.get("commit", {})
    author_data = commit_data.get("author", {}) or {}
    return {
        "sha": raw.get("sha", ""),
        "repo_platform": platform,
        "repo_owner": owner,
        "repo_name": repo,
        "author": author_data.get("name", "unknown"),
        "date": author_data.get("date", ""),
        "message": (commit_data.get("message", "") or "").split("\n")[0],
        "url": raw.get("html_url", ""),
    }


def parse_github_issue(raw, platform, owner, repo):
    """Parse a GitHub issue API response into a normalized dict.

    Returns None for items that are pull requests (not issues).

    Args:
        raw: dict from GitHub issues API.
        platform: 'github'.
        owner: repo owner.
        repo: repo name.

    Returns:
        dict or None: None if the item is a PR.
    """
    # Skip pull requests (GitHub issues API returns both)
    if raw.get("pull_request"):
        return None

    user_data = raw.get("user") or {}
    created_at = raw.get("created_at", "")
    closed_at = raw.get("closed_at")
    state = raw.get("state", "open")

    flags = determine_issue_flags(created_at, closed_at, state)

    return {
        "issue_number": raw.get("number"),
        "repo_platform": platform,
        "repo_owner": owner,
        "repo_name": repo,
        "title": raw.get("title", ""),
        "state": state,
        "author": user_data.get("login", "ghost"),
        "created_at": created_at,
        "closed_at": closed_at,
        **flags,
    }


def parse_github_pr(raw, platform, owner, repo):
    """Parse a GitHub pull request API response into a normalized dict.

    Args:
        raw: dict from GitHub pulls API.
        platform: 'github'.
        owner: repo owner.
        repo: repo name.

    Returns:
        dict with keys: pr_number, ..., is_new_24h, is_merged_24h.
    """
    user_data = raw.get("user") or {}
    created_at = raw.get("created_at", "")
    merged_at = raw.get("merged_at")

    # Determine effective state: 'merged' > 'closed' > 'open'
    state = raw.get("state", "open")
    if merged_at:
        state = "merged"

    flags = determine_pr_flags(created_at, merged_at, state)

    return {
        "pr_number": raw.get("number"),
        "repo_platform": platform,
        "repo_owner": owner,
        "repo_name": repo,
        "title": raw.get("title", ""),
        "state": state,
        "author": user_data.get("login", "ghost"),
        "created_at": created_at,
        "merged_at": merged_at,
        **flags,
    }


# =============================================================
# 9. GitCode response parsers
# =============================================================

def parse_gitcode_commit(raw, platform, owner, repo):
    """Parse a GitCode (Gitea-style) commit API response.

    Args:
        raw: dict from GitCode commits API.
        platform: 'gitcode'.
        owner: repo owner.
        repo: repo name.

    Returns:
        dict with same keys as parse_github_commit.
    """
    commit_data = raw.get("commit", {})
    author_data = commit_data.get("author", {}) or {}

    # GitCode may also put author at top level
    top_author = raw.get("author") or {}
    author_name = (
        author_data.get("name")
        or top_author.get("name")
        or top_author.get("login")
        or "unknown"
    )

    return {
        "sha": raw.get("sha", ""),
        "repo_platform": platform,
        "repo_owner": owner,
        "repo_name": repo,
        "author": author_name,
        "date": author_data.get("date", raw.get("created_at", "")),
        "message": (commit_data.get("message", "") or "").split("\n")[0],
        "url": raw.get("html_url", ""),
    }


def parse_gitcode_issue(raw, platform, owner, repo):
    """Parse a GitCode (Gitea-style) issue API response.

    Args:
        raw: dict from GitCode issues API.
        platform: 'gitcode'.
        owner: repo owner.
        repo: repo name.

    Returns:
        dict or None.
    """
    # Skip pull requests if the API returns them as issues
    if raw.get("pull_request"):
        return None

    user_data = raw.get("user") or {}
    created_at = raw.get("created_at", "")
    closed_at = raw.get("closed_at")
    state = raw.get("state", "open")

    flags = determine_issue_flags(created_at, closed_at, state)

    return {
        "issue_number": raw.get("number"),
        "repo_platform": platform,
        "repo_owner": owner,
        "repo_name": repo,
        "title": raw.get("title", ""),
        "state": state,
        "author": user_data.get("login", user_data.get("name", "ghost")),
        "created_at": created_at,
        "closed_at": closed_at,
        **flags,
    }


def parse_gitcode_pr(raw, platform, owner, repo):
    """Parse a GitCode (Gitea-style) pull request API response.

    Args:
        raw: dict from GitCode pulls API.
        platform: 'gitcode'.
        owner: repo owner.
        repo: repo name.

    Returns:
        dict with PR fields.
    """
    user_data = raw.get("user") or {}
    created_at = raw.get("created_at", "")
    merged_at = raw.get("merged_at")

    state = raw.get("state", "open")
    if merged_at:
        state = "merged"
    elif raw.get("merged"):
        state = "merged"

    flags = determine_pr_flags(created_at, merged_at, state)

    return {
        "pr_number": raw.get("number"),
        "repo_platform": platform,
        "repo_owner": owner,
        "repo_name": repo,
        "title": raw.get("title", ""),
        "state": state,
        "author": user_data.get("login", user_data.get("name", "ghost")),
        "created_at": created_at,
        "merged_at": merged_at,
        **flags,
    }


# =============================================================
# 10. API fetchers
# =============================================================

def fetch_github_commits(owner, repo, since, token=None):
    """Fetch commits from GitHub API.

    Args:
        owner: repo owner.
        repo: repo name.
        since: ISO 8601 since date.
        token: GitHub API token.

    Returns:
        list of parsed commit dicts.
    """
    url = (
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
        f"?since={since}&per_page={PER_PAGE}"
    )
    data = api_get(url, token=token)
    if not data or not isinstance(data, list):
        return []
    return [
        parse_github_commit(raw, "github", owner, repo)
        for raw in data
    ]


def fetch_github_issues(owner, repo, since, token=None):
    """Fetch issues from GitHub API (excludes PRs).

    Args:
        owner: repo owner.
        repo: repo name.
        since: ISO 8601 since date.
        token: GitHub API token.

    Returns:
        list of parsed issue dicts.
    """
    url = (
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues"
        f"?since={since}&state=all&per_page={PER_PAGE}"
    )
    data = api_get(url, token=token)
    if not data or not isinstance(data, list):
        return []
    results = []
    for raw in data:
        parsed = parse_github_issue(raw, "github", owner, repo)
        if parsed is not None:
            results.append(parsed)
    return results


def fetch_github_pulls(owner, repo, since, token=None):
    """Fetch pull requests from GitHub API.

    Args:
        owner: repo owner.
        repo: repo name.
        since: ISO 8601 since date (used for filtering after fetch).
        token: GitHub API token.

    Returns:
        list of parsed PR dicts.
    """
    url = (
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls"
        f"?state=all&per_page={PER_PAGE}&sort=updated&direction=desc"
    )
    data = api_get(url, token=token)
    if not data or not isinstance(data, list):
        return []
    return [
        parse_github_pr(raw, "github", owner, repo)
        for raw in data
    ]


def fetch_gitcode_commits(owner, repo, since, token=None):
    """Fetch commits from GitCode API.

    Args:
        owner: repo owner.
        repo: repo name.
        since: ISO 8601 since date.
        token: GitCode API token.

    Returns:
        list of parsed commit dicts.
    """
    url = (
        f"{GITCODE_API_BASE}/repos/{owner}/{repo}/commits"
        f"?since={since}&limit={PER_PAGE}"
    )
    data = api_get(url, token=token)
    if not data or not isinstance(data, list):
        return []
    return [
        parse_gitcode_commit(raw, "gitcode", owner, repo)
        for raw in data
    ]


def fetch_gitcode_issues(owner, repo, since, token=None):
    """Fetch issues from GitCode API.

    Args:
        owner: repo owner.
        repo: repo name.
        since: ISO 8601 since date.
        token: GitCode API token.

    Returns:
        list of parsed issue dicts.
    """
    url = (
        f"{GITCODE_API_BASE}/repos/{owner}/{repo}/issues"
        f"?since={since}&state=all&limit={PER_PAGE}"
    )
    data = api_get(url, token=token)
    if not data or not isinstance(data, list):
        return []
    results = []
    for raw in data:
        parsed = parse_gitcode_issue(raw, "gitcode", owner, repo)
        if parsed is not None:
            results.append(parsed)
    return results


def fetch_gitcode_pulls(owner, repo, since, token=None):
    """Fetch pull requests from GitCode API.

    Args:
        owner: repo owner.
        repo: repo name.
        since: ISO 8601 since date (used for filtering after fetch).
        token: GitCode API token.

    Returns:
        list of parsed PR dicts.
    """
    url = (
        f"{GITCODE_API_BASE}/repos/{owner}/{repo}/pulls"
        f"?state=all&limit={PER_PAGE}&sort=updated&direction=desc"
    )
    data = api_get(url, token=token)
    if not data or not isinstance(data, list):
        return []
    return [
        parse_gitcode_pr(raw, "gitcode", owner, repo)
        for raw in data
    ]


# =============================================================
# 11. Repo scanner
# =============================================================

def scan_repo(platform, owner, repo, since, token=None, db_path=None, dry_run=False):
    """Scan a single repo for commits, issues, and PRs.

    Args:
        platform: 'github' or 'gitcode'.
        owner: repo owner.
        repo: repo name.
        since: ISO 8601 since date.
        token: API token for the platform.
        db_path: Path to SQLite database (ignored in dry_run).
        dry_run: If True, don't write to DB.

    Returns:
        dict: {"commits": N, "issues": N, "pull_requests": N}
    """
    stats = {"commits": 0, "issues": 0, "pull_requests": 0}

    # Fetch commits
    if platform == "github":
        commits = fetch_github_commits(owner, repo, since, token=token)
        issues = fetch_github_issues(owner, repo, since, token=token)
        prs = fetch_github_pulls(owner, repo, since, token=token)
    else:
        commits = fetch_gitcode_commits(owner, repo, since, token=token)
        issues = fetch_gitcode_issues(owner, repo, since, token=token)
        prs = fetch_gitcode_pulls(owner, repo, since, token=token)

    stats["commits"] = len(commits)
    stats["issues"] = len(issues)
    stats["pull_requests"] = len(prs)

    if not dry_run and db_path:
        conn = init_db(db_path)
        try:
            upsert_commits(conn, commits)
            upsert_issues(conn, issues)
            upsert_prs(conn, prs)
        finally:
            conn.close()

    return stats


# =============================================================
# 12. Main
# =============================================================

def main():
    dry_run = "--dry-run" in sys.argv

    # Parse --since argument
    since_arg = None
    for i, arg in enumerate(sys.argv):
        if arg == "--since" and i + 1 < len(sys.argv):
            since_arg = sys.argv[i + 1]
            break

    since = get_since_date(since_arg)

    # Load config
    print("📋 加载仓库配置 ...")
    try:
        repos_config = parse_repos_config()
    except FileNotFoundError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    github_repos = repos_config["github"]
    gitcode_repos = repos_config["gitcode"]
    total_repos = len(github_repos) + len(gitcode_repos)

    if total_repos == 0:
        print("⚠️  未配置任何仓库 — 请在 configs/repos.toml 中添加")
        return

    print(f"   GitHub: {len(github_repos)} 个仓库")
    print(f"   GitCode: {len(gitcode_repos)} 个仓库")
    print(f"   时间窗口: {since} → now")

    # Load tokens
    gh_token = load_github_token()
    gc_token = load_gitcode_token()

    if gh_token:
        print("   🔑 GitHub token: 已加载")
    else:
        print("   ⚠️  GitHub token: 未配置（匿名访问，限速 60 req/h）")

    if gc_token:
        print("   🔑 GitCode token: 已加载")
    else:
        print("   ℹ️  GitCode token: 未配置（匿名访问）")

    print()

    # Build task list
    tasks = []
    for r in github_repos:
        tasks.append(("github", r["owner"], r["repo"], gh_token))
    for r in gitcode_repos:
        tasks.append(("gitcode", r["owner"], r["repo"], gc_token))

    # Scan concurrently
    all_stats = {}
    total = {"commits": 0, "issues": 0, "pull_requests": 0}

    print(f"🔍 扫描 {len(tasks)} 个仓库 ({MAX_WORKERS} 并发) ...")
    print()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for platform, owner, repo, token in tasks:
            key = f"{platform}/{owner}/{repo}"
            future = executor.submit(
                scan_repo, platform, owner, repo, since,
                token=token, db_path=DB_PATH, dry_run=dry_run
            )
            futures[future] = key

        for future in as_completed(futures):
            key = futures[future]
            try:
                stats = future.result()
                all_stats[key] = stats
                total["commits"] += stats["commits"]
                total["issues"] += stats["issues"]
                total["pull_requests"] += stats["pull_requests"]

                # Print per-repo result
                parts = []
                if stats["commits"]:
                    parts.append(f"{stats['commits']} commits")
                if stats["issues"]:
                    parts.append(f"{stats['issues']} issues")
                if stats["pull_requests"]:
                    parts.append(f"{stats['pull_requests']} PRs")
                detail = ", ".join(parts) if parts else "无更新"
                print(f"   ✅ {key}: {detail}")
            except Exception as e:
                print(f"   ❌ {key}: {e}", file=sys.stderr)
                all_stats[key] = {"commits": 0, "issues": 0, "pull_requests": 0}

    # Summary
    print()
    print("=" * 60)
    print("📊 汇总")
    print("=" * 60)
    print(f"   扫描仓库: {len(tasks)}")
    print(f"   Commits:  {total['commits']}")
    print(f"   Issues:   {total['issues']}")
    print(f"   PRs:      {total['pull_requests']}")

    if dry_run:
        print("\n📄 DRY RUN — 未写入数据库")
    else:
        print(f"\n📝 数据已写入: {DB_PATH}")


if __name__ == "__main__":
    main()
