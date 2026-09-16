#!/usr/bin/env python3
"""
paper-queue MCP server (stdio) — 论文清单的写入与查询口。

由 OpenClaw 在 openclaw-gateway 容器内自行拉起（见 openclaw.json 的 mcp.servers）。
**纯标准库**：openclaw 是 stock 镜像，装不了 pip 包，所以 JSON-RPC 是手写的
（stdio 传输 = 每行一个 JSON 对象，非 LSP 那种 Content-Length 分帧）。

三条硬规矩（改动前先读 docs/paper-queue.md）:
  1. **请求人只认宿主注入的 actor_id** —— 插件在 before_tool_call 里注入，模型碰不到。
     取不到就拒绝写入（fail closed），绝不用模型填的名字兜底。
  2. **request_key 只从"用户实际说了什么"派生** —— 虾酱推断出的 DOI（doi_source='inferred'）
     不参与，否则一个幻觉 DOI 会铸出错误的唯一键并污染去重。
  3. **时间窗口一律服务端算** —— 模型不做日期运算，"今天"的日界按容器 TZ。

环境变量:
  PAPER_QUEUE_DB  清单库路径（默认 /home/node/.myagentdata/paper-queue/queue.sqlite）
  TZ              "今天"的日界时区（默认 Asia/Shanghai）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

DEFAULT_DB = "/home/node/.myagentdata/paper-queue/queue.sqlite"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "paper-queue"
SERVER_VERSION = "0.1.0"

TABLE = "paper_requests"
# 必须与 schema.sql 的 `PRAGMA user_version` 一致。改表结构时两边一起 +1，
# 否则老库不会被迁移（见 _migrate_if_stale）。
SCHEMA_VERSION = 2
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# 与 schema.sql 的 CHECK 同一条规则：严格定宽 YYYY-MM-DDTHH:MM:SSZ。
# 写侧由 DB 兜底，读侧（since/until）必须自己挡。
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
ARXIV_RE = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)
WINDOWS = ("today", "24h", "7d", "all")

MAX_LIMIT = 500


# =============================================================
# 1. 连接与 schema
# =============================================================


def resolve_db() -> Path:
    """每次调用时读环境变量 —— 便于宿主侧测试直接改 env，不必重载模块。"""
    return Path(os.environ.get("PAPER_QUEUE_DB", DEFAULT_DB))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path else resolve_db()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    _migrate_if_stale(conn)
    conn.executescript(SCHEMA_PATH.read_text())


def _canonical_table_ddl(name: str) -> str:
    """从 schema.sql 里取出建表语句并改个表名 —— 保持 schema.sql 是唯一事实来源。"""
    match = re.search(r"CREATE TABLE IF NOT EXISTS\s+paper_requests(.*?\n)\)",
                      SCHEMA_PATH.read_text(), re.S)
    if not match:
        raise RuntimeError("schema.sql 里找不到 paper_requests 的建表语句")
    return f"CREATE TABLE {name}{match.group(1)}\n)"


def _migrate_if_stale(conn: sqlite3.Connection) -> None:
    """把老版本建的库升到当前 schema。

    SQLite **不能修改 CHECK 约束**，所以枚举一变就只能重建表。这个迁移必须在装机
    那一刻自动跑完：漏了的话，老库会拒绝新枚举值，而 add_items 会把它归类成
    invalid —— 用户以为记下了，库里一行都没有。
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
        return    # 全新库：schema.sql 会建好

    staging = f"{TABLE}__migrating"
    conn.execute("BEGIN")
    try:
        conn.execute(f"DROP TABLE IF EXISTS {staging}")
        conn.execute(_canonical_table_ddl(staging))
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({staging})")]
        select_list = []
        for column in columns:
            if column == "attribution_source":
                # v1 的 'message_id' 标签在生产里恒为常量（实际走的是会话兜底），
                # 所以一律降级成 'session_latest' —— 保守、诚实，不假装当时是精确绑定。
                select_list.append(
                    "CASE attribution_source"
                    " WHEN 'message_id' THEN 'session_latest'"
                    " WHEN 'session_fallback' THEN 'session_latest'"
                    " ELSE attribution_source END")
            else:
                select_list.append(column)
        conn.execute(
            f"INSERT INTO {staging} ({', '.join(columns)})"
            f" SELECT {', '.join(select_list)} FROM {TABLE}")
        conn.execute(f"DROP TABLE {TABLE}")
        conn.execute(f"ALTER TABLE {staging} RENAME TO {TABLE}")
        conn.commit()
        print(f"ℹ️  paper-queue: 库已从 schema v{version} 迁移到 v{SCHEMA_VERSION}",
              file=sys.stderr)
    except Exception:
        conn.rollback()
        raise


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(TS_FORMAT)


