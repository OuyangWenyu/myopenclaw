#!/usr/bin/env bash
# Test: yuque-daily-digest cron job registered with expected schedule/deliver.
#
# Prerequisite: the cron job has been registered (run ./scripts/start.sh with
# YUQUE_DAILY_PUSH_REPOS set in .env, on the host that runs the stack).
#
# Usage:
#   bash skills/yuque-daily-digest/test-cron-config.sh
set -euo pipefail

JOBS_FILE="${HOME}/.hermes/cron/jobs.json"
JOB_NAME="yuque-daily-digest"
EXPECTED_SCHEDULE="10 0 * * *"
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

echo "=== Test: yuque-daily-digest Cron Job Config ==="
echo "Jobs file: $JOBS_FILE"
echo ""

# 1. File exists and is valid JSON
check "jobs.json exists" \
    "test -f '$JOBS_FILE'"

check "jobs.json is valid JSON" \
    "python3 -c 'import json; json.load(open(\"$JOBS_FILE\"))' 2>&1"

# 2. Job exists (lookup by name — IDs are generated at registration)
check "Job '$JOB_NAME' exists" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
names = [j.get('name', '') for j in data['jobs']]
assert '$JOB_NAME' in names, f'Job $JOB_NAME not found in {names}'
\" 2>&1"

# 3. Schedule is 10 0 * * * (08:10 Beijing = 00:10 UTC)
check "Schedule is '$EXPECTED_SCHEDULE' (08:10 北京)" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j.get('name') == '$JOB_NAME')
expr = job.get('schedule', {}).get('expr') if isinstance(job.get('schedule'), dict) else job.get('schedule')
assert expr == '$EXPECTED_SCHEDULE', f'schedule is {expr}'
\" 2>&1"

# 4. Job is enabled
check "Job is enabled" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j.get('name') == '$JOB_NAME')
assert job.get('enabled') == True, f'enabled is {job.get(\"enabled\")}'
\" 2>&1"

# 5. Prompt drives the yuque-daily-digest skill
check "Prompt references yuque-daily-digest skill" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j.get('name') == '$JOB_NAME')
assert 'yuque-daily-digest' in job.get('prompt', ''), 'prompt does not reference the skill'
\" 2>&1"

# 6. Deliver target is a Feishu private chat
check "Deliver target is feishu:*" \
    "python3 -c \"
import json
data = json.load(open('$JOBS_FILE'))
job = next(j for j in data['jobs'] if j.get('name') == '$JOB_NAME')
deliver = job.get('deliver') or job.get('delivery') or ''
assert str(deliver).startswith('feishu:'), f'deliver is {deliver!r}'
\" 2>&1"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "=== PASS: yuque-daily-digest cron job config is valid ==="
