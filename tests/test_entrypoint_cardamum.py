"""entrypoint-wrapper 的 cardamum addressbook 自动配置块 —— 行为测试。

回归 2026-10-01（四容器崩溃循环事故）：
  1. 旧检测 `grep -q "^addressbook.default"` 只认「点分顶层键」格式，而写出的
     是 `[addressbook]` 段 + `default = ` —— 于是每次启动都重跑整块，
     四个 profile 容器并发 `sed -i` 同一份共享配置，写出重复的
     `[addressbook]` 段（TOML invalid）。
  2. `AB_ID="$(cardamum ... list | grep ... | head -1)"` 在 `set -euo pipefail`
     下无防护：cardamum 因配置损坏解析失败 → 管道返回 1 → entrypoint 直接
     退出 1 → 四个 hermes 容器崩溃循环，直到人工修配置。

本测试从 wrapper 里**提取该代码块**（按注释锚点），在临时目录里以真实 bash
（`set -euo pipefail`）运行，cardamum 用假二进制替身。锚点缺失时测试报错而
非静默通过。

Run: uv run --with pytest pytest tests/test_entrypoint_cardamum.py -v
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"

START_ANCHOR = "# Auto-create addressbook if vdir is empty (fresh machine / first run)."
END_ANCHOR = "# ── TDAI Memory plugin install + inject provider"

OLD_UUID = "abcdef12-3456-7890-abcd-ef1234567890"
NEW_UUID = "fedcba98-7654-3210-fedc-ba9876543210"

ACCOUNTS_LINE = '[accounts.default]\ndefault = true\nvdir.home-dir = "/opt/data/.contacts"\n'


def _extract_block() -> str:
    text = WRAPPER.read_text()
    start = text.index(START_ANCHOR)  # ValueError if anchor renamed → loud failure
    end = text.index(END_ANCHOR)
    assert start < end
    return text[start:end]


def _make_fake_cardamum(bin_dir: Path) -> None:
    """A cardamum stand-in driven by env vars; logs every invocation."""
    fake = bin_dir / "cardamum"
    fake.write_text(
        """#!/usr/bin/env bash
echo "$*" >> "${CARDAMUM_FAKE_LOG}"
args=("$@")
# strip -c <config>
if [[ "${args[0]:-}" == "-c" ]]; then
  args=("${args[@]:2}")
fi
case "${args[0]:-}" in
  addressbook)
    case "${args[1]:-}" in
      list)
        [[ -n "${CARDAMUM_FAKE_LIST_OUT:-}" ]] && printf '%s\\n' "${CARDAMUM_FAKE_LIST_OUT}"
        exit "${CARDAMUM_FAKE_LIST_EXIT:-0}"
        ;;
      create) echo "created fake addressbook"; exit 0 ;;
    esac
    ;;
esac
exit 0
"""
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    # The wrapper targets GNU sed (Ubuntu container). macOS ships BSD sed,
    # where `sed -i EXPR FILE` mis-parses EXPR as the backup suffix. Shim
    # that one form on Darwin so the block runs with real sed semantics
    # locally instead of erroring on a host/container toolchain difference
    # (mirror of the "本地工具链 ≠ 容器" lesson).
    shim = bin_dir / "sed"
    shim.write_text(
        """#!/usr/bin/env bash
if [[ "$(uname)" == "Darwin" && "${1:-}" == "-i" ]]; then
  shift
  exec /usr/bin/sed -i '' "$@"
fi
exec /usr/bin/sed "$@"
"""
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)


def _run_block(
    tmp_path: Path,
    config_text: str,
    list_out: str = "",
    list_exit: int = 0,
    with_contacts: bool = True,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Run the extracted block under real bash with set -euo pipefail."""
    cfg_dir = tmp_path / "config" / "cardamum"
    cfg_dir.mkdir(parents=True)
    config = cfg_dir / "config.toml"
    config.write_text(config_text)

    contacts = tmp_path / "contacts"
    contacts.mkdir()
    if with_contacts:
        ab = contacts / NEW_UUID
        ab.mkdir()
        (ab / "displayname").write_text("contacts\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_cardamum(bin_dir)
    call_log = tmp_path / "cardamum-calls.log"

    script = "\n".join(
        [
            "set -euo pipefail",
            f"CONTACTS_DIR={str(contacts)!r}",
            f"CARDAMUM_CONFIG_DIR={str(cfg_dir)!r}",
            f"CARDAMUM_CONFIG={str(config)!r}",
            _extract_block(),
        ]
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "CARDAMUM_FAKE_LOG": str(call_log),
            "CARDAMUM_FAKE_LIST_OUT": list_out,
            "CARDAMUM_FAKE_LIST_EXIT": str(list_exit),
        }
    )
    result = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, check=False
    )
    return result, config, call_log


SECTION_STYLE = ACCOUNTS_LINE + f'\n[addressbook]\ndefault = "{OLD_UUID}"\n'


class TestNoRewriteWhenUnchanged:
    """配置与磁盘一致时整块必须跳过 —— 这正是四容器并发写坏的源头。"""

    def test_config_identical_and_quiet(self, tmp_path: Path):
        result, config, call_log = _run_block(
            tmp_path, SECTION_STYLE, list_out=OLD_UUID
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert config.read_text() == SECTION_STYLE, "配置被无谓重写"
        assert "📇" not in result.stdout, "稳态下不应再有 addressbook 改写输出"


class TestBrokenConfigDoesNotKillEntrypoint:
    """cardamum 失败（配置损坏 / list 失败）时块必须存活 —— 崩溃事故的直接回归。"""

    def test_failing_list_exits_zero_with_warning(self, tmp_path: Path):
        result, config, _ = _run_block(
            tmp_path, ACCOUNTS_LINE, list_out="", list_exit=1
        )
        assert result.returncode == 0, (
            "entrypoint 被 cardamum list 失败杀死（set -e 未设防）: "
            + result.stderr
        )
        assert "⚠️" in result.stdout, "失败必须可见（有告警输出）"
        # 失败时不得写入半成品配置
        assert config.read_text() == ACCOUNTS_LINE

    def test_duplicated_section_config_survives(self, tmp_path: Path):
        """今天事故的真实配置形态：重复 [addressbook] 段（TOML invalid）。"""
        broken = SECTION_STYLE + f'\n[addressbook]\ndefault = "{OLD_UUID}"\n'
        result, config, _ = _run_block(tmp_path, broken, list_out="", list_exit=1)
        assert result.returncode == 0, result.stderr
        assert config.read_text() == broken


class TestFreshAndChangedConfigsStillWork:
    """保留原有能力：新机器写段；UUID 变化时更新。"""

    def test_fresh_config_appends_section(self, tmp_path: Path):
        result, config, call_log = _run_block(
            tmp_path, ACCOUNTS_LINE, list_out=NEW_UUID
        )
        assert result.returncode == 0, result.stderr
        text = config.read_text()
        assert "[addressbook]" in text
        assert f'default = "{NEW_UUID}"' in text
        assert text.count("[addressbook]") == 1
        assert "addressbook list" in call_log.read_text()

    def test_uuid_change_updates_default(self, tmp_path: Path):
        result, config, _ = _run_block(
            tmp_path, SECTION_STYLE, list_out=NEW_UUID
        )
        assert result.returncode == 0, result.stderr
        text = config.read_text()
        assert f'default = "{NEW_UUID}"' in text
        assert OLD_UUID not in text
        assert text.count("[addressbook]") == 1
