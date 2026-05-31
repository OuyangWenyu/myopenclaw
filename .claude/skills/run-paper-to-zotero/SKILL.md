---
name: run-paper-to-zotero
description: Use when the user wants to download a paper and add it to Zotero — by DOI, arXiv ID, or paper title. Trigger on "下载并加到Zotero", "同步至Zotero", "下载论文并导入", "加到文献库", "download and add to Zotero", "save paper to Zotero", "put this paper in my library", or any phrase that combines paper download/fetch with Zotero/library management. This skill runs the complete paper-fetch → Google Drive → Zotero linked_file pipeline.
---

# Paper-to-Zotero Pipeline

Downloads a paper PDF, uploads to Google Drive, and creates a complete Zotero entry with rich metadata and a `linked_file` PDF attachment. Everything is pre-configured — do NOT install packages or ask for credentials.

## Prerequisites (auto-verified)

```bash
docker compose exec hermes-coder test -x /opt/hermes/scripts/paper-to-zotero.py
docker compose exec hermes-coder test -f /opt/data/skills/paper-fetch/scripts/fetch.py
docker compose exec hermes-coder which rclone
```

## Run

One-shot via the smoke driver:

```bash
bash .claude/skills/run-paper-to-zotero/smoke.sh "<DOI>"
```

Or manual 4-step pipeline:

### Step 1: Download PDF

```bash
docker compose exec hermes-coder bash -c "
  cd /opt/data/skills/paper-fetch &&
  python3 scripts/fetch.py --title '<PAPER_TITLE>' --out /tmp/papers --format json > /tmp/pf.json
"
```

For a DOI: omit `--title` and pass the DOI string directly.

Check: `docker compose exec hermes-coder python3 -c "import json; d=json.load(open('/tmp/pf.json')); print(d['ok'])"` — must be `True`.

### Step 2: Upload to Google Drive

```bash
BASENAME=$(docker compose exec hermes-coder python3 -c "import json; print(json.load(open('/tmp/pf.json'))['data']['results'][0]['file'].split('/')[-1])")
docker compose exec hermes-coder rclone copy "/tmp/papers/$BASENAME" gdrive:
```

### Step 3: Create Zotero entry

```bash
docker compose exec hermes-coder /opt/hermes/scripts/paper-to-zotero.py /tmp/pf.json
```

Auto-constructs the local Google Drive path from `$GDRIVE_PAPERS_LOCAL_PATH` + filename from JSON. Returns `{"ok": true, "zotero_key": "XXX", "attachment_key": "YYY", ...}`.

### Step 4: Cleanup

```bash
docker compose exec hermes-coder rm -f "/tmp/papers/$BASENAME" /tmp/pf.json
```

## Already in library?

```bash
docker compose exec hermes-coder zot search "<DOI_OR_TITLE>"
```

If found, do only Steps 1-2 (download + upload), then:

```bash
docker compose exec hermes-coder /opt/hermes/scripts/zot-link-gdrive.py <EXISTING_KEY> "<filename>"
```

## Gotchas

- **paper-fetch outputs streaming events to stderr** — only the final line (with `--format json`) goes to stdout. The JSON in `/tmp/pf.json` is the result object.
- **paper-fetch `--out-file` does not exist** — use `--format json > /tmp/pf.json` instead.
- **arXiv papers need `10.48550/arXiv.<id>` DOI format** — paper-fetch resolves this automatically from `--title`, but if you have a raw arXiv ID, wrap it: `10.48550/arXiv.2605.26324`.
- **Zotero items created via Web API are not immediately visible in `zot read`** — `zot read` queries the local SQLite database; API-created items need a sync cycle. Use the pyzotero API to verify immediately.
- **Container filesystem is ephemeral** — `/tmp/pf.json` must be written inside the container, not on the host.
