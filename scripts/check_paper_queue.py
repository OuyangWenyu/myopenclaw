#!/usr/bin/env python3
"""
check_paper_queue.py — 只读校验论文清单（paper-queue）

回答「清单现在是什么样」而**不经过模型、不依赖虾酱在线**：直接只读打开 SQLite。
这是本功能唯一"模型说了不算"的口子，也是 cron / 监控能消费的那个。

用法:
  python3 scripts/check_paper_queue.py            # 人类可读
  python3 scripts/check_paper_queue.py --json     # JSON（适合 cron/监控）

退出码:
  0 — 清单健康，或尚未初始化（没人写过不该报警）
  1 — 发现不一致
  2 — 脚本自身错误（库不可读 / 不是 SQLite 文件 / 表结构残缺）

环境变量:
  PAPER_QUEUE_DB   清单库路径（默认 ~/.myagentdata/paper-queue/queue.sqlite）
  TZ               "今天"的日界依据的时区（默认 Asia/Shanghai）
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

DB_PATH = Path(os.environ.get(
    "PAPER_QUEUE_DB",
    os.path.expanduser("~/.myagentdata/paper-queue/queue.sqlite"),
))
TZ_NAME = os.environ.get("TZ", "Asia/Shanghai")

# 哨兵：server 首次建库时在库旁边留下。用来区分「库被删了」与「从来没建过库」——
# 后者是正常的（没人用过），前者要报警。
# ⚠️ 这个后缀必须与 mcp_server.py 的 SENTINEL_SUFFIX 一致；有守卫跨文件比对。
SENTINEL_SUFFIX = ".initialized"
SENTINEL_PATH = Path(str(DB_PATH) + SENTINEL_SUFFIX)

TABLE = "paper_requests"

# 检查器依赖的列。缺列说明库来自更早的 schema 或别的工具 —— 必须报出来，
# 而不是让后面的查询抛异常或静默少读数据。
REQUIRED_COLUMNS = {
    "id", "request_key", "input_kind", "raw_input",
    "title", "doi", "doi_source", "arxiv_id", "url", "note",
    "requester", "requester_name", "channel_ref", "message_ref", "session_ref",
    "attribution_source", "attribution_ambiguous",
    "requested_at", "cancelled_at", "cancelled_by",
}

IDENTIFIER_COLUMNS = ("title", "doi", "arxiv_id", "url")
VALID_INPUT_KINDS = {"title", "doi", "arxiv", "url"}
VALID_DOI_SOURCES = {"user", "inferred"}
# 与 schema.sql 的 CHECK 保持一致。改枚举时**三处一起改**（schema.sql / 这里 /
# mcp_server.py 写入侧），漏一处就会对正常数据报假红 —— 真异常被淹没。
VALID_ATTRIBUTION_SOURCES = {"single", "batched"}

# 与 schema.sql 的 CHECK 保持一致：严格定宽 YYYY-MM-DDTHH:MM:SSZ。
# 带毫秒/偏移的值虽然"看着像 ISO"，却会让字典序范围查询静默漏记录。
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# =============================================================
# 1. 时间窗口
# =============================================================


def _tz():
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(TZ_NAME)
    except Exception:  # tzdata 缺失
        print(f"⚠️  无法加载时区 {TZ_NAME}，改用 UTC 计算日界", file=sys.stderr)
        return timezone.utc


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def window_bounds(now: datetime | None = None) -> dict[str, str]:
    """返回各窗口的起点的 ISO 字符串（用于 TEXT 字典序比较）。"""
    now = now or datetime.now(timezone.utc)
    tz = _tz()
    local_now = now.astimezone(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "today": _iso(local_midnight),
        "last_24h": _iso(now - timedelta(hours=24)),
        "last_7d": _iso(now - timedelta(days=7)),
    }


# =============================================================
# 2. 校验
# =============================================================


class ScriptError(Exception):
    """库读不了 / 不是 SQLite 文件 —— 属于脚本自身错误（退出码 2）。"""


def open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def collect(conn: sqlite3.Connection) -> tuple[list[dict], dict]:
    """逐条校验并统计。返回 (findings, counts)。"""
    findings: list[dict] = []
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})")}
    missing = REQUIRED_COLUMNS - cols
    if missing:
        # 列缺失时后面的查询会炸，直接以脚本错误终止更诚实
        raise ScriptError(f"{TABLE} 缺少列: {', '.join(sorted(missing))}")

    rows = list(conn.execute(f"SELECT * FROM {TABLE} ORDER BY requested_at"))
    seen_active: dict[str, int] = {}
    ambiguity = 0

    for row in rows:
        rid = row["id"]

        if not any(row[c] for c in IDENTIFIER_COLUMNS):
            findings.append({"kind": "no_identifier", "row_id": rid,
                             "detail": "title/doi/arxiv_id/url 全为空，无法认出这篇论文"})

        for field in ("requested_at", "cancelled_at"):
            value = row[field]
            if value is not None and not TIMESTAMP_RE.match(value):
                findings.append({"kind": "bad_timestamp", "row_id": rid,
                                 "detail": f"{field}={value!r} 非规范 ISO8601 UTC"})

        if (row["cancelled_at"] is None) != (row["cancelled_by"] is None):
            findings.append({"kind": "cancel_mismatch", "row_id": rid,
                             "detail": "cancelled_at 与 cancelled_by 未成对出现"})

        if row["input_kind"] not in VALID_INPUT_KINDS:
            findings.append({"kind": "bad_enum", "row_id": rid,
                             "detail": f"input_kind={row['input_kind']!r}"})
        if row["doi_source"] is not None and row["doi_source"] not in VALID_DOI_SOURCES:
            findings.append({"kind": "bad_enum", "row_id": rid,
                             "detail": f"doi_source={row['doi_source']!r}"})
        if (row["attribution_source"] is not None
                and row["attribution_source"] not in VALID_ATTRIBUTION_SOURCES):
            findings.append({"kind": "bad_enum", "row_id": rid,
                             "detail": f"attribution_source={row['attribution_source']!r}"})
        if row["attribution_ambiguous"] not in (0, 1):
            findings.append({"kind": "bad_enum", "row_id": rid,
                             "detail": f"attribution_ambiguous={row['attribution_ambiguous']!r}"})

        if row["cancelled_at"] is None:
            key = row["request_key"]
            if key in seen_active:
                findings.append({"kind": "duplicate_active_key", "row_id": rid,
                                 "detail": f"request_key={key!r} 与 id={seen_active[key]} 重复且都未撤销"})
            else:
                seen_active[key] = rid
            if row["attribution_ambiguous"] == 1:
                ambiguity += 1

    bounds = window_bounds()
    active_rows = [r for r in rows if r["cancelled_at"] is None]
    counts = {
        "total": len(rows),
        "active": len(active_rows),
        "cancelled": len(rows) - len(active_rows),
        "today": sum(1 for r in active_rows if r["requested_at"] >= bounds["today"]),
        "last_24h": sum(1 for r in active_rows if r["requested_at"] >= bounds["last_24h"]),
        "last_7d": sum(1 for r in active_rows if r["requested_at"] >= bounds["last_7d"]),
        "attribution_ambiguous": ambiguity,
    }
    return findings, counts


# =============================================================
# 3. 输出
# =============================================================


def emit_json(status: str, findings: list[dict], counts: dict) -> None:
    print(json.dumps({
        "status": status,
        "db": str(DB_PATH),
        "counts": counts,
        "findings": findings,
    }, ensure_ascii=False))


def zero_counts() -> dict:
    """计数未知时的占位（如指错了库）—— 人类可读分支要读这些键，不能给空字典。"""
    return {"total": 0, "active": 0, "cancelled": 0, "today": 0,
            "last_24h": 0, "last_7d": 0, "attribution_ambiguous": 0}


def emit_human(status: str, findings: list[dict], counts: dict) -> None:
    if status == "not_initialized":
        print("")
        print("📭 清单尚未初始化（库里还没有 paper_requests 表或文件不存在）")
        print(f"   期望路径: {DB_PATH}")
        print("")
        return

    if status == "ok":
        print("")
        print("✅ 论文清单健康")
    else:
        print("")
        print("🔴 论文清单存在不一致")

    print(f"   库文件:       {DB_PATH}")
    print(f"   未撤销:       {counts['active']} 条（今天 {counts['today']}，"
          f"24 小时 {counts['last_24h']}，7 天 {counts['last_7d']}）")
    print(f"   已撤销:       {counts['cancelled']} 条")
    print(f"   总计:         {counts['total']} 条")
    if counts["attribution_ambiguous"]:
        print(f"   ⚠️  归属歧义: {counts['attribution_ambiguous']} 条"
              f"（同一回合内有多人发言，请求人可能张冠李戴 —— 人工核对 message_ref）")
    if findings:
        print(f"   发现:         {len(findings)} 处")
        for f in findings[:20]:
            print(f"     · [{f['kind']}] id={f['row_id']} {f['detail']}")
        if len(findings) > 20:
            print(f"     … 其余 {len(findings) - 20} 处略（用 --json 看全量）")
    print("")


def main() -> int:
    as_json = "--json" in sys.argv

    if not DB_PATH.exists():
        # 哨兵（server 首次建库时留下）还在、库却没了 ⇒ 这是**被删**，不是"从没建过"。
        # 两者在此之前表现完全一样，删库因此不会触发任何告警。
        if SENTINEL_PATH.exists():
            findings = [{"kind": "queue_deleted", "row_id": None,
                         "detail": f"哨兵 {SENTINEL_PATH.name} 还在，但库文件不存在 —— "
                                   "队列像是被删了（可从备份恢复）"}]
            (emit_json if as_json else emit_human)("findings", findings, zero_counts())
            return 1
        (emit_json if as_json else emit_human)("not_initialized", [], {})
        return 0

    try:
        conn = open_ro(DB_PATH)
    except sqlite3.Error as exc:
        print(f"❌ 无法打开清单库 {DB_PATH}: {exc}", file=sys.stderr)
        return 2

    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            print(f"❌ {DB_PATH} 不是可读的 SQLite 数据库: {exc}", file=sys.stderr)
            return 2

        if TABLE not in tables:
            if tables:
                # 库是好的但指错了 —— 静默当作"空清单"才是真正危险的失败
                # 表名用 !r：它来自库文件，被投毒的库可以借它往终端/CI 日志里打转义序列
                findings = [{"kind": "wrong_database", "row_id": None,
                             "detail": f"{DB_PATH} 里没有 {TABLE} 表，但存在其它表 "
                                       f"({', '.join(repr(t) for t in sorted(tables)[:5])})"
                                       " —— 路径是否指错？"}]
                (emit_json if as_json else emit_human)("findings", findings, zero_counts())
                return 1
            (emit_json if as_json else emit_human)("not_initialized", [], {})
            return 0

        findings, counts = collect(conn)
    except ScriptError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"❌ 读取清单库失败: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    status = "findings" if findings else "ok"
    (emit_json if as_json else emit_human)(status, findings, counts)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
