#!/usr/bin/env python3
"""
Collect AgentOps signals and write to the agentops ledger.

Detects:
  - Recently restarted containers
  - Stale backups
  - High disk usage
  - Gateway error loops
  - Unhealthy containers

Output: myloop/memory/agentops-ledger/inbox.md (auto items merged with manual)

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

REPO_ROOT = Path(__file__).resolve().parent.parent
MYLOOP_ROOT = os.environ.get("MYLOOP_ROOT", str(REPO_ROOT.parent / "myloop"))
AGENTOPS_LEDGER = Path(MYLOOP_ROOT) / "memory" / "agentops-ledger" / "inbox.md"

# Thresholds (configurable via env vars)
RESTART_THRESHOLD_HOURS = int(os.environ.get("AGENTOPS_RESTART_THRESHOLD", "2"))
BACKUP_STALE_HOURS = int(os.environ.get("AGENTOPS_BACKUP_STALE_HOURS", "24"))
DISK_THRESHOLD_PERCENT = int(os.environ.get("AGENTOPS_DISK_THRESHOLD", "85"))

# Paths
BACKUP_ROOT = os.environ.get(
    "AGENTOPS_BACKUP_ROOT",
    os.path.expanduser("~/Google Drive/我的云端硬盘/myopenclaw-backups"),
)
GATEWAY_ERROR_SCRIPT = str(REPO_ROOT / "scripts" / "check-gateway-errors.sh")
GATEWAY_ERR_LOG = os.path.expanduser("~/.openclaw/logs/gateway.err.log")


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


def _get_latest_backup_time(backup_root):
    """Get the timestamp of the most recent backup across all services.

    Returns datetime or None if no backups found.
    """
    backup_path = Path(backup_root)
    if not backup_path.exists():
        return None

    latest_time = None
    try:
        for service_dir in backup_path.iterdir():
            if not service_dir.is_dir():
                continue
            latest_link = service_dir / "latest"
            if latest_link.is_symlink():
                try:
                    mtime = datetime.fromtimestamp(latest_link.stat().st_mtime)
                    if latest_time is None or mtime > latest_time:
                        latest_time = mtime
                except OSError:
                    continue
    except (PermissionError, OSError) as e:
        print(f"⚠️  Cannot read backup dir {backup_root}: {e}", file=sys.stderr)

    return latest_time


def check_backup_freshness(backup_root=BACKUP_ROOT, threshold_hours=BACKUP_STALE_HOURS):
    """Check if backups are stale.

    Args:
        backup_root: path to backup directory
        threshold_hours: backups older than this generate an item

    Returns:
        list of ledger item dicts
    """
    latest = _get_latest_backup_time(backup_root)

    if latest is None:
        return [{
            "title": "备份未找到或从未执行",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "source": "auto | backup-cron",
            "status": "new",
            "owner": "owen",
            "evidence": f"备份目录 {backup_root} 中无 latest/ 符号链接",
            "why_it_matters": "数据安全依赖定期备份，没有备份意味着容器配置和记忆面临丢失风险",
            "suggested_next_action": "检查 backup-cron 容器日志，确认 BACKUP_ROOT 和云盘客户端配置正确",
            "needs_human_decision": True,
        }]

    age = datetime.now() - latest
    if age > timedelta(hours=threshold_hours):
        hours_ago = age.total_seconds() / 3600
        return [{
            "title": "备份过期",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "source": "auto | backup-cron",
            "status": "watch",
            "owner": "owen",
            "evidence": f"最新备份: {latest.strftime('%Y-%m-%d %H:%M')}（{hours_ago:.0f}h 前），阈值: {threshold_hours}h",
            "why_it_matters": f"备份已过期 {hours_ago:.0f} 小时，超过 {threshold_hours}h 阈值。数据安全存在风险",
            "suggested_next_action": "手动触发备份: docker compose exec backup-cron /scripts/backup-all-docker.sh",
            "needs_human_decision": True,
        }]

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
