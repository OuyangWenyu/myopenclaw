#!/usr/bin/env python3
"""Idempotently migrate Hermes profile configs' main model to ``deepseek-flash``.

Hermes profile 配置是**宿主机手工文件**，``start.sh`` 只在文件不存在时写模板 ——
所以既有的机器（或从快照 ``restore.sh`` 恢复出来的机器）不会被自动迁移，
会**静默地**继续跑在旧模型上，而仓库文档已经宣称三个 agent 统一是
``deepseek-flash``。这个脚本补上那一步。

只改写「已知的历史取值」，绝不覆盖操作者的主动选择：

    mimo-v2.5 / mimo-v2.5-pro   小米 MiMo 时期的主模型
    deepseek-v<数字>…           已下线的 DeepSeek V 系列 id

其余任何值都原样保留（返回 ``unchanged:<value>``），与
``scripts/ensure_hermes_web_search.py`` 的「不覆盖操作者选择」原则一致。

Surgical text edits 保留注释与键序，不依赖 PyYAML。

Called from ``scripts/start.sh``.

Usage:
    python3 scripts/ensure_hermes_model.py ~/.hermes/config.yaml [更多配置...]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MODEL = "deepseek-flash"
PROVIDER = "deepseek"
BASE_URL = "https://api.deepseek.com"

# 已知的历史取值（应当被迁移走）
_LEGACY_EXACT = {"mimo-v2.5", "mimo-v2.5-pro"}
_LEGACY_DEEPSEEK_V = re.compile(r"^deepseek-v\d")

_MODEL_BLOCK_HEADER = re.compile(r"(?m)^model:\s*$")
_KEY_LINE = "  {key}: {value}\n"


def _strip(value: str) -> str:
    return value.strip().strip("\"'").split("#", 1)[0].strip()


def is_legacy_model(value: str) -> bool:
    """判断一个 model.default 取值是否属于「应被迁移的已知历史值」。"""
    bare = _strip(value)
    return bare in _LEGACY_EXACT or bool(_LEGACY_DEEPSEEK_V.match(bare))


def _find_model_block(lines: list[str]) -> tuple[int, int] | None:
    """定位顶层 ``model:`` 块的行区间 ``[start, end)``。"""
    start = None
    for i, line in enumerate(lines):
        if _MODEL_BLOCK_HEADER.match(line):
            start = i
            break
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    return start, end


def current_model(text: str) -> str | None:
    """读出 ``model.default`` 的当前取值。"""
    block = _find_model_block(text.splitlines(keepends=True))
    if block is None:
        return None
    lines = text.splitlines(keepends=True)
    for line in lines[block[0] + 1 : block[1]]:
        m = re.match(r"^[ \t]+default:[ \t]*(.*)$", line)
        if m:
            return _strip(m.group(1))
    return None


def _set_key(lines: list[str], start: int, end: int, key: str, value: str) -> bool:
    """在 ``model:`` 块内改写（或补写）一个键，返回是否产生了改动。"""
    pattern = re.compile(rf"^[ \t]+{re.escape(key)}:[ \t]*(.*)$")
    for i in range(start + 1, end):
        m = pattern.match(lines[i])
        if m:
            if _strip(m.group(1)) == value:
                return False
            lines[i] = _KEY_LINE.format(key=key, value=value)
            return True
    # 键不存在：紧跟 model: 之后补写
    lines.insert(start + 1, _KEY_LINE.format(key=key, value=value))
    return True


def ensure_model(config_text: str) -> tuple[str, str]:
    """返回 ``(新文本, 状态)``；状态为 ``written`` / ``unchanged:<值>`` / ``no-model-block``。"""
    block = _find_model_block(config_text.splitlines(keepends=True))
    if block is None:
        return config_text, "no-model-block"

    value = current_model(config_text)
    if value is None or not is_legacy_model(value):
        return config_text, f"unchanged:{value}"

    lines = config_text.splitlines(keepends=True)
    changed = False
    for key, new_value in (("default", MODEL), ("provider", PROVIDER), ("base_url", BASE_URL)):
        changed |= _set_key(lines, block[0], block[1], key, new_value)
    return ("".join(lines), "written") if changed else (config_text, "unchanged:" + str(value))


def apply_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    new_text, status = ensure_model(text)
    if status == "written":
        path.write_text(new_text, encoding="utf-8")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configs", nargs="+", type=Path, help="Hermes 配置文件路径")
    args = parser.parse_args(argv)

    failures = 0
    for path in args.configs:
        if not path.is_file():
            continue
        try:
            print(f"{path} → {apply_file(path)}")
        except OSError as exc:  # 读/写失败要让调用方看见，不静默吞掉
            print(f"{path} → error: {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
