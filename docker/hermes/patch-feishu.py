#!/usr/bin/env python3
# [myopenclaw] Build-time text surgery on the upstream Hermes image — two fixes:
#
# 1) adapters (plugins/platforms/feishu/adapter.py): send markdown replies as
#    Feishu Card JSON 2.0 (``interactive``) instead of ``post`` + ``md``, because
#    the post renderer degrades headings/lists and shows GFM tables as raw pipe
#    source. Upstream PRs #21000/#29630 are unmerged.
#
# 2) gateway (gateway/run.py::GatewayRunner._thread_metadata_for_target): fill
#    ``metadata["reply_to_message_id"]`` for Feishu. Upstream only fills it for
#    Telegram DM topics / Slack. Without it, the feishu adapter's
#    ``_send_raw_message`` takes the bare-thread branch (``receive_id_type=
#    "thread_id"``, an invalid API field → 99992402 "field validation failed")
#    and *bot replies into omt_ topic threads never deliver* — verified live
#    2026-10-14 with the deployed adapter: same metadata without anchor → 99992402;
#    with anchor → message.reply(reply_in_thread=True) succeeds (text and cards
#    both). The adapter already READS ``metadata["reply_to_message_id"]``; the
#    gateway just never supplied it for Feishu — this is the intended contract.
#
# Runs inside ``docker compose build hermes`` (see docker/hermes/Dockerfile).
# The base image is ``nousresearch/hermes-agent:latest`` (unpinned) — every
# anchor below must occur EXACTLY ONCE, and any mismatch exits non-zero so the
# BUILD fails loudly instead of silently shipping unpatched files. Both files
# are patched in memory first and written only after ALL surgeries succeed.
#
# Contract with tests/test_feishu_markdown_card.py:
#   - ANCHORS*_OLD/NEW, SENTINEL and TOPIC_MARKER are imported by the tests to
#     build fixture skeletons; changing them here means updating fixtures there
#     in tandem (the tests are the spec of this surgery).
#   - Idempotent: a second run on already-patched files exits 0.

import argparse
import ast
import os
import shutil
import sys
from pathlib import Path

SENTINEL = "[myopenclaw] feishu markdown card patch"
TOPIC_MARKER = "[myopenclaw] feishu topic reply anchor patch"

DEFAULT_ADAPTER_PATH = "/opt/hermes/plugins/platforms/feishu/adapter.py"
DEFAULT_GATEWAY_PATH = "/opt/hermes/gateway/run.py"

# ---------------------------------------------------------------------------
# A1 — broaden the content-rejection regex (post-type errors + card errors).
# ---------------------------------------------------------------------------
A1_OLD = '_POST_CONTENT_INVALID_RE = re.compile(r"content format of the post type is incorrect", re.IGNORECASE)\n'
A1_NEW = (
    "_POST_CONTENT_INVALID_RE = re.compile(\n"
    '    r"content format of the (?:post|interactive|card) type is incorrect"\n'
    '    r"|card table.* over limit"\n'
    '    r"|card .* over limit"\n'
    '    r"|failed to create card content",\n'
    "    re.IGNORECASE,\n"
    ")\n"
)

# ---------------------------------------------------------------------------
# A2 — inline feishu_markdown_card.py verbatim above the post payload builder.
# ---------------------------------------------------------------------------
A2_OLD = (
    "def _build_markdown_post_payload(content: str) -> str:\n"
    "    rows = _build_markdown_post_rows(content)\n"
    '    return json.dumps({"zh_cn": {"content": rows}}, ensure_ascii=False)\n'
)

