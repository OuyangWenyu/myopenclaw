#!/usr/bin/env python3
"""
CC飞总 Stop hook — 将 Claude Code 对话捕获到 TDAI Memory Gateway.

Claude Code 每轮会话结束触发 Stop hook，stdin 传入含 transcript_path 的 JSON。
本脚本读取 transcript 最后一组 user+assistant 消息，POST 到 Gateway /capture，
带 session_id=personal_ccfeizong，使 CC飞总 的对话进入共享记忆（L0→L3）。

设计约束:
  - 任何异常都静默 exit 0，绝不阻塞 CC飞总 对话
  - Gateway 地址读 env MEMORY_TENCENTDB_GATEWAY_HOST/PORT（默认 tdai-memory:8420）
  - 只捕获真实 user 文本 + assistant text 块（跳过 thinking/tool_use/tool_result
    /slash-command/local-command-caveat 等噪声）
  - Tencent 管线负责价值过滤/去重/分层，本脚本只做最简单的 L0 捕获
"""

import json
import os
import sys
import urllib.request

SESSION_ID = "personal_ccfeizong"
GATEWAY_HOST = os.environ.get("MEMORY_TENCENTDB_GATEWAY_HOST", "tdai-memory")
GATEWAY_PORT = os.environ.get("MEMORY_TENCENTDB_GATEWAY_PORT", "8420")
CAPTURE_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/capture"
TIMEOUT_SECONDS = 5

# Wrappers that indicate a non-conversational user turn (skip these).
_NOISE_PREFIXES = (
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "--- Reply chain",
)


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


def read_last_turn(transcript_path: str):
    """Return (user_text, assistant_text, session_key) for the last complete turn.

    Walks the JSONL backward: finds the last assistant text message, then the
    most recent real user message before it. Returns (None, None, None) if either
    is missing.
    """
    try:
        with open(transcript_path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None, None, None

    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    assistant_text = None
    assistant_idx = None
    session_key = None
    for i in range(len(records) - 1, -1, -1):
        msg = records[i].get("message", {})
        if msg.get("role") == "assistant":
            text = extract_text(msg.get("content"))
            if text:
                assistant_text = text
                assistant_idx = i
                session_key = records[i].get("sessionId") or ""
                break

    if assistant_text is None:
        return None, None, None

    user_text = None
    for i in range(assistant_idx - 1, -1, -1):
        msg = records[i].get("message", {})
        if msg.get("role") == "user":
            text = extract_text(msg.get("content"))
            if is_real_user_text(text):
                user_text = text
                if not session_key:
                    session_key = records[i].get("sessionId") or ""
                break

    if user_text is None:
        return None, None, None

    return user_text, assistant_text, (session_key or "cc-feizong")


def post_capture(user_text: str, assistant_text: str, session_key: str) -> None:
    """Fire-and-forget POST to Gateway /capture. Errors are swallowed."""
    payload = json.dumps({
        "user_content": user_text,
        "assistant_content": assistant_text,
        "session_key": session_key,
        "session_id": SESSION_ID,
    }).encode("utf-8")

    req = urllib.request.Request(
        CAPTURE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        resp.read()


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        hook_input = json.loads(raw)
        transcript_path = hook_input.get("transcript_path")
        if not transcript_path:
            return

        user_text, assistant_text, session_key = read_last_turn(transcript_path)
        if not user_text or not assistant_text:
            return

        post_capture(user_text, assistant_text, session_key)
    except Exception:
        # Never block CC飞总 — swallow everything.
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
