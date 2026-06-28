---
name: paper-to-zotero
description: Use when the user wants to download a paper AND add it to Zotero — by DOI, arXiv ID, or paper title. Trigger on "下载并加到Zotero", "同步至Zotero", "同步到Zotero文库", "下载论文并导入", "加到文献库", "加到Zotero", "download and add to Zotero", "save paper to Zotero", "put this paper in my library", "download X paper and sync to Zotero", or ANY phrase that combines obtaining a paper (下载/找/获取/download/find/fetch/get) with Zotero/library (Zotero/文献库/library/references).
homepage: https://github.com/Agents365-ai/myopenclaw
metadata: {"author":"owen","version":"5.1","category":"research","tags":["paper","pdf","zotero","google-drive","literature"]}
---

# Paper-to-Zotero Pipeline

Run this command. Nothing else.

```bash
bash /opt/hermes/scripts/run-paper-pipeline.sh "<DOI_OR_TITLE>"
```

---
IMPORTANT: After the pipeline completes, ALWAYS verify the result by checking the paper title and DOI against what the user requested. The pipeline's `paper-fetch` tool sometimes resolves arXiv IDs / DOIs incorrectly via low-confidence Crossref matches (score < 30). Known failure mode: `arXiv:2501.08086` → matched to `10.1017/cts.2017.287` (completely wrong paper).
---

## Post-Pipeline Verification

Check the pipeline output for these red flags:

1. **Title mismatch**: Printed title doesn't match the expected paper
2. **Low confidence warning**: `"low_confidence": true, "score_below_threshold"` in the JSON output
3. **Title resolved as a number**: e.g., title = `"2501"` (Crossref parsing the arXiv ID digits as a numeric title)
4. **DOI mismatch**: The final DOI doesn't match what you'd expect for the paper
5. **Pipeline returns a wrong paper with seemingly high confidence**: Crossref sometimes matches arXiv IDs to completely unrelated DOIs even without the low_confidence flag. Example: `arXiv:2603.05538` → `10.1016/j.ijrobp.2006.07.1017` (a radiotherapy paper from 2006). This occurs when Crossref interprets the arXiv ID as a DOI query and finds an unrelated high-confidence match. **Always check the printed paper title and DOI against what the user requested, even if the pipeline shows no warnings.**

If the pipeline output looks wrong, proceed with manual recovery (see below).

## Manual Fallback (when pipeline resolves incorrectly)

### Step 1: Get correct metadata from arXiv API

```python
# Python urllib since curl isn't available
import urllib.request, ssl, xml.etree.ElementTree as ET
ctx = ssl._create_unverified_context()
url = f"https://export.arxiv.org/api/query?id_list=2501.08086"
req = urllib.request.Request(url)
resp = urllib.request.urlopen(req, context=ctx, timeout=30)
data = resp.read().decode('utf-8')
# Parse entry to get title, authors, published date
ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
root = ET.fromstring(data)
entry = root.find("atom:entry", ns)
title = entry.find("atom:title", ns).text.strip()
authors = [a.find("atom:name", ns).text.strip() for a in entry.findall("atom:author", ns)]
published = entry.find("atom:published", ns).text[:10]
# Extract comment field to detect conference acceptance (e.g. "Accepted by KDD 2026")
comment_elem = entry.find("arxiv:comment", ns)
comment = comment_elem.text.strip() if comment_elem is not None else ""
is_accepted = "accepted" in comment.lower() or "accept" in comment.lower()
```

### Step 2: Download the correct PDF

```python
import urllib.request, ssl
ctx = ssl._create_unverified_context()
url = f'https://arxiv.org/pdf/{arxiv_id}'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, context=ctx, timeout=60)
data = resp.read()
with open(f'/tmp/{arxiv_id}_paper.pdf', 'wb') as f:
    f.write(data)
```

### Step 3: Upload to Google Drive

```bash
rclone copy /tmp/{arxiv_id}_paper.pdf gdrive:
```

### Step 4: Delete the wrong Zotero entry (if any)

Find the wrong Zotero key from pipeline output (e.g., `I66UKQWU`). Delete it:

