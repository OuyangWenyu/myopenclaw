# [myopenclaw] Feishu markdown → Card JSON 2.0 payload builder + LaTeX→image pipeline.
#
# This file is inlined VERBATIM into
# plugins/platforms/feishu/adapter.py by docker/hermes/patch-feishu.py
# at image build time (between sentinel comments), so keep it:
#   - stdlib-only and self-contained (imports allowed; duplicates are harmless)
#   - free of identifiers that collide with adapter.py (checked at patch time)
#   - without a module docstring / __all__ (it lands mid-file, not as a module)
#
# Two features:
#   1. Card rendering: Feishu's ``post`` + ``md`` renderer degrades headings/lists
#      and shows GFM tables as raw pipe source; Card JSON 2.0's markdown component
#      renders them properly (headings sized, lists bulleted, real tables — max 4
#      per component, ≤5 data rows shown + pagination). Upstream PRs #21000/#29630
#      are unmerged.
#   2. LaTeX formulas: Feishu IM messages (post AND cards) never render formulas —
#      ``$...$``/``$$...$$`` stay source text (verified live 2026-10-14; KaTeX is a
#      Docs-only capability). The only true-render path is formula→image: render
#      PNG via matplotlib mathtext (subprocess, see docker/hermes/render-formula.py),
#      upload via the adapter's image API, embed inline (``![](image_key)`` inside
#      a markdown element — verified to render) and as standalone ``img`` elements
#      (``size`` gives exact display px; user picked 320px-wide-equivalent scale
#      for blocks, ~13px glyphs for inline).
#
# Tests: tests/test_feishu_markdown_card.py (card builder + patch script),
#        tests/test_feishu_latex.py (formula parsing/substitution/orchestration).

import hashlib
import json
import os
import re

# Env kill switches: unset / "" / 1 / on / yes / true → enabled; 0 / off / false / no / disabled → off.
MARKDOWN_CARD_ENV = "FEISHU_MARKDOWN_CARD"
LATEX_IMAGE_ENV = "FEISHU_LATEX_IMAGE"
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


def latex_images_enabled(env=None):
    value = (os.environ if env is None else env).get(LATEX_IMAGE_ENV, "")
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


# ---------------------------------------------------------------------------
# LaTeX formula extraction / card assembly.
#
# Pandoc-style rules for ``$...$``: the opening ``$`` must not be followed by
# whitespace or another ``$`` (and not be backslash-escaped); the closing ``$``
# must not be preceded by whitespace and must not be followed by a digit
# (rejects "$100 and $200" / "$5 to $10" currency). ``$$...$$`` is block math
# (may span lines). Spans inside fenced code blocks and inline code are ignored
# (``$HOME`` etc. must not be touched).
# ---------------------------------------------------------------------------

_BLOCK_MATH_RE = re.compile(r"\$\$(?!\$)(.+?)(?<!\$)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<![\\$])\$(?![\s$])([^\n$]*?[^\s$])\$(?![$\d])")
_INLINE_CODE_MASK_RE = re.compile(r"`[^`\n]*`")

# Render parameters (single source of truth; passed to render-formula.py per item).
# Inline: the Feishu client normalizes inline-image display width — narrow images
# get upscaled (blurry + huge; verified 2026-10-14 with a diagnostic card: a
# 600px-wide canvas rendered normal, 77/107px ones were blurry). So inline
# formulas render onto a fixed ~600px canvas (glyph centered, 2x dpi).
INLINE_RENDER_FONT_SIZE = 7.5
INLINE_RENDER_DPI = 180
INLINE_CANVAS_WIDTH = 600
INLINE_CANVAS_MARGIN_X = 24
INLINE_CANVAS_MARGIN_Y = 8
# Block: renders at 622px-wide for the reference formula; display scale picks the
# user-chosen 320px width for that reference (glyph size stays constant across formulas).
BLOCK_RENDER_FONT_SIZE = 26
BLOCK_RENDER_DPI = 200
RENDER_PAD_INCHES = 0.15
BLOCK_DISPLAY_SCALE_NUM = 320
BLOCK_DISPLAY_SCALE_DEN = 622
BLOCK_DISPLAY_MAX_WIDTH = 1000  # Feishu img size cap
# Bump when any render parameter changes — invalidates the image-key cache.
LATEX_RENDER_PARAMS_VERSION = "v2-inline7.5@180-canvas600-block26@200-pad0.15"
# Subprocess renderer (present in the image; see docker/hermes/Dockerfile).
LATEX_RENDER_PYTHON = "/opt/hermes/.venv/bin/python3"
LATEX_RENDER_SCRIPT = "/opt/hermes/scripts/render-formula.py"
# Persistent image-key cache (survives container recreation; image_keys are
# server-side resources valid across restarts).
LATEX_IMAGE_CACHE_PATH = "/opt/data/.cache/feishu-latex.json"


