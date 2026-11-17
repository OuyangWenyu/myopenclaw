"""paper-queue 每日对账脚本的单测（纯函数 + fake MCP）。

Run: uv run --with pytest --with pyyaml pytest tests/test_paper_queue_sync.py -q
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "paper_queue_sync_under_test", REPO_ROOT / "scripts" / "paper_queue_sync.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync = _load()


class FakeResult:
    def __init__(self, payload=None, *, is_error=False, text=None):
        self.structured_content = payload
        self.is_error = is_error
        self.content = [SimpleNamespace(text=text)] if text is not None else []


def _json(payload):
    return FakeResult(text=json.dumps(payload, ensure_ascii=False))


class Recorder:
    """按序返回预设响应；队列耗尽后默认**重复最后一个响应**。

    主查询落空后 `find_matches` 会追加「标点无关词串」检索（2026-11-17 的修复），
    所以「查不到」的用例会多发几次 `zotero_search`。默认重复最后一个响应，避免
    每个用例都要列一串空响应；要精确断言调用序列的用例传 `repeat_last=False`。
    """

    def __init__(self, responses, *, repeat_last=True):
        self.responses = list(responses)
        self.calls = []
        self.repeat_last = repeat_last
        self._last = None

    async def __call__(self, name, arguments):
        self.calls.append((name, arguments))
        if not self.responses:
            if self.repeat_last and self._last is not None:
                return self._last
            raise AssertionError(f"unexpected call: {name}")
        self._last = self.responses.pop(0)
        return self._last


def _row(**over):
    row = {
        "id": 1,
        "request_key": "title:abc",
        "title": "LeHami: Symplectic Latent Dynamics for World Models",
        "doi": None,
        "doi_source": None,
        "arxiv_id": None,
        "input_kind": "title",
    }
    row.update(over)
    return row


def _item(title, *, item_type="preprint", key="ITEM1", doi=""):
    return {"key": key, "title": title, "itemType": item_type, "DOI": doi}


def _search(items):
    return _json({"total": len(items), "query": "q", "items": items})


def _sessions(recorders):
    """假的 MCP session 工厂：每次 with 依次产出一个 recorder 会话。"""
    queue = list(recorders)

    @asynccontextmanager
    async def factory(url, token):
        if not queue:
            raise AssertionError("unexpected MCP session")
        yield SimpleNamespace(call_tool=queue.pop(0))

    return factory


class TestNormalizeTitle:
    def test_case_and_whitespace_folded(self):
        assert sync.normalize_title("  On The   Representation  ") == "on the representation"

    def test_html_entities_and_tags_removed(self):
        assert sync.normalize_title(
            "&lt;i&gt;Microcystis aeruginosa&lt;/i&gt; blooms"
        ) == "microcystis aeruginosa blooms"
        assert sync.normalize_title("<em>E. coli</em> growth") == "e coli growth"

    def test_unicode_dash_variants_unify(self):
        assert sync.normalize_title("Storage‐Runoff") == sync.normalize_title("Storage-Runoff")

    def test_nfkc_folds_fullwidth_and_superscript(self):
        assert sync.normalize_title("Ｅ²Forecast") == "e2forecast"

    def test_cjk_is_preserved(self):
        assert sync.normalize_title("水文模型：综述") == "水文模型 综述"

    def test_empty_input(self):
        assert sync.normalize_title("") == ""
        assert sync.normalize_title(None) == ""


class TestTitlesMatch:
    def test_exact_after_normalization(self):
        assert sync.titles_match("LeHami: Symplectic Latent Dynamics",
                                 "LeHami — Symplectic Latent Dynamics")

    def test_truncated_prefix_with_high_coverage_matches(self):
        full = "Deep learning for cross-region streamflow and flood forecasting at a global scale"
        short = full[: int(len(full) * 0.93)]
        assert sync.titles_match(short, full)

    def test_short_containment_is_rejected(self):
        assert not sync.titles_match("BERT", "BERT: Pre-training of Deep Bidirectional Transformers")

    def test_suffix_containment_is_rejected(self):
        # 「Erratum: <title>」把原论文标题当后缀包住；任意包含会误清原论文请求
        title = "A Physically Based Storage-Runoff Relationship for Steep Hillslopes and More"
        assert not sync.titles_match(title, "Erratum: " + title)
        assert not sync.titles_match(title, "Reply to: " + title)

    def test_different_titles_do_not_match(self):
        assert not sync.titles_match("How Water Level Fluctuations Impact Reservoir Eutrophication",
                                     "Global reach-level 3-hourly river flood reanalysis")

    def test_empty_titles_never_match(self):
        assert not sync.titles_match("", "anything")
        assert not sync.titles_match("anything", "")


class TestPayload:
    def test_prefers_structured_content(self):
        result = FakeResult({"status": "cancelled"}, text='{"status": "already_cancelled"}')
        assert sync._payload(result) == {"status": "cancelled"}

    def test_falls_back_to_text_json(self):
        assert sync._payload(_json({"status": "cancelled"})) == {"status": "cancelled"}

    def test_unwraps_sdk_result_envelope(self):
        # MCP SDK 把返回 str 的工具包成 {"result": "<json>"}；不拆开就会读成空清单
        inner = json.dumps({"count": 1, "items": [{"title": "x"}]}, ensure_ascii=False)
        result = FakeResult({"result": inner}, text=inner)
        assert sync._payload(result) == {"count": 1, "items": [{"title": "x"}]}

    def test_unparseable_result_envelope_is_an_error(self):
        # 拆不动就报错；否则会被读成「没有要清理的条目」，静默失效
        with pytest.raises(sync.SyncError):
            sync._payload(FakeResult({"result": "not json"}))

    def test_zotero_error_payload_raises(self):
        result = _json({"error": True, "context": "zotero_search", "message": "Web API client not initialized"})
        with pytest.raises(sync.SyncError) as exc:
            sync._payload(result)
        assert "zotero_search" in str(exc.value)

    def test_mcp_is_error_raises(self):
        result = FakeResult(None, is_error=True, text="boom")
        with pytest.raises(sync.SyncError):
            sync._payload(result)

    def test_mcp_is_error_keeps_the_text_reason(self):
        # FastMCP 的工具异常常常只有文本块；别把原因吞成 TOOL_ERROR
        result = FakeResult(None, is_error=True, text="Bearer token 无法识别，拒绝请求")
        with pytest.raises(sync.SyncError) as exc:
            sync._payload(result)
        assert "Bearer token" in str(exc.value)

    def test_invalid_payload_raises(self):
        with pytest.raises(sync.SyncError):
            sync._payload(FakeResult(None, text="not json"))


class TestFetchRows:
    def test_reads_items(self):
        recorder = Recorder([_json({"count": 1, "items": [_row()]})])
        rows = asyncio.run(sync.fetch_rows(recorder))
        assert rows[0]["id"] == 1

    def test_missing_items_is_an_error(self):
        recorder = Recorder([_json({"count": 0})])
        with pytest.raises(sync.SyncError):
            asyncio.run(sync.fetch_rows(recorder))

    def test_truncated_is_an_error(self):
        # 截断时 newest-first 会永久漏掉最老的（最可能已入库的）行 —— 必须响亮失败
        recorder = Recorder([_json({"items": [], "truncated": True})])
        with pytest.raises(sync.SyncError):
            asyncio.run(sync.fetch_rows(recorder))


class TestFindMatches:
    def test_title_hit(self):
        recorder = Recorder([_search([_item("LeHami: Symplectic Latent Dynamics for World Models")])])
        matches, warnings = asyncio.run(sync.find_matches([_row()], recorder))
        assert [m.row["request_key"] for m in matches] == ["title:abc"]
        assert matches[0].zotero_key == "ITEM1"
        assert warnings == []
        assert recorder.calls == [("zotero_search", {
            "query": "LeHami: Symplectic Latent Dynamics for World Models", "limit": sync.SEARCH_LIMIT})]

    def test_no_hit(self):
        recorder = Recorder([_search([_item("A different paper")])])
        matches, _ = asyncio.run(sync.find_matches([_row()], recorder))
        assert matches == []

    def test_attachment_is_not_treated_as_the_paper(self):
        recorder = Recorder([_search([_item("LeHami: Symplectic Latent Dynamics for World Models",
                                            item_type="attachment")])])
        matches, _ = asyncio.run(sync.find_matches([_row()], recorder))
        assert matches == []

    def test_note_is_not_treated_as_the_paper(self):
        recorder = Recorder([_search([_item("LeHami: Symplectic Latent Dynamics for World Models",
                                            item_type="note")])])
        matches, _ = asyncio.run(sync.find_matches([_row()], recorder))
        assert matches == []

    def test_webpage_is_not_treated_as_the_paper(self):
        # 浏览器插件保存的网页条目即使标题相同也不算「已入库」
        recorder = Recorder([_search([_item("LeHami: Symplectic Latent Dynamics for World Models",
                                            item_type="webpage")])])
        matches, _ = asyncio.run(sync.find_matches([_row()], recorder))
        assert matches == []

    def test_arxiv_query_normalizes_prefix_and_version(self):
        assert sync._arxiv_query("arXiv:2610.15267v2") == "2610.15267"
        assert sync._arxiv_query("2610.15267") == "2610.15267"
        assert sync._arxiv_query("weird-id") == "weird-id"

    def test_arxiv_path_resolves_attachment_parent(self):
        row = _row(title="LeHami: Symplectic Latent Dynamics for World Models",
                   arxiv_id="arXiv:2610.15267v2")
        recorder = Recorder([
            _search([]),  # 标题搜不到
            _search([]),  # 备用词串也搜不到（"LeHami:" 里的冒号把整串拆开）
            _search([_item("arXiv.2610.15267.pdf", item_type="attachment", key="ATT1")]),
            _json({"key": "ATT1", "data": {"key": "ATT1", "itemType": "attachment",
                                           "parentItem": "PAR1"}}),
            _json({"key": "PAR1", "data": {"key": "PAR1", "itemType": "preprint",
                                           "title": "LeHami: Symplectic Latent Dynamics for World Models",
                                           "DOI": "10.48550/arXiv.2610.15267"}}),
        ])
        matches, _ = asyncio.run(sync.find_matches([row], recorder))
        assert len(matches) == 1
        assert matches[0].zotero_key == "PAR1"
        assert [call[0] for call in recorder.calls] == [
            "zotero_search", "zotero_search", "zotero_search",
            "zotero_get_item", "zotero_get_item"]
        assert recorder.calls[1][1]["query"] == "Symplectic Latent Dynamics for World Models"
        assert recorder.calls[2][1]["query"] == "2610.15267"

    def test_arxiv_parent_with_different_title_does_not_match(self):
        row = _row(title="LeHami: Symplectic Latent Dynamics for World Models",
                   arxiv_id="2610.15267")
        recorder = Recorder([
            _search([]),
            _search([]),  # 备用词串也搜不到
            _search([_item("arXiv.2610.15267.pdf", item_type="attachment", key="ATT1")]),
            _json({"key": "ATT1", "data": {"key": "ATT1", "itemType": "attachment",
                                           "parentItem": "PAR1"}}),
            _json({"key": "PAR1", "data": {"key": "PAR1", "itemType": "preprint",
                                           "title": "Some completely different paper"}}),
        ])
        matches, _ = asyncio.run(sync.find_matches([row], recorder))
        assert matches == []

    def test_zotero_error_aborts_before_any_cancel(self):
        recorder = Recorder([_json({"error": True, "context": "zotero_search", "message": "down"})])
        with pytest.raises(sync.SyncError):
            asyncio.run(sync.find_matches([_row()], recorder))

    def test_mcp_exception_aborts(self):
        async def boom(name, arguments):
            raise RuntimeError("connection refused")

        with pytest.raises(RuntimeError):
            asyncio.run(sync.find_matches([_row()], boom))

    def test_rows_without_title_are_skipped(self):
        recorder = Recorder([])
        matches, warnings = asyncio.run(sync.find_matches([_row(title="")], recorder))
        assert matches == []
        assert recorder.calls == []
        assert warnings and "id=1" in warnings[0]


class TestRun:
    """生产路径 _run：取列表 → 查 Zotero → 撤销（fake session 工厂）。"""

    def test_dry_run_never_cancels(self):
        pq = Recorder([_json({"count": 1, "items": [_row()]})])
        zot = Recorder([_search([_item("LeHami: Symplectic Latent Dynamics for World Models")])])
        factory = _sessions([pq, zot])  # 只允许 2 个会话；多开会在工厂里 AssertionError
        report = asyncio.run(
            sync._run("http://pq", "t" * 64, "http://zot",
                      dry_run=True, session_factory=factory))
        assert len(report.matches) == 1
        assert report.outcomes == []
        assert pq.calls == [("paper_queue_list", {"window": "all", "limit": sync.LIST_LIMIT})]

    def test_cancels_only_after_all_searches(self):
        pq_list = Recorder([_json({"count": 1, "items": [_row()]})])
        zot = Recorder([_search([_item("LeHami: Symplectic Latent Dynamics for World Models")])])
        pq_cancel = Recorder([_json({"status": "cancelled"})])
        factory = _sessions([pq_list, zot, pq_cancel])
        report = asyncio.run(
            sync._run("http://pq", "t" * 64, "http://zot",
                      dry_run=False, session_factory=factory))
        assert [o.status for o in report.outcomes] == ["cancelled"]
        assert pq_cancel.calls == [("paper_queue_cancel", {"request_key": "title:abc"})]

    def test_search_error_aborts_before_the_cancel_session(self):
        pq_list = Recorder([_json({"count": 1, "items": [_row()]})])
        zot = Recorder([_json({"error": True, "context": "zotero_search", "message": "down"})])
        factory = _sessions([pq_list, zot])  # 第三个会话永远不会被打开
        with pytest.raises(sync.SyncError):
            asyncio.run(
                sync._run("http://pq", "t" * 64, "http://zot",
                          dry_run=False, session_factory=factory))

    def test_cancel_failure_is_recorded(self):
        pq_list = Recorder([_json({"count": 1, "items": [_row()]})])
        zot = Recorder([_search([_item("LeHami: Symplectic Latent Dynamics for World Models")])])
        pq_cancel = Recorder([_json({"error": True, "context": "paper_queue_cancel",
                                     "message": "boom"})])
        factory = _sessions([pq_list, zot, pq_cancel])
        report = asyncio.run(
            sync._run("http://pq", "t" * 64, "http://zot",
                      dry_run=False, session_factory=factory))
        assert report.outcomes[0].status.startswith("error:")


class TestRenderReport:
    def _report(self, matches=(), outcomes=(), dry_run=False):
        return sync.SyncReport(matches=list(matches), outcomes=list(outcomes),
                               dry_run=dry_run, warnings=[])

    def test_silent_when_nothing_matched(self):
        assert sync.render_report(self._report(), day="2026-11-09") == ""

    def test_lists_cleaned_titles(self):
        match = sync.Match(row=_row(), zotero_key="K1", zotero_title="x")
        outcome = sync.CancelOutcome(row=_row(), status="cancelled")
        text = sync.render_report(self._report([match], [outcome]), day="2026-11-09")
        assert "2026-11-09" in text
        assert "LeHami" in text
        assert "移出待办 1 篇" in text

    def test_dry_run_is_labelled(self):
        match = sync.Match(row=_row(), zotero_key="K1", zotero_title="x")
        text = sync.render_report(self._report([match], dry_run=True), day="2026-11-09")
        assert "dry-run" in text
        assert "将清理 1 篇" in text

    def test_includes_zotero_key_for_audit(self):
        match = sync.Match(row=_row(), zotero_key="ABCD1234", zotero_title="x")
        outcome = sync.CancelOutcome(row=_row(), status="cancelled", zotero_key="ABCD1234")
        text = sync.render_report(self._report([match], [outcome]), day="2026-11-09")
        assert "Zotero ABCD1234" in text

    def test_failed_cancel_is_reported(self):
        match = sync.Match(row=_row(), zotero_key="K1", zotero_title="x")
        outcome = sync.CancelOutcome(row=_row(), status="not_owner")
        text = sync.render_report(self._report([match], [outcome]), day="2026-11-09")
        assert "未能移出" in text
        assert "not_owner" in text


class TestMain:
    def test_missing_token_returns_one(self, monkeypatch, capsys):
        monkeypatch.delenv("PAPER_QUEUE_DAOYUAN_TOKEN", raising=False)
        assert sync.main([]) == 1
        assert "PAPER_QUEUE_DAOYUAN_TOKEN" in capsys.readouterr().err

    def test_empty_result_is_silent(self, monkeypatch, capsys):
        monkeypatch.setenv("PAPER_QUEUE_DAOYUAN_TOKEN", "k" * 64)

        async def empty(pq_url, token, zotero_url, *, dry_run):
            return sync.SyncReport(matches=[], outcomes=[], dry_run=dry_run, warnings=[])

        monkeypatch.setattr(sync, "_run", empty)
        assert sync.main([]) == 0
        assert capsys.readouterr().out == ""

    def test_cleaned_matches_are_printed(self, monkeypatch, capsys):
        monkeypatch.setenv("PAPER_QUEUE_DAOYUAN_TOKEN", "k" * 64)

        async def one(pq_url, token, zotero_url, *, dry_run):
            return sync.SyncReport(
                matches=[sync.Match(row=_row(), zotero_key="K1", zotero_title="x")],
                outcomes=[sync.CancelOutcome(row=_row(), status="cancelled")],
                dry_run=dry_run, warnings=[])

        monkeypatch.setattr(sync, "_run", one)
        assert sync.main([]) == 0
        out = capsys.readouterr().out
        assert "LeHami" in out and "移出待办 1 篇" in out

    def test_cancel_failure_exits_one(self, monkeypatch, capsys):
        monkeypatch.setenv("PAPER_QUEUE_DAOYUAN_TOKEN", "k" * 64)

        async def failed(pq_url, token, zotero_url, *, dry_run):
            return sync.SyncReport(
                matches=[sync.Match(row=_row(), zotero_key="K1", zotero_title="x")],
                outcomes=[sync.CancelOutcome(row=_row(), status="not_owner")],
                dry_run=dry_run, warnings=[])

        monkeypatch.setattr(sync, "_run", failed)
        assert sync.main([]) == 1
        assert "撤销失败" in capsys.readouterr().err

    def test_run_error_returns_one(self, monkeypatch, capsys):
        monkeypatch.setenv("PAPER_QUEUE_DAOYUAN_TOKEN", "k" * 64)

        async def boom(pq_url, token, zotero_url, *, dry_run):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(sync, "_run", boom)
        assert sync.main([]) == 1
        assert "connection refused" in capsys.readouterr().err


class TestSearchNeedles:
    """整条标题查不到时的备用检索词（2026-11-17 的真实事故取证）。

    Zotero 的 q 是子串匹配、对标点原样敏感：库里存 `Physics‐Informed`（U+2010）
    而清单里是 `Physics-Informed`（ASCII），整串就 0 结果。备用词串必须
    **标点无关且连续**（连续才可能是库内标题的子串）。
    """

    Q183 = ("Physics-Informed Neural Networks as Differentiable Surrogates "
            "for 4D-Variational Data Assimilation")
    Q184 = ("Theoretical connections between the Nash-Sutcliffe efficiency and the "
            "Kling-Gupta Efficiency for environmental model evaluation")
    Q185 = ("Hydrological implications of satellite-based water budget in the source "
            "area of China's South-to-North water transfer middle route project")

    def test_needles_have_no_punctuation_and_are_contiguous(self):
        for title in (self.Q183, self.Q184, self.Q185):
            needles = sync._search_needles(title)
            assert needles, title
            for needle in needles:
                assert " " in needle, f"单 token 不该当检索词: {needle!r}"
                assert all(w.isalnum() for w in needle.split()), needle
                assert needle in title, f"词串必须原样连续出现: {needle!r}"

    def test_needle_escapes_the_punctuation_that_broke_the_query(self):
        # 183 的整串开头就带 ASCII 连字符；备用词串要从它后面开始
        assert sync._search_needles(self.Q183)[0].startswith("Neural Networks as")
        # 184 的差异在 Nash–Sutcliffe（en dash）；备用词串取最长的标点无关段
        needles = sync._search_needles(self.Q184)
        assert any(n.startswith("Efficiency for environmental") for n in needles)
        assert any(n == "Theoretical connections between the" for n in needles)
        # 185 的差异在 China’s 的弯引号；备用词串要绕开它
        needles185 = sync._search_needles(self.Q185)
        assert any(n.endswith("source area of") for n in needles185), needles185
        assert all("China" not in n for n in needles185)

    def test_longest_run_first_and_capped(self):
        needles = sync._search_needles(self.Q184)
        assert len(needles) <= sync._MAX_NEEDLES
        assert needles == sorted(needles, key=len, reverse=True)

    def test_primary_query_is_not_repeated(self):
        # 没有标点的短标题：唯一的词串就是主查询本身 → 没有备用检索
        assert sync._search_needles("A Short Paper Title") == []
        assert sync._search_needles("") == []


class TestDoiMatch:
    """DOI 逐字相等也算命中：arXiv 预印本的标题常带缩写（PU vs Positive-Unlabeled）。"""

    def test_plain_doi_is_case_folded(self):
        row = _row(doi="10.1016/J.JHYDROL.2026.136754")
        assert sync._row_doi(row) == "10.1016/j.jhydrol.2026.136754"

    def test_arxiv_row_uses_registered_doi(self):
        row = _row(arxiv_id="arXiv:2510.01278v2")
        assert sync._row_doi(row) == "10.48550/arxiv.2510.01278"

    def test_doi_equality_is_exact_not_prefix(self):
        row = _row(doi="10.48550/arXiv.2510.01278")
        assert sync._doi_matches(row, _item("x", doi="10.48550/arxiv.2510.01278"))
        assert not sync._doi_matches(row, _item("x", doi="10.48550/arxiv.2510.0127"))
        assert not sync._doi_matches(row, _item("x", doi=""))

    def test_rows_without_doi_or_arxiv_never_match_by_doi(self):
        assert not sync._doi_matches(_row(), _item("x", doi="10.1/abc"))
        assert not sync._doi_matches(None, _item("x", doi="10.1/abc"))


class TestFindMatchesSearchFallback:
    ZOTERO_TITLE = ("Physics‐Informed Neural Networks as Differentiable Surrogates "
                    "for 4D‐Variational Data Assimilation")   # 库里是 U+2010 连字符

    def test_unicode_hyphen_variant_is_found_via_needle(self):
        row = _row(title=TestSearchNeedles.Q183)
        recorder = Recorder([
            _search([]),                         # 整串（ASCII 连字符）查不到
            _search([_item(self.ZOTERO_TITLE)]),  # 备用词串命中
        ])
        matches, warnings = asyncio.run(sync.find_matches([row], recorder))
        assert [m.zotero_key for m in matches] == ["ITEM1"]
        assert warnings == []
        assert recorder.calls[0][1]["query"] == sync._query_for(TestSearchNeedles.Q183)
        assert recorder.calls[1][1]["query"] == sync._search_needles(
            TestSearchNeedles.Q183)[0]

    def test_primary_hit_does_not_spend_a_second_search(self):
        recorder = Recorder([_search([_item("LeHami: Symplectic Latent Dynamics for World Models")])])
        matches, _ = asyncio.run(sync.find_matches(
            [_row(title="LeHami: Symplectic Latent Dynamics for World Models")], recorder))
        assert len(matches) == 1
        assert len(recorder.calls) == 1

    def test_arxiv_abbreviation_is_recovered_by_doi(self):
        # 真实案例 225：清单写 "PU Learning"，库里是 "Positive-Unlabeled Learning"
        row = _row(title="Noisy-Pair Robust Representation Alignment for PU Learning",
                   arxiv_id="2510.01278")
        recorder = Recorder([
            _search([]),  # 整串（含 PU）查不到
            _search([_item("Noisy-Pair Robust Representation Alignment for "
                           "Positive-Unlabeled Learning",
                           doi="10.48550/arXiv.2510.01278")]),
        ])
        matches, _ = asyncio.run(sync.find_matches([row], recorder))
        assert len(matches) == 1, "标题缩写不同，但 arXiv 注册 DOI 逐字相等 → 应判定已入库"

    def test_doi_on_a_different_item_does_not_match(self):
        row = _row(title="Noisy-Pair Robust Representation Alignment for PU Learning",
                   arxiv_id="2510.01278")
        recorder = Recorder([
            _search([]),
            _search([_item("Noisy-Pair Robust Representation Alignment for "
                           "Positive-Unlabeled Learning",
                           doi="10.48550/arXiv.2599.99999")]),
        ])
        matches, _ = asyncio.run(sync.find_matches([row], recorder))
        assert matches == []
