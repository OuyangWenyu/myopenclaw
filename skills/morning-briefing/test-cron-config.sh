#!/usr/bin/env bash
# Test: Cron job 260cdd03f97e 配置有效（skill + 日期预脚本）
# 2026-10-13 起：job 必须挂 cst_today.py —— 星期几只认脚本输出，代理不得凭直觉断言
# （当日 07:30 那期就是没查日期、误判成周末，把整期简报吞了）。
set -euo pipefail

JOBS_FILE="${HOME}/.hermes/cron/jobs.json"
JOB_ID="260cdd03f97e"
PASS=0
FAIL=0

check() {
    local desc="$1"
    if eval "$2"; then
        echo "  ✓ $desc"
        PASS=$((PASS + 1))
    else
        echo "  ✗ FAIL: $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Test: Cron Job Config Update ==="
echo "Job ID: $JOB_ID"
echo ""

# 1. File exists and is valid JSON
check "jobs.json exists" \
    "test -f '$JOBS_FILE'"

check "jobs.json is valid JSON" \
    "python3 -c 'import json; json.load(open(\"$JOBS_FILE\"))' 2>&1"

# 2. Job 260cdd03f97e exists
check "Job $JOB_ID exists" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
ids = [j['id'] for j in data['jobs']]
assert '$JOB_ID' in ids, f'Job $JOB_ID not found in {ids}'
\" 2>&1"

# 3. Job has skills field + 日期预脚本
check "Job has 'skills' field + date pre-script" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j['id'] == '$JOB_ID')
assert 'skills' in job, 'skills field missing'
assert job.get('script') == 'cst_today.py', f'script is {job.get(\"script\")!r}, want cst_today.py'
\" 2>&1"

check "Pre-script exists at ~/.hermes/scripts/cst_today.py" \
    "test -f \"\$HOME/.hermes/scripts/cst_today.py\""

# 4. skills includes morning-briefing
check "Skills includes 'morning-briefing'" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j['id'] == '$JOB_ID')
assert 'morning-briefing' in job.get('skills', []), f'skills does not include morning-briefing: {job.get(\"skills\")}'
\" 2>&1"

# 5. Job is enabled
check "Job is enabled" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j['id'] == '$JOB_ID')
assert job.get('enabled') == True, f'enabled is {job.get(\"enabled\")}'
\" 2>&1"

# 6. Job schedule is Mon–Sat mornings (UTC 周日–周五 23:30 = 北京周一–周六 07:30)
check "Schedule is 30 23 * * 0-5 (北京周一至周六 07:30)" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j['id'] == '$JOB_ID')
assert job['schedule']['expr'] == '30 23 * * 0-5', f'schedule is {job[\"schedule\"][\"expr\"]}'
\" 2>&1"

check "Prompt uses --check-briefing-day gate" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j['id'] == '$JOB_ID')
prompt = job.get('prompt', '')
assert '--check-briefing-day' in prompt, 'prompt missing --check-briefing-day'
assert '--check-weekend' not in prompt, 'prompt still references deprecated --check-weekend'
\" 2>&1"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "=== PASS: Cron job config is valid ==="
