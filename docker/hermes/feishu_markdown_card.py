# [myopenclaw] Feishu markdown → Card JSON 2.0 payload builder.
#
# This file is inlined VERBATIM into
# plugins/platforms/feishu/adapter.py by docker/hermes/patch-feishu-adapter.py
# at image build time (between sentinel comments), so keep it:
#   - stdlib-only and self-contained (imports allowed; duplicates are harmless)
#   - free of identifiers that collide with adapter.py (checked at patch time)
#   - without a module docstring / __all__ (it lands mid-file, not as a module)
#
# Why: Feishu's ``post`` + ``md`` renderer degrades headings/lists and shows GFM
# tables as raw pipe source; Card JSON 2.0's markdown component renders them
# properly (headings sized, lists bulleted, real tables — max 4 per component,
# ≤5 data rows shown + pagination). Upstream PRs #21000/#29630 are unmerged.
#
# Tests: tests/test_feishu_markdown_card.py (loads this file directly).

import json
import os
import re

# Env kill switch: unset / "" / 1 / on / yes / true → enabled; 0 / off / false / no / disabled → off.
MARKDOWN_CARD_ENV = "FEISHU_MARKDOWN_CARD"
# Feishu card payload cap is ~30KB; stay under it (upstream PR #21000 uses 28000).
MARKDOWN_CARD_MAX_BYTES = 28000
# Max tables per markdown component (Feishu doc: 单个富文本组件中最多可放置四个表格).
MARKDOWN_CARD_MAX_TABLES = 4

_CARD_DISABLED_VALUES = frozenset({"0", "off", "false", "no", "disabled"})
# A GFM table separator row: ``|---|---|`` (also ``| :--- | ---: |``). Anchored to its own line.
_GFM_TABLE_SEP_RE = re.compile(r"^\s*\|[-:|\s]+\|\s*$", re.MULTILINE)
# Fenced code blocks — tables inside them are literal text, not tables.
_FENCED_BLOCK_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def markdown_cards_enabled(env=None):
    # env parameter injection keeps this testable without mutating os.environ.
    value = (os.environ if env is None else env).get(MARKDOWN_CARD_ENV, "")
    return str(value).strip().lower() not in _CARD_DISABLED_VALUES


def _count_gfm_tables(text):
    return len(_GFM_TABLE_SEP_RE.findall(_FENCED_BLOCK_RE.sub("", text)))


def build_markdown_card_payload(markdown, env=None):
    # Card JSON 2.0 payload for a markdown reply, or None to keep the legacy
    # post/text path. Never raises — a builder bug must degrade, not drop the message.
    try:
        if not markdown_cards_enabled(env):
            return None
        if not isinstance(markdown, str) or not markdown.strip():
            return None
        if _count_gfm_tables(markdown) > MARKDOWN_CARD_MAX_TABLES:
            return None
        payload = json.dumps(
            {
                "schema": "2.0",
                # Card 2.0 requires update_multi=true (飞书文档: 暂时仅支持设为 true).
                "config": {"update_multi": True},
                "body": {"elements": [{"tag": "markdown", "content": markdown}]},
            },
            ensure_ascii=False,
        )
        if len(payload.encode("utf-8")) > MARKDOWN_CARD_MAX_BYTES:
            return None
        return payload
    except Exception:
        return None
