---
name: paper-to-zotero
description: Use when the user wants to download a paper AND add it to Zotero — by DOI, arXiv ID, or paper title. Trigger on "下载并加到Zotero", "同步至Zotero", "同步到Zotero文库", "下载论文并导入", "加到文献库", "加到Zotero", "download and add to Zotero", "save paper to Zotero", "put this paper in my library", "download X paper and sync to Zotero", or ANY phrase that combines obtaining a paper (下载/找/获取/download/find/fetch/get) with Zotero/library (Zotero/文献库/library/references). This skill runs the COMPLETE automated pipeline: paper-fetch → Google Drive → Zotero linked_file. ALL tools and credentials are pre-configured — do NOT install packages, do NOT pip install, do NOT ask for API keys.
homepage: https://github.com/Agents365-ai/myopenclaw
metadata: {"author":"owen","version":"3.0","category":"research","tags":["paper","pdf","zotero","google-drive","literature"]}
---

# Paper-to-Zotero Pipeline

IMPORTANT: Everything is pre-configured. Do NOT install anything, do NOT ask for credentials. Just run the command below.

Download a paper, upload to Google Drive (primary PDF store), create a Zotero entry with full metadata + linked_file PDF attachment — **one command**.

## Run (one-shot)

If the user gave a **DOI**:
```bash
/opt/hermes/scripts/run-paper-pipeline.sh "<DOI>"
```

If they gave a **paper title**:
```bash
/opt/hermes/scripts/run-paper-pipeline.sh "<TITLE>"
```

The script auto-detects DOI vs title format. For a dry run (preview metadata only, no Zotero write):
```bash
/opt/hermes/scripts/run-paper-pipeline.sh --dry-run "<DOI>"
```

The single command does all 4 steps: paper-fetch download → rclone upload → paper-to-zotero metadata enrichment + Zotero create → cleanup.

Report: `✅ 已添加: <title> | 📚 Zotero: <key>`

## Already in Zotero?

```bash
zot search "<DOI_OR_TITLE>"
```

If found, download + upload the PDF manually, then link it to the existing Zotero entry:
```bash
/opt/hermes/scripts/zot-link-gdrive.py <KEY> "<filename>"
```

## Notes

- arXiv papers use `10.48550/arXiv.<id>` DOI format — paper-fetch resolves this from a title automatically
- `zot read <key>` queries local SQLite — API-created items may not appear until Zotero syncs
