"""``docker/hermes/patch-image-limit.py`` —— 构建期给上游分类器打的补丁。

2026-11-12 现场（贝小爷 / finance profile）：用户发了一张 1220×24948 的安卓长截图，
飞书收下、Hermes 原生内联（`Image routing: native`），DeepSeek 回 400：

    .messages[3].image[0]: You have uploaded an unsupported image. Please make sure
    your image is valid and has one of the following formats: webp, png, jpeg, and gif.

这句话不含任何尺寸词汇 ⇒ 上游 ``error_classifier`` 判成 ``format_error``
（retryable=False）⇒ 直接终止，用户只看到「The model provider failed after retries」。
实测同一张图缩到最长边 8000px（391×8000）后返回 200 —— 这是**尺寸**问题，而上游
本来就有「超限 → 缩图重试一次」的恢复链（``turn_recovery`` 的 image-shrink 分支，
默认阈值恰好也是 8000），只是没被触发。

补丁把这句话加进 ``_IMAGE_TOO_LARGE_PATTERNS``。本文件钉住四件事：
词表措辞与现场报错一致、锚点只命中一次、二次运行幂等、失配时**构建失败**
（base image 是 ``:latest``，静默出厂未打补丁的分类器才是最贵的失败模式）。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "docker" / "hermes" / "patch-image-limit.py"
DOCKERFILE = REPO_ROOT / "docker" / "hermes" / "Dockerfile"

# 现场原文（小写化后就是分类器比对的形态）
DEEPSEEK_ERROR = (
    ".messages[3].image[0]: You have uploaded an unsupported image. Please make sure "
    "your image is valid and has one of the following formats: webp, png, jpeg, and gif. "
    "(request_id: 48a4f7f7-7fc1-45b6-9453-db11f606f3e4)"
)

# 上游 error_classifier.py 的这段（原样摘录，含紧邻注释里那次 "patches after
# processing" —— 正因如此锚点必须带上收尾括号，否则会二次命中）
UPSTREAM = '''# Per-image size/dimension 400s (Anthropic 5 MB / 8000 px; MiniMax "media
# exceeds size limit" #76039) — a specific 400 before the request hits 413. A
# non-image media hit is harmless: the shrink pass finds no image parts.
# "patches after processing": OpenAI Codex Responses rejects an image whose
# tile-patch budget (ceil(w/32)xceil(h/32)) exceeds its 30000-patch ceiling
# with wording that names no image-size vocabulary — without this pattern it
# fell to format_error (non-retryable), bypassing the shrink recovery (#106337).
_IMAGE_TOO_LARGE_PATTERNS = (
    "image exceeds", "image too large", "image_too_large", "image size exceeds", "image dimensions exceed",
    "dimensions exceed max allowed size", "max allowed size: 8000", "media exceeds", "media too large",
    "patches after processing",
)
'''


def _mod():
    spec = importlib.util.spec_from_file_location("patch_image_limit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPatchText:
    def test_pattern_matches_the_live_error(self):
        """措辞对不上就等于没打补丁：分类器拿小写后的报错做子串匹配。"""
        mod = _mod()
        assert mod.PATTERN in DEEPSEEK_ERROR.lower()

    def test_pattern_is_lowercase(self):
        mod = _mod()
        assert mod.PATTERN == mod.PATTERN.lower()

    def test_patch_inserts_pattern_once(self):
        mod = _mod()
        new, status = mod.apply_text(UPSTREAM)
        assert status == "written"
        assert new.count(mod.SENTINEL) == 1, "标记重复 —— 补丁落了两处"
        assert new.count(f'"{mod.PATTERN}"') == 1
        # 插进 tuple 内、且在收尾括号之前
        tuple_start = new.index("_IMAGE_TOO_LARGE_PATTERNS = (")
        pattern_at = new.index(f'"{mod.PATTERN}"')
        assert tuple_start < pattern_at < new.index("\n)\n", pattern_at)
        # 手术后的文件必须仍是合法 Python
        compile(new, "error_classifier.py", "exec")

    def test_prose_mention_does_not_confuse_the_anchor(self):
        """注释里也出现 "patches after processing" —— 锚点必须带收尾括号。"""
        mod = _mod()
        assert UPSTREAM.count("patches after processing") == 2, "夹具已不覆盖该陷阱"
        _, status = mod.apply_text(UPSTREAM)
        assert status == "written"

    def test_idempotent(self):
        mod = _mod()
        once, _ = mod.apply_text(UPSTREAM)
        twice, status = mod.apply_text(once)
        assert status == "already"
        assert twice == once

    def test_missing_anchor_is_loud(self):
        mod = _mod()
        _, status = mod.apply_text("_IMAGE_TOO_LARGE_PATTERNS = ('a', 'b')\n")
        assert status == "anchor-missing"

    def test_duplicate_anchor_is_loud(self):
        mod = _mod()
        _, status = mod.apply_text(UPSTREAM + UPSTREAM)
        assert status.startswith("anchor-ambiguous")


class TestPatchEntrypoint:
    def _run(self, path: Path) -> int:
        mod = _mod()
        return mod.main(["--path", str(path)])

    def test_writes_file_and_returns_zero(self, tmp_path):
        target = tmp_path / "error_classifier.py"
        target.write_text(UPSTREAM, encoding="utf-8")
        assert self._run(target) == 0
        assert mod_pattern() in target.read_text(encoding="utf-8")
        # 二次运行仍然 0（已打过）
        assert self._run(target) == 0

    def test_mismatch_exits_nonzero_so_the_build_fails(self, tmp_path):
        target = tmp_path / "error_classifier.py"
        target.write_text("_OTHER = ()\n", encoding="utf-8")
        assert self._run(target) == 1

    def test_missing_file_exits_two(self, tmp_path):
        assert self._run(tmp_path / "nope.py") == 2


def mod_pattern() -> str:
    return _mod().PATTERN


class TestDockerfileWiring:
    def test_patch_is_copied_and_run_at_build(self):
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert "COPY patch-image-limit.py /tmp/" in text
        assert "/tmp/patch-image-limit.py" in text, "补丁没被 RUN（COPY 了却不执行）"
        # RUN 与 rm 同一条链：中间失败（锚点失配）要让 build 挂掉，而不是留下残件
        assert re.search(r"RUN /opt/hermes/\.venv/bin/python3 /tmp/patch-image-limit\.py && \\\n", text)

