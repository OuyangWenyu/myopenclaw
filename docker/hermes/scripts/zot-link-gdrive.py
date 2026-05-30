#!/opt/uv-tools/zotero-cli-cc/bin/python
"""Create a linked_url attachment in Zotero pointing to a Google Drive file.

Reads Zotero credentials from /opt/data/.config/zot/config.toml
Uses the pyzotero library shipped with zotero-cli-cc in /opt/uv-tools/.

Usage:
    zot-link-gdrive <zotero_key> <gdrive_url> [title]
    zot-link-gdrive --dry-run <zotero_key> <gdrive_url> [title]
"""
import sys
import tomllib

from pyzotero import zotero as zt


def main():
    dry_run = False
    if len(sys.argv) > 1 and sys.argv[1] == "--dry-run":
        dry_run = True
        sys.argv.pop(1)

    if len(sys.argv) < 3:
        print("Usage: zot-link-gdrive [--dry-run] <zotero_key> <gdrive_url> [title]",
              file=sys.stderr)
        sys.exit(2)

    key = sys.argv[1]
    url = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else "PDF (Google Drive)"

    config = tomllib.load(open("/opt/data/.config/zot/config.toml", "rb"))
    lib_id = config["zotero"]["library_id"]
    api_key = config["zotero"]["api_key"]

    if not lib_id or not api_key:
        print("Error: Zotero API credentials not configured in config.toml",
              file=sys.stderr)
        sys.exit(3)

    if dry_run:
        print(f"[dry-run] Would link: {url} → Zotero item {key} (title: {title})")
        sys.exit(0)

    z = zt.Zotero(lib_id, "user", api_key)
    tmpl = z.item_template("attachment", "linked_url")
    tmpl["url"] = url
    tmpl["title"] = title
    tmpl["parentItem"] = key
    resp = z.create_items([tmpl])
    print(f"✅ Linked: {url} → Zotero item {key}")


if __name__ == "__main__":
    main()
