#!/opt/uv-tools/zotero-cli-cc/bin/python
"""Create a complete Zotero item with metadata and linked_file attachment.

Reads paper-fetch JSON output, enriches metadata from Crossref, and
creates a full Zotero entry via pyzotero with a linked_file attachment
pointing to the local Google Drive PDF.

Usage:
    paper-to-zotero <paper_fetch_json> <gdrive_local_path> [--dry-run]

Input: path to paper-fetch JSON output file
Output: Zotero item key on stdout
"""
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from pyzotero import zotero as zt

CROSSREF_UA = "zotero-bridge/1.0 (mailto:wenyuouyang@outlook.com)"
ARXIV_API = "http://export.arxiv.org/api/query"


def fetch_crossref(doi: str) -> dict | None:
    """Fetch rich metadata from Crossref. Returns None on failure."""
    url = f"https://api.crossref.org/works/{doi}"
    req = urllib.request.Request(url, headers={"User-Agent": CROSSREF_UA})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read()).get("message")
    except Exception:
        return None


def fetch_arxiv(arxiv_id: str) -> dict | None:
    """Fetch metadata from arXiv API. Returns None on failure."""
    url = f"{ARXIV_API}?id_list={arxiv_id}&max_results=1"
    req = urllib.request.Request(url, headers={"User-Agent": CROSSREF_UA})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "arxiv": "http://arxiv.org/schemas/atom"}
        root = ET.fromstring(resp.read())
        entry = root.find("atom:entry", ns)
        if entry is None:
            return None
        title = entry.find("atom:title", ns)
        summary = entry.find("atom:summary", ns)
        authors = entry.findall("atom:author", ns)
        published = entry.find("atom:published", ns)
        primary = entry.find("arxiv:primary_category", ns)
        published_doi_el = entry.find("arxiv:doi", ns)
        journal_ref_el = entry.find("arxiv:journal_ref", ns)
        return {
            "title": title.text.strip() if title is not None else "",
            "summary": summary.text.strip() if summary is not None else "",
            "authors": [a.find("atom:name", ns).text.strip()
                       for a in authors],
            "published": published.text.strip()[:10] if published is not None else "",
            "primary_category": primary.get("term") if primary is not None else "",
            "published_doi": published_doi_el.text.strip() if published_doi_el is not None else None,
            "journal_ref": journal_ref_el.text.strip() if journal_ref_el is not None else None,
        }
    except Exception:
        return None


S2_API = "https://api.semanticscholar.org/graph/v1/paper"


def fetch_published_doi_s2(arxiv_id: str) -> str | None:
    """Look up the published DOI for an arXiv paper via Semantic Scholar.

    S2 reliably links arXiv IDs to published DOIs (e.g. conference / journal
    versions), even when the arXiv API itself does not carry the <arxiv:doi>
    element.  Returns None on any failure — callers should fall back to
    preprint metadata.
    """
    url = f"{S2_API}/ArXiv:{arxiv_id}?fields=externalIds"
    req = urllib.request.Request(url, headers={"User-Agent": CROSSREF_UA})
    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            ext = json.loads(resp.read()).get("externalIds", {})
            return ext.get("DOI")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 * (attempt + 1))  # back off: 2, 4, 6 s
                continue
            return None
        except Exception:
            return None
    return None


def _pick_arxiv_id(doi: str) -> str | None:
    """Extract arXiv ID from 10.48550/arXiv.XXXX.YYYYY format."""
    if doi.startswith("10.48550/arXiv."):
        return doi.split("arXiv.")[-1]
    return None


