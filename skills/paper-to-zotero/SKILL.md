---
name: paper-to-zotero
description: Use whenever the user wants to download a paper AND add it to Zotero — given a DOI, arXiv ID, paper title, or citation. Trigger on phrases like "下载这篇论文并加到Zotero", "下载并同步到Zotero文库", "帮我找X论文加到文献库", "download and add to Zotero", "add this paper to my library", "download X paper and sync to Zotero", or any request that combines paper download with Zotero library management. This is the complete pipeline skill that orchestrates paper-fetch → Google Drive → Zotero.
---

# Paper-to-Zotero Pipeline

Download a paper PDF, upload it to Google Drive, and create a Zotero entry with a linked attachment. Google Drive is the PRIMARY PDF store; Zotero holds metadata with a clickable `linked_url` that opens the Drive file.

## Prerequisites

- `paper-fetch` skill — downloads PDFs (already installed)
- `zot` CLI — Zotero read/write (already installed)
- `rclone` — Google Drive upload (already installed, remote `gdrive:`)
- `zot-link-gdrive.py` — creates linked_url attachments (at `/opt/hermes/scripts/`)

## Complete Pipeline (5 steps)

Execute each step sequentially. Report progress to the user after each step.

### Step 1: Download PDF with paper-fetch

```bash
cd /opt/data/skills/paper-fetch
python3 scripts/fetch.py "<DOI_OR_TITLE>" --out /tmp/papers
```

If they gave a title (not a DOI), use `--title`:
```bash
python3 scripts/fetch.py --title "Model Merging on Loss Landscape" --out /tmp/papers
```

The JSON output has `data.results[0].file` (relative PDF path) and `data.results[0].meta` (title, year, author).

**Error handling**: If `ok` is `false`, tell the user which sources were tried and suggest the paper might be paywalled.

### Step 2: Upload to Google Drive

```bash
rclone copy /tmp/papers/<filename> gdrive:
```

`gdrive:` is pre-configured. The file goes into the scoped papers folder.

### Step 3: Get shareable link

```bash
rclone link gdrive:<filename>
```

Returns `https://drive.google.com/open?id=...`. This is the URL that Zotero will link to.

### Step 4: Create Zotero metadata entry

```bash
zot add --doi "<DOI>"
```

For arXiv papers, construct the canonical DOI: `10.48550/arXiv.<id>` (e.g., `10.48550/arXiv.1706.03762`).

Returns `data.key` — the Zotero item key. If the paper is already in the library (search first with `zot search`), use the existing key and skip this step.

### Step 5: Link Google Drive PDF to Zotero

```bash
/opt/hermes/scripts/zot-link-gdrive.py <ZOTERO_KEY> "<GDRIVE_URL>" "Paper Title (PDF)"
```

This creates a `linked_url` attachment — the PDF shows as a clickable link in Zotero, opening directly from Google Drive. Does NOT use Zotero cloud storage.

**Dry-run first** (optional):
```bash
/opt/hermes/scripts/zot-link-gdrive.py --dry-run <ZOTERO_KEY> "<GDRIVE_URL>"
```

### Step 6: Cleanup

```bash
rm /tmp/papers/<filename>
```

## Complete Example

```
User: "下载 Attention Is All You Need 并加到 Zotero"

Step 1:
  cd /opt/data/skills/paper-fetch
  python3 scripts/fetch.py "10.48550/arXiv.1706.03762" --out /tmp/papers
  → FILE: /tmp/papers/Vaswani_2017_Attention_Is_All_You_Need.pdf

Step 2:
  rclone copy /tmp/papers/Vaswani_2017_Attention_Is_All_You_Need.pdf gdrive:

Step 3:
  rclone link gdrive:Vaswani_2017_Attention_Is_All_You_Need.pdf
  → https://drive.google.com/open?id=abc123

Step 4:
  zot add --doi "10.48550/arXiv.1706.03762"
  → KEY: ABC789

Step 5:
  /opt/hermes/scripts/zot-link-gdrive.py ABC789 "https://drive.google.com/open?id=abc123" "Attention Is All You Need"

Step 6:
  rm /tmp/papers/Vaswani_2017_Attention_Is_All_You_Need.pdf

Report to user:
  ✅ 已添加: Attention Is All You Need
  📄 PDF: https://drive.google.com/open?id=abc123
  📚 Zotero: ABC789
```

## Edge Cases

- **Already in library**: Run `zot search "<DOI>"` first. If found, use existing key, only run Steps 1-3 (download + upload) and Step 5 (link).
- **Download fails**: Tell user which sources were tried. Suggest ILL or institutional access. Do NOT proceed to Zotero steps.
- **arXiv paper**: Use `10.48550/arXiv.<id>` as the DOI. Metadata resolution may show `no_match` — that's normal for arXiv, the basic fields (title from paper-fetch) are sufficient.
- **Title-only request**: Use `paper-fetch --title "..."` to resolve to DOI first, then proceed.
