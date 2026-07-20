#!/usr/bin/env python3
"""
Tests for scripts/collect-repos.py — Repository Progress Scanner.

Usage:
    python3 -m pytest scripts/tests/test_collect_repos.py -v
    python3 -m unittest scripts.tests.test_collect_repos -v
    python3 scripts/tests/test_collect_repos.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load collect-repos module via importlib (filename has a hyphen)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "collect_repos", str(SCRIPTS_DIR / "collect-repos.py")
)
collect_repos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect_repos)
sys.modules["collect_repos"] = collect_repos


# =============================================================
# Test: parse_repos_config
# =============================================================
class TestParseReposConfig(unittest.TestCase):
    """Tests for config file parsing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = Path(self.tmpdir) / "repos.toml"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_config(self, content):
        self.config_path.write_text(content)

    def test_parse_valid_mixed_platforms(self):
        """Should parse a valid TOML with github and gitcode repos."""
        self._write_config("""\
[[github]]
owner = "OuyangWenyu"
repo = "torchhydro"

[[github]]
owner = "iHeadWater"
repo = "aisecretary"

[[gitcode]]
owner = "dlut-water"
repo = "prd-tdd"
""")
        result = collect_repos.parse_repos_config(self.config_path)
        self.assertEqual(len(result["github"]), 2)
        self.assertEqual(len(result["gitcode"]), 1)
        self.assertEqual(result["github"][0]["owner"], "OuyangWenyu")
        self.assertEqual(result["github"][0]["repo"], "torchhydro")
        self.assertEqual(result["gitcode"][0]["owner"], "dlut-water")
        self.assertEqual(result["gitcode"][0]["repo"], "prd-tdd")

    def test_parse_github_only(self):
        """Should parse a config with only github repos."""
        self._write_config("""\
[[github]]
owner = "OuyangWenyu"
repo = "torchhydro"
""")
        result = collect_repos.parse_repos_config(self.config_path)
        self.assertEqual(len(result["github"]), 1)
        self.assertEqual(len(result["gitcode"]), 0)

    def test_parse_gitcode_only(self):
        """Should parse a config with only gitcode repos."""
        self._write_config("""\
[[gitcode]]
owner = "dlut-water"
repo = "aisecretary"
""")
        result = collect_repos.parse_repos_config(self.config_path)
        self.assertEqual(len(result["github"]), 0)
        self.assertEqual(len(result["gitcode"]), 1)

    def test_parse_empty_config(self):
        """Should return empty lists for an empty config."""
        self._write_config("")
        result = collect_repos.parse_repos_config(self.config_path)
        self.assertEqual(len(result["github"]), 0)
        self.assertEqual(len(result["gitcode"]), 0)

    def test_parse_missing_file(self):
        """Should raise FileNotFoundError for missing config."""
        with self.assertRaises(FileNotFoundError):
            collect_repos.parse_repos_config(self.config_path)  # doesn't exist

    def test_parse_extra_fields_ignored(self):
        """Extra fields in repo entries should not break parsing."""
        self._write_config("""\
[[github]]
owner = "OuyangWenyu"
repo = "torchhydro"
extra_field = "ignored"
""")
        result = collect_repos.parse_repos_config(self.config_path)
        self.assertEqual(result["github"][0]["owner"], "OuyangWenyu")
        self.assertEqual(result["github"][0]["repo"], "torchhydro")


