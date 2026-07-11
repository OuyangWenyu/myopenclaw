#!/usr/bin/env python3
"""
CC飞总 Stop hook — 将 Claude Code 对话捕获到 TDAI Memory Gateway.

Claude Code 每轮会话结束触发 Stop hook，stdin 传入含 transcript_path 的 JSON。
本脚本读取 transcript 最后一组 user+assistant 消息，POST 到 Gateway /capture，
带 session_id=personal_ccfeizong，使 CC飞总 的对话进入共享记忆（L0→L3）。

设计约束:
  - 任何异常都静默 exit 0，绝不阻塞 CC飞总 对话
  - 但失败必须可诊断 —— 写文件日志 + 成功心跳（否则管线坏了无人察觉）
  - Gateway 地址读 env MEMORY_TENCENTDB_GATEWAY_HOST/PORT（默认 tdai-memory:8420）
  - 只捕获真实 user 文本 + assistant text 块（跳过 thinking/tool_use/tool_result
    /slash-command/local-command-caveat 等噪声）
  - Tencent 管线负责价值过滤/去重/分层，本脚本只做最简单的 L0 捕获
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request

SESSION_ID = "personal_ccfeizong"
GATEWAY_HOST = os.environ.get("MEMORY_TENCENTDB_GATEWAY_HOST", "tdai-memory")
GATEWAY_PORT = os.environ.get("MEMORY_TENCENTDB_GATEWAY_PORT", "8420")
CAPTURE_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/capture"
TIMEOUT_SECONDS = 5
# Per-field content cap — bound resource use on huge turns. Tencent 管线只需
# 关键结论，超长内容截断不影响价值过滤。
MAX_CONTENT_CHARS = 48000
LOG_PATH = os.environ.get(
    "MEMORY_CAPTURE_LOG",
    "/home/node/.myagentdata/tdai-memory/capture-hook.log",
)

# Wrappers that indicate a non-conversational user turn (skip these).
_NOISE_PREFIXES = (
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "--- Reply chain",
)

logger = logging.getLogger("cc-capture")


def _setup_logging() -> None:
    """Attach an append-only file handler. Failure here must not break the hook."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    except OSError:
        # No durable log channel — fall back to a null handler so calls are no-ops.
        logger.addHandler(logging.NullHandler())


def extract_text(content) -> str:
    """Extract plain conversational text from a message content field.

    content may be:
      - str  → returned as-is (a plain user message)
      - list → only 'text' blocks are joined; thinking/tool_use/tool_result skipped
    Anything else yields an empty string.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def is_real_user_text(text: str) -> bool:
    """True only for genuine user prose (not slash commands, caveats, tool echoes)."""
    if not text:
        return False
    stripped = text.lstrip()
    return not any(stripped.startswith(p) for p in _NOISE_PREFIXES)


def _load_records(transcript_path: str) -> list:
    """Parse a JSONL transcript into records. Logs anomalies (missing file,
    mass parse failure) so a broken pipeline is visible."""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("transcript unreadable: %s (%s)", transcript_path, e)
        return []

    records = []
    parse_failures = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            parse_failures += 1

    if parse_failures and not records:
        logger.warning(
            "all %d transcript lines failed to parse: %s",
            parse_failures, transcript_path,
        )
    return records


def read_last_turn(transcript_path: str):
    """Return (user_text, assistant_text, session_key, msg_uuid) for the last turn.

    Merges contiguous assistant records (streamed text split across records within
    one turn) into a single assistant_text, then finds the most recent real user
    message before that turn. Returns (None, None, None, None) if either is missing.
    """
    records = _load_records(transcript_path)
    if not records:
        return None, None, None, None

    # Find the last assistant record that carries text.
    assistant_idx = None
    for i in range(len(records) - 1, -1, -1):
        if records[i].get("message", {}).get("role") == "assistant":
            if extract_text(records[i]["message"].get("content")):
                assistant_idx = i
                break

    if assistant_idx is None:
        return None, None, None, None

    session_key = records[assistant_idx].get("sessionId") or ""
    msg_uuid = records[assistant_idx].get("uuid") or ""

    # Merge contiguous assistant records (no intervening real user msg) into one.
    assistant_parts = []
    turn_start = assistant_idx
    for i in range(assistant_idx, -1, -1):
        msg = records[i].get("message", {})
        role = msg.get("role")
        if role == "assistant":
            text = extract_text(msg.get("content"))
            if text:
                assistant_parts.append(text)
            turn_start = i
        elif role == "user":
            if is_real_user_text(extract_text(msg.get("content"))):
                break  # real user message ends the assistant turn
            turn_start = i  # tool_result user record — part of the same turn
        else:
            turn_start = i
    assistant_parts.reverse()
    assistant_text = "\n".join(assistant_parts).strip()

    # Walk back from the turn start to the most recent real user message.
    user_text = None
    for i in range(turn_start - 1, -1, -1):
        msg = records[i].get("message", {})
        if msg.get("role") == "user":
            text = extract_text(msg.get("content"))
            if is_real_user_text(text):
                user_text = text
                if not session_key:
                    session_key = records[i].get("sessionId") or ""
                break

    if not user_text or not assistant_text:
        return None, None, None, None

    return user_text, assistant_text, (session_key or "cc-feizong"), msg_uuid


def post_capture(user_text: str, assistant_text: str,
                 session_key: str, msg_uuid: str) -> None:
    """POST to Gateway /capture. Logs the outcome; never raises past this frame."""
    payload = json.dumps({
        "user_content": user_text[:MAX_CONTENT_CHARS],
        "assistant_content": assistant_text[:MAX_CONTENT_CHARS],
        "session_key": session_key,
        "session_id": SESSION_ID,
        "idempotency_key": msg_uuid,  # message uuid — helps downstream dedup
    }).encode("utf-8")

    req = urllib.request.Request(
        CAPTURE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            resp.read()
        logger.info(
            "captured session=%s uuid=%s bytes=%d",
            session_key, msg_uuid or "-", len(payload),
        )
    except urllib.error.HTTPError as e:
        logger.warning("Gateway HTTP %s for /capture: %s", e.code, e.reason)
    except urllib.error.URLError as e:
        logger.warning("Gateway unreachable at %s: %s", CAPTURE_URL, e.reason)
    except OSError as e:
        logger.warning("capture POST failed: %s", e)


def main() -> None:
    _setup_logging()
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        hook_input = json.loads(raw)
        transcript_path = hook_input.get("transcript_path")
        if not transcript_path:
            return

        user_text, assistant_text, session_key, msg_uuid = read_last_turn(transcript_path)
        if not user_text or not assistant_text:
            return  # legitimate "nothing to capture" — stay quiet

        post_capture(user_text, assistant_text, session_key, msg_uuid)
    except Exception:
        # Last-resort backstop: never block CC飞总. Log the trace so internal
        # bugs (typos, shape changes) don't drop 100% of captures invisibly.
        logger.exception("capture hook internal error")


if __name__ == "__main__":
    main()
    sys.exit(0)
