"""守卫：飞书公式→图片流水线的纯函数部分（docker/hermes/feishu_markdown_card.py 的 LaTeX 扩展）。

背景：飞书 IM 消息（post 与卡片同）不支持公式渲染，`$...$`/`$$...$$` 永远只是
源码文本（2026-10-14 实测 + 官方语法清单确认）。唯一真渲染路径 = 渲染成 PNG 嵌
卡片：行内用 `![](image_key)`（卡片 markdown 内嵌图，实测可用）、块级用独立 img
组件（`size` 属性控显示尺寸 —— 用户拍板参考公式 320px 宽等比；行内 A 档 ≈13px
字面与正文齐平）。卡片 body 因此是**多元素交错**：[markdown 段, img, markdown 段…]。

本测试钉住三块不变量：
① 解析：pandoc 规则（开 `$` 后无空格 / 闭 `$` 前无空格且**后不接数字**）+ 跳过
   代码块与行内代码（`$HOME` 不误伤）+ 货币反例（"$100 and $200" 不得匹配）
② 卡片组装与回退：行内→markdown 元素内 `![](key)`；块级→img 元素带 size；
   渲染/上传失败→**该段保留源码**；缓存命中→不重复渲染上传；表格超限→None
③ 流水线永不抛异常（最坏退化为普通卡片/None）；开关矩阵；缓存磁盘往返；
   子进程渲染器协议（stdin JSON → outdir PNG + stdout 结果清单）

Run: uv run --with pytest --with pyyaml pytest tests/test_feishu_latex.py -v
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "docker" / "hermes" / "feishu_markdown_card.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def m():
    return _load(MODULE_PATH, "feishu_markdown_card_latex")


def _spans(m, text):
    return m.extract_formula_spans(text)


def _mk_png():
    # 最小合法 PNG（1x1）够 fake/协议测试用；真实尺寸走 width/height 字段。
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63f8cfc0f01f00050001ff9d5a1d1d0000000049454e44ae426082")


# ---------------------------------------------------------------------------
# ① 解析
# ---------------------------------------------------------------------------


class TestFormulaSpans:
    def test_inline_basic(self, m):
        text = r"内积 $\langle v,u\rangle$ 的量法"
        spans = _spans(m, text)
        assert len(spans) == 1
        s = spans[0]
        assert s["kind"] == "inline"
        assert s["latex"] == r"\langle v,u\rangle"
        assert text[s["start"]:s["end"]] == r"$\langle v,u\rangle$"

    def test_block_single_line(self, m):
        spans = _spans(m, r"$$p=\frac{a}{b}$$")
        assert len(spans) == 1 and spans[0]["kind"] == "block"
        assert spans[0]["latex"] == r"p=\frac{a}{b}"

    def test_block_multiline(self, m):
        spans = _spans(m, "推导：\n$$\np = \\frac{a}{b}\n$$\n完毕")
        assert len(spans) == 1 and spans[0]["kind"] == "block"
        assert "frac" in spans[0]["latex"]

    def test_multiple_spans_in_order(self, m):
        spans = _spans(m, r"先 $a$ 再 $$b+c$$ 最后 $d$")
        assert [s["kind"] for s in spans] == ["inline", "block", "inline"]
        assert [s["latex"] for s in spans] == ["a", "b+c", "d"]

    def test_code_fence_skipped(self, m):
        spans = _spans(m, "```sh\nVAR=$HOME$x$\n```\n真公式 $a+b$")
        assert len(spans) == 1 and spans[0]["latex"] == "a+b"

    def test_inline_code_skipped(self, m):
        spans = _spans(m, "`$HOME` 和 `$x$` 不是公式，$a$ 是")
        assert len(spans) == 1 and spans[0]["latex"] == "a"

    @pytest.mark.parametrize("text", [
        "$100 and $200",           # 闭 $ 后接数字 → pandoc 规则拒绝
        "$5 to $10",
        "价格是 $100",              # 无配对
        "$ 有空格开头$",             # 开 $ 后空格
        "$结尾有空格 $",             # 闭 $ 前空格
        r"\$x\$ 被转义的美元",       # 转义
        "裸 $ 一个不配对",
    ])
    def test_false_positives_rejected(self, m, text):
        assert _spans(m, text) == []

    def test_inline_with_digits_is_math(self, m):
        spans = _spans(m, r"周长 $2\pi r$ 公式")
        assert len(spans) == 1 and spans[0]["latex"] == r"2\pi r"

    def test_render_item_carries_canvas_for_inline_only(self, m):
        # 行内必须带 600px 画布参数（客户端把窄图放大 → 模糊+大；画布=客户端显示宽
        # 才 1:1 清晰，2026-10-14 诊断卡实测）；块级走天然尺寸 + size 属性。
        inline = m._render_item({"kind": "inline", "latex": "a"})
        assert inline["canvas_width"] == m.INLINE_CANVAS_WIDTH
        assert inline["dpi"] == m.INLINE_RENDER_DPI
        block = m._render_item({"kind": "block", "latex": "a"})
        assert "canvas_width" not in block
        assert block["dpi"] == m.BLOCK_RENDER_DPI


# ---------------------------------------------------------------------------
# ② 卡片组装与回退
# ---------------------------------------------------------------------------


def _elements(m, payload):
    body = json.loads(payload)
    assert body["schema"] == "2.0"
    return body["body"]["elements"]


class TestCardAssembly:
    def test_inline_embedded_in_markdown_element(self, m):
        text = r"内积 $\langle v,u\rangle$ 的量法"
        spans = _spans(m, text)
        payload = m.build_formula_card_payload(text, spans, {0: {"key": "img_1"}})
        elements = _elements(m, payload)
        assert len(elements) == 1
        assert elements[0]["tag"] == "markdown"
        assert elements[0]["content"] == "内积 ![](img_1) 的量法"

    def test_block_becomes_sized_img_element(self, m):
        text = r"推导 $$p=q$$ 完毕"
        spans = _spans(m, text)
        payload = m.build_formula_card_payload(
            text, spans, {0: {"key": "img_b", "width": 622, "height": 196}})
        elements = _elements(m, payload)
        tags = [e["tag"] for e in elements]
        assert "img" in tags
        img = next(e for e in elements if e["tag"] == "img")
        assert img["img_key"] == "img_b"
        assert img["scale_type"] == "crop_center"
        dw, dh = m.block_display_size(622, 196)
        assert img["size"] == f"{dw}px {dh}px"
        # 前后文字各自成 markdown 段
        md = [e for e in elements if e["tag"] == "markdown"]
        assert any("推导" in e["content"] for e in md)
        assert any("完毕" in e["content"] for e in md)

    def test_missing_image_keeps_source_text(self, m):
        text = r"$a$ 和 $b$"
        spans = _spans(m, text)
        payload = m.build_formula_card_payload(text, spans, {1: {"key": "img_b"}})
        elements = _elements(m, payload)
        content = "".join(e["content"] for e in elements if e["tag"] == "markdown")
        assert "$a$" in content       # 第一段无 key → 留源码
        assert "![](img_b)" in content

    def test_formula_only_message_yields_img_only(self, m):
        text = r"$$E=mc^2$$"
        spans = _spans(m, text)
        payload = m.build_formula_card_payload(
            text, spans, {0: {"key": "img_e", "width": 300, "height": 120}})
        elements = _elements(m, payload)
        assert elements[0]["tag"] == "img"

    def test_table_overflow_returns_none(self, m):
        table = "| a | b |\n|---|---|\n| 1 | 2 |"
        text = "$x$ " + "\n\n".join([table] * 5)
        spans = _spans(m, text)
        assert m.build_formula_card_payload(text, spans, {0: {"key": "img"}}) is None


# ---------------------------------------------------------------------------
# 缓存键 / 展示尺寸 / 开关 / 持久化
# ---------------------------------------------------------------------------


class TestCacheKeyAndSizing:
    def test_cache_key_stable_and_sensitive(self, m):
        k1 = m.formula_cache_key("inline", r"\frac{a}{b}")
        k2 = m.formula_cache_key("inline", r"\frac{a}{b}")
        k3 = m.formula_cache_key("block", r"\frac{a}{b}")
        k4 = m.formula_cache_key("inline", r"\frac{a}{c}")
        assert k1 == k2 and k1 != k3 and k1 != k4

    def test_block_display_size_keeps_ratio(self, m):
        w, h = m.block_display_size(622, 196)
        assert w == 320
        assert abs(h - round(196 * 320 / 622)) <= 1

    def test_block_display_size_capped_at_1000(self, m):
        w, h = m.block_display_size(4000, 200)
        assert w <= 1000
        assert abs((w / h) - (4000 / 200)) < 0.05

    @pytest.mark.parametrize("value", ["0", "off", "false", "no", "disabled", " OFF "])
    def test_switch_disabled(self, m, value):
        assert m.latex_images_enabled(env={m.LATEX_IMAGE_ENV: value}) is False

    @pytest.mark.parametrize("env", [{}, {"FEISHU_LATEX_IMAGE": "1"}, {"FEISHU_LATEX_IMAGE": "on"}])
    def test_switch_default_on(self, m, env):
        assert m.latex_images_enabled(env=env) is True

    def test_cache_persistence_roundtrip(self, m, tmp_path):
        p = str(tmp_path / "cache.json")
        data = {"abc": {"key": "img_1", "width": 320, "height": 101}}
        m.save_latex_image_cache(p, data)
        assert m.load_latex_image_cache(p) == data
        assert m.load_latex_image_cache(str(tmp_path / "missing.json")) == {}
        (tmp_path / "corrupt.json").write_text("{oops")
        assert m.load_latex_image_cache(str(tmp_path / "corrupt.json")) == {}


# ---------------------------------------------------------------------------
# ③ 编排（异步，注入 fake renderer/uploader）
# ---------------------------------------------------------------------------


class _Fakes:
    def __init__(self, render_ok=True, fail_index=None, upload_ok=True):
        self.render_calls = 0
        self.upload_calls = 0
        self.render_ok = render_ok
        self.fail_index = fail_index
        self.upload_ok = upload_ok

    async def render_batch(self, items):
        self.render_calls += 1
        out = []
        for i, _ in enumerate(items):
            if not self.render_ok or self.fail_index == i:
                out.append({"ok": False, "error": "render failed"})
            else:
                out.append({"ok": True, "png": _mk_png(), "width": 622, "height": 196})
        return out

    async def upload(self, png):
        self.upload_calls += 1
        if not self.upload_ok:
            raise RuntimeError("upload failed")
        return f"img_fake_{self.upload_calls}"


def _run(coro):
    return asyncio.run(coro)


class TestOrchestration:
    def test_no_formulas_returns_plain_card_without_rendering(self, m):
        f = _Fakes()
        text = "**普通回复**没有公式"
        out = _run(m.card_payload_with_formula_images(
            text, render_batch=f.render_batch, upload_png=f.upload))
        elements = _elements(m, out)
        assert len(elements) == 1 and elements[0]["content"] == text
        assert f.render_calls == 0 and f.upload_calls == 0

    def test_happy_path_inline_and_block(self, m):
        f = _Fakes()
        text = r"内积 $\langle v,u\rangle$ 与 $$p=q$$"
        out = _run(m.card_payload_with_formula_images(
            text, render_batch=f.render_batch, upload_png=f.upload))
        elements = _elements(m, out)
        content = "".join(e.get("content", "") for e in elements if e["tag"] == "markdown")
        assert "img_fake_1" in content               # 行内嵌进 markdown 段
        img = next(e for e in elements if e["tag"] == "img")
        assert img["img_key"] == "img_fake_2"        # 块级独立 img
        assert img["size"].startswith("320px ")

    def test_render_failure_keeps_that_span_source(self, m):
        f = _Fakes(fail_index=0)
        text = r"$a$ 和 $b$"
        out = _run(m.card_payload_with_formula_images(
            text, render_batch=f.render_batch, upload_png=f.upload))
        elements = _elements(m, out)
        content = "".join(e.get("content", "") for e in elements if e["tag"] == "markdown")
        assert "$a$" in content                       # 第一段失败 → 源码
        assert "img_fake_1" in content                # 第二段成功

    def test_upload_failure_keeps_source(self, m):
        f = _Fakes(upload_ok=False)
        text = r"$a+b$"
        out = _run(m.card_payload_with_formula_images(
            text, render_batch=f.render_batch, upload_png=f.upload))
        elements = _elements(m, out)
        assert "$a+b$" in elements[0]["content"]

    def test_cache_hit_skips_render_and_upload(self, m):
        f = _Fakes()
        text = r"$a+b$"
        cache = {}
        out1 = _run(m.card_payload_with_formula_images(
            text, render_batch=f.render_batch, upload_png=f.upload, cache=cache))
        assert f.render_calls == 1 and f.upload_calls == 1
        out2 = _run(m.card_payload_with_formula_images(
            text, render_batch=f.render_batch, upload_png=f.upload, cache=cache))
        assert out2 == out1
        assert f.render_calls == 1 and f.upload_calls == 1
        assert cache  # 缓存已写入

    def test_disabled_switch_returns_plain_card(self, m):
        f = _Fakes()
        text = r"$a+b$"
        out = _run(m.card_payload_with_formula_images(
            text, render_batch=f.render_batch, upload_png=f.upload, enabled=False))
        elements = _elements(m, out)
        assert elements[0]["content"] == text and f.render_calls == 0

    def test_renderer_raising_never_breaks_message(self, m):
        async def boom(items):
            raise RuntimeError("renderer exploded")
        text = r"$a+b$ 后面还有字"
        out = _run(m.card_payload_with_formula_images(
            text, render_batch=boom, upload_png=_Fakes().upload))
        elements = _elements(m, out)
        assert text in elements[0]["content"]         # 退回原始源码的普通卡片

    def test_no_empty_image_markdown_leaks(self, m):
        f = _Fakes()
        text = r"$a$ 中间 $b$ 结尾"
        out = _run(m.card_payload_with_formula_images(
            text, render_batch=f.render_batch, upload_png=f.upload))
        content = "".join(e.get("content", "") for e in elements_of(out) if e["tag"] == "markdown")
        assert "![]( )" not in content and content.count("![](img_fake_") == 2


def elements_of(payload):
    return json.loads(payload)["body"]["elements"]


# ---------------------------------------------------------------------------
# 子进程渲染器协议（用假渲染脚本在宿主机全链路验证）
# ---------------------------------------------------------------------------

_FAKE_RENDERER = '''#!/usr/bin/env python3
import json, os, struct, sys

outdir = None
args = sys.argv[1:]
if "--outdir" in args:
    outdir = args[args.index("--outdir") + 1]
payload = json.loads(sys.stdin.read() or "{}")
results = []
for i, item in enumerate(payload.get("items", [])):
    if item.get("latex") == "boom":
        results.append({"index": i, "ok": False, "error": "mathtext parse error"})
        continue
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63f8cfc0f01f00050001ff9d5a1d1d0000000049454e44ae426082")
    with open(os.path.join(outdir, f"{i}.png"), "wb") as fh:
        fh.write(png)
    results.append({"index": i, "ok": True, "width": 200, "height": 60})
print(json.dumps({"results": results}))
'''


class TestSubprocessRenderer:
    def test_protocol_roundtrip_with_fake_script(self, m, tmp_path):
        script = tmp_path / "fake-render.py"
        script.write_text(_FAKE_RENDERER)
        render = m.make_subprocess_renderer(sys_executable(), str(script))
        items = [{"kind": "inline", "latex": "a+b", "font_size": 7.5, "dpi": 90, "pad": 0.15},
                 {"kind": "block", "latex": "boom", "font_size": 26, "dpi": 200, "pad": 0.15}]
        results = _run(render(items))
        assert results[0]["ok"] is True and results[0]["png"]
        assert results[0]["width"] == 200 and results[0]["height"] == 60
        assert results[1]["ok"] is False and "parse error" in results[1]["error"]

    def test_missing_script_yields_per_item_failure(self, m, tmp_path):
        render = m.make_subprocess_renderer(sys_executable(), str(tmp_path / "nope.py"))
        results = _run(render([{"kind": "inline", "latex": "a", "font_size": 7.5, "dpi": 90, "pad": 0.15}]))
        assert results[0]["ok"] is False and results[0]["error"]


def sys_executable():
    import sys
    return sys.executable
