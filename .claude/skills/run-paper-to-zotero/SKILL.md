---
name: run-paper-to-zotero
description: Use when the user wants to download a paper and add it to Zotero — by DOI, arXiv ID, or paper title. Trigger on "下载并加到Zotero", "同步至Zotero", "下载论文并导入", "加到文献库", "download and add to Zotero", "save paper to Zotero", "put this paper in my library", or any phrase that combines paper download/fetch with Zotero/library management. This skill runs the complete paper-fetch → Google Drive → Zotero linked_file pipeline.
---

# Paper-to-Zotero Pipeline

Downloads a paper PDF, uploads to Google Drive, and creates a complete Zotero entry with rich metadata and a `linked_file` PDF attachment — all in one command. Everything is pre-configured — do NOT install packages or ask for credentials.

## Prerequisites (auto-verified)

```bash
docker compose exec hermes-coder test -x /opt/hermes/scripts/run-paper-pipeline.sh
docker compose exec hermes-coder test -f /opt/data/skills/paper-fetch/scripts/fetch.py
docker compose exec hermes-coder which rclone
```

## Run (one-shot)

```bash
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh "<DOI_OR_TITLE>"
```

For a DOI: pass the DOI directly. For a paper title: pass the title string. The script auto-detects which format you gave.

For a dry run (preview without actually creating the Zotero entry):
```bash
docker compose exec hermes-coder /opt/hermes/scripts/run-paper-pipeline.sh --dry-run "<DOI>"
```

The command runs all 4 steps atomically:
1. paper-fetch downloads the PDF
2. rclone uploads to Google Drive
3. paper-to-zotero.py creates the Zotero entry with rich metadata + linked_file
4. Cleanup of temp files

## Already in library?

```bash
docker compose exec hermes-coder zot search "<DOI_OR_TITLE>"
```

If found, download + upload the PDF manually, then link to the existing Zotero entry:

```bash
docker compose exec hermes-coder /opt/hermes/scripts/zot-link-gdrive.py <EXISTING_KEY> "<filename>"
```

## Gotchas

- arXiv papers use `10.48550/arXiv.<id>` DOI format — paper-fetch resolves this from a title automatically.
- Zotero items created via Web API are not immediately visible in `zot read` — `zot read` queries the local SQLite database; API-created items need a sync cycle.
- Container filesystem is ephemeral — the script writes and cleans up `/tmp` inside the container.
