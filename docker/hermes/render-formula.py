#!/usr/bin/env python3
# [myopenclaw] LaTeX → PNG renderer for the Feishu formula pipeline.
#
# Runs as a SUBPROCESS of the Hermes gateway (see make_subprocess_renderer in
# docker/hermes/feishu_markdown_card.py) so matplotlib never loads into the
# gateway process itself. One process per message batch; matplotlib's import
# cost is paid once per batch, not once per formula.
#
# Protocol:
#   argv:  --outdir DIR
#   stdin: {"items": [{"kind": "inline"|"block", "latex": str,
#                      "font_size": float, "dpi": int, "pad": float}]}
#   stdout: {"results": [{"index": i, "ok": true, "width": int, "height": int}
#                        | {"index": i, "ok": false, "error": str}]}
#   side effect: DIR/{i}.png for every ok item.
#   Per-item failures never fail the batch (exit 0 unless the payload itself is
#   unreadable); render parameters come from the caller (single source of truth
#   lives in feishu_markdown_card.py and rides in the JSON).

import argparse
import json
import os
import struct
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Render LaTeX formulas to PNG (matplotlib mathtext)")
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args()

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        print(json.dumps({"results": [], "error": f"invalid stdin JSON: {exc}"}))
        return 0
    items = payload.get("items") or []

    results = []
    try:
        import io

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import rcParams

        # Computer Modern — the TeX look the user approved in the sizing probe.
        rcParams["mathtext.fontset"] = "cm"
    except Exception as exc:
        for i in range(len(items)):
            results.append({"index": i, "ok": False, "error": f"matplotlib unavailable: {exc}"[:300]})
        print(json.dumps({"results": results}))
        return 0

    def _render_inline_canvas(path, latex, font_size, dpi, pad, canvas_width, margin_x, margin_y):
        # The Feishu client normalizes INLINE image display width: narrow images get
        # upscaled (blurry + huge). Diagnostic card verdict (2026-10-14): a canvas
        # matching the client's display width (~600px) renders 1:1 and normal. So:
        # tight-render in memory, then compose centered on the fixed-width canvas.
        fig = plt.figure(figsize=(0.01, 0.01), dpi=dpi)
        fig.patch.set_facecolor("white")
        fig.text(0, 0, f"${latex}$", fontsize=font_size, color="#1f2329")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0, facecolor="white")
        plt.close(fig)
        buf.seek(0)
        tight = plt.imread(buf, format="png")
        glyph_h, glyph_w = tight.shape[0], tight.shape[1]
        out_w = max(canvas_width, glyph_w + 2 * margin_x)
        out_h = glyph_h + 2 * margin_y
        canvas = plt.figure(figsize=(out_w / dpi, out_h / dpi), dpi=dpi)
        canvas.patch.set_facecolor("white")
        axes = canvas.add_axes((0, 0, 1, 1))
        axes.set_axis_off()
        axes.imshow(tight, extent=(margin_x, margin_x + glyph_w, margin_y, margin_y + glyph_h))
        axes.set_xlim(0, out_w)
        axes.set_ylim(0, out_h)
        canvas.savefig(path, dpi=dpi, facecolor="white")
        plt.close(canvas)

    for i, item in enumerate(items):
        try:
            latex = str(item.get("latex") or "")
            if not latex.strip():
                raise ValueError("empty latex")
            font_size = float(item.get("font_size") or 20)
            dpi = int(item.get("dpi") or 200)
            pad = item.get("pad")
            pad = 0.15 if pad is None else float(pad)
            kind = str(item.get("kind") or "block")

            path = os.path.join(args.outdir, f"{i}.png")
            if kind == "inline" and item.get("canvas_width"):
                _render_inline_canvas(path, latex, font_size, dpi, pad,
                                      int(item["canvas_width"]),
                                      int(item.get("canvas_margin_x") or 24),
                                      int(item.get("canvas_margin_y") or 8))
            else:
                fig = plt.figure(figsize=(0.01, 0.01), dpi=dpi)
                fig.patch.set_facecolor("white")
                fig.text(0, 0, f"${latex}$", fontsize=font_size, color="#1f2329")
                fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=pad, facecolor="white")
                plt.close(fig)

            with open(path, "rb") as fh:
                header = fh.read(24)
            width, height = struct.unpack(">II", header[16:24])
            results.append({"index": i, "ok": True, "width": width, "height": height})
        except Exception as exc:
            results.append({"index": i, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})

    print(json.dumps({"results": results}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
