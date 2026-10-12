"""entrypoint-wrapper 的「profile 网关隔离」清扫块 —— 行为测试。

事故背景（2026-10-11/12）：单遍清扫 + 只等首个服务出现，在开机风暴下会扫到
不完整的服务集 → 漏杀外部 profile 的 gateway（游荡进程）；而漏掉的游荡会
把共享 gateway_state.json 写回 `desired_state=running` 自我复活（hydra）——
daoyuan/finance/coder 三容器各留一个 `gateway-default` 游荡冒充爱玛士，
邮箱=桩 → 用户 DM「时好时坏」。

加固后的清扫：等 4 个服务出现（有界）→ 多轮（每轮重扫目录、压制复活的、
重删外部 profile 的 state 文件）→ 终检 + 失败大声告警。
可覆写变量（本测试用）：S6_SVC / GW_SVC_ROOT / GW_SWEEP_PASSES /
GW_SWEEP_INTERVAL / GW_SWEEP_WAIT。

本测试从 wrapper 按注释锚点**抽取该块**，在临时 `GW_SVC_ROOT` 上以假
s6-svc/s6-svstat 跑真实 bash（`<svc_dir>/state.up` 文件模拟"服务在跑"）。
锚点缺失时测试报错而非静默通过。

Run: uv run --with pytest pytest tests/test_entrypoint_gateway_isolation.py -v
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "docker" / "hermes" / "entrypoint-wrapper.sh"

START_ANCHOR = "# ── Profile-scoped gateway isolation"
END_ANCHOR = "# Hand off to s6-overlay init"

ALL_SVCS = ["gateway-default", "gateway-coder", "gateway-daoyuan", "gateway-finance"]


def _extract_block() -> str:
    text = WRAPPER.read_text()
    start = text.index(START_ANCHOR)  # ValueError if anchor renamed → loud failure
    end = text.index(END_ANCHOR)
    assert start < end
    return text[start:end]


def _write_fakes(bin_dir: Path) -> None:
    """假 s6-svc / s6-svstat；`<svc_dir>/state.up` 存在 = 服务在跑。"""
    svc = bin_dir / "s6-svc"
    svc.write_text(
        """#!/usr/bin/env bash
flag="$1"; dir="$2"
echo "s6-svc $flag $(basename "$dir")" >> "${S6_FAKE_LOG}"
case "$flag" in
  -k)
    # 重启本容器 gateway：保持 up
    rm -f "$dir/state.up"; touch "$dir/state.up"
    ;;
  -dk)
    n=0; [ -f "$dir/killcount" ] && n=$(cat "$dir/killcount"); n=$((n+1)); echo "$n" > "$dir/killcount"
    rm -f "$dir/state.up"
    # 模拟「复活」（hydra）：指定目录第一次被压制后再站起来
    if [ "$(basename "$dir")" = "${S6_FAKE_REVIVE:-__none__}" ] && [ "$n" -eq 1 ]; then
      touch "$dir/state.up"
    fi
    # 模拟「杀不死」：指定目录压制无效，状态保持 up
    if [ "$(basename "$dir")" = "${S6_FAKE_UNKILLABLE:-__none__}" ]; then
      touch "$dir/state.up"
    fi
    ;;
esac
exit 0
"""
    )
    svc.chmod(svc.stat().st_mode | stat.S_IXUSR)
    svstat = bin_dir / "s6-svstat"
    svstat.write_text(
        """#!/usr/bin/env bash
