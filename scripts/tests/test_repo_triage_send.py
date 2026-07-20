#!/usr/bin/env python3
"""
Tests for scripts/repo-triage-send.py — Repository Activity Feishu Reporter.

Tests for repo-triage-send.py functions using importlib for the hyphenated filename.

Usage:
    python3 -m pytest scripts/tests/test_repo_triage_send.py -v
"""

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load repo-triage-send via importlib (filename has a hyphen, not a valid module name)
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "repo_triage_send", str(SCRIPTS_DIR / "repo-triage-send.py")
)
repo_triage_send = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repo_triage_send)
sys.modules["repo_triage_send"] = repo_triage_send

# Grab functions from the loaded module
_weekday_cn = repo_triage_send._weekday_cn
build_feishu_card = repo_triage_send.build_feishu_card
format_activity_for_prompt = repo_triage_send.format_activity_for_prompt
format_template_report = repo_triage_send.format_template_report
get_tenant_token = repo_triage_send.get_tenant_token
send_feishu_message = repo_triage_send.send_feishu_message
summarize_with_llm = repo_triage_send.summarize_with_llm


# ---------------------------------------------------------------------------
# Shared test data builders
# ---------------------------------------------------------------------------

def _make_mock_repos():
    """Return mock repos with commits, issues, and PRs."""
    return [
        {
            "platform": "github",
            "owner": "OuyangWenyu",
            "repo": "torchhydro",
            "commits": [
                {
                    "sha": "abc1234",
                    "author": "owen",
                    "message": "fix: resolve memory leak in data loader",
                    "date": "2026-07-18T10:00:00Z",
                },
                {
                    "sha": "def5678",
                    "author": "owen",
                    "message": "feat: add transformer-based runoff model",
                    "date": "2026-07-18T14:00:00Z",
                },
            ],
            "new_issues": [
                {
                    "number": 42,
                    "title": "Bug: crash on empty input tensor",
                    "author": "user1",
                },
            ],
            "closed_issues": [
                {"number": 40, "title": "Fix NaN gradient", "author": "user2"},
            ],
            "new_prs": [
                {"number": 101, "title": "Add multi-GPU support", "author": "contributor1"},
            ],
            "merged_prs": [
                {"number": 100, "title": "Fix typo in README", "author": "contributor2"},
                {"number": 99, "title": "Update torch dependency", "author": "owen"},
            ],
        },
    ]


def _make_mock_totals():
    """Return mock totals dict matching _make_mock_repos()."""
    return {
        "repos_scanned": 9,
        "total_commits": 2,
        "total_new_issues": 1,
        "total_closed_issues": 1,
        "total_new_prs": 1,
        "total_merged_prs": 2,
    }


def _make_mock_summary():
    """Return a full build_summary()-shaped dict with activity."""
    return {
        "date": "2026-07-19",
        "scanned_at": "2026-07-19T08:00:00Z",
        "repos": _make_mock_repos(),
        "totals": _make_mock_totals(),
        "has_activity": True,
    }


# ============================================================================
# _weekday_cn
# ============================================================================

def test_weekday_cn_all_seven_days():
    """All 7 days map to correct Chinese weekday strings."""
    cases = [
        (date(2026, 7, 13), "周一"),  # Monday
        (date(2026, 7, 14), "周二"),  # Tuesday
        (date(2026, 7, 15), "周三"),  # Wednesday
        (date(2026, 7, 16), "周四"),  # Thursday
        (date(2026, 7, 17), "周五"),  # Friday
        (date(2026, 7, 18), "周六"),  # Saturday
        (date(2026, 7, 19), "周日"),  # Sunday
    ]
    for d, expected in cases:
        assert _weekday_cn(d) == expected, f"{d} -> {_weekday_cn(d)}, expected {expected}"


def test_weekday_cn_with_date_object():
    """A plain date object should return a two-character 周X string."""
    result = _weekday_cn(date(2026, 1, 1))
    assert result.startswith("周")
    assert len(result) == 2


# ============================================================================
# format_activity_for_prompt
# ============================================================================