```python
import urllib.request, ssl, tomllib
config = tomllib.load(open("/opt/data/.config/zot/config.toml", "rb"))
lib_id = config["zotero"]["library_id"]
api_key = config["zotero"]["api_key"]
ctx = ssl._create_unverified_context()

# First get the item version
url = f"https://api.zotero.org/users/{lib_id}/items/{WRONG_KEY}"
req = urllib.request.Request(url)
req.add_header('Zotero-API-Key', api_key)
req.add_header('Zotero-API-Version', '3')
resp = urllib.request.urlopen(req, context=ctx)
version = resp.headers.get('Last-Modified-Version', '0')

# Delete with If-Unmodified-Since-Version header (required!)
del_req = urllib.request.Request(url, method='DELETE')
del_req.add_header('Zotero-API-Key', api_key)
del_req.add_header('Zotero-API-Version', '3')
del_req.add_header('If-Unmodified-Since-Version', version)
del_resp = urllib.request.urlopen(del_req, context=ctx)
# Status 204 = success
```

### Step 5: Create Zotero item via REST API (fallback when pyzotero unavailable)

`pyzotero` is NOT installed in this environment (`ModuleNotFoundError: No module named 'pyzotero'`). Use Zotero REST API directly instead.

**`imported_url` linkMode behavior varies.** In some sessions it works (returns 201), in others it returns 400. Try `imported_url` first (simpler, no file path dependency); if it returns 400, fall back to `linked_file` mode with `path: "attachments:filename.pdf"` (requires Zotero Linked Attachment Base Directory configured to point to the gdrive sync folder). Use `linked_url` as last resort if both fail.

Create the preprint item + attachment:

```python
import urllib.request, ssl, json, tomllib, shutil, subprocess, os

with open("/opt/data/.config/zot/config.toml", "rb") as f:
    config = tomllib.load(f)
lib_id = config["zotero"]["library_id"]
api_key = config["zotero"]["api_key"]
ctx = ssl._create_unverified_context()

arxiv_id = "XXXX.XXXXX"
pdf_path = f"/tmp/{arxiv_id}_paper.pdf"
pdf_filename = f"{arxiv_id}_paper.pdf"  # use a descriptive name

# Step A: Upload PDF to Google Drive first
subprocess.run(["rclone", "copy", pdf_path, f"gdrive:{pdf_filename}"], check=True)

# Step B: Create the preprint item
item_payload = [{
    "itemType": "preprint",
    "title": "PAPER TITLE",
    "creators": [
        {"firstName": "Author1F", "lastName": "Author1L", "creatorType": "author"},
    ],
    "abstractNote": "",
    "date": "2026-03-04",
    "DOI": f"10.48550/arXiv.{arxiv_id}",
    "url": f"https://arxiv.org/abs/{arxiv_id}",
    "extra": f"arXiv:{arxiv_id}",
    "archive": "arXiv",
    "archiveID": arxiv_id,
    "libraryCatalog": "arXiv",
    "tags": [],
    "collections": [],
    "relations": {}
}]

req = urllib.request.Request(
    f"https://api.zotero.org/users/{lib_id}/items",
    data=json.dumps(item_payload).encode('utf-8'),
    method='POST',
    headers={
        'Zotero-API-Key': api_key,
        'Zotero-API-Version': '3',
        'Content-Type': 'application/json'
    }
)
resp = urllib.request.urlopen(req, context=ctx)
result = json.loads(resp.read())
success = result.get('successful', {})
item_key = list(success.values())[0]['data']['key']
print(f"Zotero item created! Key: {item_key}")

# Step C: Create attachment using linked_file mode
# Zotero API cannot do multipart upload in this env.
# Use linked_file pointing to a path relative to Zotero's Linked Attachment Base Directory.
attach_payload = [{
    "itemType": "attachment",
    "contentType": "application/pdf",
    "filename": pdf_filename,
    "title": pdf_filename,
    "parentItem": item_key,
    "linkMode": "linked_file",
    "path": f"attachments:{pdf_filename}",  # Zotero resolves this relative to Linked Attachment Base Directory
    "accessDate": "",
    "tags": [],
    "relations": {}
}]

req = urllib.request.Request(
    f"https://api.zotero.org/users/{lib_id}/items",
    data=json.dumps(attach_payload).encode('utf-8'),
    method='POST',
    headers={
        'Zotero-API-Key': api_key,
        'Zotero-API-Version': '3',
        'Content-Type': 'application/json'
    }
)
resp = urllib.request.urlopen(req, context=ctx)
result = json.loads(resp.read())
attach_key = list(result.get('successful', {}).values())[0]['data']['key']
print(f"PDF attachment created! Key: {attach_key} (linked_file → attachments:{pdf_filename})")
```

