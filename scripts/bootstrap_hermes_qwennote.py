#!/usr/bin/env python3
"""把千问办公 AI听记（QwenNote）MCP 幂等注册进 Hermes 的默认 profile 配置。

只操作 ``<hermes-home>/config.yaml`` —— 也就是 Hermes **默认 profile**（爱玛士）
的配置。其余 profile（coder / daoyuan / finance）各自拥有独立的
``<hermes-home>/profiles/<name>/config.yaml``，本脚本刻意不碰：个人听记仅授权
默认 profile 访问（见 docs/qwennote-mcp-hermes.md 的「Profile 隔离」一节）。

为什么不用 ``hermes mcp add``：无头容器内的 OAuth 探测有并发双流缺陷
（见 scripts/qwennote_oauth_login.py 头注释），且该命令在非交互 stdin 下
不会保存配置。本脚本用与 ``ensure_hermes_web_search.py`` 相同的外科文本编辑
（保留注释与键序、不要求宿主安装 PyYAML），随后由人工跑一次授权助手。

用法：
  ./scripts/bootstrap_hermes_qwennote.py                # 注册（幂等）
  ./scripts/bootstrap_hermes_qwennote.py --dry-run      # 只打印将做的修改
  ./scripts/bootstrap_hermes_qwennote.py --disable      # 移除注册（保留 token）
  ./scripts/bootstrap_hermes_qwennote.py --hermes-home /path/to/.hermes
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import sys
from pathlib import Path

SERVER_NAME = "qwennote"
SERVER_URL = "https://minutes.qwennote.cn/mcp"
MANAGED_BY = "qwennote_mcp_server"

_HEADER_RE = re.compile(r"^mcp_servers:\s*$")
_ENTRY_RE = re.compile(rf"^  {re.escape(SERVER_NAME)}:\s*$")
_URL_RE = re.compile(r"^\s+url:\s*(\S+)\s*$")


def guard_default_home(config_path: Path) -> None:
    """拒绝在 profile 子目录（profiles/<name>/config.yaml）上操作。

    本脚本的设计边界：只注册默认 profile（爱玛士）。若有人把 --hermes-home
    指到别的 profile 上，宁可报错也不要静默扩大授权面。
    """
    parts = config_path.resolve().parts
    for i, part in enumerate(parts):
        if part == "profiles" and i < len(parts) - 1:
            raise SystemExit(
                f"拒绝操作 profile 配置: {config_path}\n"
                "本脚本只注册默认 profile（<hermes-home>/config.yaml）；"
                "其他 profile 不应访问个人听记。"
            )


def _entry_block() -> str:
    return (
        f"  {SERVER_NAME}:\n"
        "    connect_timeout: 60\n"
        "    enabled: true\n"
        "    auth: oauth\n"
        "    timeout: 120\n"
        f"    url: {SERVER_URL}\n"
        f"    managed_by: {MANAGED_BY}\n"
    )


def _find_section(lines: list[str]) -> tuple[int, int] | None:
    """返回 mcp_servers 段 (header_idx, end_idx)（end 为段后第一行，可为 len）。"""
    start = None
    for i, line in enumerate(lines):
        if _HEADER_RE.match(line.rstrip("\n")):
            start = i
            break
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        stripped = lines[end].rstrip("\n")
        if stripped and not stripped.startswith((" ", "\t")):
            break
        end += 1
    return start, end


def _entry_span(lines: list[str], start: int, end: int) -> tuple[int, int] | None:
    """返回段内 qwennote 条目的 (entry_idx, entry_end)。

    条目体 = 4 空格及以上缩进的行；遇到空行或 2 空格缩进的同级键即结束。
    """
    for i in range(start + 1, end):
        if _ENTRY_RE.match(lines[i].rstrip("\n")):
            j = i + 1
            while j < end and lines[j].startswith("    "):
                j += 1
            return i, j
    return None


def ensure_server_entry(text: str, *, force: bool = False) -> tuple[str, str]:
    """确保 mcp_servers.qwennote 存在且 url 正确。

    返回 (新文本, 状态)。状态取值：appended / inserted / replaced / exists / conflict。
    """
    lines = text.splitlines(keepends=True)
    if text and not text.endswith("\n"):
        lines[-1] = lines[-1] + "\n"

    section = _find_section(lines)
    if section is None:
        block = "mcp_servers:\n" + _entry_block()
        new_text = "".join(lines)
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        return new_text + block, "appended"

    start, end = section
    span = _entry_span(lines, start, end)
    if span is not None:
        entry_idx, entry_end = span
        existing_url = None
        for line in lines[entry_idx:entry_end]:
            m = _URL_RE.match(line)
            if m:
                existing_url = m.group(1)
                break
        if existing_url == SERVER_URL:
            return text, "exists"
        if not force:
            return text, "conflict"
        lines[entry_idx:entry_end] = _entry_block().splitlines(keepends=True)
        return "".join(lines), "replaced"

    lines[end:end] = _entry_block().splitlines(keepends=True)
    return "".join(lines), "inserted"


def remove_server_entry(text: str) -> tuple[str, str]:
    """移除 mcp_servers.qwennote 条目；段因此为空时连段头一起移除。

    返回 (新文本, 状态)：removed / absent。
    """
    lines = text.splitlines(keepends=True)
    section = _find_section(lines)
    if section is None:
        return text, "absent"
    start, end = section
    span = _entry_span(lines, start, end)
    if span is None:
        return text, "absent"
    entry_idx, entry_end = span
    del lines[entry_idx:entry_end]
    # 重新计算段边界，若段内已无内容则删掉段头
    section = _find_section(lines)
    if section is not None:
        s, e = section
        has_content = any(l.strip() for l in lines[s + 1:e])
        if not has_content:
            del lines[s:e]
    return "".join(lines), "removed"


def _backup(path: Path) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup)
    backup.chmod(0o600)
    return backup


def _next_steps(hermes_home: Path) -> None:
    print()
    print("下一步（一次性 OAuth 授权，按提示在浏览器完成并在终端粘贴回调 URL）：")
    print("  docker compose cp scripts/qwennote_oauth_login.py hermes:/tmp/")
    print("  docker compose exec hermes /opt/hermes/.venv/bin/python3 /tmp/qwennote_oauth_login.py")
    print()
    print("验证与生效：")
    print("  docker compose exec hermes /opt/hermes/.venv/bin/hermes mcp test qwennote")
    print("  docker compose restart hermes          # 网关按会话加载，重启后可用")
    print("  隔离自检（三个受限 profile 不应出现 qwennote）：")
    print("    docker compose exec hermes-coder /opt/hermes/.venv/bin/hermes -p coder mcp list")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="注册/移除 qwennote（QwenNote 听记）MCP 到 Hermes 默认 profile")
    parser.add_argument("--hermes-home", default=str(Path.home() / ".hermes"),
                        help="Hermes home（默认 ~/.hermes，即默认 profile）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将做的修改，不写文件")
    parser.add_argument("--disable", action="store_true", help="移除 qwennote 注册（保留 token）")
    parser.add_argument("--force", action="store_true", help="同名但 url 不同的既有条目：允许替换")
    args = parser.parse_args(argv)

    config_path = Path(args.hermes_home).expanduser() / "config.yaml"
    guard_default_home(config_path)
    if not config_path.exists():
        raise SystemExit(f"Hermes 配置不存在: {config_path} —— 先启动一次 Hermes 生成配置")

    text = config_path.read_text(encoding="utf-8")

    if args.disable:
        new_text, status = remove_server_entry(text)
        verb = {"removed": "已移除 qwennote 注册", "absent": "qwennote 未注册，无需移除"}[status]
    else:
        new_text, status = ensure_server_entry(text, force=args.force)
        verb = {
            "appended": "已追加 mcp_servers 段并写入 qwennote",
            "inserted": "已在 mcp_servers 段中插入 qwennote",
            "replaced": "已替换既有 qwennote 条目（--force）",
            "exists": "qwennote 已注册（url 一致），无需修改",
            "conflict": "同名条目存在但 url 不同",
        }[status]

    if status == "conflict":
        raise SystemExit(
            f"{config_path} 中 mcp_servers.{SERVER_NAME} 已存在但 url 不是 {SERVER_URL}。\n"
            "确认要替换请加 --force。"
        )

    print(f"[{config_path}] {verb}")
    if new_text != text:
        if args.dry_run:
            print("DRY-RUN: 不做写入。将产生的差异（节选）：")
            for line in new_text.splitlines():
                if SERVER_NAME in line or line.startswith("  ") or line.startswith("mcp_servers"):
                    print("  | " + line)
            return
        backup = _backup(config_path)
        config_path.write_text(new_text, encoding="utf-8")
        config_path.chmod(0o640)
        print(f"已写入，备份: {backup.name}")

    if not args.disable:
        _next_steps(Path(args.hermes_home).expanduser())


if __name__ == "__main__":
    main()
