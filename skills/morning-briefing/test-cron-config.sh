#!/usr/bin/env bash
# Test: Cron job 260cdd03f97e uses skill not script, config is valid
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

# 3. Job has skills field (not script)
check "Job has 'skills' field (not 'script')" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j['id'] == '$JOB_ID')
assert 'skills' in job, 'skills field missing'
assert 'script' not in job or job.get('script') is None, 'script field should be removed'
\" 2>&1"

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

# 6. Job schedule is weekday mornings
check "Schedule is 0 0 * * 1-5 (weekdays)" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j['id'] == '$JOB_ID')
assert job['schedule']['expr'] == '0 0 * * 1-5', f'schedule is {job[\"schedule\"][\"expr\"]}'
\" 2>&1"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "=== PASS: Cron job config is valid ==="