dir="$1"
if [ -f "$dir/state.up" ]; then echo "up (pid 4242) 3 seconds"; else echo "down (exitcode 0) 3 seconds"; fi
"""
    )
    svstat.chmod(svstat.stat().st_mode | stat.S_IXUSR)


def _run_block(
    tmp_path: Path,
    profile: str,
    revive: str | None = None,
    unkillable: str | None = None,
    pre_create_dirs: bool = True,
    wait: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    svc_root = tmp_path / "service"
    if pre_create_dirs:
        for name in ALL_SVCS:
            d = svc_root / name
            d.mkdir(parents=True)
            (d / "state.up").touch()  # 启动窗口：全部先站着

    hermes_home = tmp_path / "hermes_home"
    (hermes_home / "profiles" / "coder").mkdir(parents=True)
    (hermes_home / "profiles" / "coder" / "gateway_state.json").write_text(
        '{"desired_state":"running"}'
    )
    (hermes_home / "gateway_state.json").write_text('{"desired_state":"running"}')

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fakes(bin_dir)
    call_log = tmp_path / "svc-calls.log"

    script = "\n".join(
        [
            "set -euo pipefail",
            f"export HERMES_HOME={str(hermes_home)!r}",
            f"export HERMES_PROFILE={profile!r}",
            f"export GW_SVC_ROOT={str(svc_root)!r}",
            f"export S6_SVC={str(bin_dir / 's6-svc')!r}",
            f"export S6_FAKE_LOG={str(call_log)!r}",
            "export GW_SWEEP_PASSES=3",
            "export GW_SWEEP_INTERVAL=0",
            f"export GW_SWEEP_WAIT={wait}",
            _extract_block(),
            "wait",
        ]
    )
    env = os.environ.copy()
    if revive:
        env["S6_FAKE_REVIVE"] = revive
    if unkillable:
        env["S6_FAKE_UNKILLABLE"] = unkillable
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    return result, call_log, svc_root, hermes_home


class TestRestrictedProfileSweep:
    """受限容器（daoyuan）：外部全灭、自家只重启一次、state 文件被清。"""

    def test_extras_suppressed_every_pass_and_own_restarted_once(self, tmp_path: Path):
        r, log, _, home = _run_block(tmp_path, "daoyuan")
        assert r.returncode == 0, r.stderr
        calls = log.read_text()
        assert calls.count("s6-svc -k gateway-daoyuan") == 1, (
            "本容器 gateway 应只重启一次（多轮清扫不重复打断它）"
        )
        for extra in ("gateway-default", "gateway-coder", "gateway-finance"):
            assert calls.count(f"s6-svc -dk {extra}") >= 3, f"{extra} 未被逐轮压制"
        assert "s6-svc -dk gateway-daoyuan" not in calls
        assert "✅ gateway 隔离完成" in r.stdout
        # state 文件**不得**被清扫删除：共享卷上的这些文件属于**其他容器**的
        # 活 gateway（2026-10-12 实测：四容器同时启动时互相删对方 state =
        # 跨容器友军火力；开启清理仅保留 wrapper 既有的一次性启动块）。
        assert (home / "profiles" / "coder" / "gateway_state.json").exists(), (
            "清扫删了其他 profile 的 state 文件——共享卷上是别人家活 gateway 的文件"
        )
        assert (home / "gateway_state.json").exists(), "清扫删了根 state 文件（属默认容器）"

    def test_revived_stray_is_suppressed_again(self, tmp_path: Path):
        """hydra 场景：第一轮压制后游荡站起来 → 后续轮次必须再次压制并显式记录。"""
        r, log, _, _ = _run_block(tmp_path, "daoyuan", revive="gateway-default")
        assert r.returncode == 0, r.stderr
        calls = log.read_text()
        assert calls.count("s6-svc -dk gateway-default") >= 2
        assert "♻️" in r.stdout, "复活的游荡被再次压制时应显式记录"
        assert "✅ gateway 隔离完成" in r.stdout

    def test_unkillable_stray_triggers_loud_warning(self, tmp_path: Path):
        """终检失败路径：杀不死的游荡必须大声告警，不得静默。"""
        r, _, _, _ = _run_block(tmp_path, "daoyuan", unkillable="gateway-default")
        assert "❌" in r.stdout, "终检失败时必须输出 ❌ 告警"
        assert "gateway-default" in r.stdout
        assert "✅ gateway 隔离完成" not in r.stdout


class TestDefaultProfileSweep:
    """默认容器（HERMES_PROFILE 为空 → default）：自家保住，其余全灭。"""

    def test_default_keeps_its_gateway_and_kills_others(self, tmp_path: Path):
        r, log, _, home = _run_block(tmp_path, "")
        assert r.returncode == 0, r.stderr
        calls = log.read_text()
        assert calls.count("s6-svc -k gateway-default") == 1
        assert "s6-svc -dk gateway-default" not in calls
        for extra in ("gateway-coder", "gateway-daoyuan", "gateway-finance"):
            assert calls.count(f"s6-svc -dk {extra}") >= 3
        assert "✅ gateway 隔离完成" in r.stdout
        # default 容器：根 state 文件属于自己；外部 profile 的文件属其他容器 —— 都不删
        assert (home / "gateway_state.json").exists()
        assert (home / "profiles" / "coder" / "gateway_state.json").exists()


class TestSweepAgainstRealBootTiming:
    """回归 2026-10-12 真机事故：服务目录**还没出现**时清扫必须存活。

    wrapper 在容器启动早期就跑，而 hermes 的服务目录是随后才被创建的 ——
    `found="$(ls ... | wc -l | tr -d ' ')"` 在 pipefail 下，ls 失败（无匹配）
    会让整个后台子壳**静默退出**：清扫一行没干，四容器全留 default 游荡。
    合成 fixture 一开始就把目录建好，所以此前四个用例全绿也漏掉了它。
    """

    def test_survives_when_no_service_dirs_yet(self, tmp_path: Path):
        r, _, _, _ = _run_block(tmp_path, "daoyuan", pre_create_dirs=False, wait=5)
        assert r.returncode == 0, (
            "wait 循环在服务目录尚未出现时把清扫子壳打死了（pipefail + ls 失败）: "
            + r.stderr
        )
        assert "✅ gateway 隔离完成" in r.stdout, "清扫未跑到底"