# =============================================================
# 2. 标识与去重键
# =============================================================


def normalize_title(raw: str) -> str:
    """小写 + 折叠空白 —— 标题类去重的唯一归一化（已知很弱，契约允许重复）。"""
    return re.sub(r"\s+", " ", (raw or "").strip()).lower()


def _strip_doi_prefix(raw: str) -> str:
    s = (raw or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    if s.lower().startswith("doi:"):
        s = s[4:]
    return s.strip()


def _strip_arxiv_prefix(raw: str) -> str:
    return (raw or "").strip()


def detect_kind(raw: str) -> str:
    """判断用户给的是哪种标识（仅用于描述 input_kind，不决定去重键）。"""
    s = (raw or "").strip()
    if DOI_RE.match(_strip_doi_prefix(s)):
        return "doi"
    if ARXIV_RE.match(_strip_arxiv_prefix(s)):
        return "arxiv"
    if s.lower().startswith(("http://", "https://")):
        return "url"
    return "title"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_url(raw: str) -> str:
    parts = urlsplit((raw or "").strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path, parts.query, ""))  # 丢 fragment


def canonical_key(kind: str, raw: str) -> str:
    if kind == "doi":
        return "doi:" + _strip_doi_prefix(raw).lower()
    if kind == "arxiv":
        m = ARXIV_RE.match(_strip_arxiv_prefix(raw))
        return "arxiv:" + (m.group(1) if m else _strip_arxiv_prefix(raw).lower())
    if kind == "url":
        return "url:" + _sha256(_normalize_url(raw))
    return "title:" + _sha256(normalize_title(raw))


# =============================================================
# 3. 时间窗口
# =============================================================


def _tz():
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(os.environ.get("TZ", "Asia/Shanghai"))
    except Exception:
        print("⚠️  paper-queue: 无法加载时区，改用 UTC 计算日界", file=sys.stderr)
        return timezone.utc


def parse_window(window=None, since=None, until=None, now=None):
    """返回 (start_iso|None, end_iso|None) —— None 表示该端不限。"""
    if since or until:
        # 显式边界也要过同一条严格格式校验：范围查询按 TEXT 字典序比较，
        # '2026-9-16T00:00:00Z'（月份没补零）或带偏移的值都不会报错，
        # 只会**静默返回空列表**——那是最难查的一类故障。
        for label, value in (("since", since), ("until", until)):
            if value is None:
                continue
            if not isinstance(value, str) or not TS_RE.match(value):
                raise ValueError(
                    f"{label} 必须是严格 ISO8601 UTC（形如 2026-09-16T00:00:00Z），收到 {value!r}")
        return (since, until)

    if window is not None and not isinstance(window, str):
        raise ValueError(f"window 必须是字符串，收到 {type(window).__name__}")
    key = (window or "all").strip().lower()
    if key == "all":
        return (None, None)
    if key not in WINDOWS:
        raise ValueError(f"未知的时间窗口 {window!r}，可选: {', '.join(WINDOWS)}")

    now = now or datetime.now(timezone.utc)
    if key == "today":
        local = now.astimezone(_tz())
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return (now_iso(midnight), None)
    if key == "24h":
        return (now_iso(now - timedelta(hours=24)), None)
    return (now_iso(now - timedelta(days=7)), None)


# =============================================================
# 4. 工具实现
# =============================================================


def _actor_id(actor: dict | None) -> str:
    return str((actor or {}).get("actor_id") or "").strip()


def _require_actor_id(actor: dict | None) -> str:
    """写路径专用的身份校验：非空且格式像 Discord 雪花。"""
    value = _actor_id(actor)
    if not value:
        raise ValueError("缺少宿主注入的请求人身份（actor_id），拒绝写入")
    if not ACTOR_ID_RE.match(value):
        raise ValueError(f"请求人身份格式不合法（{value[:40]!r}），拒绝写入")
    return value


ACTOR_SECRET = os.environ.get("PAPER_QUEUE_ACTOR_SECRET", "").strip()