def build_item(doi: str, pf_meta: dict) -> tuple[dict, dict]:
    """Build a Zotero item dict and extra dict from available metadata sources.

    Returns (item_data, extra_fields).
    """
    item = {}
    extra = {}

    arxiv_id = _pick_arxiv_id(doi)

    # Try Crossref first
    cr = fetch_crossref(doi)

    # For arXiv DOIs: attempt to upgrade metadata from preprint to
    # published version.  The arXiv API carries the formal published DOI
    # (<arxiv:doi>) when authors updated their submission.  Semantic
    # Scholar is more reliable but shares a rate-limit pool with
    # paper-fetch (which calls S2 first), so we only use arXiv here.
    # Use fetch_published_doi_s2() directly for one-off lookups where
    # S2 is known to be available.
    arxiv_data = None
    if arxiv_id and (not cr or cr.get("type") in ("posted-content", "preprint")):
        arxiv_data = fetch_arxiv(arxiv_id)
        published_doi = arxiv_data.get("published_doi") if arxiv_data else None
        if published_doi:
            published_cr = fetch_crossref(published_doi)
            if published_cr:
                cr = published_cr

    if cr:
        # Title
        titles = cr.get("title", [])
        item["title"] = titles[0] if titles else (pf_meta.get("title") or "")

        # Creators
        creators = []
        for a in cr.get("author", []):
            creators.append({
                "creatorType": "author",
                "firstName": a.get("given", ""),
                "lastName": a.get("family", ""),
            })
        if creators:
            item["creators"] = creators

        # Abstract
        abstract = cr.get("abstract")
        if abstract:
            item["abstractNote"] = abstract

        # Date
        date_parts = (cr.get("published-print") or cr.get("created") or
                      cr.get("issued") or cr.get("deposited"))
        if date_parts and "date-parts" in date_parts:
            dp = date_parts["date-parts"][0]
            if len(dp) == 3:
                item["date"] = f"{dp[0]:04d}-{dp[1]:02d}-{dp[2]:02d}"
            elif len(dp) >= 1:
                item["date"] = str(dp[0])

        # Item type — map CrossRef types to Zotero item types
        cr_type = cr.get("type", "")
        type_map = {
            "journal-article": "journalArticle",
            "proceedings-article": "conferencePaper",
            "book-chapter": "bookSection",
            "book": "book",
        }
        item["itemType"] = type_map.get(cr_type, "preprint")

        # Journal info → extra
        container = cr.get("container-title", [])
        if container:
            extra["publicationTitle"] = container[0]
        vol = cr.get("volume")
        if vol:
            extra["volume"] = str(vol)
        issue = cr.get("issue")
        if issue:
            extra["issue"] = str(issue)
        page = cr.get("page")
        if page:
            extra["pages"] = str(page)
        issn = cr.get("ISSN", [])
        if issn:
            extra["ISSN"] = issn[0] if isinstance(issn, list) else str(issn)
        publisher = cr.get("publisher")
        if publisher:
            extra["publisher"] = publisher

    else:
        # Crossref failed — try arXiv (may have been pre-fetched above)
        if arxiv_data is None:
            arxiv_data = fetch_arxiv(arxiv_id) if arxiv_id else None

        if arxiv_data:
            item["title"] = arxiv_data["title"]
            item["abstractNote"] = arxiv_data["summary"]
            item["date"] = arxiv_data["published"]
            creators = []
            for name in arxiv_data["authors"]:
                parts = name.rsplit(" ", 1)
                if len(parts) == 2:
                    creators.append({
                        "creatorType": "author",
                        "firstName": parts[0],
                        "lastName": parts[1],
                    })
                else:
                    creators.append({
                        "creatorType": "author",
                        "name": name,
                    })
            item["creators"] = creators
            item["itemType"] = "preprint"
            extra["repository"] = "arXiv"
            extra["archiveID"] = f"arXiv:{arxiv_id}"
            extra["libraryCatalog"] = "arXiv.org"
        else:
            # Fallback to paper-fetch metadata
            item["title"] = pf_meta.get("title") or ""
            item["date"] = str(pf_meta.get("year") or "")
            author = pf_meta.get("author")
            if author:
                parts = author.rsplit(" ", 1) if " " in author else [author, ""]
                item["creators"] = [{
                    "creatorType": "author",
                    "firstName": parts[0] if len(parts) > 1 else "",
                    "lastName": parts[-1] if len(parts) > 1 else author,
                }]
            item["itemType"] = "preprint"
            if arxiv_id:
                extra["repository"] = "arXiv"
                extra["archiveID"] = f"arXiv:{arxiv_id}"
                extra["libraryCatalog"] = "arXiv.org"

    # When metadata was upgraded from arXiv preprint → published version,
    # note the arXiv source so it is not lost.
    if arxiv_id and arxiv_data:
        for key, val in [
            ("repository", "arXiv"),
            ("archiveID", f"arXiv:{arxiv_id}"),
            ("libraryCatalog", "arXiv.org"),
        ]:
            if key not in extra:
                extra[key] = val

    # Common fields
    item["DOI"] = doi
    item["url"] = f"https://doi.org/{doi}"

    # Try to build citationKey from extra
    pub = extra.get("publicationTitle", "")
    if item.get("title") and pub:
        # Simple citation key: firstAuthorYear_JournalAbbrev
        first_author = ""
        if item.get("creators"):
            first_author = item["creators"][0].get("lastName",
                            item["creators"][0].get("name", ""))
        year = item.get("date", "")[:4]
        journal = "".join(w[0] for w in pub.split() if w[0].isalpha())[:10]
        if first_author and year:
            extra["citationKey"] = f"{first_author}{year}_{journal}"

    # Language
    item["language"] = "en"

    return item, extra