def test_format_activity_for_prompt_with_activity():
    """Output should contain repo keys, commit messages, issue/PR titles."""
    repos = _make_mock_repos()
    totals = _make_mock_totals()
    result = format_activity_for_prompt(repos)

    assert "## github/OuyangWenyu/torchhydro" in result
    assert "fix: resolve memory leak in data loader" in result
    assert "feat: add transformer-based runoff model" in result
    assert "Bug: crash on empty input tensor" in result
    assert "Fix NaN gradient" in result
    assert "Add multi-GPU support" in result
    assert "Fix typo in README" in result


def test_format_activity_truncates_long_commit_message():
    """Commit messages > 80 chars are truncated with '...'."""
    long_msg = "x" * 100
    repos = [
        {
            "platform": "github",
            "owner": "o",
            "repo": "r",
            "commits": [
                {"sha": "abc1234", "author": "owen", "message": long_msg, "date": "2026-07-18T10:00:00Z"},
            ],
            "new_issues": [],
            "closed_issues": [],
            "new_prs": [],
            "merged_prs": [],
        },
    ]
    totals = {
        "repos_scanned": 1,
        "total_commits": 1,
        "total_new_issues": 0,
        "total_closed_issues": 0,
        "total_new_prs": 0,
        "total_merged_prs": 0,
    }
    result = format_activity_for_prompt(repos)

    # The full 100-char message must not leak through
    assert long_msg not in result
    # Truncation indicator should be present
    assert "..." in result


def test_format_activity_skips_empty_repos():
    """Repos with zero activity across all categories should be absent."""
    repos = [
        {
            "platform": "github",
            "owner": "active",
            "repo": "r1",
            "commits": [{"sha": "abc", "author": "a", "message": "test", "date": "..."}],
            "new_issues": [],
            "closed_issues": [],
            "new_prs": [],
            "merged_prs": [],
        },
        {
            "platform": "gitcode",
            "owner": "inactive",
            "repo": "r2",
            "commits": [],
            "new_issues": [],
            "closed_issues": [],
            "new_prs": [],
            "merged_prs": [],
        },
    ]
    totals = {
        "repos_scanned": 2,
        "total_commits": 1,
        "total_new_issues": 0,
        "total_closed_issues": 0,
        "total_new_prs": 0,
        "total_merged_prs": 0,
    }
    result = format_activity_for_prompt(repos)
    assert "active" in result
    assert "inactive" not in result


def test_format_activity_all_empty_returns_empty_string():
    """When no repo has any activity, return an empty string."""
    repos = [
        {
            "platform": "github",
            "owner": "o",
            "repo": "r",
            "commits": [],
            "new_issues": [],
            "closed_issues": [],
            "new_prs": [],
            "merged_prs": [],
        },
    ]
    totals = {
        "repos_scanned": 1,
        "total_commits": 0,
        "total_new_issues": 0,
        "total_closed_issues": 0,
        "total_new_prs": 0,
        "total_merged_prs": 0,
    }
    result = format_activity_for_prompt(repos)
    assert result == ""


# ============================================================================
# format_template_report
# ============================================================================

def test_format_template_report_with_full_activity():
    """Template report should include emoji indicators and repo headers."""
    summary = _make_mock_summary()
    result = format_template_report(summary)

    assert "📦 github/OuyangWenyu/torchhydro" in result
    assert "📝" in result   # commits
    assert "🆕" in result   # new issues
    assert "🔒" in result   # closed issues
    assert "🔀" in result   # new PRs
    assert "✅" in result   # merged PRs


def test_format_template_report_none_summary():
    """None summary returns the fallback 'no activity' string."""
    result = format_template_report(None)
    assert "无仓库活动" in result


def test_format_template_report_has_activity_false():
    """Summary with has_activity=False returns the fallback string."""
    summary = {"has_activity": False, "repos": [], "totals": {}}
    result = format_template_report(summary)
    assert "无仓库活动" in result


# ============================================================================
# build_feishu_card
# ============================================================================

