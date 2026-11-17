#!/usr/bin/env python3
"""论文清单每日对账：已入库 Zotero 的条目自动撤销（Hermes cron --no-agent）。

脚本由 Hermes cron 以 no-agent 模式执行：stdout 原样投递到 `--deliver`
指定的飞书私聊；空 stdout = 静默（没有需要清理的条目）；非 0 退出 =
Hermes 投递失败告警。

判据（保守，宁可漏清、不可误清）：
  - 论文清单的活跃行（`cancelled_at IS NULL`；带道元 token 时只含 Owen 自己的行）
  - 按标题在 Zotero 库中查询：归一化后完全相等，或一方完整包含另一方且
    较短者覆盖较长者 >= 90%
  - 主查询用整条标题。Zotero 的 `q`（`titleCreatorYear`）是**子串**匹配，整串里
    只要有一个字符的 Unicode 变体不同（U+2010 连字符、en dash、弯引号）就查不到
    —— 归一化后其实完全相等。所以查不到时退化成「标点无关的连续词串」再查
    （2026-11-17：183/184/185 三篇因此在库里躺了两周没被清理）
  - DOI 逐字相等也算命中（行有 `doi`，或用 arXiv 号推出的 `10.48550/arXiv.<id>`）：
    DOI 比标题更权威，且不和「标题前缀」规则冲突
  - arXiv 行额外用 arXiv 号查询（PDF 附件名 `arXiv.<id>.pdf` 会命中），
    解析附件父条目后比对其标题
  - **任一查询失败 → 本次不撤销任何条目**（fail closed）

「清理」= 调 `paper_queue_cancel`（撤销，不物理删行）：保留历史、可重新入队。
DOI 在当前 `zotero_search`（Zotero quick search，titleCreatorYear）下**搜不到**，
只作为返回字段参与比对，不参与检索。

用法:
    python3 paper_queue_sync.py --dry-run   # 只打印将清理的条目，不撤销
    python3 paper_queue_sync.py             # 对账并撤销

环境变量:
    PAPER_QUEUE_MCP_URL        默认 http://paper-queue-mcp:8003/mcp
    PAPER_QUEUE_DAOYUAN_TOKEN  必填（服务端绑定固定 actor Owen）
    ZOTERO_MCP_URL             默认 http://zotero-mcp:8002/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import sys
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

# 固定 +8：容器没有 tzdata 也能算北京日期；Asia/Shanghai 无夏令时。
CST = timezone(timedelta(hours=8))

DEFAULT_PAPER_QUEUE_URL = "http://paper-queue-mcp:8003/mcp"
DEFAULT_ZOTERO_URL = "http://zotero-mcp:8002/mcp"
LIST_LIMIT = 500          # paper-queue 服务端单次响应的最大行数
SEARCH_LIMIT = 20         # 每个标题取回足够候选即可；命中不依赖排序
QUERY_MAX_CHARS = 300     # 标题查询截断，匹配仍用完整标题
MIN_COVERAGE = 0.9        # 包含匹配时较短者至少覆盖较长者的比例
CHILD_ITEM_TYPES = frozenset({"attachment", "note", "annotation"})
# 只认学术条目：网页/博客条目（浏览器插件保存）即使标题相同也不算「已入库」。
ACADEMIC_ITEM_TYPES = frozenset({
    "journalArticle", "conferencePaper", "preprint", "book", "bookSection",
    "thesis", "report", "manuscript", "dataset", "standard",
})
OK_CANCEL_STATUSES = frozenset({"cancelled", "already_cancelled"})
_ARXIV_ID_RE = re.compile(r"^(?:arxiv:)?\s*(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)


class SyncError(RuntimeError):
    """带可公开原因的失败（写 stderr，退出码 1）。"""


@dataclass(frozen=True)
class Match:
    row: dict[str, Any]
    zotero_key: str
    zotero_title: str


@dataclass(frozen=True)
class CancelOutcome:
    row: dict[str, Any]
    status: str
    zotero_key: str = ""


@dataclass(frozen=True)
class SyncReport:
    matches: list[Match]
    outcomes: list[CancelOutcome]
    dry_run: bool
    warnings: list[str]


ToolCall = Callable[[str, dict[str, Any]], Awaitable[Any]]


def cst_date(now: datetime | None = None) -> str:
    """返回北京时间的日期字符串（YYYY-MM-DD）。naive datetime 直接拒绝。"""
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("cst_date requires a timezone-aware datetime")
    return now.astimezone(CST).date().isoformat()


# =============================================================
# 标题匹配
# =============================================================


_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*\b[^>]*>")
_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(text: str | None) -> str:
    """把标题折叠成可比较的形态。

    HTML 实体/标签（Zotero 标题里常见 ``&lt;i&gt;``）、全角、大小写、标点、
    Unicode 连字符变体（``‐`` / ``–`` / ``—``）统一；CJK 字符保留。
    """
    value = html.unescape(text or "")
    value = _HTML_TAG_RE.sub(" ", value)
    value = unicodedata.normalize("NFKC", value)
    value = value.casefold()
    value = _NON_WORD_RE.sub(" ", value)
    return _WS_RE.sub(" ", value).strip()


def titles_match(a: str | None, b: str | None) -> bool:
    """精确相等，或较短者是较长者的**前缀**且覆盖 >= 90%。

    - 不做模糊相似度：误判会静默丢掉真实的下载请求，代价远高于漏清。
    - 只允许前缀（不允许任意包含）：``Erratum: <title>`` / ``Reply to: <title>``
      这类条目会把原论文的标题当后缀包住，任意包含会误清原论文的请求。
    """
    left, right = normalize_title(a), normalize_title(b)
    if not left or not right:
        return False
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if len(short) < len(long) * MIN_COVERAGE:
        return False
    return long.startswith(short)


# =============================================================
# MCP 结果与调用
# =============================================================


def _first_text(result: Any) -> str:
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            return str(text).strip()
    return ""


def _short_error(payload: Any) -> str:
    if isinstance(payload, dict):
        context = str(payload.get("context") or "tool")
        message = str(payload.get("message") or payload.get("error") or "")
        if message:
            return f"{context}: {message}"[:300]
    text = " ".join(str(payload or "").split())
    return text[:300] or "TOOL_ERROR"


def _error_text(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text[:300]


def _payload(result: Any) -> dict[str, Any]:
    """从 MCP tool result 取 JSON payload；工具报错（含 Zotero 的 error 信封）抛 SyncError。"""
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    payload = getattr(result, "structured_content", None)
    if payload is None:
        payload = getattr(result, "structuredContent", None)
    # MCP SDK 会把「返回 str 的工具」包成 {"result": "<json 字符串>"}。
    # paper-queue 与 zotero 的工具都返回 JSON 字符串，必须拆掉这层信封；
    # 拆不动就报错 —— 退回空结果会被读成「没有要清理的条目」，从而静默失效。
    if (
        isinstance(payload, dict)
        and len(payload) == 1
        and isinstance(payload.get("result"), str)
    ):
        try:
            payload = json.loads(payload["result"])
        except (TypeError, ValueError) as exc:
            raise SyncError("INVALID_PAYLOAD: 无法解析 result 信封") from exc
    if payload is None:
        text = _first_text(result)
        if text:
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                payload = None
    if is_error:
        # FastMCP 的工具异常常常只有文本块（没有结构化内容）；别把原因吞成 TOOL_ERROR。
        raise SyncError(_short_error(payload) if payload is not None else _short_error(_first_text(result)))
    if not isinstance(payload, dict):
        raise SyncError("INVALID_PAYLOAD")
    # Zotero MCP 的失败是 HTTP 200 + JSON 错误信封，而不是 MCP isError。
    if payload.get("error") is True:
        raise SyncError(_short_error(payload))
    return payload


def _row_key(row: dict[str, Any]) -> str:
    return str(row.get("request_key") or "").strip()


def _row_title(row: dict[str, Any]) -> str:
    return str(row.get("title") or "").strip()


def _query_for(title: str) -> str:
    return _WS_RE.sub(" ", title).strip()[:QUERY_MAX_CHARS]


# Zotero 的 q（titleCreatorYear）是子串匹配，对**标点原样**敏感：库里存的是
# `Physics‐Informed`（U+2010）而清单里是 `Physics-Informed`（ASCII 连字符），
# 整串查询就 0 结果 —— 归一化之后两者明明相等。所以主查询落空后退化成
# 「标点无关的**连续**词串」（连续才能保证是库内标题的子串），判定仍走
# titles_match（检索放宽、判定不放宽）。
_PLAIN_WORD_RE = re.compile(r"[0-9A-Za-z]+\Z")
_MIN_RUN_WORDS = 3      # 太短的词串会把目标挤到 20 条之外，白花请求
_MAX_NEEDLES = 2


def _search_needles(title: str) -> list[str]:
    """整条标题查不到时的备用检索词：标点无关的**连续**词串（长串优先）。

    只取连续词串、不取散词：Zotero 的 q 是子串匹配，散词拼起来不在库内标题里。
    """
    words = _WS_RE.sub(" ", title or "").strip().split(" ")
    if not words or words == [""]:
        return []

    runs: list[str] = []
    current: list[str] = []
    for word in words:
        if _PLAIN_WORD_RE.match(word):
            current.append(word)
        else:
            if len(current) >= _MIN_RUN_WORDS:
                runs.append(" ".join(current))
            current = []
    if len(current) >= _MIN_RUN_WORDS:
        runs.append(" ".join(current))
    # 长词串更特异，命中率最高
    runs.sort(key=len, reverse=True)

    primary = _query_for(title)
    needles: list[str] = []
    for run in runs:
        needle = run.strip()[:QUERY_MAX_CHARS]
        if needle and needle != primary and needle not in needles:
            needles.append(needle)
        if len(needles) >= _MAX_NEEDLES:
            break
    return needles


def _row_doi(row: dict[str, Any]) -> str:
    """行的 DOI；arXiv 行用其注册 DOI 兜底。只做逐字比较，不做前缀/模糊。"""
    doi = str(row.get("doi") or "").strip()
    if doi:
        return doi.lower()
    arxiv_id = _arxiv_query(str(row.get("arxiv_id") or ""))
    return f"10.48550/arxiv.{arxiv_id}".lower() if arxiv_id else ""


def _doi_matches(row: dict[str, Any] | None, item: dict[str, Any]) -> bool:
    if row is None:
        return False
    wanted = _row_doi(row)
    if not wanted:
        return False
    found = str(item.get("DOI") or item.get("doi") or "").strip().lower()
    return bool(found) and found == wanted


def _is_child(item: dict[str, Any]) -> bool:
    return str(item.get("itemType") or "").lower() in CHILD_ITEM_TYPES


def _is_academic(item: dict[str, Any]) -> bool:
    return str(item.get("itemType") or "") in ACADEMIC_ITEM_TYPES


def _arxiv_query(value: str) -> str:
    """``arXiv:2610.15267v2`` → ``2610.15267``；Zotero 只认裸号（附件名里是裸号）。"""
    text = (value or "").strip()
    match = _ARXIV_ID_RE.match(text)
    return match.group(1) if match else text


async def fetch_rows(call_paper_queue: ToolCall) -> list[dict[str, Any]]:
    payload = _payload(
        await call_paper_queue(
            "paper_queue_list", {"window": "all", "limit": LIST_LIMIT}
        )
    )
    if payload.get("truncated"):
        # 截断时只核对到一部分，而排序是 newest-first —— 最老的（最可能已入库的）
        # 反而会被永久漏掉。宁可响亮失败，也不要静默漏清。
        raise SyncError("清单响应被截断（超过服务端单次上限），拒绝只核对一部分")
    items = payload.get("items")
    if not isinstance(items, list):
        raise SyncError("INVALID_PAYLOAD: paper_queue_list 缺少 items")
    return [row for row in items if isinstance(row, dict)]


async def _search_zotero(call_zotero: ToolCall, query: str) -> list[dict[str, Any]]:
    payload = _payload(
        await call_zotero("zotero_search", {"query": query, "limit": SEARCH_LIMIT})
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise SyncError("INVALID_PAYLOAD: zotero_search 缺少 items")
    return [item for item in items if isinstance(item, dict)]


async def _parent_item(call_zotero: ToolCall, item: dict[str, Any]) -> dict[str, Any] | None:
    """附件 → 父条目。单条解析失败只跳过该候选（不中断整轮对账）。"""
    item_key = str(item.get("key") or "").strip()
    if not item_key:
        return None
    try:
        full = _payload(await call_zotero("zotero_get_item", {"item_key": item_key}))
    except SyncError:
        return None
    data = full.get("data") if isinstance(full.get("data"), dict) else full
    parent_key = str(data.get("parentItem") or "").strip()
    if not parent_key:
        return None
    try:
        parent_payload = _payload(
            await call_zotero("zotero_get_item", {"item_key": parent_key})
        )
    except SyncError:
        return None
    parent = (
        parent_payload.get("data")
        if isinstance(parent_payload.get("data"), dict)
        else parent_payload
    )
    return {
        "key": parent.get("key") or parent_key,
        "title": parent.get("title") or "",
        "itemType": parent.get("itemType") or "",
        "DOI": parent.get("DOI") or "",
    }


def _matches(title: str, item: dict[str, Any], row: dict[str, Any] | None) -> bool:
    """条目是否可认定为目标论文：学术类型 +（标题匹配 或 DOI 逐字相等）。"""
    if not _is_academic(item):
        return False
    if titles_match(title, str(item.get("title") or "")):
        return True
    return _doi_matches(row, item)


def _matching_item(
    title: str, items: list[dict[str, Any]], row: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    for item in items:
        if _matches(title, item, row):
            return item
    return None


async def _matching_via_children(
    title: str,
    items: list[dict[str, Any]],
    call_zotero: ToolCall,
    row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for item in items:
        if _is_child(item):
            parent = await _parent_item(call_zotero, item)
            if parent is not None and _matches(title, parent, row):
                return parent
        elif _matches(title, item, row):
            return item
    return None


async def find_matches(
    rows: list[dict[str, Any]], call_zotero: ToolCall
) -> tuple[list[Match], list[str]]:
    """逐行核对 Zotero。任何搜索失败都会抛出 SyncError（调用方不得撤销任何条目）。"""
    matches: list[Match] = []
    warnings: list[str] = []

    for row in rows:
        title = _row_title(row)
        request_key = _row_key(row)
        if not title or not request_key:
            warnings.append(f"跳过缺少标题/request_key 的行 id={row.get('id')}")
            continue

        primary = _query_for(title)
        hit = _matching_item(title, await _search_zotero(call_zotero, primary), row)
        # 主查询是子串匹配、对标点原样敏感：整串查不到时用标点无关的词串再查，
        # 判定仍旧走 titles_match / DOI（搜索放宽、判定不放宽）。
        if hit is None:
            for needle in _search_needles(title):
                if needle == primary:
                    continue
                hit = _matching_item(
                    title, await _search_zotero(call_zotero, needle), row
                )
                if hit is not None:
                    break

        arxiv_id = str(row.get("arxiv_id") or "").strip()
        if hit is None and arxiv_id:
            candidates = await _search_zotero(call_zotero, _arxiv_query(arxiv_id))
            hit = await _matching_via_children(title, candidates, call_zotero, row)

        if hit is not None:
            matches.append(
                Match(
                    row=row,
                    zotero_key=str(hit.get("key") or ""),
                    zotero_title=str(hit.get("title") or ""),
                )
            )

    return matches, warnings


async def apply_cancels(
    matches: list[Match], call_paper_queue: ToolCall
) -> list[CancelOutcome]:
    """逐条撤销；单条失败只记录，不阻断其余条目。"""
    outcomes: list[CancelOutcome] = []
    for match in matches:
        request_key = _row_key(match.row)
        try:
            payload = _payload(
                await call_paper_queue(
                    "paper_queue_cancel", {"request_key": request_key}
                )
            )
            status = str(payload.get("status") or "unknown")
        except Exception as exc:  # noqa: BLE001 - 单条失败不应中断整批
            status = f"error: {_error_text(exc)}"
        outcomes.append(
            CancelOutcome(row=match.row, status=status, zotero_key=match.zotero_key)
        )
    return outcomes


# =============================================================
# 渲染
# =============================================================


def _match_line(title: str, zotero_key: str) -> str:
    return f"- {title}（Zotero {zotero_key}）" if zotero_key else f"- {title}"


def render_report(report: SyncReport, *, day: str) -> str:
    """空字符串 = 无需投递（Hermes 静默）。"""
    if report.dry_run:
        if not report.matches:
            return ""
        lines = [
            f"🧪 **论文清单自动清理（dry-run）· {day}**",
            "",
            f"将清理 {len(report.matches)} 篇（已入库 Zotero，未实际撤销）：",
        ]
        lines += [
            _match_line(_row_title(match.row), match.zotero_key)
            for match in report.matches
        ]
        return "\n".join(lines)

    succeeded = [o for o in report.outcomes if o.status in OK_CANCEL_STATUSES]
    failed = [o for o in report.outcomes if o.status not in OK_CANCEL_STATUSES]
    if not succeeded and not failed:
        return ""

    lines = [f"🧹 **论文清单自动清理 · {day}**"]
    if succeeded:
        lines += ["", f"已入库 Zotero，移出待办 {len(succeeded)} 篇："]
        lines += [
            _match_line(_row_title(o.row), o.zotero_key) for o in succeeded
        ]
    if failed:
        lines += ["", f"⚠️ {len(failed)} 篇未能移出（下次自动重试）："]
        lines += [
            f"- {_row_title(o.row)}（{o.status}）" for o in failed
        ]
    return "\n".join(lines)


# =============================================================
# MCP 会话
# =============================================================


@asynccontextmanager
async def _mcp_session(url: str, token: str | None):
    """建一条 streamable-HTTP 会话；mcp/httpx 延迟导入，host 单测无需安装。"""
    try:
        import httpx2 as httpx
    except ImportError:  # pragma: no cover - 旧版 mcp 环境
        import httpx  # type: ignore[no-redef]
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    client = httpx.AsyncClient(
        headers=headers, timeout=httpx.Timeout(30.0, read=120.0)
    )
    async with client:
        async with streamable_http_client(url, http_client=client) as streams:
            # Hermes venv 的 SDK 产出 2 元组；新版产出 3 元组 —— 下标解包两种都兼容。
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session


async def _run(
    paper_queue_url: str,
    token: str,
    zotero_url: str,
    *,
    dry_run: bool,
    session_factory=_mcp_session,
) -> SyncReport:
    # 顺序开闭会话：取列表 → 查 Zotero → 撤销，不并发持有两条流。
    async with session_factory(paper_queue_url, token) as pq:
        rows = await fetch_rows(pq.call_tool)

    async with session_factory(zotero_url, None) as zotero:
        matches, warnings = await find_matches(rows, zotero.call_tool)

    outcomes: list[CancelOutcome] = []
    if not dry_run and matches:
        async with session_factory(paper_queue_url, token) as pq:
            outcomes = await apply_cancels(matches, pq.call_tool)

    return SyncReport(matches=matches, outcomes=outcomes, dry_run=dry_run, warnings=warnings)


# =============================================================
# main
# =============================================================


def _read_env() -> tuple[str, str, str]:
    paper_queue_url = (
        (os.environ.get("PAPER_QUEUE_MCP_URL") or "").strip() or DEFAULT_PAPER_QUEUE_URL
    )
    token = (os.environ.get("PAPER_QUEUE_DAOYUAN_TOKEN") or "").strip()
    zotero_url = (os.environ.get("ZOTERO_MCP_URL") or "").strip() or DEFAULT_ZOTERO_URL
    return paper_queue_url, token, zotero_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="论文清单每日对账：已入库 Zotero 的条目自动撤销（cron no-agent 用）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印将清理的条目，不调用撤销"
    )
    args = parser.parse_args(argv)

    paper_queue_url, token, zotero_url = _read_env()
    if not paper_queue_url.startswith(("http://", "https://")):
        print("paper-queue sync: PAPER_QUEUE_MCP_URL 格式不正确", file=sys.stderr)
        return 1
    if len(token) < 32:
        print(
            "paper-queue sync: PAPER_QUEUE_DAOYUAN_TOKEN 未配置或格式不正确",
            file=sys.stderr,
        )
        return 1
    if not zotero_url.startswith(("http://", "https://")):
        print("paper-queue sync: ZOTERO_MCP_URL 格式不正确", file=sys.stderr)
        return 1

    try:
        report = asyncio.run(
            _run(paper_queue_url, token, zotero_url, dry_run=args.dry_run)
        )
    except Exception as exc:  # noqa: BLE001 - cron 失败告警需要简短原因
        print(f"paper-queue sync: {_error_text(exc)}", file=sys.stderr)
        return 1

    for warning in report.warnings:
        print(f"paper-queue sync: {warning}", file=sys.stderr)

    text = render_report(report, day=cst_date())
    if text:
        print(text)

    failed = [o for o in report.outcomes if o.status not in OK_CANCEL_STATUSES]
    if failed:
        print(f"paper-queue sync: {len(failed)} 篇撤销失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