# 签名覆盖的身份字段。插件（index.ts 的 sign()）必须用完全相同的顺序与分隔符 ——
# 两边不一致的表现是"每次写入都被拒"，而不是悄悄放行（fail closed 的好处）。
ACTOR_SIGNED_FIELDS = ("actor_id", "session_ref", "actor_message")
# 签名还必须覆盖**请求内容**：注入的 actor_* 会被写进模型可见的会话记录，只签身份
# 的话签名就成了一枚可重放的 bearer 值（模型在插件缺席时拿见过的四元组 + 任意内容重放）。
ITEM_SIGNED_FIELDS = ("title", "doi", "doi_source", "arxiv_id", "url", "note", "raw_input")

# Discord 雪花 ID。加格式校验是为了挡掉垃圾 requester（实测 actor_id 传对象会落库成
# "{'x': 1}"），也让"伪造"至少得伪造得像样。
ACTOR_ID_RE = re.compile(r"^\d{10,25}$")

# 体量上限：入参来自模型，没有上限就等于允许它把几 MB 文本灌进库（还会被热备上云、
# 被消费方读走）。
MAX_ITEMS = 50
MAX_FIELD_CHARS = 2000
MAX_SIGNED_ITEMS = 50


def _signed_item_value(item: dict, field: str) -> str:
    """只在**两边都是字符串**时按原文签；其它类型一律记空串，保证跨语言一致。"""
    value = item.get(field)
    return value if isinstance(value, str) else ""


def signable_payload(tool: str, args: dict) -> str:
    """本次请求内容的规范字符串（签名要覆盖它）。"""
    if tool == "paper_queue_cancel":
        return str(args.get("request_key") or "")
    items = args.get("items")
    if not isinstance(items, list):
        return ""
    lines = []
    for item in items[:MAX_SIGNED_ITEMS]:
        if not isinstance(item, dict):
            lines.append("")
            continue
        lines.append("|".join(f"{field}={_signed_item_value(item, field)}"
                              for field in ITEM_SIGNED_FIELDS))
    return "\n".join(lines)