# ---------------------------------------------------------------------------
# A3 — send(): route markdown chunks through the card builder and unify the
# card → post → text downgrade chain (replaces the old post-only fallbacks).
# ---------------------------------------------------------------------------
A3_OLD = (
    '        async def _send_plain(chunk: str) -> Any:\n'
    "            return await self._feishu_send_with_retry(\n"
    "                chat_id=chat_id,\n"
    '                msg_type="text",\n'
    '                payload=json.dumps({"text": _strip_markdown_to_plain_text(chunk)}, ensure_ascii=False),\n'
    "                reply_to=reply_to,\n"
    "                metadata=metadata,\n"
    "            )\n"
    "\n"
    "        try:\n"
    "            for chunk in chunks:\n"
    "                msg_type, payload = self._build_outbound_payload(chunk, prefer_post=prefer_post)\n"
    "                try:\n"
    "                    response = await self._feishu_send_with_retry(\n"
    "                        chat_id=chat_id, msg_type=msg_type, payload=payload, reply_to=reply_to, metadata=metadata,\n"
    "                    )\n"
    "                except Exception as exc:\n"
    '                    if msg_type != "post" or not _POST_CONTENT_INVALID_RE.search(str(exc)):\n'
    "                        raise\n"
    '                    logger.warning("[Feishu] Invalid post payload rejected by API; falling back to plain text")\n'
    "                    response = await _send_plain(chunk)\n"
    "                if (\n"
    '                    msg_type == "post"\n'
    "                    and not self._response_succeeded(response)\n"
    '                    and _POST_CONTENT_INVALID_RE.search(str(getattr(response, "msg", "") or ""))\n'
    "                ):\n"
    '                    logger.warning("[Feishu] Post payload rejected by API response; falling back to plain text")\n'
    "                    response = await _send_plain(chunk)\n"
    "                last_response = response\n"
)

A3_NEW = (
    '        async def _send_plain(chunk: str) -> Any:\n'
    "            return await self._feishu_send_with_retry(\n"
    "                chat_id=chat_id,\n"
    '                msg_type="text",\n'
    '                payload=json.dumps({"text": _strip_markdown_to_plain_text(chunk)}, ensure_ascii=False),\n'
    "                reply_to=reply_to,\n"
    "                metadata=metadata,\n"
    "            )\n"
    "\n"
    "        def _content_rejected(resp: Any, attempted: str) -> bool:\n"
    f"            # {SENTINEL} — downgrade trigger.\n"
    "            # Known content errors always downgrade; any other business failure of the\n"
    '            # new ``interactive`` type downgrades too (card → post retry, never drop).\n'
    "            if self._response_succeeded(resp):\n"
    "                return False\n"
    '            if _POST_CONTENT_INVALID_RE.search(str(getattr(resp, "msg", "") or "")):\n'
    "                return True\n"
    '            return attempted == "interactive"\n'
    "\n"
    "        try:\n"
    "            for chunk in chunks:\n"
    "                msg_type, payload = self._build_outbound_payload(chunk, prefer_post=prefer_post)\n"
    f'                # {SENTINEL} — ``post`` already means "markdown"\n'
    "                # (prefer_post locked the decision whole-message); None keeps the legacy post path.\n"
    '                if msg_type == "post":\n'
    "                    card_payload = build_markdown_card_payload(chunk)\n"
    "                    if card_payload is not None:\n"
    '                        msg_type, payload = "interactive", card_payload\n'
    "                try:\n"
    "                    response = await self._feishu_send_with_retry(\n"
    "                        chat_id=chat_id, msg_type=msg_type, payload=payload, reply_to=reply_to, metadata=metadata,\n"
    "                    )\n"
    "                except Exception as exc:\n"
    '                    if msg_type not in ("post", "interactive") or not _POST_CONTENT_INVALID_RE.search(str(exc)):\n'
    "                        raise\n"
    "                    response = None\n"
    "                if response is None or _content_rejected(response, msg_type):\n"
    f"                    # {SENTINEL} — card → post → text fallback chain.\n"
    '                    if msg_type == "interactive":\n'
    '                        logger.warning("[Feishu] Card payload rejected by API; retrying as post format")\n'
    "                        try:\n"
    "                            response = await self._feishu_send_with_retry(\n"
    "                                chat_id=chat_id,\n"
    '                                msg_type="post",\n'
    "                                payload=_build_markdown_post_payload(chunk),\n"
    "                                reply_to=reply_to,\n"
    "                                metadata=metadata,\n"
    "                            )\n"
    "                        except Exception:\n"
    "                            response = None\n"
    '                        msg_type = "post"\n'
    '                    if response is None or _content_rejected(response, "post"):\n'
    '                        logger.warning("[Feishu] Post payload rejected by API; falling back to plain text")\n'
    "                        response = await _send_plain(chunk)\n"
    "                last_response = response\n"
)