### Step 6: Verify the entry

```python
req = urllib.request.Request(
    f"https://api.zotero.org/users/{lib_id}/items/{item_key}",
    headers={'Zotero-API-Key': api_key, 'Zotero-API-Version': '3'}
)
resp = urllib.request.urlopen(req, context=ctx)
item = json.loads(resp.read())
d = item['data']
print(f"Title: {d['title']}")
print(f"Type: {d.get('itemType', '?')}")

# Check attachment
req2 = urllib.request.Request(
    f"https://api.zotero.org/users/{lib_id}/items/{item_key}/children",
    headers={'Zotero-API-Key': api_key, 'Zotero-API-Version': '3'}
)
resp2 = urllib.request.urlopen(req2, context=ctx)
children = json.loads(resp2.read())
if children:
    c = children[0]['data']
    print(f"Attachment: {c.get('title', '?')} ({c.get('linkMode', '?')}) → {c.get('url', 'N/A')}")
```

### Step 7: Clean up

```bash
rm -f /tmp/{arxiv_id}_paper.pdf
```

## Post-Creation: Updating Zotero Item Type & Fields

When a paper is accepted at a conference (e.g., KDD 2026), update the Zotero entry from `preprint` to `conferencePaper`.

**CRITICAL: Zotero `conferencePaper` fields differ from `preprint`:**
- ❌ `publicationTitle` — NOT valid for conferencePaper
- ✅ `proceedingsTitle` — the correct field for proceedings name
- ✅ `conferenceName` — e.g., "KDD 2026"
- ✅ `place` — conference location
- ✅ `publisher` — valid for conferencePaper

Example update script:

```python
import urllib.request, ssl, json, tomllib

with open('/opt/data/.config/zot/config.toml', 'rb') as f:
    config = tomllib.load(f)
lib_id = config['zotero']['library_id']
api_key = config['zotero']['api_key']

ctx = ssl._create_unverified_context()
key = 'NTDSXSJQ'  # your zotero key

url = f'https://api.zotero.org/users/{lib_id}/items/{key}'
req = urllib.request.Request(url)
req.add_header('Zotero-API-Key', api_key)
req.add_header('Zotero-API-Version', '3')
resp = urllib.request.urlopen(req, context=ctx)
version = resp.headers.get('Last-Modified-Version', '0')
item = json.loads(resp.read())

d = item['data']
update_payload = {
    'itemType': 'conferencePaper',
    'title': d['title'],
    'creators': d['creators'],
    'abstractNote': d.get('abstractNote', ''),
    'proceedingsTitle': 'Proceedings of the 32nd ACM SIGKDD Conference on Knowledge Discovery and Data Mining (KDD 2026)',
    'conferenceName': 'KDD 2026',
    'place': '',
    'date': '2026',
    'publisher': 'Association for Computing Machinery',
    'volume': '',
    'pages': '',
    'series': '',
    'seriesNumber': '',
    'edition': '',
    'ISBN': '',
    'DOI': d.get('DOI', ''),
    'url': d.get('url', ''),
    'language': d.get('language', 'en'),
    'shortTitle': '',
    'extra': 'Accepted by KDD 2026',
    'archive': '',
    'archiveLocation': '',
    'archiveID': '',
    'libraryCatalog': d.get('libraryCatalog', ''),
    'callNumber': '',
    'rights': '',
    'accessDate': '',
    'tags': [],
    'collections': [],
    'relations': {}
}
item['data'] = update_payload

update_url = f'https://api.zotero.org/users/{lib_id}/items/{key}'
update_data = json.dumps(item).encode('utf-8')
update_req = urllib.request.Request(update_url, data=update_data, method='PUT')
update_req.add_header('Zotero-API-Key', api_key)
update_req.add_header('Zotero-API-Version', '3')
update_req.add_header('Content-Type', 'application/json')
update_req.add_header('If-Unmodified-Since-Version', version)
update_resp = urllib.request.urlopen(update_req, context=ctx)
```