def actor_signature_error(tool: str, args: dict) -> str | None:
    """校验归属签名；签不对就拒绝写入。

    这一层防的**不是"读"而是"算"**：密钥就写在 openclaw.json 里，模型读得到；但它
    算不出 HMAC。于是"身份注入插件没加载"这种失败会变成**响亮的拒绝写入**，而不是
    静默采信模型自己填的身份 —— 后者伪造出的行与真实的行在库里完全无法区分。

    **密钥缺失时拒绝写入**（不静默跳过）：否则任何一次"env 被删/改名"都会让防护静默
    消失，而插件那边还在签名——看起来一切正常。
    """
    if not ACTOR_SECRET:
        return ("未配置归属签名密钥（PAPER_QUEUE_ACTOR_SECRET），拒绝写入 —— "
                "请运行 ./scripts/start.sh 完成装机")
    provided = args.get("actor_sig")
    if not isinstance(provided, str) or not provided:
        return "缺少归属签名（actor_sig）—— 身份注入插件可能未加载，拒绝写入"
    material = "|".join(str(args.get(field) or "") for field in ACTOR_SIGNED_FIELDS)
    material += "|" + signable_payload(tool, args)
    expected = hmac.new(ACTOR_SECRET.encode("utf-8"), material.encode("utf-8"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        return "归属签名校验失败，拒绝写入"
    return None


def _text(value) -> str | None:
    """把入参规整成去空白字符串；非标量（dict/list/bool）一律当空。

    工具入参是**模型生成的**，而 OpenClaw 对 MCP 工具的参数**不做 schema 校验**
    （文档原话：MCP 的 schema "deferred to their owning execution boundary"，
    也就是本进程）。所以这里必须自己挡：形状错了顶多这一条 invalid，
    **绝不能抛出去**——异常会冒泡出 main()，stdio 进程直接退出，
    该会话余下的所有工具调用一起完蛋。
    """
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    if not text:
        return None
    # 超长就截断而不是报错：模型把整段摘要塞进 note 是常见操作，不该因此丢掉整条请求。
    return text[:MAX_FIELD_CHARS]


def _resolve_item(item) -> tuple[dict, str | None]:
    """把一条入参规约成落库字段。返回 (fields, error)。"""
    if not isinstance(item, dict):
        return {}, f"每个 item 必须是对象，收到的是 {type(item).__name__}"

    title = _text(item.get("title"))
    doi = _text(item.get("doi"))
    arxiv_id = _text(item.get("arxiv_id"))
    url = _text(item.get("url"))

    # 模型很可能把 DOI / arXiv ID / 链接直接塞进 title 字段（用户原话就是这么说的）。
    # 认一下再落库，否则会拿一串 DOI 去算标题哈希 —— 去重彻底失效，同篇论文每次
    # 报的措辞不同就成了不同 key。
    doi_source_hint = None
    if title and not (doi or arxiv_id or url):
        detected = detect_kind(title)
        if detected == "doi":
            doi, doi_source_hint, title = title, "user", None
        elif detected == "arxiv":
            arxiv_id, title = title, None
        elif detected == "url":
            url, title = title, None

    # 保守默认：没声明来源的 DOI 一律视为虾酱推断的 —— 因为"模型擅自补了个 DOI"
    # 恰恰就是幻觉发生的那条路径。要让 DOI 参与去重，必须显式声明 doi_source='user'。
    doi_source = _text(item.get("doi_source")) or doi_source_hint or ("inferred" if doi else None)
    if doi_source not in (None, "user", "inferred"):
        return {}, f"doi_source 只能是 'user' 或 'inferred'，实为 {doi_source!r}"

    user_doi = doi if doi_source == "user" else None

    if user_doi:
        kind, key_source = "doi", user_doi
    elif arxiv_id:
        kind, key_source = "arxiv", arxiv_id
    elif url:
        kind, key_source = "url", url
    elif title:
        kind, key_source = "title", title
    else:
        return {}, ("没有可用的标识：至少要有题目、或用户给出的 DOI、或 arXiv ID、或直链。"
                    + ("（提供了 DOI 但未声明 doi_source='user'，且没有题目）" if doi else ""))

    fields = {
        "request_key": canonical_key(kind, key_source),
        "input_kind": kind,
        "raw_input": _text(item.get("raw_input")) or title or doi or arxiv_id or url or "",
        "title": title,
        "doi": doi,
        "doi_source": doi_source,
        "arxiv_id": arxiv_id,
        "url": url,
        "note": _text(item.get("note")),
    }
    return fields, None


def add_items(conn: sqlite3.Connection, items: list[dict], actor: dict) -> list[dict]:
    """逐条入队。**部分失败不回滚** —— 一条坏的不该带走整批。

    但身份缺失是**硬失败**（抛 ValueError → 工具返回 isError），不是逐条结果：
    把它降级成一条 "invalid" 会被读成"这条处理过了"，代价是请求静默丢失。
    """
    requester = _require_actor_id(actor)
    if not isinstance(items, list) or not items:
        raise ValueError("items 不能为空")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"一次最多入队 {MAX_ITEMS} 篇，收到 {len(items)} 篇")

    results: list[dict] = []

    for index, item in enumerate(items):
        fields, error = _resolve_item(item or {})
        if error:
            results.append({"index": index, "status": "invalid", "reason": error})
            continue

        row = {
            **fields,
            "requester": requester,
            "requester_name": (actor or {}).get("actor_name") or None,
            "channel_ref": (actor or {}).get("channel_ref") or None,
            "message_ref": (actor or {}).get("actor_message") or None,
            "session_ref": (actor or {}).get("session_ref") or None,
            "attribution_source": (actor or {}).get("attribution_source") or None,
            "attribution_ambiguous": 1 if (actor or {}).get("actor_ambiguous") else 0,
            "requested_at": now_iso(),
        }
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        try:
            cur = conn.execute(
                f"INSERT INTO {TABLE} ({cols}) VALUES ({marks})", tuple(row.values()))
            conn.commit()
            results.append({"index": index, "status": "queued",
                            "id": cur.lastrowid, "request_key": row["request_key"],
                            "title": row["title"]})
        except sqlite3.IntegrityError as exc:
            conn.rollback()   # 失败的语句会把事务留在打开状态，先复位再用同一连接
            existing = conn.execute(
                f"SELECT id, title, requested_at FROM {TABLE}"
                " WHERE request_key = ? AND cancelled_at IS NULL",
                (row["request_key"],)).fetchone()
            if existing is None:
                # 走到这里说明**不是**唯一索引冲突，而是别的完整性约束（CHECK 等）。
                # 报成 duplicate 会让 SKILL.md 回答"这篇已经在清单里了" —— 用户以为
                # 记下了，实际一行都没写。必须与真正的重复区分开。
                results.append({"index": index, "status": "invalid",
                                "reason": f"写入被数据库拒绝（非重复）: {exc}"})
            else:
                results.append({"index": index, "status": "duplicate",
                                "request_key": row["request_key"],
                                "existing_id": existing["id"],
                                "existing_title": existing["title"],
                                "existing_requested_at": existing["requested_at"]})
    return results


_LIST_COLUMNS = ("id, request_key, input_kind, title, doi, doi_source, arxiv_id, url, note,"
                 " requester, requester_name, channel_ref, message_ref,"
                 " attribution_source, attribution_ambiguous, requested_at, cancelled_at")


def list_items(conn: sqlite3.Connection, actor: dict, window: str | None = None,
               since: str | None = None, until: str | None = None,
               requester: str | None = None, include_cancelled: bool = False,
               limit: int = 50, now: datetime | None = None) -> list[dict]:
    """默认查**调用者自己**的清单；显式传 requester 才查别人。"""
    who = (requester or "").strip() or _actor_id(actor)
    if not who:
        raise ValueError("缺少请求人身份（actor_id），无法确定查谁的清单")

    start, end = parse_window(window, since, until, now=now)
    sql = [f"SELECT {_LIST_COLUMNS} FROM {TABLE} WHERE requester = ?"]
    params: list = [who]
    if not include_cancelled:
        sql.append("AND cancelled_at IS NULL")
    if start:
        sql.append("AND requested_at >= ?")
        params.append(start)
    if end:
        sql.append("AND requested_at <= ?")
        params.append(end)
    sql.append("ORDER BY requested_at DESC, id DESC LIMIT ?")
    params.append(max(1, min(int(limit or 50), MAX_LIMIT)))

    return [dict(r) for r in conn.execute(" ".join(sql), tuple(params))]


def cancel_item(conn: sqlite3.Connection, actor: dict, request_key: str) -> dict:
    who = _require_actor_id(actor)
    key = (request_key or "").strip()
    if not key:
        raise ValueError("缺少 request_key")

    row = conn.execute(
        f"SELECT id, requester FROM {TABLE} WHERE request_key = ? AND cancelled_at IS NULL",
        (key,)).fetchone()
    if row:
        if row["requester"] != who:
            # 清单是**可查**别人的（群友能问、能拿到 request_key），但撤销只能撤自己的 ——
            # 否则一句"把那条去掉"就能悄悄撤掉别人的请求，而当事人不会收到任何提示。
            return {"status": "not_owner", "request_key": key}
        conn.execute(f"UPDATE {TABLE} SET cancelled_at = ?, cancelled_by = ? WHERE id = ?",
                     (now_iso(), who, row["id"]))
        conn.commit()
        return {"status": "cancelled", "id": row["id"], "request_key": key}

    if conn.execute(f"SELECT 1 FROM {TABLE} WHERE request_key = ?", (key,)).fetchone():
        return {"status": "already_cancelled", "request_key": key}
    return {"status": "not_found", "request_key": key}


# =============================================================
# 5. MCP JSON-RPC
# =============================================================

_ACTOR_PROPS = {
    "actor_id": {"type": "string", "description": "宿主注入的请求人 Discord 用户 ID（权威，勿自行填写）"},
    "actor_name": {"type": "string", "description": "宿主注入的请求人显示名（非权威）"},
    "actor_message": {"type": "string", "description": "宿主注入的消息 ID（溯源用）"},
    "channel_ref": {"type": "string", "description": "宿主注入的会话引用"},
    "session_ref": {"type": "string", "description": "宿主注入的会话键"},
    "attribution_source": {"type": "string", "description": "宿主注入的归属绑定方式"},
    "actor_ambiguous": {"type": "boolean", "description": "宿主注入：同一回合内出现多人发言时置真"},
    "actor_sig": {"type": "string", "description": "宿主注入的归属签名（勿自行填写）"},
}

_ITEM_PROPS = {
    "title": {"type": "string", "description": "论文题目（用户说过的原文，主要标识）"},
    "doi": {"type": "string", "description": "DOI，可选"},
    "doi_source": {"type": "string", "enum": ["user", "inferred"],
                   "description": "DOI 来源：用户给出的用 user；自己推断的必须填 inferred（inferred 不参与去重键）"},
    "arxiv_id": {"type": "string", "description": "arXiv ID，可选"},
    "url": {"type": "string", "description": "PDF 直链，可选"},
    "note": {"type": "string", "description": "附加说明，如“要正文/补充材料”"},
    "raw_input": {"type": "string", "description": "用户原话片段，便于日后人工核对"},
}

TOOLS = [
    {
        "name": "paper_queue_add",
        "description": ("把一篇或多篇论文记入清单（**只记不下**：不要下载、不要调用 paper-fetch）。"
                        "用户说“下载/帮我下/加到文献库/同步到 Zotero”时用它；一条消息里有多篇就一次传多个 item。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {
                    "type": "object", "properties": _ITEM_PROPS}, "description": "要入队的论文"},
                **_ACTOR_PROPS,
            },
            "required": ["items"],
        },
    },
    {
        "name": "paper_queue_list",
        "description": ("列出清单（**只读**）。不传 requester 时默认列出**提问者自己**的清单。"
                        "用户说“今天加的”“过去 24 小时加的”“我的清单”时，用 window 过滤。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "enum": list(WINDOWS),
                           "description": "时间窗口：today=今天(按 TZ 日界) / 24h / 7d / all（默认）"},
                "since": {"type": "string", "description": "起始时间 ISO8601 UTC，覆盖 window"},
                "until": {"type": "string", "description": "结束时间 ISO8601 UTC，覆盖 window"},
                "requester": {"type": "string",
                              "description": "查别人的清单时显式传其 Discord 用户 ID；不传=自己的"},
                "include_cancelled": {"type": "boolean", "description": "是否包含已撤销的（默认否）"},
                "limit": {"type": "integer", "description": "最多返回条数（默认 50）"},
                **_ACTOR_PROPS,
            },
        },
    },
    {
        "name": "paper_queue_cancel",
        "description": "撤销一条清单记录（用户说“记错了/不用下了/去掉那条”时用）。撤销后同一篇可以重新入队。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_key": {"type": "string", "description": "要撤销的 request_key"},
                **_ACTOR_PROPS,
            },
            "required": ["request_key"],
        },
    },
]


