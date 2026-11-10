"""dailyinfo 全量简报镜像脚本的单测（纯函数 + fake MCP）。

Run: uv run --with pytest --with pyyaml pytest tests/test_dailyinfo_digest.py -q
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "dailyinfo_digest_under_test", REPO_ROOT / "scripts" / "dailyinfo_digest.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


digest = _load()


class FakeResult:
    def __init__(self, payload=None, *, is_error=False, text=None):
        self.structured_content = payload
        self.is_error = is_error
        self.content = [SimpleNamespace(text=text)] if text is not None else []


def _page(body, *, total=None, cursor=None):
    return FakeResult(
        {
            "body": body,
            "body_total_chars": len(body) if total is None else total,
            "next_cursor": cursor,
        }
    )


def _wrapped_error(code):
    """生产形态：FastMCP 把 ToolError 包装成 "Error executing tool <name>: <CODE>"。"""
    return FakeResult(
        None,
        is_error=True,
        text=f"Error executing tool dailyinfo_get_briefing: {code}",
    )


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    """重试只验证调用次数；真实退避（1s/2s）会拖慢套件且不可控。"""
    monkeypatch.setattr(digest, "RETRY_BACKOFF_SECONDS", 0)


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        if not self.responses:
            raise AssertionError(f"unexpected call: {name}")
        return self.responses.pop(0)


class TestCstDate:
    def test_cron_fire_time_is_next_day_in_beijing(self):
        # 北京 07:30 = UTC 前一天 23:30
        assert digest.cst_date(datetime(2026, 11, 7, 23, 30, tzinfo=timezone.utc)) == "2026-11-08"

    def test_utc_midnight_boundary(self):
        assert digest.cst_date(datetime(2026, 11, 7, 15, 59, tzinfo=timezone.utc)) == "2026-11-07"
        assert digest.cst_date(datetime(2026, 11, 7, 16, 0, tzinfo=timezone.utc)) == "2026-11-08"

    def test_no_dst_shift(self):
        assert digest.cst_date(datetime(2026, 6, 15, 16, 0, tzinfo=timezone.utc)) == "2026-06-16"
        assert digest.cst_date(datetime(2026, 12, 15, 16, 0, tzinfo=timezone.utc)) == "2026-12-16"

    def test_naive_datetime_is_rejected(self):
        with pytest.raises(ValueError):
            digest.cst_date(datetime(2026, 11, 8, 0, 0))


class TestPlaceholder:
    def test_dailyinfo_placeholder(self):
        assert digest.is_placeholder_body("## 过去 24 小时\n\n📭 过去 24 小时无新内容\n")

    def test_real_body(self):
        assert not digest.is_placeholder_body("## 📚 Nature 今日简报\n\n1. **Paper**\n" + "x" * 400)

    def test_long_body_with_markers_is_not_placeholder(self):
        text = "📭 过去" + "无新内容" + "x" * 300
        assert not digest.is_placeholder_body(text)


class TestPayload:
    def test_prefers_structured_content(self):
        result = FakeResult({"a": 1}, text='{"a": 2}')
        assert digest._payload(result) == {"a": 1}

    def test_falls_back_to_text_json(self):
        result = FakeResult(None, text='{"a": 2}')
        assert digest._payload(result) == {"a": 2}

    @pytest.mark.parametrize(
        "text",
        [
            "NOT_FOUND",
            "Error executing tool dailyinfo_get_briefing: NOT_FOUND",
        ],
    )
    def test_tool_error_code_is_preserved(self, text):
        with pytest.raises(digest.DigestError) as exc:
            digest._payload(FakeResult(None, is_error=True, text=text))
        assert exc.value.code == "NOT_FOUND"

    @pytest.mark.parametrize(
        "text",
        ["transport exploded", "connection reset by PEER"],
    )
    def test_unrecognized_error_text_is_kept(self, text):
        with pytest.raises(digest.DigestError) as exc:
            digest._payload(FakeResult(None, is_error=True, text=text))
        assert exc.value.code == text


class TestFetchBody:
    def test_follows_cursor_and_validates_total(self):
        recorder = Recorder(
            [
                _page("abc", total=6, cursor="c1"),
                _page("def", total=6, cursor=None),
            ]
        )
        body = asyncio.run(digest.fetch_body(recorder, "papers-2026-11-08"))
        assert body == "abcdef"
        assert [call[1].get("cursor") for call in recorder.calls] == [None, "c1"]

    def test_length_mismatch_fails_loud(self):
        recorder = Recorder([_page("abc", total=99, cursor=None)])
        with pytest.raises(digest.DigestError) as exc:
            asyncio.run(digest.fetch_body(recorder, "papers-2026-11-08"))
        assert exc.value.code == "BODY_LENGTH_MISMATCH"

    def test_stale_cursor_restarts_from_scratch(self):
        recorder = Recorder(
            [
                FakeResult(None, is_error=True, text="STALE_CURSOR"),
                _page("fresh", cursor=None),
            ]
        )
        body = asyncio.run(digest.fetch_body(recorder, "papers-2026-11-08"))
        assert body == "fresh"
        assert len(recorder.calls) == 2

    def test_wrapped_stale_cursor_restarts_from_scratch(self):
        recorder = Recorder([_wrapped_error("STALE_CURSOR"), _page("fresh", cursor=None)])
        body = asyncio.run(digest.fetch_body(recorder, "papers-2026-11-08"))
        assert body == "fresh"
        assert len(recorder.calls) == 2

    def test_content_changed_is_retried(self):
        # dailyinfo 文档：CONTENT_CHANGED = 读取期间持续变化，稍后重试。
        recorder = Recorder([_wrapped_error("CONTENT_CHANGED"), _page("recovered", cursor=None)])
        body = asyncio.run(digest.fetch_body(recorder, "papers-2026-11-08"))
        assert body == "recovered"
        assert len(recorder.calls) == 2

    def test_content_changed_exhausts_retries_with_clean_code(self):
        error = _wrapped_error("CONTENT_CHANGED")
        recorder = Recorder([error, error, error])
        with pytest.raises(digest.DigestError) as exc:
            asyncio.run(digest.fetch_body(recorder, "papers-2026-11-08"))
        assert exc.value.code == "CONTENT_CHANGED"
        assert len(recorder.calls) == 3

    def test_repeated_cursor_fails(self):
        recorder = Recorder([_page("abc", total=99, cursor="same"), _page("x", total=99, cursor="same")])
        with pytest.raises(digest.DigestError) as exc:
            asyncio.run(digest.fetch_body(recorder, "papers-2026-11-08"))
        assert exc.value.code == "CURSOR_REPEATED"


class TestCollectDigest:
    def _listing(self, ids):
        return FakeResult(
            {
                "briefings": [
                    {"id": briefing_id, "category": category}
                    for category, briefing_id in ids.items()
                ]
            }
        )

    def test_listing_content_changed_is_retried(self):
        # list_briefings 同样走服务端 _consistent，一次瞬时变化不该让整份 digest 崩掉。
        recorder = Recorder(
            [
                _wrapped_error("CONTENT_CHANGED"),
                self._listing({"papers": "papers-2026-11-08"}),
                _page("PAPERS BODY", cursor=None),
            ]
        )
        sections = asyncio.run(digest.collect_digest(recorder, "2026-11-08"))
        assert next(s for s in sections if s.category == "papers").kind == "body"
        assert len(recorder.calls) == 3

    def test_orders_categories_and_marks_missing(self):
        recorder = Recorder(
            [
                self._listing({"papers": "papers-2026-11-08", "ai_news": "ai_news-2026-11-08"}),
                _page("AI NEWS BODY", cursor=None),
                _page("PAPERS BODY", cursor=None),
            ]
        )
        sections = asyncio.run(digest.collect_digest(recorder, "2026-11-08"))
        assert [s.category for s in sections] == list(digest.CATEGORY_ORDER)
        by_category = {s.category: s for s in sections}
        assert by_category["ai_news"].kind == "body"
        assert by_category["ai_news"].body == "AI NEWS BODY"
        assert by_category["papers"].kind == "body"
        assert by_category["code"].kind == "missing"
        assert by_category["resource"].kind == "missing"
        assert by_category["arxiv"].kind == "missing"

    @pytest.mark.parametrize(
        "text",
        [
            "CONTENT_CHANGED",
            "Error executing tool dailyinfo_get_briefing: CONTENT_CHANGED",
        ],
    )
    def test_single_category_failure_degrades_to_error(self, text):
        error = FakeResult(None, is_error=True, text=text)
        recorder = Recorder(
            [self._listing({"papers": "papers-2026-11-08"}), error, error, error]
        )
        sections = asyncio.run(digest.collect_digest(recorder, "2026-11-08"))
        papers = next(s for s in sections if s.category == "papers")
        assert papers.kind == "error"
        assert papers.detail == "CONTENT_CHANGED"

    @pytest.mark.parametrize(
        "text",
        [
            "NOT_FOUND",
            "Error executing tool dailyinfo_get_briefing: NOT_FOUND",
        ],
    )
    def test_not_found_is_treated_as_missing(self, text):
        recorder = Recorder(
            [
                self._listing({"papers": "papers-2026-11-08"}),
                FakeResult(None, is_error=True, text=text),
            ]
        )
        sections = asyncio.run(digest.collect_digest(recorder, "2026-11-08"))
        assert next(s for s in sections if s.category == "papers").kind == "missing"


class TestRender:
    def test_renders_bodies_notices_and_warnings(self):
        text = digest.render_digest(
            "2026-11-08",
            [
                digest.Section("ai_news", "body", "AI BODY"),
                digest.Section("code", "placeholder"),
                digest.Section("resource", "missing"),
                digest.Section("papers", "error", detail="CONTENT_CHANGED"),
                digest.Section("arxiv", "body", "ARXIV BODY"),
            ],
        )
        assert "📮 **dailyinfo 每日简报 · 2026-11-08**" in text
        assert "**ai_news**\n\nAI BODY" in text
        assert "📭 **code**：今日各源均无新内容" in text
        assert "📭 **resource**：今日暂无简报" in text
        assert "⚠️ **papers**：拉取失败（CONTENT_CHANGED）" in text
        assert "**arxiv**\n\nARXIV BODY" in text

    def test_all_missing_still_renders(self):
        sections = [digest.Section(category, "missing") for category in digest.CATEGORY_ORDER]
        text = digest.render_digest("2026-11-08", sections)
        assert text.count("📭") == len(digest.CATEGORY_ORDER)


class TestFeishuSanitize:
    """dailyinfo 的 body 是给 Discord/网页的 Markdown；飞书卡片不渲染 blockquote，
    也不渲染 HTML 标签。这里钉住“摘要可见、标签不可见”。"""

    def test_decodes_and_strips_escaped_tags(self):
        text = (
            "1. **Far-UVC (222 nm) disrupts &lt;em&gt;Microcystis aeruginosa&lt;/em&gt;**\n"
            "   > 该研究说明远紫外可破坏藻类细胞包膜。"
        )
        out = digest.sanitize_body_for_feishu(text)
        assert "Microcystis aeruginosa" in out
        assert "&lt;" not in out and "&gt;" not in out
        assert "<em>" not in out

    def test_sub_and_sup_keep_inner_text(self):
        assert digest.sanitize_body_for_feishu("CO&lt;sub&gt;2&lt;/sub&gt;") == "CO2"
        assert digest.sanitize_body_for_feishu("δ&lt;sup&gt;18&lt;/sup&gt;O") == "δ18O"

    def test_raw_tags_are_stripped(self):
        assert digest.sanitize_body_for_feishu("<em>X</em> <b>Y</b>") == "X Y"

    def test_blockquote_becomes_visible_summary(self):
        text = "1. **Title**\n   > 该研究通过 CRISPR 实现持久降脂。"
        out = digest.sanitize_body_for_feishu(text)
        assert "   **摘要：** 该研究通过 CRISPR 实现持久降脂。" in out
        assert "> 该研究" not in out

    def test_nested_and_no_space_blockquotes(self):
        assert digest.sanitize_body_for_feishu(">摘要") == "**摘要：** 摘要"
        assert digest.sanitize_body_for_feishu(">>嵌套") == "**摘要：** 嵌套"

    def test_br_becomes_newline(self):
        assert digest.sanitize_body_for_feishu("a<br>b<br/>c") == "a\nb\nc"

    def test_literal_less_than_and_ampersand_are_preserved(self):
        assert digest.sanitize_body_for_feishu("P &lt; 0.05 &amp; A") == "P < 0.05 & A"

    def test_render_digest_uses_the_sanitized_body(self):
        text = digest.render_digest(
            "2026-11-08",
            [digest.Section("papers", "body",
                            "1. **A &lt;i&gt;B&lt;/i&gt;**\n   > 摘要句")],
        )
        assert "**摘要：** 摘要句" in text
        assert "&lt;" not in text
        assert "<i>" not in text


class TestMain:
    def test_invalid_date(self, capsys):
        assert digest.main(["--date", "2026-13-01"]) == 2
        assert "日期参数不合法" in capsys.readouterr().err

    def test_missing_env(self, monkeypatch, capsys):
        monkeypatch.delenv("DAILYINFO_MCP_URL", raising=False)
        monkeypatch.delenv("DAILYINFO_MCP_KEY", raising=False)
        assert digest.main([]) == 1
        assert "DAILYINFO_MCP_URL/KEY" in capsys.readouterr().err

    def test_success_prints_digest(self, monkeypatch, capsys):
        monkeypatch.setenv("DAILYINFO_MCP_URL", "http://dailyinfo-mcp:8768/mcp")
        monkeypatch.setenv("DAILYINFO_MCP_KEY", "k" * 40)

        async def fake_collect(url, key, day):
            return [digest.Section("ai_news", "body", "BODY")]

        monkeypatch.setattr(digest, "_collect_with_session", fake_collect)
        assert digest.main(["--date", "2026-11-08"]) == 0
        out = capsys.readouterr().out
        assert "dailyinfo 每日简报 · 2026-11-08" in out
        assert "BODY" in out

    def test_mcp_failure_returns_one(self, monkeypatch, capsys):
        monkeypatch.setenv("DAILYINFO_MCP_URL", "http://dailyinfo-mcp:8768/mcp")
        monkeypatch.setenv("DAILYINFO_MCP_KEY", "k" * 40)

        async def boom(url, key, day):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(digest, "_collect_with_session", boom)
        assert digest.main(["--date", "2026-11-08"]) == 1
        assert "connection refused" in capsys.readouterr().err
