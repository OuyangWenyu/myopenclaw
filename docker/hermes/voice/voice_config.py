#!/usr/bin/env python3
"""Install the DeepSeek voice capability into a Hermes ``config.yaml``.

The capability is a pair of Hermes command providers:

    tts.providers.deepseek  ->  python3 /opt/hermes-voice/ds_voice.py tts ...
    stt.providers.deepseek  ->  python3 /opt/hermes-voice/ds_voice.py asr ...

This module only edits the parts it owns.  It uses a line-based YAML editor
(no PyYAML) so comments, key order, indentation and line endings survive; it
is intentionally stdlib-only because it runs both on the host (scripts/start.sh,
scripts/bind_agent.py) and inside the Hermes image.

Activation policy: ``edge``/``local``/missing are treated as legacy defaults and
switched to ``deepseek`` when ``activate=True``.  Any explicit alternative
(``openai``, ``elevenlabs``, ...) is preserved.  ``stt.enabled: false`` is
preserved by skipping the STT section entirely.

CLI:

    voice_config.py --ensure PATH [--activate|--no-activate]
                    [--auto-tts preserve|true|false] [--voice stella|tide]
                    [--dry-run] [--no-backup] [--quiet]
    voice_config.py --check PATH
    voice_config.py --print-fragment [--activate|--no-activate]
                    [--auto-tts preserve|true|false] [--voice stella|tide]
"""

import argparse
import difflib
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

PROVIDER_NAME = "deepseek"
TTS_COMMAND = (
    "python3 /opt/hermes-voice/ds_voice.py tts "
    "--text-file {input_path} --out {output_path} --format {format} --voice {voice}"
)
STT_COMMAND = (
    "python3 /opt/hermes-voice/ds_voice.py asr "
    "--in {input_path} --out {output_path}"
)
ENV_KEY = "DEEPSEEK_API_KEY"


class InlineSection(Exception):
    """A section uses YAML flow style and cannot be edited safely."""


class InlineProviders(Exception):
    """The providers map uses YAML flow style."""


def _read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _body(line):
    return line.rstrip("\r\n")


def _ending(line):
    body = line
    if body.endswith("\r\n"):
        return "\r\n"
    if body.endswith("\n"):
        return "\n"
    return ""


def _indent_of(line):
    body = _body(line)
    return len(body) - len(body.lstrip(" "))


def _is_blank(line):
    return _body(line).strip() == ""


def _top_key(line):
    """Return ``(key, rest)`` for a top-level mapping key, else ``None``."""
    match = re.match(r"^([A-Za-z0-9_]+):(.*)$", _body(line))
    if not match:
        return None
    return match.group(1), match.group(2)


def _section_end(lines, start):
    for index in range(start + 1, len(lines)):
        if _is_blank(lines[index]):
            continue
        if _indent_of(lines[index]) == 0:
            return index
    return len(lines)


def _find_section(lines, key):
    for index, line in enumerate(lines):
        parsed = _top_key(line)
        if parsed and parsed[0] == key:
            return index, _section_end(lines, index)
    return None


def _inline_value(line):
    parsed = _top_key(line)
    if parsed is None:
        return None
    rest = parsed[1].strip()
    if rest == "" or rest.startswith("#"):
        return None
    return rest


def _find_child(lines, start, end, key, indent):
    prefix = " " * indent + key + ":"
    for index in range(start + 1, end):
        if _indent_of(lines[index]) != indent:
            continue
        body = _body(lines[index])
        if not body.startswith(prefix):
            continue
        rest = body[len(prefix):]
        if rest == "" or rest[0] in " \t#":
            return index
    return None


def _child_end(lines, index, end, sequence_items=False):
    parent = _indent_of(lines[index])
    for cursor in range(index + 1, end):
        if _is_blank(lines[cursor]):
            continue
        indent = _indent_of(lines[cursor])
        if indent < parent:
            return cursor
        if indent == parent:
            if sequence_items and _body(lines[cursor]).lstrip().startswith("-"):
                continue
            return cursor
    return end


def _trim_end(lines, start, end):
    cursor = end
    while cursor > start + 1 and _is_blank(lines[cursor - 1]):
        cursor -= 1
    return cursor


def _scalar_value(line):
    match = re.match(r"^[A-Za-z0-9_]+:(.*)$", _body(line).lstrip())
    if not match:
        return ""
    value = match.group(1).strip()
    if "#" in value:
        value = value.split("#", 1)[0].strip()
    return value.strip().strip("'").strip('"')