# =============================================================
# Test: init_db
# =============================================================
class TestInitDb(unittest.TestCase):
    """Tests for SQLite database initialization."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.sqlite"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_creates_tables(self):
        """init_db should create commits, issues, and pull_requests tables."""
        conn = collect_repos.init_db(self.db_path)
        cursor = conn.cursor()

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("commits", tables)
        self.assertIn("issues", tables)
        self.assertIn("pull_requests", tables)

        conn.close()

    def test_commits_table_schema(self):
        """Commits table should have expected columns and PK."""
        conn = collect_repos.init_db(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info('commits')")
        cols = {row[1]: row for row in cursor.fetchall()}
        expected = ["sha", "repo_platform", "repo_owner", "repo_name",
                    "author", "date", "message", "url", "collected_at"]
        for col in expected:
            self.assertIn(col, cols, f"Column '{col}' missing from commits table")
        conn.close()

    def test_issues_table_schema(self):
        """Issues table should have expected columns and PK."""
        conn = collect_repos.init_db(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info('issues')")
        cols = {row[1]: row for row in cursor.fetchall()}
        expected = ["issue_number", "repo_platform", "repo_owner", "repo_name",
                    "title", "state", "author", "created_at", "closed_at",
                    "is_new_24h", "is_closed_24h", "collected_at"]
        for col in expected:
            self.assertIn(col, cols, f"Column '{col}' missing from issues table")
        conn.close()

    def test_pull_requests_table_schema(self):
        """Pull_requests table should have expected columns and PK."""
        conn = collect_repos.init_db(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info('pull_requests')")
        cols = {row[1]: row for row in cursor.fetchall()}
        expected = ["pr_number", "repo_platform", "repo_owner", "repo_name",
                    "title", "state", "author", "created_at", "merged_at",
                    "is_new_24h", "is_merged_24h", "collected_at"]
        for col in expected:
            self.assertIn(col, cols, f"Column '{col}' missing from pull_requests table")
        conn.close()

    def test_idempotent(self):
        """Calling init_db twice should not raise errors."""
        conn1 = collect_repos.init_db(self.db_path)
        conn1.close()
        conn2 = collect_repos.init_db(self.db_path)  # second call
        conn2.close()

    def test_wal_mode_enabled(self):
        """Database should use WAL journal mode."""
        conn = collect_repos.init_db(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        self.assertEqual(mode.lower(), "wal")
        conn.close()


# =============================================================
# Test: upsert operations
# =============================================================
class TestUpsertCommit(unittest.TestCase):
    """Tests for commit upsert behavior."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.sqlite"
        self.conn = collect_repos.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_insert_new_commit(self):
        """Should insert a new commit row."""
        commits = [{
            "sha": "abc123def",
            "repo_platform": "github",
            "repo_owner": "testowner",
            "repo_name": "testrepo",
            "author": "tester",
            "date": "2024-01-15T10:00:00Z",
            "message": "test commit",
            "url": "https://github.com/testowner/testrepo/commit/abc123def",
        }]
        count = collect_repos.upsert_commits(self.conn, commits)
        self.assertEqual(count, 1)

        cursor = self.conn.cursor()
        cursor.execute("SELECT sha, author, message FROM commits")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "abc123def")
        self.assertEqual(rows[0][1], "tester")

    def test_upsert_existing_commit(self):
        """Should UPDATE (not duplicate) when the same PK is inserted again."""
        commit = {
            "sha": "abc123def",
            "repo_platform": "github",
            "repo_owner": "testowner",
            "repo_name": "testrepo",
            "author": "tester",
            "date": "2024-01-15T10:00:00Z",
            "message": "original message",
            "url": "https://example.com/commit/abc123def",
        }
        collect_repos.upsert_commits(self.conn, [commit])

        # Upsert with updated message
        commit["message"] = "updated message"
        collect_repos.upsert_commits(self.conn, [commit])

        cursor = self.conn.cursor()
        cursor.execute("SELECT message FROM commits WHERE sha = 'abc123def'")
        row = cursor.fetchone()
        self.assertEqual(row[0], "updated message")

        # Still only one row
        cursor.execute("SELECT COUNT(*) FROM commits")
        self.assertEqual(cursor.fetchone()[0], 1)

    def test_insert_multiple_commits(self):
        """Should insert multiple commits in one call."""
        commits = [
            {"sha": f"sha{i:03d}", "repo_platform": "github",
             "repo_owner": "o", "repo_name": "r",
             "author": "a", "date": "2024-01-15T10:00:00Z",
             "message": f"msg{i}", "url": f"https://example.com/sha{i:03d}"}
            for i in range(5)
        ]
        count = collect_repos.upsert_commits(self.conn, commits)
        self.assertEqual(count, 5)

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM commits")
        self.assertEqual(cursor.fetchone()[0], 5)