# ---------------------------------------------------------------------------
# A4 — retry helper: content-invalid cards skip the retry loop too (re-raise
# immediately, same as post, so send()'s fallback chain handles them).
# ---------------------------------------------------------------------------
A4_OLD = '                if msg_type == "post" and _POST_CONTENT_INVALID_RE.search(str(exc)):\n'
A4_NEW = '                if msg_type in ("interactive", "post") and _POST_CONTENT_INVALID_RE.search(str(exc)):\n'

# ---------------------------------------------------------------------------
# A5 — gateway: anchor Feishu thread sends via metadata.reply_to_message_id.
# ---------------------------------------------------------------------------
A5_OLD = (
    "        if platform == Platform.SLACK and reply_to_message_id is not None:\n"
    "            # Slack's reply_in_thread=false path uses message_id to tell real threads from synthetic keys.\n"
    '            metadata["message_id"] = str(reply_to_message_id)\n'
    "        return metadata\n"
)

A5_NEW = (
    "        if platform == Platform.SLACK and reply_to_message_id is not None:\n"
    "            # Slack's reply_in_thread=false path uses message_id to tell real threads from synthetic keys.\n"
    '            metadata["message_id"] = str(reply_to_message_id)\n'
    f"        # {TOPIC_MARKER}\n"
    "        # 没有这个锚点，feishu 适配器的 _send_raw_message 会把 thread_id 当 receive_id 发\n"
    "        #（飞书 API 无此 receive_id_type → 99992402 field validation failed），bot 在 omt_\n"
    "        # 话题里的回复**永远送不出去**（上游既有 bug，2026-10-14 用部署中的适配器实测：\n"
    "        # 同 metadata 无锚点 → 99992402；补锚点 → message.reply(reply_in_thread=True) 成功，\n"
    "        # 文本与卡片均验证）。适配器本就在读 metadata[\"reply_to_message_id\"]，补上即走对路径。\n"
    "        if platform == Platform.FEISHU and reply_to_message_id is not None:\n"
    '            metadata["reply_to_message_id"] = str(reply_to_message_id)\n'
    "        return metadata\n"
)

# Distinctive strings that must be present once the sentinel is found (idempotence checks).
_ADAPTER_MARKERS = (
    'r"content format of the (?:post|interactive|card) type is incorrect"',
    "def build_markdown_card_payload(markdown, env=None):",
    "card_payload = build_markdown_card_payload(chunk)",
    'if msg_type in ("interactive", "post") and _POST_CONTENT_INVALID_RE.search(str(exc)):',
)
_ADAPTER_REPLACEMENTS = (
    ("A1", A1_OLD, A1_NEW),
    ("A2", A2_OLD, None),
    ("A3", A3_OLD, A3_NEW),
    ("A4", A4_OLD, A4_NEW),
)


def _inline_module(module_src: str) -> str:
    src = module_src.rstrip("\n")
    return (
        f"# ---- {SENTINEL} — begin ----\n"
        f"{src}\n"
        f"# ---- {SENTINEL} — end ----\n"
        "\n\n"
        + A2_OLD
    )


def _drift(name: str, count: int) -> "SystemExit":
    return SystemExit(
        f"[patch-feishu] anchor {name}: occurs {count} time(s), want exactly 1 — base image drifted "
        "(FROM nousresearch/hermes-agent:latest is unpinned). Re-derive the anchor from the in-image "
        "file and update docker/hermes/patch-feishu.py."
    )


