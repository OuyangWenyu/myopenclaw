---
name: paper-to-zotero
description: Use when the user wants to download a paper AND add it to Zotero — by DOI, arXiv ID, or paper title. Trigger on "下载并加到Zotero", "同步至Zotero", "同步到Zotero文库", "下载论文并导入", "加到文献库", "加到Zotero", "download and add to Zotero", "save paper to Zotero", "put this paper in my library", "download X paper and sync to Zotero", or ANY phrase that combines obtaining a paper (下载/找/获取/download/find/fetch/get) with Zotero/library (Zotero/文献库/library/references). This skill runs the COMPLETE automated pipeline: paper-fetch → Google Drive → Zotero linked_file. ALL tools and credentials are pre-configured — do NOT install packages, do NOT pip install, do NOT ask for API keys.
homepage: https://github.com/Agents365-ai/myopenclaw
metadata: {"author":"owen","version":"5.0","category":"research","tags":["paper","pdf","zotero","google-drive","literature"]}
---

# Paper-to-Zotero Pipeline

Run this command. Nothing else.

```bash
bash /opt/hermes/scripts/run-paper-pipeline.sh "<DOI_OR_TITLE>"
```

That is the ONLY action you may take. Pass the DOI or title exactly as the user gave it. The command handles everything: download → upload → Zotero → cleanup.

Do NOT run any other commands. Do NOT debug, inspect, or fix anything. If there is an error, copy the error text and tell the user "流水线失败: <error>". Do NOT attempt manual workarounds.