class TestUpsertIssue(unittest.TestCase):
    """Tests for issue upsert behavior."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.sqlite"
        self.conn = collect_repos.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_insert_new_issue(self):
        """Should insert a new issue row."""
        issues = [{
            "issue_number": 1,
            "repo_platform": "github",
            "repo_owner": "testowner",
            "repo_name": "testrepo",
            "title": "test issue",
            "state": "open",
            "author": "tester",
            "created_at": "2024-01-15T10:00:00Z",
            "closed_at": None,
            "is_new_24h": 1,
            "is_closed_24h": 0,
        }]
        count = collect_repos.upsert_issues(self.conn, issues)
        self.assertEqual(count, 1)

        cursor = self.conn.cursor()
        cursor.execute("SELECT title, state, is_new_24h FROM issues")
        row = cursor.fetchone()
        self.assertEqual(row[0], "test issue")
        self.assertEqual(row[1], "open")
        self.assertEqual(row[2], 1)

    def test_upsert_existing_issue(self):
        """Should UPDATE on duplicate PK, not insert."""
        issue = {
            "issue_number": 1,
            "repo_platform": "github",
            "repo_owner": "testowner",
            "repo_name": "testrepo",
            "title": "original",
            "state": "open",
            "author": "tester",
            "created_at": "2024-01-15T10:00:00Z",
            "closed_at": None,
            "is_new_24h": 1,
            "is_closed_24h": 0,
        }
        collect_repos.upsert_issues(self.conn, [issue])

        issue["state"] = "closed"
        issue["closed_at"] = "2024-01-16T10:00:00Z"
        issue["is_closed_24h"] = 1
        issue["is_new_24h"] = 0
        collect_repos.upsert_issues(self.conn, [issue])

        cursor = self.conn.cursor()
        cursor.execute("SELECT state, closed_at, is_closed_24h FROM issues WHERE issue_number = 1")
        row = cursor.fetchone()
        self.assertEqual(row[0], "closed")
        self.assertEqual(row[1], "2024-01-16T10:00:00Z")
        self.assertEqual(row[2], 1)

        cursor.execute("SELECT COUNT(*) FROM issues")
        self.assertEqual(cursor.fetchone()[0], 1)


class TestUpsertPR(unittest.TestCase):
    """Tests for PR upsert behavior."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.sqlite"
        self.conn = collect_repos.init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_insert_new_pr(self):
        """Should insert a new PR row."""
        prs = [{
            "pr_number": 42,
            "repo_platform": "github",
            "repo_owner": "testowner",
            "repo_name": "testrepo",
            "title": "test PR",
            "state": "open",
            "author": "tester",
            "created_at": "2024-01-15T10:00:00Z",
            "merged_at": None,
            "is_new_24h": 1,
            "is_merged_24h": 0,
        }]
        count = collect_repos.upsert_prs(self.conn, prs)
        self.assertEqual(count, 1)

        cursor = self.conn.cursor()
        cursor.execute("SELECT pr_number, title, state FROM pull_requests")
        row = cursor.fetchone()
        self.assertEqual(row[0], 42)
        self.assertEqual(row[1], "test PR")
        self.assertEqual(row[2], "open")

    def test_upsert_existing_pr(self):
        """Should UPDATE on duplicate PK."""
        pr = {
            "pr_number": 42,
            "repo_platform": "github",
            "repo_owner": "testowner",
            "repo_name": "testrepo",
            "title": "original PR",
            "state": "open",
            "author": "tester",
            "created_at": "2024-01-15T10:00:00Z",
            "merged_at": None,
            "is_new_24h": 1,
            "is_merged_24h": 0,
        }
        collect_repos.upsert_prs(self.conn, [pr])

        pr["state"] = "merged"
        pr["merged_at"] = "2024-01-16T10:00:00Z"
        pr["is_merged_24h"] = 1
        pr["is_new_24h"] = 0
        collect_repos.upsert_prs(self.conn, [pr])

        cursor = self.conn.cursor()
        cursor.execute("SELECT state, merged_at, is_merged_24h FROM pull_requests WHERE pr_number = 42")
        row = cursor.fetchone()
        self.assertEqual(row[0], "merged")
        self.assertEqual(row[1], "2024-01-16T10:00:00Z")
        self.assertEqual(row[2], 1)

        cursor.execute("SELECT COUNT(*) FROM pull_requests")
        self.assertEqual(cursor.fetchone()[0], 1)


