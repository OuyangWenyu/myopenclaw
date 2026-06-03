---
name: run-paper-to-zotero
description: Use when the user wants to download a paper and add it to Zotero — by DOI, arXiv ID, or paper title. Trigger on "下载并加到Zotero", "同步至Zotero", "下载论文并导入", "加到文献库", "download and add to Zotero", "save paper to Zotero", "put this paper in my library", or any phrase that combines paper download/fetch with Zotero/library management. This skill runs the complete paper-fetch → Google Drive → Zotero linked_file pipeline.
---

# Paper-to-Zotero Pipeline

YOU MUST run EXACTLY ONE command. Do NOT manually download, upload, or create Zotero entries. Do NOT use curl, wget, rclone, or the Zotero API directly.

## The ONLY command you may run:

```bash
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh "<DOI_OR_TITLE>"
```

If the user gave a DOI: pass the DOI string. If they gave a paper title: pass the title. The script auto-detects the format. The single command does all 4 steps: paper-fetch download → rclone upload → paper-to-zotero metadata + linked_file → cleanup.

## What you MUST NOT do:

- DO NOT download PDFs manually (no curl, wget, python fetch)
- DO NOT upload to Google Drive manually (no rclone copy)
- DO NOT create Zotero entries manually (no pyzotero, zot add, Web API)
- DO NOT create attachments manually
- DO NOT run manual steps inside the container

If the command fails: report the error to the user. Do NOT attempt manual workarounds.