def _mask_code_spans(markdown):
    masked = list(markdown)

    def _blank(a, b):
        for i in range(a, b):
            if masked[i] != "\n":
                masked[i] = " "

    for match in _FENCED_BLOCK_RE.finditer(markdown):
        _blank(match.start(), match.end())
    interim = "".join(masked)
    for match in _INLINE_CODE_MASK_RE.finditer(interim):
        _blank(match.start(), match.end())
    return "".join(masked)


def extract_formula_spans(markdown):
    # [{"kind": "inline"|"block", "latex": str, "start": int, "end": int}] in source order.
    # Never raises; returns [] on any problem.
    spans = []
    try:
        if not isinstance(markdown, str) or "$" not in markdown:
            return spans
        masked = _mask_code_spans(markdown)
        for match in _BLOCK_MATH_RE.finditer(masked):
            latex = markdown[match.start() + 2:match.end() - 2].strip()
            if latex:
                spans.append({"kind": "block", "latex": latex,
                              "start": match.start(), "end": match.end()})
        if spans:
            chars = list(masked)
            for span in spans:
                for i in range(span["start"], span["end"]):
                    chars[i] = " "
            masked = "".join(chars)
        for match in _INLINE_MATH_RE.finditer(masked):
            spans.append({"kind": "inline", "latex": markdown[match.start() + 1:match.end() - 1],
                          "start": match.start(), "end": match.end()})
        spans.sort(key=lambda s: s["start"])
        return spans
    except Exception:
        return []


def formula_cache_key(kind, latex):
    return hashlib.sha256(
        f"{LATEX_RENDER_PARAMS_VERSION}|{kind}|{latex}".encode("utf-8")
    ).hexdigest()


def block_display_size(width, height, max_width=BLOCK_DISPLAY_MAX_WIDTH):
    # Display px for a block formula, preserving the render's aspect ratio so
    # scale_type=crop_center never crops. User picked 320px for the 622px reference.
    try:
        w, h = max(1, int(width)), max(1, int(height))
        scale = BLOCK_DISPLAY_SCALE_NUM / BLOCK_DISPLAY_SCALE_DEN
        if w * scale > max_width:
            scale = max_width / w
        return max(1, round(w * scale)), max(1, round(h * scale))
    except Exception:
        return width, height


def _render_item(span):
    inline = span["kind"] == "inline"
    item = {
        "kind": span["kind"],
        "latex": span["latex"],
        "font_size": INLINE_RENDER_FONT_SIZE if inline else BLOCK_RENDER_FONT_SIZE,
        "dpi": INLINE_RENDER_DPI if inline else BLOCK_RENDER_DPI,
        "pad": RENDER_PAD_INCHES,
    }
    if inline:
        item["canvas_width"] = INLINE_CANVAS_WIDTH
        item["canvas_margin_x"] = INLINE_CANVAS_MARGIN_X
        item["canvas_margin_y"] = INLINE_CANVAS_MARGIN_Y
    return item


def build_formula_card_payload(markdown, spans, images):
    # Card JSON 2.0 payload with the message split into interleaved elements:
    # markdown segments (inline formulas embedded as ``![](image_key)`` — client
    # renders them inline) and standalone ``img`` elements for block formulas
    # (``size`` gives the exact display px; inline markdown images have no size API).
    # ``images``: {span_index: {"key": str, "width": int, "height": int}} — a missing
    # entry keeps that span's SOURCE TEXT (per-formula fallback).
    # Returns None to fall back to the legacy post path (never raises).
    try:
        if not isinstance(markdown, str) or not isinstance(spans, list) or not spans:
            return None
        if _count_gfm_tables(markdown) > MARKDOWN_CARD_MAX_TABLES:
            return None
        images = images or {}
        elements = []
        md_buf = []
        cursor = 0

        def _flush_md():
            content = "".join(md_buf)
            if content.strip():
                elements.append({"tag": "markdown", "content": content})
            md_buf.clear()

        for index, span in enumerate(spans):
            md_buf.append(markdown[cursor:span["start"]])
            info = images.get(index) or {}
            key = info.get("key")
            if span["kind"] == "inline":
                if key:
                    md_buf.append(f"![]({key})")
                else:
                    md_buf.append(markdown[span["start"]:span["end"]])
            else:  # block → standalone img element
                if key:
                    _flush_md()
                    element = {"tag": "img", "img_key": key,
                               "alt": {"tag": "plain_text", "content": "公式"}}
                    width, height = info.get("width"), info.get("height")
                    if width and height:
                        dw, dh = block_display_size(width, height)
                        element["scale_type"] = "crop_center"
                        element["size"] = f"{dw}px {dh}px"
                    elements.append(element)
                    md_buf.append("\n")
                else:
                    md_buf.append(markdown[span["start"]:span["end"]])
            cursor = span["end"]
        md_buf.append(markdown[cursor:])
        _flush_md()
        if not elements:
            return None
        payload = json.dumps(
            {"schema": "2.0", "config": {"update_multi": True}, "body": {"elements": elements}},
            ensure_ascii=False,
        )
        if len(payload.encode("utf-8")) > MARKDOWN_CARD_MAX_BYTES:
            return None
        return payload
    except Exception:
        return None