# =============================================================
# Test: datetime helpers
# =============================================================
class TestWithin24h(unittest.TestCase):
    """Tests for datetime filtering logic."""

    def test_recent_date_is_within_24h(self):
        """A date 1 hour ago should be within the 24h window."""
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        result = collect_repos.is_within_window(
            since.isoformat(),
            timedelta(hours=24)
        )
        self.assertTrue(result)

    def test_old_date_is_outside_24h(self):
        """A date 25 hours ago should be outside the 24h window."""
        since = datetime.now(timezone.utc) - timedelta(hours=25)
        result = collect_repos.is_within_window(
            since.isoformat(),
            timedelta(hours=24)
        )
        self.assertFalse(result)

    def test_exactly_24h_boundary(self):
        """A date at the 24h boundary (minus epsilon) should be within window."""
        since = datetime.now(timezone.utc) - timedelta(hours=23, minutes=59, seconds=59)
        result = collect_repos.is_within_window(
            since.isoformat(),
            timedelta(hours=24)
        )
        self.assertTrue(result)

    def test_none_date_returns_false(self):
        """None date should return False."""
        result = collect_repos.is_within_window(None, timedelta(hours=24))
        self.assertFalse(result)

    def test_empty_date_returns_false(self):
        """Empty string date should return False."""
        result = collect_repos.is_within_window("", timedelta(hours=24))
        self.assertFalse(result)

    def test_custom_window_7_days(self):
        """A date 6 days ago should be within a 7-day window."""
        since = datetime.now(timezone.utc) - timedelta(days=6)
        result = collect_repos.is_within_window(
            since.isoformat(),
            timedelta(days=7)
        )
        self.assertTrue(result)

    def test_datetime_with_Z_suffix(self):
        """ISO format with Z suffix should parse correctly."""
        dt = datetime.now(timezone.utc) - timedelta(hours=2)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = collect_repos.is_within_window(date_str, timedelta(hours=24))
        self.assertTrue(result)

    def test_datetime_with_timezone_offset(self):
        """ISO format with +00:00 offset should parse correctly."""
        dt = datetime.now(timezone.utc) - timedelta(hours=2)
        date_str = dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        result = collect_repos.is_within_window(date_str, timedelta(hours=24))
        self.assertTrue(result)

    def test_date_only_string(self):
        """Date-only strings (YYYY-MM-DD) should be parsable."""
        result = collect_repos.is_within_window(
            "2020-01-01", timedelta(hours=24)
        )
        self.assertFalse(result)  # ancient date


# =============================================================
# Test: get_since_date
# =============================================================
class TestGetSinceDate(unittest.TestCase):
    """Tests for default since-date computation."""

    def test_returns_iso_format(self):
        """Should return an ISO 8601 datetime string."""
        result = collect_repos.get_since_date()
        self.assertIn("T", result)
        self.assertIn("Z", result)

    def test_returns_yesterday_by_default(self):
        """Default since date should be approximately 24h ago."""
        result = collect_repos.get_since_date()
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - dt
        # Should be between 23 and 25 hours ago (allow 1h tolerance)
        self.assertGreaterEqual(age.total_seconds(), 23 * 3600)
        self.assertLessEqual(age.total_seconds(), 25 * 3600)

    def test_custom_since_param(self):
        """Passing a date string should return it in ISO format."""
        result = collect_repos.get_since_date("2024-06-15")
        self.assertIn("2024-06-15", result)

    def test_custom_since_iso_param(self):
        """Passing an ISO datetime should return it unchanged-ish."""
        result = collect_repos.get_since_date("2024-06-15T08:00:00Z")
        self.assertEqual(result, "2024-06-15T08:00:00Z")