def _replace_scalar(lines, index, key, indent, value):
    """Replace a scalar value, preserving an inline comment and line ending."""
    old = lines[index]
    rest = _body(old).split(":", 1)[1]
    comment = ""
    if "#" in rest:
        comment = " " + rest[rest.index("#"):].strip()
    ending = _ending(old) or "\n"
    lines[index] = " " * indent + f"{key}: {value}{comment}{ending}"


def _insert_line(lines, index, text, newline):
    lines.insert(index, text + newline)


def _field_lines(kind, voice):
    if kind == "tts":
        return [
            "type: command",
            f'command: "{TTS_COMMAND}"',
            "output_format: wav",
            f"voice: {voice}",
            "voice_compatible: true",
            "max_text_length: 4000",
            "timeout: 180",
            "env_passthrough:",
            f"- {ENV_KEY}",
        ]
    return [
        "type: command",
        f'command: "{STT_COMMAND}"',
        "format: txt",
        "timeout: 300",
        "env_passthrough:",
        f"- {ENV_KEY}",
    ]


def _provider_lines(kind, voice, base_indent):
    """Lines for ``deepseek:`` at *base_indent* (providers map children)."""
    field_indent = base_indent + 2
    lines = [" " * base_indent + PROVIDER_NAME + ":"]
    lines.extend(" " * field_indent + field for field in _field_lines(kind, voice))
    return lines


def _providers_lines(kind, voice):
    return ["  providers:"] + _provider_lines(kind, voice, 4)


def _full_section_lines(kind, voice, activate):
    lines = [kind + ":"]
    if kind == "stt":
        lines.append("  enabled: true")
    if activate:
        lines.append(f"  provider: {PROVIDER_NAME}")
    lines.extend(_providers_lines(kind, voice))
    return lines


def _append_block(lines, block, newline):
    if lines and not _is_blank(lines[-1]):
        lines.append(newline)
    lines.extend(line + newline for line in block)


def _command_is_ours(value):
    return "ds_voice.py" in value or "ds_tts.py" in value or "ds_asr.py" in value


def _replace_env_block(lines, start, end, newline):
    """Rewrite the env_passthrough list to exactly ``[DEEPSEEK_API_KEY]``."""
    index = _find_child(lines, start, end, "env_passthrough", 6)
    if index is None:
        position = _trim_end(lines, start, end)
        lines[position:position] = ["      env_passthrough:" + newline,
                                    "      - " + ENV_KEY + newline]
        return True
    inline = _inline_value(lines[index])
    block_end = index + 1 if inline else _child_end(lines, index, end, sequence_items=True)
    existing = [
        _body(item).strip()
        for item in lines[index + 1:block_end]
        if _body(item).strip().startswith("-")
    ]
    header_ok = inline is None
    if header_ok and existing == ["- " + ENV_KEY]:
        return False
    lines[index:block_end] = ["      env_passthrough:" + newline,
                              "      - " + ENV_KEY + newline]
    return True


def _replace_command(lines, start, end, kind, newline):
    index = _find_child(lines, start, end, "command", 6)
    expected = TTS_COMMAND if kind == "tts" else STT_COMMAND
    if index is None:
        position = _trim_end(lines, start, end)
        lines[position:position] = ['      command: "' + expected + '"' + newline]
        return True
    current = _scalar_value(lines[index])
    if current == expected:
        return False
    _replace_scalar(lines, index, "command", 6, '"' + expected + '"')
    return True


def _migrate_provider(lines, section_start, section_end, kind, newline):
    """Migrate an existing ``deepseek:`` provider block, preserving custom fields."""
    index = _find_child(lines, section_start, section_end, PROVIDER_NAME, 4)
    if index is None:
        return False
    block_end = _child_end(lines, index, section_end)
    command_index = _find_child(lines, index, block_end, "command", 6)
    command_value = _scalar_value(lines[command_index]) if command_index is not None else ""
    ours = command_index is None or _command_is_ours(command_value)
    if not ours:
        return False

    changed = False
    if _replace_command(lines, index, block_end, kind, newline):
        changed = True
        section_end = _find_section(lines, kind)[1]
        block_end = _child_end(lines, index, section_end)

    if _replace_env_block(lines, index, block_end, newline):
        changed = True
        section_end = _find_section(lines, kind)[1]
        block_end = _child_end(lines, index, section_end)

    if kind == "stt":
        normalize = _find_child(lines, index, block_end, "normalize", 6)
        if normalize is not None:
            del lines[normalize]
            changed = True
    return changed