def main():
    dry_run = False
    metadata_only = False
    pdf_filename = None
    args = sys.argv[1:]

    if "--dry-run" in args:
        dry_run = True
        args.remove("--dry-run")
    if "--metadata-only" in args:
        metadata_only = True
        args.remove("--metadata-only")
    # --pdf-filename: attach a linked_file in metadata-only mode
    if "--pdf-filename" in args:
        idx = args.index("--pdf-filename")
        args.pop(idx)
        if idx < len(args):
            pdf_filename = args.pop(idx)

    if len(args) < 1:
        print("Usage: paper-to-zotero [--dry-run] [--metadata-only]"
              " [--pdf-filename <name>] <paper_fetch_json|DOI>",
              file=sys.stderr)
        sys.exit(2)

    input_arg = args[0]

    # ── Metadata-only mode: input is a DOI, no JSON file ─────────
    if metadata_only:
        doi = input_arg
        pf_meta = {}
        pf_filename = pdf_filename or ""
        title = doi
    else:
        # Read paper-fetch output
        pf_path = input_arg
        with open(pf_path) as f:
            pf = json.load(f)

        if not pf.get("ok"):
            print("Error: paper-fetch failed, cannot create Zotero entry",
                  file=sys.stderr)
            sys.exit(3)

        result = pf["data"]["results"][0]
        doi = result.get("doi", "")
        pf_meta = result.get("meta", {})
        title = pf_meta.get("title", result.get("doi", "Unknown"))

        pf_filename = os.path.basename(result.get("file", ""))
        if not pf_filename:
            print("   ℹ️  No PDF in paper-fetch output — creating metadata-only entry")

    # Use Zotero's attachments: scheme — resolves relative to each
    # computer's Linked Attachment Base Directory (set in Zotero prefs).
    # This makes linked_file paths portable across OSes and user accounts.
    attachment_path = f"attachments:{pf_filename}" if pf_filename else None

    # Read Zotero config
    config = tomllib.load(open("/opt/data/.config/zot/config.toml", "rb"))
    lib_id = config["zotero"]["library_id"]
    api_key = config["zotero"]["api_key"]

    if not lib_id or not api_key:
        print("Error: Zotero API credentials not configured", file=sys.stderr)
        sys.exit(6)

    # Build item data
    item_data, extra_fields = build_item(doi, pf_meta)

    if dry_run:
        print(f"[dry-run] Would create Zotero item:")
        print(f"  DOI: {doi}")
        print(f"  Type: {item_data.get('itemType', '?')}")
        print(f"  Title: {item_data.get('title', '')[:80]}")
        print(f"  Authors: {len(item_data.get('creators', []))}")
        print(f"  Date: {item_data.get('date', '')}")
        print(f"  Abstract: {'yes' if item_data.get('abstractNote') else 'no'}")
        print(f"  Extra: {list(extra_fields.keys())}")
        if attachment_path:
            print(f"  Attachment: linked_file → {attachment_path}")
        else:
            print(f"  Attachment: (none — metadata only)")
        sys.exit(0)

    # Create Zotero item
    z = zt.Zotero(lib_id, "user", api_key)

    # Build the item template
    tmpl = z.item_template(item_data.pop("itemType", "preprint"))
    tmpl.update(item_data)

    # Set extra fields as a formatted string
    extra_parts = []
    for k, v in extra_fields.items():
        extra_parts.append(f"{k}: {v}")
    if extra_parts:
        tmpl["extra"] = "\n".join(extra_parts)

    # Create the parent item
    resp = z.create_items([tmpl])
    created = resp.get("success", {})
    if not created:
        failed = resp.get("failed", {})
        err_msg = failed.get("0", {}).get("message", str(resp))
        print(f"Error creating Zotero item: {err_msg}", file=sys.stderr)
        sys.exit(7)

    parent_key = list(created.values())[0]

    # Create linked_file attachment (if PDF available)
    attach_key = None
    if attachment_path:
        attach_tmpl = z.item_template("attachment", "linked_file")
        attach_tmpl["title"] = pf_filename
        attach_tmpl["parentItem"] = parent_key
        attach_tmpl["path"] = attachment_path

        attach_resp = z.create_items([attach_tmpl])
        attach_created = attach_resp.get("success", {})
        if attach_created:
            attach_key = list(attach_created.values())[0]
        else:
            attach_key = "(failed)"

    result_out = {
        "ok": True,
        "zotero_key": parent_key,
        "title": title,
        "doi": doi,
    }
    if attach_key:
        result_out["attachment_key"] = attach_key
        result_out["pdf_path"] = attachment_path
    print(json.dumps(result_out))


if __name__ == "__main__":
    main()
