---
name: zotero-split-scans
description: Split a batch-scanned archival PDF in your Zotero library into document-level items. Use whenever you ask to "split" a Zotero folder/batch scan item (e.g. an item titled "[Folder N, Box X]"), break a multi-document archive scan into separate items, or process a new batch scan from the archive. The batch PDFs are assumed to come from the vFlat iPhone app — one OCR'ed PDF per physical archive folder, with multiple documents inside.
allowed-tools: Bash(python3:*) Read
---

# Zotero Split Scans

Splits one "whole folder" batch scan item into per-document Zotero items, with metadata copied from the batch item and visual (not OCR-trusting) document identification.

> **Conventions are adaptable defaults.** The choices below — the `for review` tag, the `DONE ` title marker, the vFlat-style "one embedded photo per page" assumption, the letter/manuscript/article item-type mapping, and the "deepest collection only" rule — reflect one archival workflow. They are sensible defaults, not requirements; adjust the prose here and the constants in `scripts/zsplit.py` (e.g. `DONE_PREFIX`) to match your own library.

## Hard rules

- **Never write under `~/Zotero/`** (storage, settings, database). Local storage is read-only; every write goes through the Zotero web API.
- **Never delete the batch item, and leave its content alone.** The single allowed edit: on a clean `execute` run, the script prepends `DONE ` to its title (e.g. `[Folder 42, Box 1]` → `DONE [Folder 42, Box 1]`) so processed folders stand out in the library. Its PDF, tags, collections, and other metadata are never touched.
- All new items get the **`for review`** tag.
- New items go into the batch item's **deepest collection only** (the folder-level subcollection, e.g. "folder 42, box 1.2.1") — not parent collections, not anywhere else, unless you say otherwise.

## Workflow

### 1. Locate

```bash
python3 ~/.claude/skills/zotero-split-scans/scripts/zsplit.py locate <ITEMKEY or "title query">
```

Resolves the batch item, copies its PDF out of local Zotero storage (`~/Zotero/storage/<attKey>/`, API download as fallback), extracts each page's embedded scan photo to the workdir `/tmp/zotero-split/<batchKey>/`, and writes `batch.json` (metadata + per-page OCR previews) and `manifest.template.json`.

vFlat pages each contain exactly one embedded JPEG (the camera photo) under an invisible OCR text layer — the script extracts the original photo; it only rasterizes as a fallback.

### 2. Inspect visually

**Read every page image** (`page-001.jpeg`, …) with the Read tool. Do not segment documents from the OCR previews alone — your visual recognition is more accurate. Decide:

- Document boundaries (which pages belong together — letterheads, signatures, page numbers, paper/handwriting changes are the cues)
- Item type per document:
  - `letter` — letters **and memoranda**
  - `newspaperArticle` / `magazineArticle` — press clippings
  - `manuscript` — everything else (default)
- Title, creators, date as visible on the document
- Whether any page is **handwritten** → produce a transcription from the image (do not rely on OCR)

### 3. Write the manifest

Write `manifest.json` in the workdir (start from `manifest.template.json`):

```json
{
  "batch_key": "XXXXXXXX",
  "collections": ["<deepest collection key only>"],
  "documents": [
    {
      "pages": [1, 2],
      "itemType": "letter",
      "title": "",
      "creators": [
        {"creatorType": "author", "firstName": "Clay", "lastName": "Myers"},
        {"creatorType": "recipient", "firstName": "…", "lastName": "…"}
      ],
      "date": "1983-07-25",
      "transcription": null
    }
  ]
}
```

Metadata rules:

- Every item needs a `title` — except letters, which may instead have at least an `author` creator. **Never invent a title starting with `[Letter`** (that pattern is Zotero's auto-generated display title; leave letter titles empty instead).
- `date` as written on the document (ISO if a full date is known).
- `transcription`: plain text for handwritten documents; becomes a child note. `null` otherwise.
- Optional per-document fields passed through when the item type supports them: `letterType`, `publicationTitle`, `place`, `abstractNote`, `manuscriptType`, `numPages`, `section`. For an article's printed page range use `bibPages` (a string like `"42-48"`) — **not** `pages`, which is reserved for selecting the scan pages of the document.
- Tags, Archive, and Loc. in Archive are copied from the batch item automatically — don't put them in the manifest.

### 4. Execute

```bash
python3 ~/.claude/skills/zotero-split-scans/scripts/zsplit.py execute /tmp/zotero-split/<batchKey>/manifest.json
```

Splits the PDF with pypdf (preserves the OCR text layer), creates the items, uploads each split PDF, adds transcription notes, then runs a verification pass listing each new item's children. Warns if any batch pages were left unassigned.

When every document is created successfully, it finally prepends `DONE ` to the batch item's title — your at-a-glance signal in the library for which folders are processed. This is idempotent (a re-run won't stack `DONE DONE`), and a run where any item failed to create leaves the title unmarked so `DONE` always means "fully split". Unassigned pages only warn; they don't block the marker.

### 5. Report

Tell the user: items created (keys + labels + web links from `results.json`), any attachment/note failures, any unassigned pages, whether the batch item was marked `DONE` (and if not, why — e.g. a creation failure left it unmarked), and a reminder that everything is tagged `for review`.

## Gotchas

- `pyzotero.attachment_simple` returns **three** buckets: `success`, `failure`, and `unchanged`. An already-uploaded identical file lands in `unchanged` — that is success, not failure.
- Credentials live in `~/.zotero_env` (`ZOTERO_LIBRARY_ID` / `ZOTERO_LIBRARY_TYPE` / `ZOTERO_API_KEY`). See `.zotero_env.example` in the repo.
- Full-library `q=` searches sometimes time out; prefer passing the 8-char item key.
- If the batch item was just scanned, the local client may not have synced yet — if the item isn't found via the API, ask the user to sync Zotero first.
