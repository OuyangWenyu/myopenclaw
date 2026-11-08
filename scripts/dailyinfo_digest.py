#!/usr/bin/env python3
"""dailyinfo 每日全量简报 → 道元飞书私聊（Hermes cron --no-agent）。

脚本由 Hermes cron 以 no-agent 模式执行：stdout 原样投递到 `--deliver`
指定的飞书私聊；空 stdout = 静默；非 0 退出 = Hermes 投递失败告警。

数据来源是 dailyinfo 的只读 MCP（`dailyinfo_list_briefings` /
`dailyinfo_get_briefing`），与 dailyinfo 推送到 Discord 的正文是同一份
`briefing.body`（见 dailyinfo `scripts/publication/publishers.py`）。

用法:
    python3 dailyinfo_digest.py                 # 北京时间今天
    python3 dailyinfo_digest.py --date 2026-11-08

环境变量:
    DAILYINFO_MCP_URL   例如 http://host.docker.internal:8768/mcp
    DAILYINFO_MCP_KEY   dailyinfo keys.json 里的个人 key
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

# 固定 +8：容器没有 tzdata 也能算北京日期；Asia/Shanghai 无夏令时。
CST = timezone(timedelta(hours=8))

# 与 dailyinfo Discord 推送节奏一致：早报 → papers → arxiv。
CATEGORY_ORDER = ("ai_news", "code", "resource", "papers", "arxiv")

LIST_LIMIT = 100
BODY_PAGE_LIMIT = 20000  # get_briefing 服务端上限
MAX_PAGES = 50
STALE_CURSOR_RETRIES = 2
PLACEHOLDER_MARKERS = ("📭 过去", "无新内容")


class DigestError(RuntimeError):
    """带公开错误码的失败（错误码来自 dailyinfo MCP 的 QueryError 文本）。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Section:
    category: str
    kind: str  # body | placeholder | missing | error
    body: str = ""
    detail: str = ""


ToolCall = Callable[[str, dict[str, Any]], Awaitable[Any]]


