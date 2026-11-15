#!/usr/bin/env python3
"""每日自检 dailyinfo 的三路推送（Discord / 网页 / 道元飞书）。

背景
----
dailyinfo 是独立 compose 栈（``~/code/dailyinfo``）：

* Discord 推送：容器内 scheduler 的 ``push_early`` / ``push_papers`` /
  ``push_arxiv``（05:30 / 06:00 / 07:00 北京时间），投递状态落在
  ``<data>/deliveries/discord/<category>-<YYYY-MM-DD>.json``
* 网页发布：宿主 launchd ``ai.dailyinfo.web-publish``（07:40），投递状态落在
  ``<data>/deliveries/web/<category>-<YYYY-MM-DD>.json``
* 道元镜像：Hermes no-agent cron ``dailyinfo-digest``（07:30），执行记录在
  ``~/.hermes/profiles/daoyuan/cron/executions.db``

本脚本按**当天有 canonical publication 的分类**逐项核对上面三路，并在
``--fix`` 时做一次幂等补救（重跑 scheduler job / 触发 web publish / 重发道元
digest）。它不判断内容质量，也不改 dailyinfo 的数据。

用法
----
    python3 scripts/check_dailyinfo_push.py             # 只检查
    python3 scripts/check_dailyinfo_push.py --fix       # 检查 + 补救 + 复检
    python3 scripts/check_dailyinfo_push.py --json      # 机器可读（只输出 JSON）
    python3 scripts/check_dailyinfo_push.py --date 2026-11-15 --no-wait

退出码：0 三路都成功；1 仍有失败/缺失；2 脚本或配置错误。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# Asia/Shanghai 没有夏令时，固定偏移即可，避免 macOS 上缺 tzdata。
CST = timezone(timedelta(hours=8))

CANONICAL_CATEGORIES = ("papers", "ai_news", "code", "resource", "arxiv")

# dailyinfo 的 scheduler 把五个分类分成三个推送 job。
CATEGORY_JOB = {
    "ai_news": "push_early",
    "code": "push_early",
    "resource": "push_early",
    "papers": "push_papers",
    "arxiv": "push_arxiv",
}

DIGEST_JOB_NAME = "dailyinfo-digest"
DIGEST_SQLITE_BUSY_RETRY = 2

# MCP 侧已知故障的处置提示（脚本只给提示，不自动改 dailyinfo 代码）。
KNOWN_ISSUES = {
    "CONTENT_CHANGED": (
        "dailyinfo-mcp 的一致性校验连续两次签名不一致。已确认容器内 stat 的 "
        "st_ino 在 Docker Desktop virtiofs 绑定挂载上不稳定，而 "
        "scripts/mcp_queries.py::_signature 把 st_ino 算进签名，导致几乎每次 "
        "调用都 CONTENT_CHANGED。2026-11-15 已在 dailyinfo 侧去掉 st_ino 并重建镜像；"
        "若再次出现，先确认镜像是否回退到旧代码。"
    ),
}

# 这些错误重试没有意义（确定性失败），不要触发会再发失败告警的重跑。
_NON_RETRYABLE = {"CONTENT_CHANGED", "CONTENT_TOO_LARGE"}

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_CONFIG = 2


class LockBusy(RuntimeError):
    """另一个检查进程持有锁。"""


# ── 配置 ────────────────────────────────────────────────────────────


@dataclass
class Config:
    data_root: Path
    dailyinfo_repo: Path
    myopenclaw_repo: Path
    daoyuan_cron_dir: Path
    digest_job_name: str = DIGEST_JOB_NAME
    state_path: Path = field(default_factory=Path)
    lock_path: Path = field(default_factory=Path)
    web_wait_seconds: int = 900
    digest_wait_seconds: int = 240
    push_timeout_seconds: int = 900
    web_timeout_seconds: int = 300
    lock_wait_seconds: int = 600
    web_launchd_label: str = "ai.dailyinfo.web-publish"


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def load_config() -> Config:
    repo = Path(__file__).resolve().parent.parent
    data_root = _env_path(
        "DAILYINFO_DATA_ROOT", Path.home() / ".myagentdata" / "dailyinfo"
    )
    logs = repo / "logs"
    return Config(
        data_root=data_root,
        dailyinfo_repo=_env_path("DAILYINFO_REPO", Path.home() / "code" / "dailyinfo"),
        myopenclaw_repo=_env_path("MYOPENCLAW_REPO", repo),
        daoyuan_cron_dir=_env_path(
            "DAOYUAN_CRON_DIR", Path.home() / ".hermes" / "profiles" / "daoyuan" / "cron"
        ),
        digest_job_name=os.environ.get("DAILYINFO_DIGEST_JOB", DIGEST_JOB_NAME),
        state_path=logs / "dailyinfo-push-check.json",
        lock_path=logs / ".dailyinfo-push-check.lock",
        web_wait_seconds=int(os.environ.get("DAILYINFO_WEB_WAIT_SECONDS", "900")),
        digest_wait_seconds=int(os.environ.get("DAILYINFO_DIGEST_WAIT_SECONDS", "240")),
        push_timeout_seconds=int(os.environ.get("DAILYINFO_PUSH_TIMEOUT", "900")),
        web_timeout_seconds=int(os.environ.get("DAILYINFO_WEB_TIMEOUT", "300")),
        lock_wait_seconds=int(os.environ.get("DAILYINFO_LOCK_WAIT", "600")),
    )


# ── 纯函数（单测覆盖） ──────────────────────────────────────────────


def beijing_date(value: str | datetime | None) -> str | None:
    """把 UTC ISO 时间戳转成北京日期（YYYY-MM-DD）。"""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(CST).date().isoformat()


def expected_categories(data_root: Path, day: str) -> list[str]:
    """当天有 canonical publication 的分类（没有内容的分类不推送）。"""
    try:
        year, month, dom = day.split("-")
    except ValueError:
        return []
    base = Path(data_root) / "publications" / "briefings" / year / month / dom
    if not base.is_dir():
        return []
    found = []
    for category in CANONICAL_CATEGORIES:
        if (base / category / "briefing.json").is_file():
            found.append(category)
    return found


def channel_overall(entries: dict[str, dict[str, Any]]) -> str:
    """一路推送的整体状态：success / failed / missing / empty。"""
    if not entries:
        return "empty"
    statuses = [entry.get("status") for entry in entries.values()]
    if all(status == "success" for status in statuses):
        return "success"
    if any(status == "failed" for status in statuses):
        return "failed"
    return "missing"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def delivery_entries(data_root: Path, sink: str, day: str, categories: Iterable[str]):
    """逐分类读投递状态文件；缺失/损坏都算 missing。"""
    entries: dict[str, dict[str, Any]] = {}
    for category in categories:
        path = Path(data_root) / "deliveries" / sink / f"{category}-{day}.json"
        payload = _read_json(path)
        if payload is None:
            entries[category] = {"status": "missing", "error": None}
            continue
        status = payload.get("status")
        entries[category] = {
            "status": status if status in {"success", "failed", "pending"} else "missing",
            "error": payload.get("last_error"),
            "attempts": payload.get("attempt_count"),
            "last_attempted_at": payload.get("last_attempted_at"),
            "delivered_at": payload.get("delivered_at"),
            "external_ref": payload.get("external_ref"),
        }
    return entries


def pick_latest_execution(rows: Iterable[dict[str, Any]], day: str):
    """从 execution 行里挑出北京时间当天的最后一条。"""
    best = None
    for row in rows:
        if beijing_date(row.get("started_at")) != day:
            continue
        if best is None or (row.get("started_at") or "") > (best.get("started_at") or ""):
            best = row
    return best


def execution_status(row: dict[str, Any] | None) -> str:
    """把一条 execution 行折叠成 success / failed / running / missing。"""
    if row is None:
        return "missing"
    status = (row.get("status") or "").lower()
    if status in {"running", "claimed", "pending"}:
        return "running"
    if status == "completed":
        if (row.get("delivery_outcome") or "").lower() == "failed":
            return "failed"
        return "success"
    return "failed"


def overall_exit_code(report: dict[str, Any]) -> int:
    """0 = 三路都成功；1 = 有失败/缺失；2 = 配置错误。"""
    if report.get("config_error"):
        return EXIT_CONFIG
    channels = report.get("channels") or {}
    if not channels:
        return EXIT_CONFIG
    for channel in channels.values():
        if channel.get("status") != "success":
            return EXIT_ISSUES
    return EXIT_OK


# ── 进程/容器辅助 ──────────────────────────────────────────────────


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 120,
         stdin: str | None = None, env: dict[str, str] | None = None):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _short(text: str | None, limit: int = 500) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit]


def web_publish_running() -> bool:
    """网页发布是宿主上一条 5–10 分钟的 uv 进程，检查时要等它跑完。"""
    try:
        proc = _run(["pgrep", "-fl", "dailyinfo publish --sink web"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    return any("check_dailyinfo_push" not in line for line in proc.stdout.splitlines())


def compose_services(cfg: Config) -> dict[str, dict[str, str]]:
    try:
        proc = _run(
            ["docker", "compose", "-f", "compose.yml", "ps", "--format", "json"],
            cwd=cfg.dailyinfo_repo,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    services: dict[str, dict[str, str]] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        name = payload.get("Service")
        if not name:
            continue
        services[name] = {
            "state": str(payload.get("State") or ""),
            "health": str(payload.get("Health") or ""),
            "status": str(payload.get("Status") or ""),
        }
    return services


def discord_tls_probe(cfg: Config) -> str:
    """从 scheduler 容器内部验证到 discord.com 的 TLS 握手。"""
    code = (
        "import socket, ssl\n"
        "ctx = ssl.create_default_context()\n"
        "s = socket.create_connection(('discord.com', 443), timeout=8)\n"
        "ss = ctx.wrap_socket(s, server_hostname='discord.com')\n"
        "print('ok', ss.version())\n"
    )
    try:
        proc = _run(
            ["docker", "compose", "-f", "compose.yml", "exec", "-T", "scheduler",
             "python", "-"],
            cwd=cfg.dailyinfo_repo,
            timeout=30,
            stdin=code,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"probe-error: {exc}"
    if proc.returncode != 0:
        return f"fail: {_short(proc.stderr) or 'exit ' + str(proc.returncode)}"
    return _short(proc.stdout) or "ok"


# ── 状态采集 ────────────────────────────────────────────────────────


def collect_delivery(cfg: Config, sink: str, day: str, categories: list[str]):
    entries = delivery_entries(cfg.data_root, sink, day, categories)
    return {"status": channel_overall(entries), "categories": entries}


def query_executions(db_path: Path, job_id: str) -> list[dict[str, Any]]:
    """读 Hermes cron 执行记录；只读，失败时用 immutable 兜底。"""
    if not db_path.is_file():
        return []
    row_factory = sqlite3.Row
    last_error: Exception | None = None
    for uri in (
        f"file:{db_path}?mode=ro",
        f"file:{db_path}?mode=ro&immutable=1",
    ):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = row_factory
            try:
                rows = conn.execute(
                    "select id, job_id, status, started_at, finished_at, error,"
                    " delivery_outcome from executions where job_id = ?"
                    " order by started_at desc limit 50",
                    (job_id,),
                ).fetchall()
            finally:
                conn.close()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:  # 锁/权限：换 immutable 再试
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return []


def load_digest_job(cfg: Config) -> dict[str, Any] | None:
    payload = _read_json(cfg.daoyuan_cron_dir / "jobs.json")
    if not payload:
        return None
    for job in payload.get("jobs") or []:
        if job.get("name") == cfg.digest_job_name:
            return job
    return None


_PROBE_CODES = {
    "CONTENT_CHANGED",
    "CONTENT_UNAVAILABLE",
    "CONTENT_TOO_LARGE",
    "STALE_CURSOR",
    "CURSOR_REPEATED",
    "PAGE_LIMIT_EXCEEDED",
    "BODY_INVALID",
    "BODY_LENGTH_MISMATCH",
    "INVALID_PAYLOAD",
    "INVALID_ARGUMENT",
}

# 在容器里直接展开 ExceptionGroup，拿到被 Hermes 折叠掉的真实错误码。
_DIGEST_PROBE_SCRIPT = r'''
import asyncio, sys
sys.path.insert(0, "/opt/data/profiles/daoyuan/scripts")
import dailyinfo_digest as d

DAY = sys.argv[1]


def leaves(exc):
    out = []

    def walk(item):
        subs = getattr(item, "exceptions", None)
        if subs:
            for sub in subs:
                walk(sub)
        else:
            out.append("%s: %s" % (type(item).__name__, item))

    walk(exc)
    return out


async def run():
    url, key = d._read_env()
    try:
        sections = await d._collect_with_session(url, key, DAY)
    except BaseException as exc:  # noqa: BLE001
        print("DIGEST_PROBE_FAIL " + " | ".join(leaves(exc))[-500:])
        return 1
    print("DIGEST_PROBE_OK sections=%d" % len(sections))
    return 0


raise SystemExit(asyncio.run(run()))
'''


def digest_probe(cfg: Config, day: str) -> dict[str, Any]:
    """不投递地跑一遍 digest 采集，拿到被 Hermes 折叠掉的真实错误码。

    ``hermes cron`` 的 execution.error 只留 "unhandled errors in a TaskGroup"，
    真正的 ``CONTENT_CHANGED`` / ``STALE_CURSOR`` 在脚本异常组的最内层。
    """
    try:
        proc = _run(
            ["docker", "compose", "-f", "docker-compose.yml", "exec", "-T",
             "hermes-daoyuan", "/opt/hermes/.venv/bin/python", "-c",
             _DIGEST_PROBE_SCRIPT, day],
            cwd=cfg.myopenclaw_repo,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": _short(str(exc), 300), "code": None}
    if proc.returncode == 0 and "DIGEST_PROBE_OK" in proc.stdout:
        return {"ok": True, "detail": _short(proc.stdout.strip(), 300)}
    marker = "DIGEST_PROBE_FAIL "
    detail = ""
    if marker in proc.stdout:
        detail = proc.stdout.split(marker, 1)[1].strip()
    detail = _short(detail or proc.stderr, 300)
    code = next(
        (token for token in detail.replace(":", " ").split()
         if token.strip(",.") in _PROBE_CODES),
        None,
    )
    return {"ok": False, "detail": detail, "code": code}


def collect_digest(cfg: Config, day: str, *, probe: bool = False) -> dict[str, Any]:
    job = load_digest_job(cfg)
    if job is None or not job.get("id"):
        return {
            "status": "missing",
            "detail": f"Hermes cron 里没有 {cfg.digest_job_name}",
            "job": cfg.digest_job_name,
        }
    try:
        rows = query_executions(cfg.daoyuan_cron_dir / "executions.db", job["id"])
    except sqlite3.Error as exc:
        return {
            "status": "missing",
            "job": cfg.digest_job_name,
            "job_id": job["id"],
            "detail": f"读取 cron 执行记录失败: {_short(str(exc), 200)}",
        }
    latest = pick_latest_execution(rows, day)
    status = execution_status(latest)
    entry: dict[str, Any] = {
        "status": status,
        "job": cfg.digest_job_name,
        "job_id": job["id"],
        "execution_id": latest.get("id") if latest else None,
        "started_at": latest.get("started_at") if latest else None,
        "delivery_outcome": latest.get("delivery_outcome") if latest else None,
        "error": _short(latest.get("error")) if latest else None,
    }
    error_text = entry.get("error") or ""
    if probe and status in {"failed", "missing"}:
        result = digest_probe(cfg, day)
        entry["probe"] = result
        error_text = f"{error_text} {result.get('code') or ''} {result.get('detail') or ''}"
        if result.get("code") and not entry.get("error"):
            entry["error"] = result.get("detail")
    for marker, hint in KNOWN_ISSUES.items():
        if marker in error_text:
            entry["hint"] = hint
            entry["code"] = marker
            break
    return entry


# ── 修复动作 ────────────────────────────────────────────────────────


def fix_discord(cfg: Config, failing: list[str]) -> list[dict[str, Any]]:
    jobs = sorted({CATEGORY_JOB[category] for category in failing})
    actions = []
    for job in jobs:
        try:
            proc = _run(
                ["docker", "compose", "-f", "compose.yml", "exec", "-T", "scheduler",
                 "python", "-m", "scripts.scheduler", "run-job", job],
                cwd=cfg.dailyinfo_repo,
                timeout=cfg.push_timeout_seconds,
            )
            actions.append({
                "channel": "discord",
                "action": f"run-job {job}",
                "ok": proc.returncode == 0,
                "detail": _short(proc.stderr or proc.stdout, 300),
            })
        except (OSError, subprocess.TimeoutExpired) as exc:
            actions.append({
                "channel": "discord",
                "action": f"run-job {job}",
                "ok": False,
                "detail": _short(str(exc), 300),
            })
    return actions


def fix_web(cfg: Config) -> list[dict[str, Any]]:
    """触发 07:40 的 launchd 任务，而不是在沙箱里直接跑 uv。"""
    actions: list[dict[str, Any]] = []
    try:
        proc = _run(["launchctl", "start", cfg.web_launchd_label], timeout=30)
        ok = proc.returncode == 0
        detail = _short(proc.stderr or proc.stdout, 300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        ok, detail = False, _short(str(exc), 300)
    actions.append({
        "channel": "web",
        "action": f"launchctl start {cfg.web_launchd_label}",
        "ok": ok,
        "detail": detail,
    })
    if ok or shutil.which("uv") is None:
        return actions
    # launchd 不可用（例如 Linux/未安装）时退回直接执行。
    env = dict(os.environ, DAILYINFO_DATA_ROOT=str(cfg.data_root))
    try:
        proc = _run(
            ["uv", "run", "dailyinfo", "publish", "--sink", "web"],
            cwd=cfg.dailyinfo_repo,
            timeout=cfg.web_timeout_seconds,
            env=env,
        )
        actions.append({
            "channel": "web",
            "action": "dailyinfo publish --sink web",
            "ok": proc.returncode == 0,
            "detail": _short(proc.stderr or proc.stdout, 300),
        })
    except (OSError, subprocess.TimeoutExpired) as exc:
        actions.append({
            "channel": "web",
            "action": "dailyinfo publish --sink web",
            "ok": False,
            "detail": _short(str(exc), 300),
        })
    return actions


def fix_digest(cfg: Config, job_id: str) -> list[dict[str, Any]]:
    try:
        proc = _run(
            ["docker", "compose", "-f", "docker-compose.yml", "exec", "-T", "hermes-daoyuan",
             "/opt/hermes/.venv/bin/hermes", "-p", "daoyuan", "cron", "run", job_id],
            cwd=cfg.myopenclaw_repo,
            timeout=cfg.digest_wait_seconds + 120,
        )
        return [{
            "channel": "daoyuan",
            "action": f"hermes cron run {job_id}",
            "ok": proc.returncode == 0,
            "detail": _short(proc.stderr or proc.stdout, 300),
        }]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [{
            "channel": "daoyuan",
            "action": f"hermes cron run {job_id}",
            "ok": False,
            "detail": _short(str(exc), 300),
        }]


# ── 主流程 ──────────────────────────────────────────────────────────


def _wait_for_web(cfg: Config) -> None:
    deadline = time.monotonic() + cfg.web_wait_seconds
    waited = False
    while web_publish_running() and time.monotonic() < deadline:
        waited = True
        time.sleep(10)
    if waited:
        time.sleep(2)  # 让最后的 delivery 状态文件落盘


def _wait_for_digest(cfg: Config, day: str) -> None:
    deadline = time.monotonic() + cfg.digest_wait_seconds
    while time.monotonic() < deadline:
        if collect_digest(cfg, day).get("status") != "running":
            return
        time.sleep(5)


def check(cfg: Config, day: str, *, fix: bool, wait: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "date": day,
        "checked_at": datetime.now(CST).isoformat(timespec="seconds"),
        "data_root": str(cfg.data_root),
        "actions": [],
        "diagnostics": {},
    }
    if not cfg.data_root.is_dir():
        report["config_error"] = f"dailyinfo 数据根不存在: {cfg.data_root}"
        return report
    categories = expected_categories(cfg.data_root, day)
    if not categories:
        # 数据没生成（不是配置错误）：三路都无从谈起，按「有失败」上报。
        detail = f"{day} 没有任何 canonical publication"
        report["categories"] = []
        report["channels"] = {
            "discord": {"status": "missing", "categories": {}, "detail": detail},
            "web": {"status": "missing", "categories": {}, "detail": detail},
            "daoyuan": {"status": "missing", "detail": detail},
        }
        report["ok"] = False
        report["finished_at"] = report["checked_at"]
        return report

    report["categories"] = categories
    if wait:
        _wait_for_web(cfg)
        _wait_for_digest(cfg, day)

    report["channels"] = {
        "discord": collect_delivery(cfg, "discord", day, categories),
        "web": collect_delivery(cfg, "web", day, categories),
        "daoyuan": collect_digest(cfg, day, probe=True),
    }

    services = compose_services(cfg)
    scheduler = services.get("scheduler", {})
    report["diagnostics"] = {
        "scheduler": scheduler.get("status") or "unknown",
        "mcp": services.get("dailyinfo-mcp", {}).get("status") or "unknown",
    }
    if report["channels"]["discord"]["status"] != "success":
        report["diagnostics"]["discord_tls"] = discord_tls_probe(cfg)

    if fix:
        before = report["channels"]["daoyuan"]
        report["actions"] = _apply_fixes(cfg, day, report)
        after = collect_digest(cfg, day, probe=False)
        if after.get("status") != "success":
            for key in ("probe", "hint", "code"):
                if before.get(key) and not after.get(key):
                    after[key] = before[key]
        report["channels"] = {
            "discord": collect_delivery(cfg, "discord", day, categories),
            "web": collect_delivery(cfg, "web", day, categories),
            "daoyuan": after,
        }

    report["ok"] = overall_exit_code(report) == EXIT_OK
    report["finished_at"] = datetime.now(CST).isoformat(timespec="seconds")
    return report


def _apply_fixes(cfg: Config, day: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    channels = report["channels"]
    if channels["discord"]["status"] != "success":
        failing = [
            category
            for category, entry in channels["discord"]["categories"].items()
            if entry.get("status") != "success"
        ]
        if failing:
            actions += fix_discord(cfg, failing)
    if channels["web"]["status"] != "success" and not web_publish_running():
        actions += fix_web(cfg)
    if channels["daoyuan"]["status"] not in {"success", "running"}:
        daoyuan = channels["daoyuan"]
        job_id = daoyuan.get("job_id")
        known = daoyuan.get("code")
        if job_id and known in _NON_RETRYABLE:
            # 重试只会再发一条失败告警；根因在 dailyinfo-mcp 侧。
            actions.append({
                "channel": "daoyuan",
                "action": f"skip retry ({known})",
                "ok": False,
                "detail": "已知不可重试根因，需修复 dailyinfo-mcp 后重建镜像",
            })
        elif job_id:
            actions += fix_digest(cfg, job_id)
    if actions:
        if any(action["channel"] == "daoyuan" and action.get("ok") for action in actions):
            # cron run 只是派发；等这一天真正出现新的终态执行记录再复检。
            previous = (report["channels"].get("daoyuan") or {}).get("execution_id")
            deadline = time.monotonic() + cfg.digest_wait_seconds
            while time.monotonic() < deadline:
                current = collect_digest(cfg, day)
                if (
                    current.get("status") != "running"
                    and current.get("execution_id") != previous
                ):
                    break
                time.sleep(5)
        if any(action["channel"] == "web" and action.get("ok") for action in actions):
            # 网页发布是 5–10 分钟的宿主进程，等它真正跑完再复检。
            deadline = time.monotonic() + cfg.web_wait_seconds
            while web_publish_running() and time.monotonic() < deadline:
                time.sleep(10)
        else:
            deadline = time.monotonic() + min(120, cfg.digest_wait_seconds)
            while web_publish_running() and time.monotonic() < deadline:
                time.sleep(5)
        time.sleep(2)
    return actions


def render_human(report: dict[str, Any]) -> str:
    lines = [f"dailyinfo 推送自检 {report.get('date')}  {report.get('checked_at')}"]
    if report.get("config_error"):
        lines.append(f"  ⚠️  {report['config_error']}")
    channels = report.get("channels") or {}

    def mark(status: str) -> str:
        return {"success": "✅", "failed": "❌", "missing": "❌", "running": "⏳"}.get(
            status, "⚠️"
        )

    for name, label in (("discord", "Discord"), ("web", "网页"), ("daoyuan", "道元")):
        channel = channels.get(name) or {}
        status = channel.get("status", "missing")
        lines.append(f"  {label:<8}{mark(status)} {status}")
        if channel.get("detail"):
            lines.append(f"           {channel['detail']}")
        if name == "daoyuan":
            if channel.get("error"):
                lines.append(f"           {channel['error']}")
            if channel.get("hint"):
                lines.append(f"           💡 {channel['hint']}")
            continue
        for category, entry in (channel.get("categories") or {}).items():
            if entry.get("status") != "success":
                lines.append(
                    f"           {category}: {entry.get('status')}"
                    f" {entry.get('error') or ''}".rstrip()
                )
    diagnostics = report.get("diagnostics") or {}
    if diagnostics:
        lines.append(
            "  诊断:     "
            + "; ".join(f"{key}={value}" for key, value in diagnostics.items())
        )
    for action in report.get("actions") or []:
        detail = (action.get("detail") or "")[:140]
        lines.append(
            f"  修复:     [{action['channel']}] {action['action']} -> "
            f"{'ok' if action['ok'] else 'failed'} {detail}".rstrip()
        )
    lines.append("结果: " + ("✅ 三路都正常" if report.get("ok") else "❌ 仍有失败"))
    return "\n".join(lines)


def write_state(path: Path, report: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        print(f"warning: 无法写入状态文件 {path}: {exc}", file=sys.stderr)


@contextmanager
def file_lock(path: Path, timeout: int):
    """串行化 --fix：launchd 与 DSH 任务同时触发时，后来者等前者跑完。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockBusy(f"等待锁超时: {path}")
                time.sleep(2)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dailyinfo 三路推送自检")
    parser.add_argument("--date", help="北京时间日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--fix", action="store_true", help="失败时做一次幂等补救并复检")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--no-wait", action="store_true", help="不等待进行中的 web/digest")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except Exception as exc:  # noqa: BLE001 - 配置错误要显式退出 2
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        day = args.date or datetime.now(CST).date().isoformat()
        date.fromisoformat(day)
    except ValueError:
        print(f"invalid --date: {args.date}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        if args.fix:
            with file_lock(cfg.lock_path, cfg.lock_wait_seconds):
                report = check(cfg, day, fix=True, wait=not args.no_wait)
        else:
            report = check(cfg, day, fix=False, wait=not args.no_wait)
    except LockBusy as exc:
        print(f"dailyinfo push check: {exc}", file=sys.stderr)
        return EXIT_ISSUES

    write_state(cfg.state_path, report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_human(report))
    return overall_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