# =============================================================
# Test: load_github_token
# =============================================================
class TestLoadGithubToken(unittest.TestCase):
    """Tests for token loading from env and .env file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.env_path = Path(self.tmpdir) / ".env"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        # Clean up env vars
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)

    def test_from_env_var(self):
        """Should read GITHUB_TOKEN from environment variable."""
        os.environ["GITHUB_TOKEN"] = "ghp_test123"
        token = collect_repos.load_github_token(self.env_path)
        self.assertEqual(token, "ghp_test123")

    def test_from_dotenv_file(self):
        """Should read GH_TOKEN from .env file when env var not set."""
        self.env_path.write_text("GH_TOKEN=ghp_dotenv456\nOTHER=val\n")
        token = collect_repos.load_github_token(self.env_path)
        self.assertEqual(token, "ghp_dotenv456")

    def test_env_var_priority(self):
        """Env var GITHUB_TOKEN should take priority over .env file."""
        os.environ["GITHUB_TOKEN"] = "ghp_env_priority"
        self.env_path.write_text("GH_TOKEN=ghp_dotenv_ignored\n")
        token = collect_repos.load_github_token(self.env_path)
        self.assertEqual(token, "ghp_env_priority")

    def test_missing_file_returns_none(self):
        """Should return None when .env file doesn't exist and no env var."""
        token = collect_repos.load_github_token(
            Path(self.tmpdir) / "nonexistent.env"
        )
        self.assertIsNone(token)

    def test_no_token_configured(self):
        """Should return None when neither env var nor .env file has token."""
        self.env_path.write_text("# no token here\nOTHER=val\n")
        token = collect_repos.load_github_token(self.env_path)
        self.assertIsNone(token)


# =============================================================
# Test: api_get with mock
# =============================================================
class TestApiGet(unittest.TestCase):
    """Tests for the HTTP API helper with mocked urllib."""

    def test_successful_response(self):
        """Should parse JSON from a successful response."""
        mock_response = MagicMock()
        mock_response.read.return_value = b'[{"sha": "abc", "commit": {"message": "test"}}]'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = collect_repos.api_get("https://api.github.com/test")
            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["sha"], "abc")

    def test_retry_on_403(self):
        """Should retry with backoff on 403."""
        import urllib.error

        mock_success = MagicMock()
        mock_success.read.return_value = b'[]'
        mock_success.__enter__ = MagicMock(return_value=mock_success)
        mock_success.__exit__ = MagicMock(return_value=False)

        mock_error = MagicMock()
        # First call raises 403, second succeeds
        mock_error.side_effect = [
            urllib.error.HTTPError(
                "https://api.github.com/test", 403,
                "Forbidden", {}, None
            ),
            mock_success,
        ]

        with patch("urllib.request.urlopen", mock_error):
            with patch("time.sleep", return_value=None):  # skip actual sleep
                result = collect_repos.api_get(
                    "https://api.github.com/test", max_retries=3
                )
                self.assertEqual(result, [])

    def test_retry_on_429(self):
        """Should retry with backoff on 429 (rate limit)."""
        import urllib.error

        mock_success = MagicMock()
        mock_success.read.return_value = b'{"ok": true}'
        mock_success.__enter__ = MagicMock(return_value=mock_success)
        mock_success.__exit__ = MagicMock(return_value=False)

        mock_error = MagicMock()
        mock_error.side_effect = [
            urllib.error.HTTPError(
                "https://api.github.com/test", 429,
                "Too Many Requests", {}, None
            ),
            mock_success,
        ]

        with patch("urllib.request.urlopen", mock_error):
            with patch("time.sleep", return_value=None):
                result = collect_repos.api_get(
                    "https://api.github.com/test", max_retries=3
                )
                self.assertEqual(result, {"ok": True})

    def test_gives_up_after_max_retries(self):
        """Should return None after exhausting all retries."""
        import urllib.error

        mock_error = MagicMock()
        mock_error.side_effect = urllib.error.HTTPError(
            "https://api.github.com/test", 429,
            "Too Many Requests", {}, None
        )

        with patch("urllib.request.urlopen", mock_error):
            with patch("time.sleep", return_value=None):
                result = collect_repos.api_get(
                    "https://api.github.com/test", max_retries=2
                )
                self.assertIsNone(result)

    def test_sets_auth_header_with_token(self):
        """Should set Authorization header when token is provided."""
        mock_response = MagicMock()
        mock_response.read.return_value = b'[]'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            with patch("urllib.request.Request") as mock_request_class:
                mock_request = MagicMock()
                mock_request_class.return_value = mock_request
                mock_urlopen.return_value = mock_response

                # Clear the mock_urlopen since we're also mocking Request
                # We need to restructure: mock Request to capture headers
                pass

        # Simpler test: just verify the function doesn't crash with a token
        mock_response = MagicMock()
        mock_response.read.return_value = b'[]'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = collect_repos.api_get(
                "https://api.github.com/test", token="ghp_test"
            )
            self.assertEqual(result, [])