## Slow/Unstable Downloads (Copernicus / Publisher PDFs)

Some publisher servers (especially Copernicus) have very slow transfer speeds and frequent timeouts. Strategy:

1. **Try the pipeline first** — for most DOIs it works. If it returns `not_found` (not wrong match), proceed manually.

2. **Scrape the HTML page** to find the PDF link (not all PDF URLs follow a simple pattern):
   ```python
   import urllib.request, ssl, re
   ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
   ctx.check_hostname = False
   ctx.verify_mode = ssl.CERT_NONE
   url = "https://hess.copernicus.org/articles/30/3853/2026/"
   req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
   resp = urllib.request.urlopen(req, context=ctx, timeout=30)
   html = resp.read().decode('utf-8')
   pdf_links = re.findall(r'href=["\']([^"\']*\.pdf[^"\']*?)["\']', html)
   ```

3. **Use wget with resume (`-c` flag)** for slow downloads — Copernicus often drops connections mid-download. Do this as a **background process** (it may take minutes):
   ```bash
   wget --no-check-certificate -c -t 5 -T 120 -O /tmp/paper.pdf "https://..." 2>&1
   ```

4. **Monitor progress by polling file size**:
   ```bash
   ls -la /tmp/paper.pdf  # check how much has downloaded
   ```

5. **If wget also fails with `Connection timed out`**, use background mode + poll:
   ```bash
   terminal(background=true, command="wget --no-check-certificate -c -O /tmp/paper.pdf URL")
   # Then poll every 30-60s:
   terminal("sleep 60 && ls -la /tmp/paper.pdf")
   ```

6. **Verify PDF integrity** — check the header and trailer:
   ```python
   d = open('/tmp/paper.pdf','rb').read()
   assert d[:4] == b'%PDF', "Not a PDF"
   assert d[-5:] == b'%%EOF', "Incomplete PDF (truncated)"
   ```

7. **Get metadata from Crossref API** (faster than scraping):
   ```python
   import urllib.request, ssl, json
   ctx = ssl._create_unverified_context()
   url = f'https://api.crossref.org/works/{doi}'
   req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
   resp = urllib.request.urlopen(req, context=ctx, timeout=15)
   msg = json.loads(resp.read())['message']
   title = msg['title'][0]
   authors = msg.get('author', [])
   ```

## Known Issues & Pitfalls

| Issue | Symptom | Fix |
|-------|---------|-----|
| Crossref low-confidence match | title = "2501" or wrong title | Manual fallback (above) |
| Crossref matches arXiv ID digits to wrong DOI | e.g., `2606.21189` → `10.1351/goldbook.21189` | Always check `low_confidence: true` flag |
| Pipeline returns `not_found` | paper-fetch can't find the PDF | Fall through to manual: scrape publisher HTML for PDF link, download via wget with resume |
| Copernicus download extremely slow / drops connection | wget gets to 80-90% then "Read error: Connection timed out" | Use `wget -c` (resume) in background, check progress by polling file size every 30-60s |
| Conference paper update fails 400 | `'publicationTitle' is not a valid field for type 'conferencePaper'` | Use `proceedingsTitle` instead of `publicationTitle` for conferencePaper |
| rclone timeout on `ls` | Command hangs | Use `timeout 10 rclone lsl gdrive:` or just `rclone copy` directly |
| curl not installed | "command not found" | Use Python urllib instead |
| `paper-to-zotero.py` fails with `ModuleNotFoundError: No module named 'pyzotero'` | `pyzotero` not installed | Use Zotero REST API directly (see Step 5) |
| Zotero attachment `imported_url` returns 400 | "imported_url is not a valid linkMode" | Use `linked_file` linkMode with `path: "attachments:filename.pdf"` instead. First upload PDF to Google Drive, configure Zotero's Linked Attachment Base Directory to point to the gdrive sync folder. |
| Python SSL `UNEXPECTED_EOF_WHILE_READING` | urllib fails to connect to some servers | Use `wget --no-check-certificate` instead of Python urllib; or use `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` with `check_hostname=False, verify_mode=CERT_NONE` |