async def card_payload_with_formula_images(markdown, *, render_batch, upload_png, cache=None, enabled=True):
    # Full card payload for a chunk, with formulas rendered to images when possible.
    # ``render_batch(items) -> [{"ok", "png", "width", "height", "error"}]`` and
    # ``upload_png(png_bytes) -> image_key`` are injected (adapter closures /
    # test fakes). Any formula that fails keeps its source text. Never raises:
    # returns the plain card payload (or None per its rules) on catastrophic failure.
    try:
        if not enabled or not isinstance(markdown, str) or "$" not in markdown:
            return build_markdown_card_payload(markdown)
        spans = extract_formula_spans(markdown)
        if not spans:
            return build_markdown_card_payload(markdown)
        cache = cache if cache is not None else {}
        images = {}
        pending = []  # (span_index, cache_key, span)
        for index, span in enumerate(spans):
            ck = formula_cache_key(span["kind"], span["latex"])
            if ck in cache:
                info = cache[ck]
                images[index] = info if isinstance(info, dict) else {"key": info}
            else:
                pending.append((index, ck, span))
        if pending:
            rendered = await render_batch([_render_item(span) for _, _, span in pending])
            for (index, ck, span), result in zip(pending, rendered):
                if not result or not result.get("ok") or not result.get("png"):
                    continue
                try:
                    key = await upload_png(result["png"])
                except Exception:
                    key = None
                if key:
                    info = {"key": key, "width": result.get("width"), "height": result.get("height")}
                    cache[ck] = info
                    images[index] = info
        return build_formula_card_payload(markdown, spans, images)
    except Exception:
        return build_markdown_card_payload(markdown)


def make_subprocess_renderer(python_exe, script_path, timeout=30):
    # Async render_batch backed by the in-image render-formula.py subprocess:
    # ONE process per message (matplotlib import dominates; stdout JSON protocol,
    # PNGs written to a temp dir read back into bytes). Any failure yields
    # per-item {"ok": False} — the pipeline then keeps source text.
    async def render_batch(items):
        import asyncio
        import subprocess
        import tempfile

        def _run():
            try:
                with tempfile.TemporaryDirectory(prefix="feishu-latex-") as outdir:
                    proc = subprocess.run(
                        [python_exe, script_path, "--outdir", outdir],
                        input=json.dumps({"items": items}).encode("utf-8"),
                        capture_output=True, timeout=timeout,
                    )
                    if proc.returncode != 0:
                        reason = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
                        return [{"ok": False, "error": f"renderer exit {proc.returncode}: {reason}"}
                                for _ in items]
                    try:
                        parsed = json.loads((proc.stdout or b"{}").decode("utf-8"))
                    except Exception:
                        return [{"ok": False, "error": "renderer produced invalid JSON"} for _ in items]
                    by_index = {}
                    for entry in parsed.get("results") or []:
                        if isinstance(entry, dict):
                            by_index[entry.get("index")] = entry
                    out = []
                    for i in range(len(items)):
                        entry = by_index.get(i) or {"ok": False, "error": "missing result"}
                        png = None
                        if entry.get("ok"):
                            try:
                                with open(os.path.join(outdir, f"{i}.png"), "rb") as fh:
                                    png = fh.read()
                            except Exception:
                                png = None
                        out.append({"ok": bool(entry.get("ok")) and png is not None, "png": png,
                                    "width": entry.get("width"), "height": entry.get("height"),
                                    "error": entry.get("error")})
                    return out
            except Exception as exc:
                return [{"ok": False, "error": f"renderer spawn failed: {exc}"} for _ in items]

        return await asyncio.to_thread(_run)

    return render_batch


def load_latex_image_cache(path):
    # Best-effort JSON load of {cache_key: {"key","width","height"}}. {} on any problem.
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_latex_image_cache(path, cache):
    # Best-effort atomic write (tmp + replace); silent on failure.
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass
