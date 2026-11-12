#!/usr/bin/env python3
# [myopenclaw] Build-time text surgery on the upstream Hermes image:
# teach the API-error classifier that DeepSeek's "unsupported image" means
# *size limit*, not *bad format*.
#
# 现场（2026-11-12，贝小爷 finance profile）：用户发了一张 1220×24948 的安卓长截图，
# DeepSeek 回
#   400 .messages[3].image[0]: You have uploaded an unsupported image. Please make
#   sure your image is valid and has one of the following formats: webp, png, jpeg,
#   and gif.
# 措辞里没有任何尺寸词汇 ⇒ 上游 `error_classifier` 落到 `format_error`
# （retryable=False，ABORT_FALLBACK）⇒ 直接终止，用户只收到「model provider failed
# after retries」。实测同一张图缩到最长边 8000px（391×8000）后 DeepSeek 正常返回，
# 也就是说这是**尺寸**问题，而上游本来就有「超限就缩图重试一次」的恢复链
# （`turn_recovery` 的 image-shrink 分支，默认阈值恰好也是 8000），只是没被触发。
#
# 补法：把 DeepSeek 的措辞加进 `_IMAGE_TOO_LARGE_PATTERNS`。classification 变成
# `image_too_large`（retryable=True）→ 走缩图重试。真·坏字节的图仍由
# `_IMAGE_CORRUPT_PATTERNS` 先匹配（`_IMAGE_TOOL_RULES` 里 corrupt 排在 too_large
# 之前），不会被这条误抓。
#
# Runs inside `docker compose build hermes`. Base image is
# `nousresearch/hermes-agent:latest` (unpinned) — the anchor must occur EXACTLY
# ONCE, and any mismatch exits non-zero so the BUILD fails loudly instead of
# silently shipping an unpatched classifier. Idempotent: a second run on an
# already-patched file exits 0.
#
# Contract with tests/test_hermes_image_limit_patch.py:
#   - ANCHOR_OLD / ANCHOR_NEW / SENTINEL / PATTERN are imported by the test to
#     build its fixture; changing them here means updating the fixture there.
#   - The phrase must stay lowercase — the classifier matches against
#     ``str(error).lower()``.

import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_PATH = "/opt/hermes/agent/error_classifier.py"

SENTINEL = "[myopenclaw] deepseek unsupported-image = size limit"
PATTERN = "uploaded an unsupported image"

# 上游 tuple 的末尾两项：最后一条 pattern + 收尾括号。**必须带上括号** ——
# "patches after processing" 在紧邻的注释里也出现过一次（grep -c = 2），
# 只锚那一行会二次命中。
ANCHOR_OLD = '''    "patches after processing",
)
'''

ANCHOR_NEW = f'''    "patches after processing",
    # {SENTINEL}
    # DeepSeek 把「尺寸超限」说成 "unsupported image"（实测 1220×24948 的长截图；
    # 缩到最长边 8000px 即通过），措辞里没有任何尺寸词汇 ⇒ 上游判成 format_error
    # 直接终止。归一到 image_too_large 后走 turn_recovery 的缩图重试。
    "{PATTERN}",
)
'''


def apply_text(text: str) -> tuple[str, str]:
    """返回 ``(新文本, 状态)``；状态为 ``written`` / ``already`` / ``anchor-missing``。"""
    if SENTINEL in text:
        return text, "already"
    count = text.count(ANCHOR_OLD)
    if count == 0:
        return text, "anchor-missing"
    if count > 1:
        return text, f"anchor-ambiguous:{count}"
    return text.replace(ANCHOR_OLD, ANCHOR_NEW, 1), "written"


def apply_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    new_text, status = apply_text(text)
    if status == "written":
        tmp = path.with_name(path.name + ".myopenclaw-tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)
        shutil.rmtree(path.parent / "__pycache__", ignore_errors=True)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=Path(DEFAULT_PATH))
    args = parser.parse_args(argv)

    path: Path = args.path
    if not path.is_file():
        print(f"[patch-image-limit] 目标文件不存在: {path}", file=sys.stderr)
        return 2
    status = apply_file(path)
    if status == "written":
        print(f"[patch-image-limit] patched OK ({path})")
        return 0
    if status == "already":
        print("[patch-image-limit] already patched — nothing to do")
        return 0
    # 锚点失配 = 上游改了这段代码，必须让 BUILD 失败，不能静默出厂未打补丁的镜像
    print(
        f"[patch-image-limit] 锚点失配（{status}）—— 上游 error_classifier.py 结构变了，"
        f"请重新对齐 ANCHOR_OLD 后再构建",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
