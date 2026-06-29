#!/usr/bin/env bash
# =============================================================
# Weekly AI News recap generation
# Called by cc-connect cron every Sunday 00:00 UTC (08:00 Beijing)
# =============================================================
set -euo pipefail

DATE=$(date +%Y-%m-%d)
WEEKLY_DIR="/home/node/.myagentdata/dailyinfo/briefings/weekly"
mkdir -p "$WEEKLY_DIR"

echo "[$(date '+%H:%M:%S')] Generating weekly AI News recap for $DATE..."

cd /home/node/code/dailyinfo
DAILYINFO_DATA_ROOT=/home/node/.myagentdata/dailyinfo uv run python scripts/weekly_summary.py --force 2>&1

if [ -f "$WEEKLY_DIR/weekly_recap_${DATE}.md" ]; then
    chars=$(wc -c < "$WEEKLY_DIR/weekly_recap_${DATE}.md")
    echo "[$(date '+%H:%M:%S')] ✅ Weekly recap generated: $WEEKLY_DIR/weekly_recap_${DATE}.md ($chars chars)"
    exit 0
else
    echo "[$(date '+%H:%M:%S')] ❌ Failed to generate weekly recap"
    exit 1
fi