def _ensure_provider_section(lines, kind, activate, voice, newline):
    """Return (changed, skipped_reason)."""
    found = _find_section(lines, kind)
    if found is None:
        _append_block(lines, _full_section_lines(kind, voice, activate), newline)
        return True, None

    start, end = found
    if _inline_value(lines[start]) is not None:
        raise InlineSection(kind)

    if kind == "stt":
        enabled = _find_child(lines, start, end, "enabled", 2)
        if enabled is not None and _scalar_value(lines[enabled]).lower() == "false":
            return False, "stt-disabled"

    changed = False
    provider = _find_child(lines, start, end, "provider", 2)
    if activate:
        legacy = {"edge", ""} if kind == "tts" else {"local", ""}
        if provider is None:
            _insert_line(lines, start + 1, f"  provider: {PROVIDER_NAME}", newline)
            changed = True
            start, end = _find_section(lines, kind)
        elif _scalar_value(lines[provider]) in legacy:
            _replace_scalar(lines, provider, "provider", 2, PROVIDER_NAME)
            changed = True

    providers = _find_child(lines, start, end, "providers", 2)
    if providers is None:
        position = _trim_end(lines, start, end)
        block = ["  providers:"] + _provider_lines(kind, voice, 4)
        lines[position:position] = [line + newline for line in block]
        changed = True
    else:
        if _inline_value(lines[providers]) is not None:
            raise InlineProviders(kind)
        providers_end = _child_end(lines, providers, end)
        if _find_child(lines, providers, providers_end, PROVIDER_NAME, 4) is None:
            position = _trim_end(lines, providers, providers_end)
            block = _provider_lines(kind, voice, 4)
            lines[position:position] = [line + newline for line in block]
            changed = True
        else:
            ok = _migrate_provider(lines, start, _find_section(lines, kind)[1], kind, newline)
            changed = changed or ok
    return changed, None


def _ensure_voice_section(lines, value, newline):
    found = _find_section(lines, "voice")
    if found is None:
        _append_block(lines, ["voice:", f"  auto_tts: {value}"], newline)
        return True
    start, end = found
    if _inline_value(lines[start]) is not None:
        raise InlineSection("voice")
    child = _find_child(lines, start, end, "auto_tts", 2)
    if child is None:
        _insert_line(lines, start + 1, f"  auto_tts: {value}", newline)
        return True
    if _scalar_value(lines[child]).lower() == value:
        return False
    _replace_scalar(lines, child, "auto_tts", 2, value)
    return True


def _apply(text, activate, auto_tts, voice):
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if not text.strip():
        block = _full_section_lines("tts", voice, activate)
        block += [""] + _full_section_lines("stt", voice, activate)
        if auto_tts != "preserve":
            block += ["", "voice:", f"  auto_tts: {auto_tts}"]
        lines = [line + newline for line in block]
        return "".join(lines), ["tts", "stt"] + (
            ["voice"] if auto_tts != "preserve" else [])

    changes = []
    tts_changed, _ = _ensure_provider_section(lines, "tts", activate, voice, newline)
    if tts_changed:
        changes.append("tts")
    stt_changed, skipped = _ensure_provider_section(lines, "stt", activate, voice, newline)
    if stt_changed:
        changes.append("stt")
    if skipped == "stt-disabled":
        changes.append("skipped:stt-disabled")
    if auto_tts != "preserve":
        if _ensure_voice_section(lines, auto_tts, newline):
            changes.append("voice")
    return "".join(lines), changes