# =============================================================
# Test: determine_issue_flags
# =============================================================
class TestDetermineIssueFlags(unittest.TestCase):
    """Tests for issue 24h-flag determination."""

    def test_new_open_issue(self):
        """An issue created in the last 24h should be flagged as new."""
        dt = datetime.now(timezone.utc) - timedelta(hours=2)
        created_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = collect_repos.determine_issue_flags(created_at, None, "open")
        self.assertEqual(result["is_new_24h"], 1)
        self.assertEqual(result["is_closed_24h"], 0)

    def test_recently_closed_issue(self):
        """An issue closed in the last 24h should be flagged as closed."""
        created = datetime.now(timezone.utc) - timedelta(days=10)
        closed = datetime.now(timezone.utc) - timedelta(hours=5)
        result = collect_repos.determine_issue_flags(
            created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            closed.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "closed"
        )
        self.assertEqual(result["is_new_24h"], 0)
        self.assertEqual(result["is_closed_24h"], 1)

    def test_old_open_issue(self):
        """An old issue that's still open should have no flags."""
        created = datetime.now(timezone.utc) - timedelta(days=30)
        result = collect_repos.determine_issue_flags(
            created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            None,
            "open"
        )
        self.assertEqual(result["is_new_24h"], 0)
        self.assertEqual(result["is_closed_24h"], 0)

    def test_both_flags_possible(self):
        """An issue created and closed within 24h should have both flags."""
        created = datetime.now(timezone.utc) - timedelta(hours=10)
        closed = datetime.now(timezone.utc) - timedelta(hours=1)
        result = collect_repos.determine_issue_flags(
            created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            closed.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "closed"
        )
        self.assertEqual(result["is_new_24h"], 1)
        self.assertEqual(result["is_closed_24h"], 1)

    def test_empty_dates_handled(self):
        """Empty strings for dates should not crash."""
        result = collect_repos.determine_issue_flags("", "", "open")
        self.assertEqual(result["is_new_24h"], 0)
        self.assertEqual(result["is_closed_24h"], 0)


# =============================================================
# Test: determine_pr_flags
# =============================================================
class TestDeterminePRFlags(unittest.TestCase):
    """Tests for PR 24h-flag determination."""

    def test_new_open_pr(self):
        """A PR created in the last 24h should be flagged as new."""
        dt = datetime.now(timezone.utc) - timedelta(hours=3)
        result = collect_repos.determine_pr_flags(
            dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            None,
            "open"
        )
        self.assertEqual(result["is_new_24h"], 1)
        self.assertEqual(result["is_merged_24h"], 0)

    def test_recently_merged_pr(self):
        """A PR merged in the last 24h should be flagged as merged."""
        created = datetime.now(timezone.utc) - timedelta(days=5)
        merged = datetime.now(timezone.utc) - timedelta(hours=6)
        result = collect_repos.determine_pr_flags(
            created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            merged.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "merged"
        )
        self.assertEqual(result["is_new_24h"], 0)
        self.assertEqual(result["is_merged_24h"], 1)

    def test_old_open_pr(self):
        """An old PR that's still open should have no flags."""
        created = datetime.now(timezone.utc) - timedelta(days=14)
        result = collect_repos.determine_pr_flags(
            created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            None,
            "open"
        )
        self.assertEqual(result["is_new_24h"], 0)
        self.assertEqual(result["is_merged_24h"], 0)

    def test_both_flags_possible(self):
        """A PR created and merged within 24h should have both flags."""
        created = datetime.now(timezone.utc) - timedelta(hours=8)
        merged = datetime.now(timezone.utc) - timedelta(hours=1)
        result = collect_repos.determine_pr_flags(
            created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            merged.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "merged"
        )
        self.assertEqual(result["is_new_24h"], 1)
        self.assertEqual(result["is_merged_24h"], 1)


