#!/usr/bin/env bash
# =============================================================
# scripts/install-repo-triage-cron.sh
# 幂等安装 Hermes cron 任务：工作日仓库动态推送
# 每天 BJT 7:50 (UTC 23:50) Mon-Fri 触发 repo-triage skill
# =============================================================
set -euo pipefail

CRON_FILE="${HOME}/.hermes/cron/jobs.json"
JOB_NAME="工作日仓库动态推送"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

dry_run=false
if [[ "${1:-}" == "--dry-run" ]]; then
    dry_run=true
fi

# ── 前置检查 ────────────────────────────────────────────────
if [[ ! -f "${CRON_FILE}" ]]; then
    echo "❌ cron 配置文件不存在: ${CRON_FILE}" >&2
    echo "   请确认 Hermes 已启动（cron 配置文件由网关自动创建）" >&2
    exit 1
fi

# ── 幂等安装 ────────────────────────────────────────────────
python3 - "$CRON_FILE" "$JOB_NAME" "$dry_run" << 'PYEOF'
import json, sys

cron_file = sys.argv[1]
job_name = sys.argv[2]
dry_run = sys.argv[3] == "True"

with open(cron_file) as f:
    data = json.load(f)

# Check if job already exists
existing = None
for i, job in enumerate(data.get("jobs", [])):
    if job.get("name") == job_name:
        existing = i
        break

new_job = {
    "name": job_name,
    "prompt": "执行 repo-triage 技能，获取今日仓库活动数据并生成中文摘要。按 SKILL.md 中的规则分析 JSON 数据。重点突出与用户本人相关的活动、被合并的 PR。忽略 trivial commits。如果 has_activity 为 false 则回复 [SILENT]。",
    "skills": ["repo-triage"],
    "skill": "repo-triage",
    "model": None,
    "provider": None,
    "base_url": None,
    "schedule": {
        "kind": "cron",
        "expr": "50 23 * * 0-4",
        "display": "50 23 * * 0-4"
    },
    "schedule_display": "50 23 * * 0-4",
    "repeat": {"times": None, "completed": 0},
    "enabled": True,
    "state": "scheduled",
    "deliver": "origin",
    "origin": {
        "platform": "feishu",
        "chat_id": "oc_d2fbe8b5bc4d5bed46877c0b1ca2d963",
        "chat_name": "oc_d2fbe8b5bc4d5bed46877c0b1ca2d963",
        "thread_id": None
    }
}

if existing is not None:
    if dry_run:
        print(f"📋 任务已存在，将更新: {job_name}")
        print(json.dumps(new_job, ensure_ascii=False, indent=2))
    else:
        data["jobs"][existing] = new_job
        print(f"📎 已更新现有任务: {job_name}")
else:
    if dry_run:
        print(f"📋 将创建新任务: {job_name}")
        print(json.dumps(new_job, ensure_ascii=False, indent=2))
    else:
        if "jobs" not in data:
            data["jobs"] = []
        data["jobs"].append(new_job)
        print(f"✅ 已创建新任务: {job_name}")

if not dry_run:
    with open(cron_file, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
PYEOF

echo ""
echo "📋 任务详情:"
echo "   skill:  repo-triage"
echo "   调度:   UTC 23:50 Sun-Thu = BJT 07:50 Mon-Fri"
echo "   推送:   飞书 Hermes 私聊 (oc_d2fbe8b5bc4d5bed46877c0b1ca2d963)"
echo "   静默:   无活动时回复 [SILENT]，不推送"
echo ""
echo "⚠️  需要重启 Hermes 使 cron 生效:"
echo "   docker compose restart hermes"
echo ""
echo "🔍 验证:"
echo "   cat ~/.hermes/cron/jobs.json | python3 -m json.tool | grep -A10 repo-triage"