def _text_result(payload, is_error: bool = False) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _call_tool(conn: sqlite3.Connection, name: str, args: dict) -> dict:
    actor = {k: args.get(k) for k in _ACTOR_PROPS if k in args}
    if name == "paper_queue_add":
        error = actor_signature_error(name, args)
        if error:
            return _text_result(error, is_error=True)
        return _text_result(add_items(conn, args.get("items") or [], actor))
    if name == "paper_queue_list":
        # 读不验签：requester 本来就是公开可查的参数（"查别人的清单"是设计内的能力），
        # 伪造它不会写入任何东西。只有**写**才需要归属可信。
        rows = list_items(
            conn, actor,
            window=args.get("window"), since=args.get("since"), until=args.get("until"),
            requester=args.get("requester"),
            include_cancelled=bool(args.get("include_cancelled")),
            limit=args.get("limit") or 50,
        )
        return _text_result({"count": len(rows), "items": rows})
    if name == "paper_queue_cancel":
        error = actor_signature_error(name, args)
        if error:
            return _text_result(error, is_error=True)
        return _text_result(cancel_item(conn, actor, args.get("request_key") or ""))
    return _text_result(f"未知工具: {name}", is_error=True)


def handle(req: dict, conn: sqlite3.Connection) -> dict | None:
    """处理一条 JSON-RPC 请求。通知（无 id）返回 None —— 不应答。"""
    method = req.get("method")
    rid = req.get("id")
    is_notification = rid is None

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if is_notification:
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params")
        params = params if isinstance(params, dict) else {}
        raw_args = params.get("arguments")
        args = raw_args if isinstance(raw_args, dict) else {}
        try:
            result = _call_tool(conn, str(params.get("name") or ""), args)
        except Exception as exc:
            # 这里刻意用宽捕获：入参是**模型生成的**，OpenClaw 对 MCP 工具不做 schema
            # 校验，任何没预料到的形状都不该让 stdio 进程退出（退出 = 该会话余下的
            # 工具调用全部失效）。转成 isError 结果交给模型自己纠正。
            result = _text_result(f"调用失败: {type(exc).__name__}: {exc}", is_error=True)
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    conn = connect()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(req, dict):
            continue
        try:
            response = handle(req, conn)
        except Exception as exc:
            # 最后一道兜底：单条畸形请求绝不能把进程带走。
            response = {"jsonrpc": "2.0", "id": req.get("id"),
                        "error": {"code": -32603,
                                  "message": f"internal error: {type(exc).__name__}: {exc}"}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