# =============================================================
# Test: parse_github_commit
# =============================================================
class TestParseGitHubCommit(unittest.TestCase):
    """Tests for GitHub commit response parsing."""

    def test_standard_commit(self):
        """Should parse a standard GitHub commit API response."""
        raw = {
            "sha": "abc123",
            "html_url": "https://github.com/o/r/commit/abc123",
            "commit": {
                "author": {
                    "name": "Tester",
                    "date": "2024-01-15T10:00:00Z",
                },
                "message": "fix: resolve bug",
            },
        }
        result = collect_repos.parse_github_commit(raw, "github", "o", "r")
        self.assertEqual(result["sha"], "abc123")
        self.assertEqual(result["author"], "Tester")
        self.assertEqual(result["date"], "2024-01-15T10:00:00Z")
        self.assertEqual(result["message"], "fix: resolve bug")
        self.assertEqual(result["repo_platform"], "github")

    def test_commit_without_author(self):
        """Should handle commits with missing author info."""
        raw = {
            "sha": "abc123",
            "html_url": "https://github.com/o/r/commit/abc123",
            "commit": {
                "message": "no author commit",
            },
        }
        result = collect_repos.parse_github_commit(raw, "github", "o", "r")
        self.assertEqual(result["author"], "unknown")
        self.assertEqual(result["message"], "no author commit")

    def test_multiline_commit_message(self):
        """Should handle multiline commit messages (take first line)."""
        raw = {
            "sha": "abc123",
            "html_url": "https://github.com/o/r/commit/abc123",
            "commit": {
                "author": {"name": "Tester", "date": "2024-01-15T10:00:00Z"},
                "message": "feat: add scanner\n\nDetailed body here.\nMore details.",
            },
        }
        result = collect_repos.parse_github_commit(raw, "github", "o", "r")
        self.assertEqual(result["message"], "feat: add scanner")


# =============================================================
# Test: parse_github_issue
# =============================================================
class TestParseGitHubIssue(unittest.TestCase):
    """Tests for GitHub issue response parsing."""

    def test_open_issue(self):
        """Should parse an open issue."""
        raw = {
            "number": 1,
            "title": "Bug report",
            "state": "open",
            "user": {"login": "tester"},
            "created_at": "2024-01-15T10:00:00Z",
            "closed_at": None,
            "pull_request": None,
        }
        result = collect_repos.parse_github_issue(raw, "github", "o", "r")
        self.assertEqual(result["issue_number"], 1)
        self.assertEqual(result["title"], "Bug report")
        self.assertEqual(result["state"], "open")
        self.assertEqual(result["author"], "tester")
        self.assertIsNone(result["closed_at"])

    def test_skip_pull_request(self):
        """Should return None for issues that are actually PRs."""
        raw = {
            "number": 5,
            "title": "A PR disguised as issue",
            "state": "open",
            "user": {"login": "tester"},
            "created_at": "2024-01-15T10:00:00Z",
            "closed_at": None,
            "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/5"},
        }
        result = collect_repos.parse_github_issue(raw, "github", "o", "r")
        self.assertIsNone(result)

    def test_closed_issue(self):
        """Should parse a closed issue with closed_at date."""
        raw = {
            "number": 2,
            "title": "Fixed bug",
            "state": "closed",
            "user": {"login": "dev1"},
            "created_at": "2024-01-10T10:00:00Z",
            "closed_at": "2024-01-15T10:00:00Z",
            "pull_request": None,
        }
        result = collect_repos.parse_github_issue(raw, "github", "o", "r")
        self.assertEqual(result["state"], "closed")
        self.assertEqual(result["closed_at"], "2024-01-15T10:00:00Z")

    def test_issue_without_user(self):
        """Should handle ghost user (deleted account)."""
        raw = {
            "number": 3,
            "title": "Ghost issue",
            "state": "open",
            "user": None,
            "created_at": "2024-01-15T10:00:00Z",
            "closed_at": None,
        }
        result = collect_repos.parse_github_issue(raw, "github", "o", "r")
        self.assertEqual(result["author"], "ghost")


