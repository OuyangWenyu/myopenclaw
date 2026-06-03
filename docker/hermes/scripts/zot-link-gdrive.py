#!/opt/uv-tools/zotero-cli-cc/bin/python
"""Create a linked_file attachment in Zotero pointing to a Google Drive file.

Reads Zotero credentials from /opt/data/.config/zot/config.toml
Uses the pyzotero library shipped with zotero-cli-cc in /opt/uv-tools/.

Usage:
    zot-link-gdrive <zotero_key> <filename> [--dry-run]

Auto-constructs the full local path from $GDRIVE_PAPERS_LOCAL_PATH + filename.
"""
import os
import sys
import tomllib

from pyzotero import zotero as zt


def main():
    dry_run = False
    if len(sys.argv) > 1 and sys.argv[1] == "--dry-run":
        dry_run = True
        sys.argv.pop(1)

    if len(sys.argv) < 3:
        print("Usage: zot-link-gdrive [--dry-run] <zotero_key> <filename>",
              file=sys.stderr)
        sys.exit(2)

    key = sys.argv[1]
    filename = sys.argv[2]

    # Use Zotero's attachments: scheme — resolves relative to each
    # computer's Linked Attachment Base Directory (set in Zotero prefs).
    attachment_path = f"attachments:{filename}"

    config = tomllib.load(open("/opt/data/.config/zot/config.toml", "rb"))
    lib_id = config["zotero"]["library_id"]
    api_key = config["zotero"]["api_key"]

    if not lib_id or not api_key:
        print("Error: Zotero API credentials not configured in config.toml",
              file=sys.stderr)
        sys.exit(3)

    if dry_run:
        print(f"[dry-run] Would link: {attachment_path} → Zotero item {key} (filename: {filename})")
        sys.exit(0)

    z = zt.Zotero(lib_id, "user", api_key)
    tmpl = z.item_template("attachment", "linked_file")
    tmpl["path"] = attachment_path
    tmpl["title"] = filename
    tmpl["parentItem"] = key
    resp = z.create_items([tmpl])
    created = resp.get("success", {})
    if created:
        attach_key = list(created.values())[0]
        print(f"✅ Linked: {attachment_path} → Zotero item {key} (attachment: {attach_key})")
    else:
        failed = resp.get("failed", {})
        err_msg = failed.get("0", {}).get("message", str(resp))
        print(f"Error: {err_msg}", file=sys.stderr)
        sys.exit(5)


if __name__ == "__main__":
    main()