def cst_date(now: datetime | None = None) -> str:
    """返回北京时间的日期字符串（YYYY-MM-DD）。

    cron 在 UTC 23:30 触发 = 北京次日 07:30，因此必须按 +8 取「今天」。
    naive datetime 直接拒绝，避免时区歧义。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("cst_date requires a timezone-aware datetime")
    return now.astimezone(CST).date().isoformat()


def is_placeholder_body(text: str) -> bool:
    """与 dailyinfo 的 is_placeholder 保持一致：短正文 + 两个标记。"""
    stripped = (text or "").strip()
    return len(stripped) < 200 and all(marker in stripped for marker in PLACEHOLDER_MARKERS)


def _payload(result: Any) -> dict[str, Any]:
    """从 CallToolResult 取 JSON payload；工具报错时抛 DigestError(code)。"""
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    payload = getattr(result, "structured_content", None)
    if payload is None:
        payload = getattr(result, "structuredContent", None)
    if payload is None:
        for block in getattr(result, "content", None) or []:
            text = getattr(block, "text", None)
            if text:
                try:
                    payload = json.loads(text)
                except (TypeError, ValueError):
                    payload = None
                break
    if is_error:
        detail = ""
        if isinstance(payload, dict):
            detail = str(payload.get("code") or payload.get("error") or "")
        if not detail:
            for block in getattr(result, "content", None) or []:
                detail = str(getattr(block, "text", "") or "").strip()
                if detail:
                    break
        raise DigestError(_short_code(detail) or "TOOL_ERROR")
    if not isinstance(payload, dict):
        raise DigestError("INVALID_PAYLOAD")
    return payload


def _short_code(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text[:120]


def _error_text(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text[:300]


async def _fetch_body_once(call_tool: ToolCall, briefing_id: str) -> str:
    parts: list[str] = []
    expected_total: int | None = None
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(MAX_PAGES):
        arguments: dict[str, Any] = {"briefing_id": briefing_id, "limit": BODY_PAGE_LIMIT}
        if cursor:
            arguments["cursor"] = cursor
        data = _payload(await call_tool("dailyinfo_get_briefing", arguments))
        body = data.get("body")
        if not isinstance(body, str):
            raise DigestError("BODY_INVALID")
        if expected_total is None and isinstance(data.get("body_total_chars"), int):
            expected_total = data["body_total_chars"]
        parts.append(body)
        cursor = data.get("next_cursor")
        if not cursor:
            joined = "".join(parts)
            if expected_total is not None and len(joined) != expected_total:
                raise DigestError("BODY_LENGTH_MISMATCH")
            return joined
        if cursor in seen_cursors:
            raise DigestError("CURSOR_REPEATED")
        seen_cursors.add(cursor)
    raise DigestError("PAGE_LIMIT_EXCEEDED")


async def fetch_body(call_tool: ToolCall, briefing_id: str) -> str:
    """读取一份简报全文；STALE_CURSOR 表示内容已变，从头重读。"""
    last: DigestError | None = None
    for _ in range(STALE_CURSOR_RETRIES + 1):
        try:
            return await _fetch_body_once(call_tool, briefing_id)
        except DigestError as exc:
            last = exc
            if exc.code != "STALE_CURSOR":
                raise
    assert last is not None
    raise last


def _briefing_ids(listing: dict[str, Any]) -> dict[str, str]:
    ids: dict[str, str] = {}
    # list 结果是 newest-first；同一类别同一天只有一份，setdefault 防重复。
    for row in listing.get("briefings") or []:
        if not isinstance(row, dict):
            continue
        category = row.get("category")
        briefing_id = row.get("id")
        if category in CATEGORY_ORDER and isinstance(briefing_id, str) and briefing_id:
            ids.setdefault(category, briefing_id)
    return ids


async def collect_digest(call_tool: ToolCall, day: str) -> list[Section]:
    """拉当天全部类别；先 list，再逐类 get。单类失败降级为 error section。"""
    listing = _payload(
        await call_tool(
            "dailyinfo_list_briefings",
            {
                "date_from": day,
                "date_to": day,
                "categories": list(CATEGORY_ORDER),
                "limit": LIST_LIMIT,
            },
        )
    )
    ids = _briefing_ids(listing)
    sections: list[Section] = []
    for category in CATEGORY_ORDER:
        briefing_id = ids.get(category)
        if not briefing_id:
            sections.append(Section(category, "missing"))
            continue
        try:
            body = await fetch_body(call_tool, briefing_id)
        except DigestError as exc:
            if exc.code == "NOT_FOUND":
                sections.append(Section(category, "missing"))
            else:
                sections.append(Section(category, "error", detail=exc.code))
            continue
        kind = "placeholder" if is_placeholder_body(body) else "body"
        sections.append(Section(category, kind, body=body))
    return sections


def render_digest(day: str, sections: Iterable[Section]) -> str:
    """纯渲染：分类顺序固定，正文逐字保留，缺失/占位/失败给出短提示。"""
    blocks: list[str] = [f"📮 **dailyinfo 每日简报 · {day}**"]
    for section in sections:
        if section.kind == "body":
            body = section.body.strip("\n")
            if body:
                blocks.append(f"**{section.category}**\n\n{body}")
        elif section.kind == "placeholder":
            blocks.append(f"📭 **{section.category}**：今日各源均无新内容")
        elif section.kind == "missing":
            blocks.append(f"📭 **{section.category}**：今日暂无简报")
        elif section.kind == "error":
            blocks.append(f"⚠️ **{section.category}**：拉取失败（{section.detail}）")
    return "\n\n---\n\n".join(blocks)


@asynccontextmanager
async def _mcp_session(url: str, key: str):
    """建立 MCP streamable-HTTP 会话；mcp/httpx2 延迟导入，host 单测无需安装。"""
    try:
        import httpx2 as httpx
    except ImportError:  # pragma: no cover - 旧版 mcp 环境
        import httpx  # type: ignore[no-redef]
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {key}"},
        timeout=httpx.Timeout(30.0, read=120.0),
    )
    async with client:
        async with streamable_http_client(url, http_client=client) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


async def _collect_with_session(url: str, key: str, day: str) -> list[Section]:
    async with _mcp_session(url, key) as session:

        async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
            return await session.call_tool(name, arguments)

        return await collect_digest(call_tool, day)


def _read_env() -> tuple[str, str]:
    url = (os.environ.get("DAILYINFO_MCP_URL") or "").strip()
    key = (os.environ.get("DAILYINFO_MCP_KEY") or "").strip()
    return url, key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dailyinfo 每日全量简报（cron no-agent 用）")
    parser.add_argument("--date", default=None, help="北京时间日期 YYYY-MM-DD（默认今天）")
    args = parser.parse_args(argv)

    try:
        day = args.date or cst_date()
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        print(f"dailyinfo digest: 日期参数不合法: {exc}", file=sys.stderr)
        return 2

    url, key = _read_env()
    if not url.startswith(("http://", "https://")) or len(key) < 32:
        print("dailyinfo digest: DAILYINFO_MCP_URL/KEY 未配置或格式不正确", file=sys.stderr)
        return 1

    try:
        sections = asyncio.run(_collect_with_session(url, key, day))
    except Exception as exc:  # noqa: BLE001 - cron 失败告警需要简短原因
        print(f"dailyinfo digest: {_error_text(exc)}", file=sys.stderr)
        return 1

    print(render_digest(day, sections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
