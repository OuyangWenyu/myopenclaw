#!/usr/bin/env python3
"""
Collect AgentOps signals and write to the agentops ledger.

Detects:
  - Recently restarted containers
  - Stale backups
  - High disk usage
  - Gateway error loops
  - Unhealthy containers
  - Unusable tirith security scanner (fails open silently)

Output: ~/.myagentdata/agentops/inbox.md (auto items merged with manual)

Usage:
  python3 scripts/collect_agentops.py
  python3 scripts/collect_agentops.py --dry-run   # print to stdout only
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTOPS_DATA_DIR = os.environ.get(
    "AGENTOPS_DATA_DIR",
    os.path.expanduser("~/.myagentdata/agentops"),
)
AGENTOPS_LEDGER = Path(AGENTOPS_DATA_DIR) / "inbox.md"

# Thresholds (configurable via env vars)
RESTART_THRESHOLD_HOURS = int(os.environ.get("AGENTOPS_RESTART_THRESHOLD", "2"))
# Default 24h matches daily backup-cron (BACKUP_CRON=0 2 * * *).
BACKUP_STALE_HOURS = int(os.environ.get("AGENTOPS_BACKUP_STALE_HOURS", "24"))
DISK_THRESHOLD_PERCENT = int(os.environ.get("AGENTOPS_DISK_THRESHOLD", "85"))

# Paths
BACKUP_ROOT = os.environ.get(
    "AGENTOPS_BACKUP_ROOT",
    os.path.expanduser("~/Google Drive/我的云端硬盘/myopenclaw-backups"),
)
GATEWAY_ERROR_SCRIPT = str(REPO_ROOT / "scripts" / "check-gateway-errors.sh")
GATEWAY_ERR_LOG = os.path.expanduser("~/.openclaw/logs/gateway.err.log")

# Hermes 的预执行安全扫描器 tirith 装在 $HERMES_HOME/bin/，而 $HERMES_HOME(=
# ~/.hermes) 同时是容器挂载的 /opt/data —— 宿主与容器共用同一路径，所以宿主机上
# 就能读到容器实际会 spawn 的那个文件。
TIRITH_BIN_DIR = Path(os.path.expanduser("~/.hermes/bin"))
ELF_MAGIC = b"\x7fELF"


# =============================================================
# 1. Container status parsing
# =============================================================


def get_container_ps_data():
    """Get docker compose ps output as JSON list."""
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True, text=True, timeout=15,
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            print(f"⚠️  docker compose ps failed: {result.stderr}", file=sys.stderr)
            return []
        lines = result.stdout.strip().split("\n")
        return [json.loads(line) for line in lines if line.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"⚠️  docker compose ps error: {e}", file=sys.stderr)
        return []


def parse_container_status(ps_data):
    """Parse docker compose ps JSON output into structured container status.

    Args:
        ps_data: list of dicts from docker compose ps --format json

    Returns:
        list of dicts with keys: name, state, status, healthy
    """
    containers = []
    for entry in ps_data:
        name = entry.get("Name", "unknown")
        state = entry.get("State", "unknown")
        status = entry.get("Status", "")

        # Determine health from status string
        healthy = None
        if "(healthy)" in status:
            healthy = True
        elif "(unhealthy)" in status:
            healthy = False

        containers.append({
            "name": name,
            "state": state,
            "status": status,
            "healthy": healthy,
        })
    return containers


# =============================================================
# 2. Running time parser
# =============================================================


def _parse_running_time(status_str):
    """Parse Docker container status string into timedelta.

    Handles formats like:
      "Up 3 days"
      "Up 2 hours"
      "Up 30 minutes"
      "Up 45 seconds"
      "Up About an hour"
      "Up Less than a second"

    Returns timedelta or None if unparseable.
    """
    if not status_str or "Up" not in status_str:
        return None

    # Normalize: strip "Up " prefix, handle "About", "Less than"
    rest = status_str.replace("Up ", "").strip()

    if "Less than a second" in rest:
        return timedelta(seconds=0)

    # Remove "About " prefix
    rest = rest.replace("About ", "").replace("about ", "")

    # Extract number and unit
    # Patterns: "3 days", "2 hours", "30 minutes", "45 seconds", "an hour"
    patterns = [
        (r"(\d+)\s*days?", "days"),
        (r"(\d+)\s*hours?", "hours"),
        (r"(\d+)\s*minutes?", "minutes"),
        (r"(\d+)\s*seconds?", "seconds"),
        (r"an\s+hour", "hours_singular"),
    ]

    for pattern, unit in patterns:
        if unit == "hours_singular":
            m = re.match(r"an\s+hour", rest)
            if m:
                return timedelta(hours=1)
        else:
            m = re.search(pattern, rest)
            if m:
                value = int(m.group(1))
                if unit == "days":
                    return timedelta(days=value)
                elif unit == "hours":
                    return timedelta(hours=value)
                elif unit == "minutes":
                    return timedelta(minutes=value)
                elif unit == "seconds":
                    return timedelta(seconds=value)

    return None


# =============================================================
# 3. Restart detection
# =============================================================


def detect_restarts(containers, threshold_hours=RESTART_THRESHOLD_HOURS):
    """Detect recently restarted containers.

    Args:
        containers: list from parse_container_status()
        threshold_hours: containers running less than this are flagged

    Returns:
        list of ledger item dicts
    """
    items = []
    threshold = timedelta(hours=threshold_hours)

    for c in containers:
        if c["state"] != "running":
            continue
        uptime = _parse_running_time(c["status"])
        if uptime is not None and uptime < threshold:
            hours = uptime.total_seconds() / 3600
            items.append({
                "title": f"{c['name']} 近期重启",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "source": "auto | docker compose ps",
                "status": "watch",
                "owner": "owen",
                "evidence": f"容器 {c['name']} 运行时间: {c['status']}（< {threshold_hours}h 阈值）",
                "why_it_matters": f"容器 {c['name']} 在最近 {threshold_hours} 小时内重启过，可能发生过崩溃或被手动重启",
                "suggested_next_action": f"检查 docker compose logs {c['name']} --tail 50 确认重启原因",
                "needs_human_decision": False,
            })

    return items


# =============================================================
# 4. Backup freshness
# =============================================================


# 探测有两个来源，优先级从高到低：
#
#   1. **心跳** —— backup-cron 每次跑完写一份（逐服务记成功/失败）。它刻意放在云盘
#      目录**之外**：实测云盘目录对宿主进程的可见性是**按进程上下文分裂**的 ——
#      launchd 能 readdir 云盘根目录、却读不了里面的文件（EPERM）；交互式 shell 恰好
#      反过来（读得了文件、readdir 却 EPERM）。放在云盘里的信号总有一类宿主进程读不到，
#      所以心跳落本地（~/.myagentdata/agentops/）。
#
#   2. **快照目录名** —— 退化路径。两条硬约束：
#      · 判据是**目录名里的时间戳**，不是 mtime：`rsync -a` 的 -t 会把目标目录的 mtime
#        覆盖成源目录的，mtime 根本不是备份时间（prune 逻辑早已因此改用目录名）。
#      · **不要求 `latest` 是符号链接**。备份脚本用
#        `rsync -a --delete "${DEST}/" "${LATEST}/"` 建 latest，那是**真实目录**；
#        这里历史上判的是 `is_symlink()`，于是 5/5 服务全不匹配、恒定返回「无备份」——
#        58 次运行 58 次误报（2026-09-16 修）。
SNAPSHOT_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{6})$")


class BackupProbe(NamedTuple):
    """备份新鲜度探测结果。

    `unreadable=True` 表示**探测失败**（读不到），与「确实没有备份」是两回事 ——
    混为一谈就会把「我不知道」说成「没有」，制造假警报。
    """

    last_success: "datetime | None"
    source: str      # "heartbeat" | "snapshot" | "none"
    unreadable: bool
    ok: bool         # 最近一次运行是否全部成功（无心跳时为 True —— 无从证伪）
    detail: str


def _heartbeat_file():
    return Path(os.environ.get(
        "AGENTOPS_BACKUP_HEARTBEAT",
        os.path.expanduser("~/.myagentdata/agentops/backup-heartbeat.json"),
    ))


def _read_heartbeat(path):
    """→ (moment, ok, detail)。ok=None 表示「压根没读到心跳」，不是「备份失败」。"""
    try:
        raw = Path(path).read_text()
    except FileNotFoundError:
        return None, None, "心跳文件不存在"
    except OSError as e:
        return None, None, f"心跳文件不可读: {e}"

    try:
        data = json.loads(raw)
    except ValueError as e:
        return None, None, f"心跳文件不是合法 JSON: {e}"
    if not isinstance(data, dict):
        return None, None, "心跳文件不是 JSON 对象"

    moment = None
    # 优先取 epoch：容器可能跑在与宿主不同的时区（backup-cron 实测跑在 UTC），
    # 字符串会被宿主按本地时区解释歪，绝对时刻只有 epoch 不会错。
    epoch = data.get("epoch")
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        try:
            moment = datetime.fromtimestamp(epoch)
        except (OverflowError, OSError, ValueError):
            moment = None
    if moment is None:
        ts = data.get("timestamp")
        if isinstance(ts, str):
            try:
                moment = datetime.fromisoformat(ts)
            except ValueError:
                moment = None
            else:
                if moment.tzinfo is not None:
                    moment = moment.astimezone().replace(tzinfo=None)
    if moment is None:
        return None, None, "心跳文件没有可解析的时间戳"

    failed = data.get("failed") or []
    if not isinstance(failed, list):
        failed = [str(failed)]
    if data.get("status") == "ok" and not failed:
        return moment, True, f"心跳 {moment:%Y-%m-%d %H:%M}（本次全部成功）"
    return moment, False, (
        f"心跳 {moment:%Y-%m-%d %H:%M}：本次未全部成功"
        f"（status={data.get('status')!r}, failed={failed}）"
    )


def _scan_snapshot_dirs(backup_root):
    """按快照**目录名**找最新时间。→ (moment|None, unreadable, detail)"""
    root = Path(backup_root)
    try:
        service_dirs = list(root.iterdir())
    except FileNotFoundError:
        return None, False, f"备份目录不存在: {backup_root}"
    except OSError as e:
        return None, True, f"备份目录不可读（{e.strerror or e}）: {backup_root}"

    latest = None
    count = 0
    for service_dir in service_dirs:
        try:
            if not service_dir.is_dir():
                continue
            entries = list(service_dir.iterdir())
        except OSError:
            continue
        for entry in entries:
            match = SNAPSHOT_DIR_RE.match(entry.name)
            if not match:
                continue
            try:
                moment = datetime.strptime(
                    f"{match.group(1)}_{match.group(2)}", "%Y-%m-%d_%H%M%S"
                )
            except ValueError:
                continue
            count += 1
            if latest is None or moment > latest:
                latest = moment

    if latest is None:
        return None, False, f"备份目录中没有任何快照: {backup_root}"
    return latest, False, f"最新快照 {latest:%Y-%m-%d %H:%M}（共 {count} 份）"


def probe_backup(backup_root=BACKUP_ROOT, heartbeat=None):
    """探测备份新鲜度：心跳优先，快照目录名兜底。"""
    heartbeat = Path(heartbeat) if heartbeat is not None else _heartbeat_file()

    moment, heartbeat_ok, heartbeat_detail = _read_heartbeat(heartbeat)
    if moment is not None:
        return BackupProbe(moment, "heartbeat", False, heartbeat_ok, heartbeat_detail)

    snapshot_moment, unreadable, snapshot_detail = _scan_snapshot_dirs(backup_root)
    detail = f"{snapshot_detail}（{heartbeat_detail}）"
    if snapshot_moment is not None:
        return BackupProbe(snapshot_moment, "snapshot", False, True, detail)
    return BackupProbe(None, "none", unreadable, True, detail)


def check_backup_freshness(backup_root=BACKUP_ROOT, threshold_hours=BACKUP_STALE_HOURS):
    """Check if backups are stale.

    Args:
        backup_root: path to backup directory
        threshold_hours: backups older than this generate an item

    Returns:
        list of ledger item dicts
    """
    probe = probe_backup(backup_root)

    def item(title, status, evidence, why, action):
        return {
            "title": title,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "source": "auto | backup-cron",
            "status": status,
            "owner": "owen",
            "evidence": evidence,
            "why_it_matters": why,
            "suggested_next_action": action,
            "needs_human_decision": True,
        }

    if not probe.ok:
        return [item(
            "备份失败",
            "new",
            probe.detail,
            "最近一次备份没有全部成功，部分数据可能没有进入云端快照",
            "查看 backup-cron 日志定位失败的服务: docker compose logs backup-cron --tail 50",
        )]

    if probe.last_success is None:
        if probe.unreadable:
            return [item(
                "备份状态无法确认",
                "watch",
                probe.detail,
                "探测不到备份，但原因是读不到目录（权限/IO）—— 不代表备份不存在，需人工确认，"
                "不要据此判断数据有风险",
                "在能读该目录的上下文里确认: ls \"${BACKUP_ROOT}\"",
            )]
        return [item(
            "备份未找到或从未执行",
            "new",
            probe.detail,
            "数据安全依赖定期备份，没有备份意味着容器配置和记忆面临丢失风险",
            "检查 backup-cron 容器日志，确认 BACKUP_ROOT 和云盘客户端配置正确",
        )]

    age = datetime.now() - probe.last_success
    if age > timedelta(hours=threshold_hours):
        hours_ago = age.total_seconds() / 3600
        return [item(
            "备份过期",
            "watch",
            f"{probe.detail}，即 {hours_ago:.0f}h 前，阈值: {threshold_hours}h",
            f"备份已过期 {hours_ago:.0f} 小时，超过 {threshold_hours}h 阈值。数据安全存在风险",
            "手动触发备份: docker compose exec backup-cron /scripts/backup-all-docker.sh",
        )]

    return []


# =============================================================
# 5. Disk usage
# =============================================================


def _get_disk_usage():
    """Get the highest disk usage percentage from data volumes.

    Returns float (0-100) or None on error.
    """
    try:
        result = subprocess.run(
            ["df", "-P", "/System/Volumes/Data"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                # Parse df output: Filesystem 512-blocks Used Available Capacity Mounted
                parts = lines[1].split()
                if len(parts) >= 5:
                    return float(parts[4].rstrip("%"))
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return None


def check_disk_usage(threshold_percent=DISK_THRESHOLD_PERCENT):
    """Check if disk usage is above threshold.

    Args:
        threshold_percent: percentage above which to alert

    Returns:
        list of ledger item dicts
    """
    usage = _get_disk_usage()
    if usage is None:
        return []

    if usage >= threshold_percent:
        return [{
            "title": f"磁盘使用率 {usage:.0f}% 超过阈值",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "source": "auto | df -h",
            "status": "new",
            "owner": "owen",
            "evidence": f"数据卷 /System/Volumes/Data 使用率: {usage:.0f}%（阈值: {threshold_percent}%）",
            "why_it_matters": f"磁盘使用率 {usage:.0f}% 超过 {threshold_percent}% 阈值，可能导致服务写入失败或 Docker 异常",
            "suggested_next_action": "清理旧镜像: docker system prune -a；或清理 ~/.myagentdata 中的旧数据",
            "needs_human_decision": True,
        }]

    return []


# =============================================================
# 6. Gateway error detection
# =============================================================


def _run_check_gateway_errors():
    """Run check-gateway-errors.sh --json and return parsed result.

    Returns dict or None on error.
    """
    script = GATEWAY_ERROR_SCRIPT
    if not Path(script).exists():
        return None

    try:
        result = subprocess.run(
            ["bash", script, "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        elif result.returncode == 1 and result.stdout.strip():
            # Error loop detected — still returns JSON
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def check_gateway_errors():
    """Check for OpenClaw gateway error loops.

    Returns:
        list of ledger item dicts
    """
    data = _run_check_gateway_errors()
    if data is None:
        return []

    if data.get("status") == "error_loop_detected":
        error_msg = data.get("error_message", "unknown")
        count = data.get("repeat_count_in_sample", 0)
        total = data.get("total_occurrences", 0)

        return [{
            "title": f"OpenClaw 网关错误循环: {error_msg[:60]}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "source": "auto | check-gateway-errors.sh",
            "status": "new",
            "owner": "owen",
            "evidence": f"错误消息: {error_msg}；采样中重复 {count} 次（共 {total} 次）",
            "why_it_matters": "网关错误循环会导致日志刷屏、磁盘占用，可能影响消息处理",
            "suggested_next_action": "检查 ~/.openclaw/logs/gateway.err.log，运行 openclaw doctor --fix（在 Docker 容器内）",
            "needs_human_decision": True,
        }]

    return []


def check_tirith_binary():
    """Hermes 的预执行安全扫描器 tirith 必须能在容器里跑起来。

    它拦的是同形字 URL、管道直通解释器、混淆载荷这类东西 —— 而 agent 的输入来自
    飞书/Discord/网页内容，一次 prompt injection 就能让它执行恶意命令。

    它失效的方式是**安静**的：spawn 失败只按 (异常类, errno) 去重报一条 WARNING，
    连续 3 次后熔断器打开，该进程余下时间直接放行 —— 所以它能在
    `tirith_enabled: true` 的前提下从未真正评估过任何命令而无人察觉。

    最现实的失效形态是平台不对：二进制装在 `$HERMES_HOME/bin/`，而 `$HERMES_HOME`
    (= ~/.hermes) 同时是容器挂载的 /opt/data —— 宿主侧按 `platform.system()` 判定
    平台时下载的 apple-darwin 包会一直躺在那儿，容器里每次 spawn 都
    `Exec format error`；且解析只看可执行位、不做平台校验，所以任何叫 tirith* 的
    文件都会被拿去 spawn。

    Returns:
        list of ledger item dicts
    """
    if not TIRITH_BIN_DIR.is_dir():
        return []

    bad = []
    for path in sorted(TIRITH_BIN_DIR.glob("tirith*")):
        try:
            with path.open("rb") as fh:
                magic = fh.read(4)
        except OSError as exc:
            bad.append(f"{path.name}（无法读取: {exc}）")
            continue
        if magic != ELF_MAGIC:
            bad.append(f"{path.name}（魔数 {magic!r}，非 Linux ELF）")

    if not bad:
        return []

    detail = "；".join(bad)
    return [{
        "title": f"Hermes tirith 安全扫描器不可用: {detail[:60]}",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "source": "auto | collect_agentops.py",
        "status": "new",
        "owner": "owen",
        "evidence": f"{detail}（宿主 {TIRITH_BIN_DIR}，容器内 /opt/data/bin/）",
        "why_it_matters": (
            "tirith 失效是静默的：spawn 失败去重后只报一条 WARNING，连续 3 次后熔断器"
            "打开、该进程余下时间直接放行 —— 表面 tirith_enabled: true，实际命令从未被扫描"
        ),
        "suggested_next_action": (
            "移走错平台二进制并触发容器内重装：docker compose exec -T -w /opt/hermes hermes "
            "/opt/hermes/.venv/bin/python3 -c \"from tools.tirith_security import _install_tirith; "
            "print(_install_tirith())\"；随后重启 hermes 系容器清掉熔断器；"
            "验证 bash tests/test-tirith-binary.sh"
        ),
        "needs_human_decision": True,
    }]


# =============================================================
# 7. Ledger formatting
# =============================================================


def format_ledger_item(item):
    """Format a single ledger item dict as markdown.

    Args:
        item: dict with keys: title, date, source, status, owner,
              evidence, why_it_matters, suggested_next_action, needs_human_decision

    Returns:
        markdown string for one item (starting with ## title)
    """
    lines = [f"## {item['title']}", ""]
    lines.append(f"- date: {item['date']}")
    lines.append(f"- source: {item['source']}")
    lines.append(f"- project: myopenclaw")
    lines.append(f"- axis: agentops")
    lines.append(f"- status: {item['status']}")
    lines.append(f"- owner: {item['owner']}")
    lines.append(f"- evidence: {item['evidence']}")
    lines.append(f"- why_it_matters: {item['why_it_matters']}")
    lines.append(f"- suggested_next_action: {item['suggested_next_action']}")
    decision = "yes" if item["needs_human_decision"] else "no"
    lines.append(f"- needs_human_decision: {decision}")
    return "\n".join(lines)


def format_ledger_items(items):
    """Format multiple ledger items into a single markdown string.

    Items are separated by blank lines.
    """
    return "\n\n".join(format_ledger_item(item) for item in items)


# =============================================================
# 8. Ledger merge (auto + manual)
# =============================================================


def merge_ledger(existing_content, auto_items):
    """Merge auto-generated items with existing manual items.

    - Auto items (source: auto | ...) are replaced with new auto items
    - Manual items (source: NOT auto) are preserved unchanged
    - Auto items appear after manual items

    Args:
        existing_content: current content of inbox.md (str or empty)
        auto_items: list of new auto-generated item dicts

    Returns:
        merged markdown string
    """
    # Parse existing content into blocks
    manual_items = []
    current_block = None
    current_source = None

    for line in (existing_content or "").split("\n"):
        if line.startswith("## "):
            # Save previous block
            if current_block is not None and current_source is not None:
                if "auto" not in current_source.lower():
                    manual_items.append("\n".join(current_block))
            # Start new block
            current_block = [line]
            current_source = None
        elif line.startswith("- source:") and current_source is None:
            current_source = line
            if current_block is not None:
                current_block.append(line)
        elif current_block is not None:
            current_block.append(line)

    # Don't forget the last block
    if current_block is not None and current_source is not None:
        if "auto" not in current_source.lower():
            manual_items.append("\n".join(current_block))

    # Format new auto items
    auto_section = format_ledger_items(auto_items)

    # Merge: manual first, then auto
    parts = []
    if manual_items:
        parts.append("\n\n".join(manual_items))
    if auto_section:
        parts.append(auto_section)

    return "\n\n".join(parts)


# =============================================================
# 9. Main collection pipeline
# =============================================================


def collect_all_signals():
    """Run all signal detectors and return combined list of ledger items.

    Returns:
        list of ledger item dicts
    """
    items = []

    # Container status
    ps_data = get_container_ps_data()
    if ps_data:
        containers = parse_container_status(ps_data)
        items.extend(detect_restarts(containers))

    # Backup freshness
    items.extend(check_backup_freshness())

    # Disk usage
    items.extend(check_disk_usage())

    # Gateway errors
    items.extend(check_gateway_errors())

    # Hermes tirith 安全扫描器可用性（静默失效，无其它信号能发现）
    items.extend(check_tirith_binary())

    return items


def main():
    dry_run = "--dry-run" in sys.argv

    # Collect
    print("🔍 Collecting AgentOps signals ...")
    items = collect_all_signals()
    print(f"   Found {len(items)} signal(s):")
    for item in items:
        decision = "⚡" if item["needs_human_decision"] else "📋"
        print(f"   {decision} {item['title']}")

    if not items:
        print("   ✅ All systems nominal — no issues detected")

    # Format auto items
    auto_md = format_ledger_items(items)

    if dry_run:
        print(f"\n{'='*60}")
        print("📄 DRY RUN — would write to ledger:")
        print(f"{'='*60}")
        print(auto_md if auto_md else "(empty — nothing to write)")
        return

    # Read existing ledger
    existing = ""
    if AGENTOPS_LEDGER.exists():
        existing = AGENTOPS_LEDGER.read_text()

    # Merge and write
    merged = merge_ledger(existing, items)

    AGENTOPS_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    AGENTOPS_LEDGER.write_text(merged.strip() + "\n")

    print(f"\n📝 Written to {AGENTOPS_LEDGER}")
    if items:
        print(f"   Auto items: {len(items)}")
    else:
        print(f"   No issues — ledger unchanged")


if __name__ == "__main__":
    main()