def test_build_feishu_card_structure():
    """Card dict must have config, header, and elements with correct inner shape."""
    today = date(2026, 7, 19)
    card = build_feishu_card("Hello World", today)

    assert isinstance(card, dict)
    assert "config" in card
    assert "header" in card
    assert "elements" in card

    # config
    assert card["config"]["wide_screen_mode"] is True

    # header
    assert card["header"]["template"] == "blue"
    assert card["header"]["title"]["tag"] == "plain_text"

    # elements
    assert isinstance(card["elements"], list)
    assert len(card["elements"]) >= 1
    assert card["elements"][0]["tag"] == "markdown"
    assert card["elements"][0]["content"] == "Hello World"


def test_build_feishu_card_title_format():
    """Card title must contain Chinese-format date: month, day, and weekday."""
    today = date(2026, 7, 19)
    card = build_feishu_card("test", today)
    title = card["header"]["title"]["content"]

    assert "仓库动态" in title
    assert "7月" in title
    assert "19日" in title
    assert "周" in title  # Chinese weekday prefix present


# ============================================================================
# summarize_with_llm
# ============================================================================

def test_summarize_no_api_key_falls_back(monkeypatch):
    """When DEEPSEEK_API_KEY is absent, fall back to template report."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    summary = _make_mock_summary()
    result = summarize_with_llm(summary)
    # Template report markers should be present
    assert "📦" in result
    assert "📝" in result


def test_summarize_successful_llm_call(monkeypatch):
    """A successful DeepSeek API call returns the LLM response text."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")

    mock_body = json.dumps({
        "choices": [
            {"message": {"content": "今天的仓库动态总结：torchhydro 有 2 个新提交。"}},
        ],
    })
    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_body.encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    summary = _make_mock_summary()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = summarize_with_llm(summary)

    assert "今天的仓库动态总结" in result
    assert "torchhydro" in result


def test_summarize_network_error_falls_back(monkeypatch):
    """A network error (OSError) should trigger template-report fallback."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")

    summary = _make_mock_summary()
    with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
        result = summarize_with_llm(summary)

    assert "📦" in result
    assert "📝" in result


# ============================================================================
# send_feishu_message
# ============================================================================

def test_send_feishu_message_success():
    """A successful Feishu API call returns the parsed JSON with code=0."""
    mock_body = json.dumps({"code": 0, "msg": "ok"})
    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_body.encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    card = {"config": {"wide_screen_mode": True}, "header": {}, "elements": []}

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = send_feishu_message("test-token", "ou_test123", card)

    assert result["code"] == 0
    assert result["msg"] == "ok"


def test_send_feishu_message_failure():
    """A Feishu API error response should be returned as-is."""
    mock_body = json.dumps({"code": 999, "msg": "invalid tenant access token"})
    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_body.encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    card = {"config": {}, "header": {}, "elements": []}

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = send_feishu_message("bad-token", "ou_test123", card)

    assert result["code"] == 999


# ============================================================================
# get_tenant_token
# ============================================================================

def test_get_tenant_token_success():
    """Successful auth returns the tenant_access_token from Feishu response."""
    mock_body = json.dumps({"tenant_access_token": "t-abc123token"})
    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_body.encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        token = get_tenant_token("test-app-id", "test-secret")

    assert token == "t-abc123token"


# ============================================================================
# _repo_key
# ============================================================================

def test_repo_key():
    """_repo_key formats platform/owner/repo correctly."""
    _repo_key_fn = repo_triage_send._repo_key
    repo = {"platform": "github", "owner": "OuyangWenyu", "repo": "torchhydro"}
    assert _repo_key_fn(repo) == "github/OuyangWenyu/torchhydro"

    repo2 = {"platform": "gitcode", "owner": "dlut-water", "repo": "prd-tdd"}
    assert _repo_key_fn(repo2) == "gitcode/dlut-water/prd-tdd"


# ============================================================================
# _truncate_msg
# ============================================================================

def test_truncate_msg_short():
    """Short messages are returned unchanged."""
    _truncate_msg_fn = repo_triage_send._truncate_msg
    assert _truncate_msg_fn("fix: bug") == "fix: bug"


def test_truncate_msg_long():
    """Messages longer than 80 chars are truncated with '...'."""
    _truncate_msg_fn = repo_triage_send._truncate_msg
    long_msg = "x" * 100
    result = _truncate_msg_fn(long_msg)
    assert result.endswith("...")
    assert len(result) == 83  # 80 chars + 3 dots