def _check_text(text):
    missing = []
    lines = text.splitlines(keepends=True)
    for kind, expected in (("tts", TTS_COMMAND), ("stt", STT_COMMAND)):
        found = _find_section(lines, kind)
        if found is None:
            missing.append(kind + ".providers.deepseek")
            continue
        start, end = found
        if _inline_value(lines[start]) is not None:
            missing.append(kind + ".providers.deepseek")
            continue
        providers = _find_child(lines, start, end, "providers", 2)
        if providers is None:
            missing.append(kind + ".providers.deepseek")
            continue
        providers_end = _child_end(lines, providers, end)
        deepseek = _find_child(lines, providers, providers_end, PROVIDER_NAME, 4)
        if deepseek is None:
            missing.append(kind + ".providers.deepseek")
            continue
        block_end = _child_end(lines, deepseek, providers_end)
        command = _find_child(lines, deepseek, block_end, "command", 6)
        if command is None or _scalar_value(lines[command]) != expected:
            missing.append(kind + ".providers.deepseek.command")
        env = _find_child(lines, deepseek, block_end, "env_passthrough", 6)
        if env is None:
            missing.append(kind + ".providers.deepseek.env_passthrough")
        else:
            env_end = _child_end(lines, env, block_end, sequence_items=True)
            items = [_body(line).strip() for line in lines[env + 1:env_end]
                     if _body(line).strip().startswith("-")]
            if "- " + ENV_KEY not in items:
                missing.append(kind + ".providers.deepseek.env_passthrough")
    return missing


def render_fragment(activate=True, auto_tts="preserve", voice="stella"):
    newline = "\n"
    lines = _full_section_lines("tts", voice, activate)
    lines += [""] + _full_section_lines("stt", voice, activate)
    if auto_tts != "preserve":
        lines += ["", "voice:", f"  auto_tts: {auto_tts}"]
    return newline.join(lines) + newline


def ensure_voice_config(path, activate=True, auto_tts="preserve", voice="stella", backup=True):
    path = Path(path).expanduser()
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return "error:not-found"
    except UnicodeDecodeError:
        return "error:not-utf8"
    except OSError as exc:
        return f"error:read:{exc}"

    try:
        new_text, changes = _apply(text, activate, auto_tts, voice)
    except InlineSection as exc:
        return f"error:inline-section:{exc}"
    except InlineProviders as exc:
        return f"error:inline-providers:{exc}"

    if new_text == text:
        return "unchanged"

    if backup:
        stamp = time.strftime("%Y%m%d%H%M%S")
        target = path.with_name(path.name + ".bak.voice-" + stamp)
        if target.exists():
            target = path.with_name(path.name + ".bak.voice-" + stamp + "-" + str(os.getpid()))
        try:
            shutil.copy2(path, target)
        except OSError as exc:
            return f"error:backup:{exc}"

    mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".voice_config_", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(new_text)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        return f"error:write:{exc}"
    return "written:" + "+".join(changes)


def _diff_text(path, old, new):
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path), tofile=str(path) + " (proposed)",
    ))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Install/verify the DeepSeek voice capability in a Hermes config.yaml")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--ensure", metavar="PATH")
    action.add_argument("--check", metavar="PATH")
    action.add_argument("--print-fragment", action="store_true")
    parser.add_argument("--activate", dest="activate", action="store_true", default=None)
    parser.add_argument("--no-activate", dest="activate", action="store_false")
    parser.add_argument("--auto-tts", choices=("preserve", "true", "false"), default="preserve")
    parser.add_argument("--voice", choices=("stella", "tide"), default="stella")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", dest="backup", action="store_false", default=True)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.activate is None:
        args.activate = bool(os.environ.get(ENV_KEY, "").strip())

    if args.print_fragment:
        sys.stdout.write(render_fragment(args.activate, args.auto_tts, args.voice))
        return 0

    path = Path(args.ensure or args.check).expanduser()
    try:
        text = _read_text(path)
    except FileNotFoundError:
        print(f"{path}: error:not-found", file=sys.stderr if args.check else sys.stdout)
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        print(f"{path}: error:read:{exc}", file=sys.stderr if args.check else sys.stdout)
        return 1

    if args.check:
        missing = _check_text(text)
        if missing:
            print("missing:" + ",".join(missing))
            return 1
        print("ok")
        return 0

    if args.dry_run:
        try:
            new_text, changes = _apply(text, args.activate, args.auto_tts, args.voice)
        except (InlineSection, InlineProviders) as exc:
            print(f"{path}: error:{exc}")
            return 1
        if new_text == text:
            print(f"{path}: unchanged")
            return 0
        sys.stdout.write(_diff_text(path, text, new_text))
        print(f"{path}: dry-run:" + "+".join(changes))
        return 0

    status = ensure_voice_config(
        path, activate=args.activate, auto_tts=args.auto_tts,
        voice=args.voice, backup=args.backup,
    )
    if not args.quiet:
        print(f"{path}: {status}")
    return 1 if status.startswith("error:") else 0


if __name__ == "__main__":
    raise SystemExit(main())