def patch_adapter(source: str, module_src: str) -> str | None:
    """Return the patched adapter source, or None when already patched."""
    if SENTINEL in source:
        missing = [m for m in _ADAPTER_MARKERS if m not in source]
        if missing:
            raise SystemExit(
                f"[patch-feishu] adapter has the sentinel but is missing {len(missing)} marker(s) — "
                "half-applied or hand-edited? Refusing to guess:\n"
                + "\n".join(f"  - {m!r}" for m in missing)
            )
        return None

    # Upstream may have shipped its own card support (PR #21000 merged) — do not double-patch.
    for clash in ("build_markdown_card_payload", "MARKDOWN_CARD_ENV"):
        if clash in source:
            raise SystemExit(
                f"[patch-feishu] adapter already references {clash!r} without our sentinel — upstream "
                "may have merged card rendering. Re-review upstream, then DELETE this patch (and its "
                "Dockerfile step) instead of applying it."
            )

    out = source
    for name, old, new in _ADAPTER_REPLACEMENTS:
        if new is None:
            new = _inline_module(module_src)
        count = out.count(old)
        if count != 1:
            raise _drift(name, count)
        out = out.replace(old, new, 1)
    return out


def patch_gateway(source: str) -> str | None:
    """Return the patched gateway/run.py source, or None when already patched."""
    if TOPIC_MARKER in source:
        return None
    count = source.count(A5_OLD)
    if count != 1:
        raise _drift("A5", count)
    return source.replace(A5_OLD, A5_NEW, 1)


def _patch_one(kind: str, path: Path, transform) -> str | None:
    if not path.is_file():
        raise SystemExit(f"[patch-feishu] {kind} not found: {path}")
    result = transform(path.read_text(encoding="utf-8"))
    if result is not None:
        try:
            ast.parse(result)
        except SyntaxError as exc:
            raise SystemExit(f"[patch-feishu] patched {kind} ({path}) does not parse: {exc}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the myopenclaw Feishu patches to the in-image sources")
    parser.add_argument("--adapter-path", default=DEFAULT_ADAPTER_PATH, help=f"default: {DEFAULT_ADAPTER_PATH}")
    parser.add_argument("--gateway-path", default=DEFAULT_GATEWAY_PATH, help=f"default: {DEFAULT_GATEWAY_PATH}")
    parser.add_argument(
        "--module",
        default=str(Path(__file__).resolve().parent / "feishu_markdown_card.py"),
        help="module to inline (default: sibling feishu_markdown_card.py)",
    )
    args = parser.parse_args()

    module_path = Path(args.module)
    if not module_path.is_file():
        print(f"[patch-feishu] module not found: {module_path}", file=sys.stderr)
        return 1
    module_src = module_path.read_text(encoding="utf-8")
    if SENTINEL in module_src:
        print("[patch-feishu] module contains the sentinel — would corrupt idempotence", file=sys.stderr)
        return 1

    adapter_path = Path(args.adapter_path)
    gateway_path = Path(args.gateway_path)

    # Patch everything in memory first; only write when ALL surgeries succeeded
    # (a gateway-anchor failure must not leave the adapter half-patched).
    try:
        adapter_result = _patch_one("adapter", adapter_path, lambda src: patch_adapter(src, module_src))
        gateway_result = _patch_one("gateway", gateway_path, patch_gateway)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    written = []
    for kind, path, result in (("adapter", adapter_path, adapter_result), ("gateway", gateway_path, gateway_result)):
        if result is None:
            continue
        tmp = path.with_name(path.name + ".myopenclaw-tmp")
        tmp.write_text(result, encoding="utf-8")
        os.replace(tmp, path)
        shutil.rmtree(path.parent / "__pycache__", ignore_errors=True)
        written.append(f"{kind}: {path}")

    if not written:
        print("[patch-feishu] already patched — nothing to do")
        return 0
    for line in written:
        print(f"[patch-feishu] patched OK ({line})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