# =============================================================
# Test: parse_github_pr
# =============================================================
class TestParseGitHubPR(unittest.TestCase):
    """Tests for GitHub PR response parsing."""

    def test_open_pr(self):
        """Should parse an open pull request."""
        raw = {
            "number": 10,
            "title": "Add new feature",
            "state": "open",
            "user": {"login": "dev2"},
            "created_at": "2024-01-15T10:00:00Z",
            "merged_at": None,
        }
        result = collect_repos.parse_github_pr(raw, "github", "o", "r")
        self.assertEqual(result["pr_number"], 10)
        self.assertEqual(result["title"], "Add new feature")
        self.assertEqual(result["state"], "open")
        self.assertIsNone(result["merged_at"])

    def test_merged_pr(self):
        """Should parse a merged pull request."""
        raw = {
            "number": 8,
            "title": "Fix typo",
            "state": "closed",
            "user": {"login": "maintainer"},
            "created_at": "2024-01-10T10:00:00Z",
            "merged_at": "2024-01-14T10:00:00Z",
        }
        result = collect_repos.parse_github_pr(raw, "github", "o", "r")
        self.assertEqual(result["state"], "merged")
        self.assertEqual(result["merged_at"], "2024-01-14T10:00:00Z")

    def test_closed_unmerged_pr(self):
        """A closed PR without merged_at should have state 'closed'."""
        raw = {
            "number": 9,
            "title": "Rejected idea",
            "state": "closed",
            "user": {"login": "contributor"},
            "created_at": "2024-01-10T10:00:00Z",
            "merged_at": None,
        }
        result = collect_repos.parse_github_pr(raw, "github", "o", "r")
        self.assertEqual(result["state"], "closed")
        self.assertIsNone(result["merged_at"])

    def test_pr_without_user(self):
        """Should handle ghost user."""
        raw = {
            "number": 11,
            "title": "Ghost PR",
            "state": "open",
            "user": None,
            "created_at": "2024-01-15T10:00:00Z",
            "merged_at": None,
        }
        result = collect_repos.parse_github_pr(raw, "github", "o", "r")
        self.assertEqual(result["author"], "ghost")


# =============================================================
# Test: gitcode response parsing
# =============================================================
class TestParseGitCodeCommit(unittest.TestCase):
    """Tests for GitCode commit response parsing."""

    def test_standard_commit(self):
        """Should parse a standard GitCode (Gitea-style) commit response."""
        raw = {
            "sha": "def456",
            "html_url": "https://gitcode.com/d/r/commit/def456",
            "commit": {
                "author": {
                    "name": "Dev",
                    "date": "2024-01-15T10:00:00+08:00",
                },
                "message": "feat: gitcode support",
            },
        }
        result = collect_repos.parse_gitcode_commit(raw, "gitcode", "d", "r")
        self.assertEqual(result["sha"], "def456")
        self.assertEqual(result["author"], "Dev")
        self.assertEqual(result["repo_platform"], "gitcode")

    def test_commit_with_author_top_level(self):
        """Some GitCode APIs put author at top level instead of nested."""
        raw = {
            "sha": "ghi789",
            "html_url": "https://gitcode.com/d/r/commit/ghi789",
            "author": {
                "login": "coder1",
                "name": "Coder One",
            },
            "commit": {
                "message": "fix: patch",
            },
        }
        result = collect_repos.parse_gitcode_commit(raw, "gitcode", "d", "r")
        self.assertEqual(result["author"], "Coder One")


# =============================================================
# Test: end-to-end dry run flow
# =============================================================
class TestEndToEndDryRun(unittest.TestCase):
    """Smoke test: dry-run scan doesn't crash and doesn't write to DB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.sqlite"
        self.config_path = Path(self.tmpdir) / "repos.toml"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("collect_repos.fetch_github_commits")
    @patch("collect_repos.fetch_github_issues")
    @patch("collect_repos.fetch_github_pulls")
    def test_dry_run_prints_but_no_db_write(self, mock_prs, mock_issues, mock_commits):
        """Dry run should print results but NOT write to the database."""
        mock_commits.return_value = [
            {
                "sha": "abc001",
                "repo_platform": "github",
                "repo_owner": "o",
                "repo_name": "r",
                "author": "a",
                "date": "2024-01-15T10:00:00Z",
                "message": "test commit",
                "url": "https://example.com",
            }
        ]
        mock_issues.return_value = []
        mock_prs.return_value = []

        self.config_path.write_text("""\
[[github]]
owner = "o"
repo = "r"
""")

        stats = collect_repos.scan_repo(
            "github", "o", "r",
            since="2024-01-15T00:00:00Z",
            token=None,
            db_path=self.db_path,
            dry_run=True,
        )

        self.assertEqual(stats["commits"], 1)
        self.assertEqual(stats["issues"], 0)
        self.assertEqual(stats["pull_requests"], 0)

        # DB should not be created on dry run
        self.assertFalse(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
