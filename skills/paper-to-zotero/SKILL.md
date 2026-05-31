---
name: paper-to-zotero
description: Use when the user wants to download a paper AND add it to Zotero — by DOI, arXiv ID, or paper title. Trigger on "下载并加到Zotero", "同步至Zotero", "同步到Zotero文库", "下载论文并导入", "加到文献库", "加到Zotero", "download and add to Zotero", "save paper to Zotero", "put this paper in my library", "download X paper and sync to Zotero", or ANY phrase that combines obtaining a paper (下载/找/获取/download/find/fetch/get) with Zotero/library (Zotero/文献库/library/references). This skill runs the COMPLETE automated pipeline: paper-fetch → Google Drive → Zotero linked_file. ALL tools and credentials are pre-configured — do NOT install packages, do NOT pip install, do NOT ask for API keys.
homepage: https://github.com/Agents365-ai/myopenclaw
metadata: {"author":"owen","version":"2.0","category":"research","tags":["paper","pdf","zotero","google-drive","literature"]}
---

# Paper-to-Zotero Pipeline

IMPORTANT: Everything is pre-configured. Do NOT install anything, do NOT ask for credentials. Just run the commands below.

Download a paper, upload to Google Drive (primary PDF store), create a Zotero entry with full metadata + linked_file PDF attachment.

## Step 1: Download PDF

If the user gave a **paper title** (most common):
```bash
cd /opt/data/skills/paper-fetch && python3 scripts/fetch.py --title "<TITLE>" --out /tmp/papers --format json > /tmp/pf.json
```

If they gave a **DOI**:
```bash
cd /opt/data/skills/paper-fetch && python3 scripts/fetch.py "<DOI>" --out /tmp/papers --format json > /tmp/pf.json
```

Check: `python3 -c "import json; d=json.load(open('/tmp/pf.json')); print(d['ok'])"` — must print `True`. If `False`, stop.

## Step 2: Upload to Google Drive

```bash
BASENAME=$(python3 -c "import json; d=json.load(open('/tmp/pf.json')); print(d['data']['results'][0]['file'].split('/')[-1])")
rclone copy "/tmp/papers/$BASENAME" gdrive:
```

## Step 3: Create Zotero entry (metadata + linked_file PDF)

```bash
/opt/hermes/scripts/paper-to-zotero.py /tmp/pf.json
```

This single command reads the paper-fetch JSON, auto-constructs the local Google Drive path from `$GDRIVE_PAPERS_LOCAL_PATH` + filename, enriches metadata (Crossref → arXiv → paper-fetch fallback), creates the Zotero item with all fields, and attaches a linked_file PDF. Returns `{"ok": true, "zotero_key": "XXX", ...}`.

## Step 4: Cleanup

```bash
rm "/tmp/papers/$BASENAME" /tmp/pf.json
```

Report: `✅ 已添加: <title> | 📚 Zotero: <key>`

## Already in Zotero?

```bash
zot search "<DOI_OR_TITLE>"
```

If found, only do Steps 1-2, then:
```bash
/opt/hermes/scripts/zot-link-gdrive.py <KEY> "$BASENAME"
```

## Notes

- paper-fetch `--out-file` flag does NOT exist — use `--format json > /tmp/pf.json` to capture output
- arXiv papers use `10.48550/arXiv.<id>` format DOI — paper-fetch resolves this from `--title` automatically
- `zot read <key>` queries local SQLite — API-created items may not appear until Zotero syncs
