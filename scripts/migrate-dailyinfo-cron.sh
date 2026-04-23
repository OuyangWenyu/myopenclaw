#!/usr/bin/env bash
# =============================================================
# scripts/migrate-dailyinfo-cron.sh
# One-shot migration from the old OpenClaw-cron push flow to the
# new host-level launchd + dailyinfo CLI flow.
#
# Steps (idempotent):
#   1. Back up ~/.openclaw/cron/jobs.json.
#   2. Remove 4 legacy jobs (papers/ainews/code/resource-daily-push).
#      They embed a plaintext Discord Bot token and duplicate what
#      `dailyinfo push` now handles.
#   3. Merge non-conflicting files from
#      ~/.openclaw/workspace/{briefings,pushed}/ into
#      ~/.myagentdata/dailyinfo/{briefings,pushed}/ via
#      `rsync --ignore-existing --remove-source-files`, then drop
#      the emptied source directories.
#
# This does NOT restart openclaw-gateway. If you want the container
# to pick up the cron change immediately, run:
#     docker compose restart openclaw-gateway
# =============================================================
set -euo pipefail

CRON_FILE="${HOME}/.openclaw/cron/jobs.json"
SRC_ROOT="${HOME}/.openclaw/workspace"
DST_ROOT="${HOME}/.myagentdata/dailyinfo"

echo "==> 一次性迁移：从旧 openclaw cron 迁到 dailyinfo launchd"
echo ""

# ── Step 1 & 2: prune legacy jobs ───────────────────────────────
if [[ -f "${CRON_FILE}" ]]; then
    backup="${CRON_FILE}.bak.before-dailyinfo-migration"
    if [[ ! -f "${backup}" ]]; then
        cp "${CRON_FILE}" "${backup}"
        echo "📦 已备份原 jobs.json → ${backup}"
    else
        echo "📦 已有备份，跳过: ${backup}"
    fi

    python3 - "${CRON_FILE}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())

# Legacy jobs whose prompts target the old ~/.openclaw/workspace/briefings
# push flow. Matching by exact name keeps unrelated jobs (e.g. the giffgaff
# reminder) intact.
legacy_names = {
    "papers-daily-push",
    "ainews-daily-push",
    "code-daily-push",
    "resource-daily-push",
}

jobs = data.get("jobs", [])
before = len(jobs)
kept = [j for j in jobs if j.get("name") not in legacy_names]
removed = [j.get("name") for j in jobs if j.get("name") in legacy_names]
data["jobs"] = kept

path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

print(f"🧹 删除 {before - len(kept)} 条旧 job: {removed}")
print(f"📋 保留 {len(kept)} 条 job: {[j.get('name') for j in kept]}")
PY
else
    echo "ℹ️  ${CRON_FILE} 不存在，跳过 cron job 清理"
fi

echo ""

# ── Step 3: merge legacy workspace data into ~/.myagentdata ─────
if [[ -d "${SRC_ROOT}" ]]; then
    moved_any=0
    for sub in briefings pushed; do
        src_sub="${SRC_ROOT}/${sub}"
        dst_sub="${DST_ROOT}/${sub}"
        [[ -d "${src_sub}" ]] || continue

        for cat in papers ai_news code resource; do
            src="${src_sub}/${cat}"
            dst="${dst_sub}/${cat}"
            [[ -d "${src}" ]] || continue

            mkdir -p "${dst}"

            before=$(find "${src}" -type f 2>/dev/null | wc -l | tr -d ' ')
            rsync -a --ignore-existing --remove-source-files "${src}/" "${dst}/"
            find "${src}" -type d -empty -delete 2>/dev/null || true
            after_files=$(find "${dst}" -type f 2>/dev/null | wc -l | tr -d ' ')

            if (( before > 0 )); then
                echo "🔀 ${sub}/${cat}: 处理 ${before} 个源文件 → 目标当前共 ${after_files} 个"
                moved_any=1
            fi
        done

        find "${src_sub}" -type d -empty -delete 2>/dev/null || true
    done

    find "${SRC_ROOT}" -type d -empty -delete 2>/dev/null || true

    if (( moved_any == 0 )); then
        echo "ℹ️  旧 workspace 已无 dailyinfo 相关文件，无需合并"
    fi
else
    echo "ℹ️  ${SRC_ROOT} 不存在，跳过数据合并"
fi

echo ""
echo "✅ 迁移完成"
echo ""
echo "下一步："
echo "  1. 安装 launchd 任务：./scripts/launchd/install-dailyinfo.sh"
echo "  2. 重启 openclaw-gateway 以让 cron 变化立刻生效（可选）："
echo "     docker compose restart openclaw-gateway"
