#!/usr/bin/env bash
# smoke.sh — paper-to-zotero pipeline end-to-end verification
# Run from repo root: bash .claude/skills/run-paper-to-zotero/smoke.sh
set -euo pipefail

DOI="${1:-10.48550/arXiv.1706.03762}"  # default: Attention Is All You Need
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "=== paper-to-zotero pipeline smoke test ==="
echo "DOI: $DOI"
echo ""

# ── Prerequisite checks ──────────────────────────────────────────
echo "1. Checking prerequisites..."
docker compose ps hermes-coder --format json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d.get('State')=='running' else 1)" \
  || { echo "ERROR: hermes-coder container not running. Run: ./scripts/start.sh"; exit 1; }
echo "   hermes-coder: running"

docker compose exec -T hermes-coder test -x /opt/hermes/scripts/paper-to-zotero.py \
  || { echo "ERROR: paper-to-zotero.py not found in container"; exit 1; }
echo "   paper-to-zotero.py: found"

docker compose exec -T hermes-coder test -f /opt/data/skills/paper-fetch/scripts/fetch.py \
  || { echo "ERROR: paper-fetch not installed"; exit 1; }
echo "   paper-fetch: installed"

docker compose exec -T hermes-coder which rclone > /dev/null \
  || { echo "ERROR: rclone not found"; exit 1; }
echo "   rclone: available"

echo ""

# ── Step 1: Download PDF ─────────────────────────────────────────
echo "2. Downloading PDF via paper-fetch..."

# Save JSON inside the container (not on host)
docker compose exec -T hermes-coder bash -c "
  cd /opt/data/skills/paper-fetch &&
  python3 scripts/fetch.py '${DOI}' --out /tmp/papers --format json > /tmp/pf_smoke.json
"

# Check result by reading from inside container
OK=$(docker compose exec -T hermes-coder bash -c "
  python3 -c \"import json; print(json.load(open('/tmp/pf_smoke.json'))['ok'])\"
")
if [ "$OK" != "True" ]; then
  echo "ERROR: paper-fetch failed"
  docker compose exec -T hermes-coder cat /tmp/pf_smoke.json
  exit 1
fi

PF_FILE=$(docker compose exec -T hermes-coder bash -c "
  python3 -c \"import json; print(json.load(open('/tmp/pf_smoke.json'))['data']['results'][0]['file'])\"
")
PF_TITLE=$(docker compose exec -T hermes-coder bash -c "
  python3 -c \"import json; print(json.load(open('/tmp/pf_smoke.json'))['data']['results'][0]['meta']['title'][:60])\"
")
echo "   Downloaded: $PF_FILE"
echo "   Title: $PF_TITLE"
echo ""

# ── Step 2: Upload to Google Drive ───────────────────────────────
echo "3. Uploading to Google Drive..."
BASENAME=$(basename "$PF_FILE")
docker compose exec -T hermes-coder rclone copy "/tmp/papers/$BASENAME" gdrive:
echo "   Uploaded: $BASENAME → gdrive:"
echo ""

# ── Step 3: Create Zotero entry ──────────────────────────────────
echo "4. Creating Zotero entry..."
GDRIVE_PATH="${GDRIVE_PAPERS_LOCAL_PATH:-$HOME/Google Drive/我的云端硬盘/Papers/Zotero_Papers}"
RESULT=$(docker compose exec -T hermes-coder bash -c "
  /opt/hermes/scripts/paper-to-zotero.py /tmp/pf_smoke.json '${GDRIVE_PATH}/${BASENAME}'
")
echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"

ZOTERO_KEY=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['zotero_key'])")
ATTACH_KEY=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin)['attachment_key'])")
echo ""

# ── Step 4: Verify ───────────────────────────────────────────────
echo "5. Verifying Zotero entry via API..."
docker compose exec -T hermes-coder /opt/uv-tools/zotero-cli-cc/bin/python -c "
import tomllib, json
from pyzotero import zotero as zt

config = tomllib.load(open('/opt/data/.config/zot/config.toml', 'rb'))
z = zt.Zotero(config['zotero']['library_id'], 'user', config['zotero']['api_key'])
item = z.item('${ZOTERO_KEY}')
data = item['data']
print(f'  Type: {data[\"itemType\"]}')
print(f'  Title: {data[\"title\"][:80]}')
print(f'  Authors: {len(data.get(\"creators\", []))}')
print(f'  Date: {data.get(\"date\", \"\")}')
print(f'  DOI: {data.get(\"DOI\", \"\")}')
print(f'  Abstract: {\"yes\" if data.get(\"abstractNote\") else \"no\"}')

children = z.children('${ZOTERO_KEY}')
for child in children:
    cd = child['data']
    print(f'  Attachment: {cd.get(\"linkMode\", \"?\")} → {cd.get(\"path\", \"?\")[:60]}')
"
echo ""

# ── Step 5: Cleanup ───────────────────────────────────────────────
echo "6. Cleaning up..."
docker compose exec -T hermes-coder /opt/uv-tools/zotero-cli-cc/bin/python -c "
import tomllib
from pyzotero import zotero as zt
config = tomllib.load(open('/opt/data/.config/zot/config.toml', 'rb'))
z = zt.Zotero(config['zotero']['library_id'], 'user', config['zotero']['api_key'])
z.delete_item(z.item('${ATTACH_KEY}'))
z.delete_item(z.item('${ZOTERO_KEY}'))
print('  Deleted Zotero items: ${ZOTERO_KEY}, ${ATTACH_KEY}')
"

docker compose exec -T hermes-coder rclone deletefile "gdrive:${BASENAME}" 2>/dev/null || true
docker compose exec -T hermes-coder rm -f "/tmp/papers/${BASENAME}" /tmp/pf_smoke.json
echo "   Cleaned up temp files"

echo ""
echo "=== Pipeline verified OK ==="
