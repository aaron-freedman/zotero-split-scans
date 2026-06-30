#!/usr/bin/env python3
"""Split a batch-scanned Zotero PDF item into document-level items.

Subcommands:
  locate  <item key | title query>   Resolve batch item, copy its PDF from local
                                     Zotero storage (read-only), extract per-page
                                     images for visual inspection, write batch.json
                                     and a manifest.json template to the workdir.
  execute <workdir>/manifest.json    Split the PDF per manifest, create Zotero
                                     items via the web API, upload attachments,
                                     add transcription notes, verify.

Credentials come from ~/.zotero_env (ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE,
ZOTERO_API_KEY).

Never writes anything under ~/Zotero — local storage is read-only; all writes
go through the web API. Never deletes the batch item; the only edit it makes to
the batch item is prepending a "DONE " marker to its title once the split
finishes cleanly, so processed folders stand out in the library.
"""

import json
import re
import shutil
import sys
from pathlib import Path

import fitz  # PyMuPDF
import httpx
from pypdf import PdfReader, PdfWriter
from pyzotero import zotero

# pyzotero 1.7.6 uploads attachment files to S3 with a bare httpx.post(),
# inheriting httpx's 5s default write timeout — too tight for multi-MB scan
# PDFs — and its error handler references httpx.ConnectionError, which httpx
# removed (AttributeError instead of a clean retry). Patch both.
httpx.ConnectionError = httpx.ConnectError
_orig_httpx_post = httpx.post


def _patient_post(*args, **kwargs):
    kwargs.setdefault("timeout", httpx.Timeout(300.0, connect=30.0))
    return _orig_httpx_post(*args, **kwargs)


httpx.post = _patient_post

WORKROOT = Path("/tmp/zotero-split")
LOCAL_STORAGE = Path.home() / "Zotero" / "storage"
# Marker prepended to a batch item's title once its split finishes cleanly.
# Trailing space keeps it readable: "DONE [Folder 42, Box 1]".
DONE_PREFIX = "DONE "


def get_client():
    env = {}
    env_path = Path.home() / ".zotero_env"
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return zotero.Zotero(
        env["ZOTERO_LIBRARY_ID"], env["ZOTERO_LIBRARY_TYPE"], env["ZOTERO_API_KEY"]
    )


def resolve_batch_item(z, query):
    if re.fullmatch(r"[A-Z0-9]{8}", query):
        return z.item(query)
    matches = [
        it
        for it in z.items(q=query, itemType="-attachment || note", limit=25)
        if query.lower() in (it["data"].get("title") or "").lower()
    ]
    if not matches:
        sys.exit(f"No item matching {query!r}")
    if len(matches) > 1:
        for it in matches:
            print(it["key"], "|", it["data"].get("title"))
        sys.exit("Multiple matches — rerun with the item key.")
    return matches[0]


def find_pdf_attachment(z, item_key):
    for child in z.children(item_key):
        if child["data"].get("contentType") == "application/pdf":
            return child
    sys.exit(f"No PDF attachment under item {item_key}")


def extract_page_images(pdf_path, outdir):
    """Extract the embedded scan photo from each page (vFlat-style PDFs have
    exactly one image per page); fall back to rasterizing at 200 dpi."""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc, start=1):
        images = page.get_images(full=True)
        out = None
        if len(images) == 1:
            info = doc.extract_image(images[0][0])
            out = outdir / f"page-{i:03d}.{info['ext']}"
            out.write_bytes(info["image"])
        else:
            pix = page.get_pixmap(dpi=200)
            out = outdir / f"page-{i:03d}.png"
            pix.save(out)
        text = page.get_text().strip()
        pages.append(
            {
                "page": i,
                "image": str(out),
                "ocr_chars": len(text),
                "ocr_preview": text[:300],
            }
        )
    doc.close()
    return pages


