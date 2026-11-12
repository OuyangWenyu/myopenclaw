#!/usr/bin/env python3
"""幂等维护 Hermes profile 配置的**主模型**与**备份模型**。

主模型：把已知的历史取值迁移到 ``deepseek-flash``。
备份模型：确保 ``fallback_providers`` 含 ``zai``、``fallback_model`` 是
``zai / glm-5.3-flash``（2026-11-12 补：finance 一个 fallback 都没配，其余三个
配了但缺凭据 —— 凭据现在由 compose 注入 ``GLM_API_KEY``）。

Hermes profile 配置是**宿主机手工文件**，``start.sh`` 只在文件不存在时写模板 ——
所以既有的机器（或从快照 ``restore.sh`` 恢复出来的机器）不会被自动迁移，
会**静默地**继续跑在旧模型上，而仓库文档已经宣称三个 agent 统一是
``deepseek-flash``。这个脚本补上那一步。

只改写「已知的历史取值」与「缺失/空转的备份配置」，绝不覆盖操作者的主动选择：

    mimo-v2.5 / mimo-v2.5-pro   小米 MiMo 时期的主模型
    deepseek-v<数字>…           已下线的 DeepSeek V 系列 id

其余任何值都原样保留（返回 ``unchanged:<value>``）；``fallback_model`` 若不是
``zai/glm-5.3-flash`` 也原样保留（返回 ``unchanged-fallback:<p>/<m>``），与
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


# ── 备份模型（fallback）─────────────────────────────────────────────
# 2026-11-12：四个 profile 里三个写了 fallback_model，finance 一个都没写 —— 而
# 且三个写了的那份**也是空转的**（容器里没有 GLM_API_KEY/ZAI_API_KEY，zai 无凭据，
# 主模型一失败就是「model provider failed after retries」）。凭据由 compose 注入，
# 这里只负责把「配置形状」补齐，且与主模型同一原则：不覆盖操作者显式选的别的备份。
FALLBACK_PROVIDER = "zai"
FALLBACK_MODEL = "glm-5.3-flash"

_FALLBACK_MODEL_HEADER = re.compile(r"^fallback_model:[ \t]*(.*)$")
_FALLBACK_PROVIDERS_HEADER = re.compile(r"^fallback_providers:[ \t]*(.*)$")


def _find_header(lines: list[str], header: re.Pattern[str]) -> int | None:
    for i, line in enumerate(lines):
        if header.match(line):
            return i
    return None


def _inline_value(line: str, header: re.Pattern[str]) -> str:
    m = header.match(line)
    return _strip(m.group(1)) if m else ""


def _block_end(lines: list[str], start: int) -> int:
    """``start`` 是头部行，返回块结束行（不含）：块内 = 缩进行或 ``- `` 列表项。"""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            break
        if line.startswith((" ", "\t")) or line.lstrip().startswith("- "):
            end += 1
            continue
        break
    return end


def current_fallback(text: str) -> tuple[str | None, str | None]:
    """读 ``fallback_model`` 的 ``(provider, model)``；键不存在返回 ``(None, None)``。"""
    lines = text.splitlines(keepends=True)
    i = _find_header(lines, _FALLBACK_MODEL_HEADER)
    if i is None:
        return None, None
    inline = _inline_value(lines[i], _FALLBACK_MODEL_HEADER)
    if inline:  # 行内映射（少见）：尽力解析；解析不出交给调用方当「操作者自选」
        p = re.search(r"provider:\s*([^,}]+)", inline)
        m = re.search(r"model:\s*([^,}]+)", inline)
        return (_strip(p.group(1)) if p else None, _strip(m.group(1)) if m else None)
    provider = model = None
    for line in lines[i + 1 : _block_end(lines, i)]:
        m = re.match(r"^[ \t]+provider:[ \t]*(.*)$", line)
        if m:
            provider = _strip(m.group(1))
        m = re.match(r"^[ \t]+model:[ \t]*(.*)$", line)
        if m:
            model = _strip(m.group(1))
    return provider, model


def _ensure_fallback_provider(text: str) -> tuple[str, bool]:
    """``fallback_providers`` 必须含 zai（行内 ``[]`` 会被改写成块列表）。"""
    lines = text.splitlines(keepends=True)
    i = _find_header(lines, _FALLBACK_PROVIDERS_HEADER)
    if i is None:
        j = _find_header(lines, _FALLBACK_MODEL_HEADER)
        if j is None:
            blk = _find_model_block(lines)
            j = blk[1] if blk else len(lines)
        lines[j:j] = ["fallback_providers:\n", f"- {FALLBACK_PROVIDER}\n"]
        return "".join(lines), True

    inline = _inline_value(lines[i], _FALLBACK_PROVIDERS_HEADER)
    end = _block_end(lines, i)
    if inline:
        items = [x.strip().strip("\"'") for x in inline.strip("[]").split(",") if x.strip()]
        if FALLBACK_PROVIDER in items:
            return text, False
        items.append(FALLBACK_PROVIDER)
        # 行内形态 → 块列表：上游默认配置本来就是 `fallback_providers:\n- zai` 这个形状
        lines[i:end] = ["fallback_providers:\n"] + [f"- {item}\n" for item in items]
        return "".join(lines), True

    present = {
        _strip(line.lstrip()[2:])
        for line in lines[i + 1 : end]
        if line.lstrip().startswith("- ")
    }
    if FALLBACK_PROVIDER in present:
        return text, False
    lines[end:end] = [f"- {FALLBACK_PROVIDER}\n"]
    return "".join(lines), True


def _ensure_fallback_model(text: str) -> tuple[str, bool]:
    """``fallback_model`` 必须是 zai / glm-5.3-flash（缺键补键，不改其它形状）。"""
    lines = text.splitlines(keepends=True)
    i = _find_header(lines, _FALLBACK_MODEL_HEADER)
    if i is None:
        j = _find_header(lines, _FALLBACK_PROVIDERS_HEADER)
        at = _block_end(lines, j) if j is not None else len(lines)
        lines[at:at] = [
            "fallback_model:\n",
            f"  provider: {FALLBACK_PROVIDER}\n",
            f"  model: {FALLBACK_MODEL}\n",
        ]
        return "".join(lines), True

    if _inline_value(lines[i], _FALLBACK_MODEL_HEADER):
        return text, False  # 行内映射的形状不改写；取值已由 current_fallback 判过

    end = _block_end(lines, i)
    block = lines[i + 1 : end]
    changed = False
    for key, value in (("provider", FALLBACK_PROVIDER), ("model", FALLBACK_MODEL)):
        pattern = re.compile(rf"^[ \t]+{re.escape(key)}:[ \t]*(.*)$")
        for k, line in enumerate(block):
            m = pattern.match(line)
            if m:
                if _strip(m.group(1)) != value:
                    block[k] = f"  {key}: {value}\n"
                    changed = True
                break
        else:
            block.append(f"  {key}: {value}\n")
            changed = True
    if changed:
        lines[i + 1 : end] = block
        return "".join(lines), True
    return text, False


def ensure_fallback(config_text: str) -> tuple[str, str]:
    """幂等补齐备份模型；状态 ``written`` / ``unchanged-fallback:<p>/<m>``。"""
    provider, model = current_fallback(config_text)
    if (provider is not None or model is not None) and (provider, model) != (
        FALLBACK_PROVIDER,
        FALLBACK_MODEL,
    ):
        # 操作者显式选了别的备份模型（或形状不认识）→ 一个字都不动
        return config_text, f"unchanged-fallback:{provider or '?'}/{model or '?'}"

    text, changed = _ensure_fallback_provider(config_text)
    text, changed_model = _ensure_fallback_model(text)
    changed = changed or changed_model
    if changed:
        return text, "written"
    return config_text, f"unchanged-fallback:{FALLBACK_PROVIDER}/{FALLBACK_MODEL}"


_AGENT_HEADER = re.compile(r"^agent:[ \t]*(.*)$")
_REASONING_HEADER = re.compile(r"^([ \t]+)reasoning_overrides:[ \t]*(.*)$")

# glm-5.3-flash **强制思考**：Hermes 全局 reasoningDefault=off 时会把 disable 发给
# 智谱，被 400 拒（"This model always engages in thinking and cannot be disabled"）。
# 兜底场景取性价比档 low（国内端点词表只有 low/high/max）。见
# tests/test_hermes_fallback_glm.py 与 docs/hermes-channels.md。
REASONING_EFFORT = "low"


def _is_sibling(line: str, indent: str) -> bool:
    """``line`` 是否是 ``indent`` 这一层的**兄弟键**（而不是缩进更深的子项）。"""
    if not line.startswith(indent):
        return False
    rest = line[len(indent) :]
    return bool(rest.strip()) and not rest.startswith((" ", "\t")) and not rest.startswith("- ")


def ensure_reasoning_override(config_text: str) -> tuple[str, str]:
    """幂等确保 ``agent.reasoning_overrides`` 里 ``glm-5.3-flash: low``。

    状态 ``written`` / ``unchanged-reasoning:<值>``（操作者显式填了别的档位就保留）。
    """
    lines = config_text.splitlines(keepends=True)
    agent_i = _find_header(lines, _AGENT_HEADER)
    if agent_i is None:
        sep = "" if config_text.endswith("\n") or not config_text else "\n"
        return (
            config_text
            + sep
            + "agent:\n  reasoning_overrides:\n"
            + f"    {FALLBACK_MODEL}: {REASONING_EFFORT}\n"
        ), "written"
    if _inline_value(lines[agent_i], _AGENT_HEADER):
        return config_text, "unchanged-reasoning:inline-agent"

    agent_end = _block_end(lines, agent_i)
    ro_i = next(
        (k for k in range(agent_i + 1, agent_end) if _REASONING_HEADER.match(lines[k])),
        None,
    )
    if ro_i is None:
        lines[agent_i + 1 : agent_i + 1] = [
            "  reasoning_overrides:\n",
            f"    {FALLBACK_MODEL}: {REASONING_EFFORT}\n",
        ]
        return "".join(lines), "written"

    match = _REASONING_HEADER.match(lines[ro_i])
    assert match is not None  # 上面的 next() 已确认匹配
    indent = match.group(1)
    inline = _inline_value(lines[ro_i], _REASONING_HEADER)
    if inline:  # `reasoning_overrides: {a: b}` 行内形态
        if f"{FALLBACK_MODEL}:" in inline:
            value = _strip(
                inline.split(f"{FALLBACK_MODEL}:", 1)[1].split(",")[0].split("}")[0]
            )
            return config_text, f"unchanged-reasoning:{FALLBACK_MODEL}={value}"
        items = [x.strip() for x in inline.strip("{}").split(",") if x.strip()]
        items.append(f"{FALLBACK_MODEL}: {REASONING_EFFORT}")
        lines[ro_i] = f"{indent}reasoning_overrides:\n" + "".join(
            f"{indent}  {item}\n" for item in items
        )
        return "".join(lines), "written"

    ro_end = next(
        (k for k in range(ro_i + 1, agent_end) if _is_sibling(lines[k], indent)),
        agent_end,
    )
    item_indent = indent + "  "
    pattern = re.compile(rf"^{re.escape(item_indent)}{re.escape(FALLBACK_MODEL)}:[ \t]*(.*)$")
    for k in range(ro_i + 1, ro_end):
        m = pattern.match(lines[k])
        if m:
            value = _strip(m.group(1))
            return config_text, f"unchanged-reasoning:{FALLBACK_MODEL}={value}"
    lines[ro_end:ro_end] = [f"{item_indent}{FALLBACK_MODEL}: {REASONING_EFFORT}\n"]
    return "".join(lines), "written"


def apply_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    steps = (
        ("model", ensure_model),
        ("fallback", ensure_fallback),
        ("reasoning", ensure_reasoning_override),
    )
    statuses: dict[str, str] = {}
    for name, func in steps:
        text, statuses[name] = func(text)
    if any(status == "written" for status in statuses.values()):
        path.write_text(text, encoding="utf-8")
    written = [name for name, status in statuses.items() if status == "written"]
    if written:
        return "written" if written == ["model"] else "written-" + "+".join(written)
    if statuses["model"] == "no-model-block":
        return statuses["model"]
    parts = []
    for name in ("model", "fallback", "reasoning"):
        status = statuses[name]
        parts.append(status.split(":", 1)[1] if ":" in status else status)
    return "unchanged:" + "/".join(parts)


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