def cmd_locate(query):
    z = get_client()
    item = resolve_batch_item(z, query)
    data = item["data"]
    att = find_pdf_attachment(z, item["key"])
    att_key, filename = att["key"], att["data"]["filename"]

    workdir = WORKROOT / item["key"]
    workdir.mkdir(parents=True, exist_ok=True)

    local_pdf = LOCAL_STORAGE / att_key / filename
    pdf_copy = workdir / "batch.pdf"
    if local_pdf.exists():
        shutil.copy(local_pdf, pdf_copy)  # copy OUT of storage; storage untouched
        source = str(local_pdf)
    else:
        z.dump(att_key, "batch.pdf", str(workdir))
        source = "web API (local copy not found)"

    pages = extract_page_images(pdf_copy, workdir)

    batch = {
        "batch_key": item["key"],
        "attachment_key": att_key,
        "pdf_source": source,
        "title": data.get("title"),
        "tags": [t["tag"] for t in data.get("tags", [])],
        "archive": data.get("archive", ""),
        "archiveLocation": data.get("archiveLocation", ""),
        "collections": data.get("collections", []),
        "pages": pages,
    }
    (workdir / "batch.json").write_text(json.dumps(batch, indent=2))

    template = {
        "batch_key": item["key"],
        "collections": data.get("collections", []),
        "documents": [
            {
                "pages": [1],
                "itemType": "letter|manuscript|newspaperArticle|magazineArticle",
                "title": "",
                "creators": [
                    {"creatorType": "author", "firstName": "", "lastName": ""}
                ],
                "date": "",
                "letterType": "",
                "transcription": None,
            }
        ],
    }
    (workdir / "manifest.template.json").write_text(json.dumps(template, indent=2))

    print(json.dumps(batch, indent=2))
    print(f"\nWorkdir: {workdir}")
    print("View each page image, then write manifest.json (see manifest.template.json).")


def slugify(s, fallback):
    s = re.sub(r"[^\w\s-]", "", s).strip()
    s = re.sub(r"[\s]+", " ", s)
    return s[:80] or fallback


def doc_label(doc):
    """Human-readable name for the split PDF file."""
    if doc.get("title"):
        return doc["title"]
    authors = [
        c.get("lastName") or c.get("name", "")
        for c in doc.get("creators", [])
        if c.get("creatorType") == "author"
    ]
    recipients = [
        c.get("lastName") or c.get("name", "")
        for c in doc.get("creators", [])
        if c.get("creatorType") == "recipient"
    ]
    label = "Letter"
    if authors:
        label += " from " + ", ".join(authors)
    if recipients:
        label += " to " + ", ".join(recipients)
    if doc.get("date"):
        label += f" ({doc['date']})"
    return label


def mark_batch_done(z, batch_key):
    """Prepend DONE_PREFIX to the batch item's title so finished folders are
    obvious in the library. The title is the ONLY field on the batch item we
    ever touch.

    Idempotent: if the title already starts with the marker we leave it alone,
    so a re-run (this command isn't otherwise idempotent) won't stack
    'DONE DONE '. We re-fetch the item live rather than trusting batch.json so
    we hand the API the current version — that's what update_item uses for the
    If-Unmodified-Since-Version check, avoiding a 412 conflict.

    Returns (title, changed) where `changed` is False when it was already DONE.
    """
    item = z.item(batch_key)
    title = item["data"].get("title") or ""
    if title.startswith(DONE_PREFIX):
        return title, False
    new_title = DONE_PREFIX + title
    item["data"]["title"] = new_title
    z.update_item(item)
    return new_title, True


def cmd_execute(manifest_path):
    manifest_path = Path(manifest_path)
    workdir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    batch = json.loads((workdir / "batch.json").read_text())

    z = get_client()
    reader = PdfReader(workdir / "batch.pdf")
    n_pages = len(reader.pages)

    # sanity: every manifest page must exist; warn on pages not assigned anywhere
    assigned = [p for d in manifest["documents"] for p in d["pages"]]
    bad = [p for p in assigned if not 1 <= p <= n_pages]
    if bad:
        sys.exit(f"Manifest references nonexistent pages: {bad}")
    missing = sorted(set(range(1, n_pages + 1)) - set(assigned))
    if missing:
        print(f"WARNING: pages not assigned to any document: {missing}")

    outdir = workdir / "output"
    outdir.mkdir(exist_ok=True)
    tags = [{"tag": t} for t in batch["tags"]] + [{"tag": "for review"}]

    results = []
    create_failures = 0
    for idx, doc in enumerate(manifest["documents"], start=1):
        item_type = doc["itemType"]
        tpl = z.item_template(item_type)
        tpl["title"] = doc.get("title", "")
        tpl["creators"] = doc.get("creators", [])
        tpl["date"] = doc.get("date", "")
        tpl["tags"] = tags
        tpl["archive"] = batch["archive"]
        tpl["archiveLocation"] = batch["archiveLocation"]
        tpl["collections"] = manifest["collections"]
        for field in ("letterType", "publicationTitle", "place", "abstractNote",
                      "manuscriptType", "numPages", "section"):
            if doc.get(field) and field in tpl:
                tpl[field] = doc[field]
        # NOTE: the manifest's `pages` selects which scan pages make up this
        # document. Several item types (magazine/newspaper/journalArticle) also
        # have a bibliographic `pages` field that must be a STRING (page range),
        # so it is set only from a separate `bibPages` key — never from the scan
        # selector, which is a list and would be rejected by the API.
        if doc.get("bibPages") and "pages" in tpl:
            tpl["pages"] = doc["bibPages"]

        resp = z.create_items([tpl])
        if not resp.get("success"):
            print(f"FAILED to create item {idx}: {resp}")
            create_failures += 1
            continue
        new_key = resp["success"]["0"]

        writer = PdfWriter()
        for p in doc["pages"]:
            writer.add_page(reader.pages[p - 1])
        label = doc_label(doc)
        pdf_out = outdir / f"{slugify(label, f'document-{idx}')}.pdf"
        with open(pdf_out, "wb") as f:
            writer.write(f)

        att_resp = z.attachment_simple([str(pdf_out)], parentid=new_key)
        # attachment_simple buckets: success / failure / unchanged — check all
        uploaded = bool(att_resp.get("success")) or bool(att_resp.get("unchanged"))
        if not uploaded:
            print(f"ATTACHMENT FAILED for {new_key}: {att_resp}")

        if doc.get("transcription"):
            note = z.item_template("note")
            body = doc["transcription"].replace("\n", "<br/>")
            note["note"] = f"<p><b>Transcription</b></p><p>{body}</p>"
            note["tags"] = [{"tag": "for review"}]
            nresp = z.create_items([note], parentid=new_key)
            if not nresp.get("success"):
                print(f"NOTE FAILED for {new_key}: {nresp}")

        results.append(
            {"key": new_key, "label": label, "pages": doc["pages"],
             "attachment_ok": uploaded,
             "url": f"https://www.zotero.org/users/{z.library_id}/items/{new_key}"}
        )
        print(f"[{idx}/{len(manifest['documents'])}] {new_key}  {label}  "
              f"pages {doc['pages']}  attachment={'ok' if uploaded else 'FAILED'}")

    # verification pass
    print("\n--- verification ---")
    for r in results:
        children = z.children(r["key"])
        kinds = [c["data"].get("contentType") or c["data"]["itemType"] for c in children]
        print(f"{r['key']}: children={kinds}")
    (workdir / "results.json").write_text(json.dumps(results, indent=2))

    # Mark the batch folder item DONE so processed folders stand out in the
    # library. Only when every document became an item — a DONE marker has to
    # mean "fully processed" to be worth anything. Unassigned pages only warn
    # (often blank separators), so they don't block DONE. Marking is cosmetic
    # and runs last: if it fails, the real work is already saved, so warn
    # rather than crash.
    batch_key = manifest["batch_key"]
    if create_failures:
        print(f"\n{create_failures} item(s) failed to create — leaving batch "
              f"item {batch_key} title unmarked. Fix and re-run to mark it DONE.")
    else:
        try:
            new_title, changed = mark_batch_done(z, batch_key)
            state = "Marked" if changed else "Already marked"
            print(f"\n{state} batch item {batch_key} DONE: {new_title!r}")
        except Exception as e:
            print(f"\nWARNING: items created OK, but failed to mark batch item "
                  f"{batch_key} DONE ({e}). Prepend 'DONE ' to its title by hand.")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("locate", "execute"):
        sys.exit(__doc__)
    if sys.argv[1] == "locate":
        cmd_locate(sys.argv[2])
    else:
        cmd_execute(sys.argv[2])
