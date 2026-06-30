# zotero-split-scans

A [Claude Code](https://docs.claude.com/en/docs/claude-code) **skill** that turns one batch-scanned archival PDF in your Zotero library into a clean set of per-document Zotero items — each with its own metadata, its own split PDF, and (for handwritten pages) a transcription note.

It's built for a specific but common archive-research workflow: you sit in a reading room and scan a whole physical folder with a phone app, ending up with a single OCR'd PDF that actually contains a dozen different letters, memos, and clippings. This skill helps Claude pull that pile apart into properly catalogued items, using **visual** document recognition rather than trusting messy OCR.

---

## What it does

The skill runs in two scripted phases with a human-guided step in the middle. Claude drives all of it; you just confirm boundaries and metadata.

```
locate  ─►  inspect (you + Claude look at the pages)  ─►  write manifest  ─►  execute
```

1. **Locate** — Given an item key or a title query, it finds the batch item in your library, copies its PDF *out* of local Zotero storage (read-only), and extracts each page's embedded scan photo into a working directory along with an OCR preview of every page.
2. **Inspect** — Claude reads the actual page images (not just the OCR text) to decide where one document ends and the next begins, what each one is (letter, memo, news clipping, manuscript…), and its title/author/date. Visual recognition is far more reliable than archival OCR.
3. **Write the manifest** — Claude writes a `manifest.json` describing each target document: which scan pages it spans, its item type, creators, date, and an optional transcription for handwritten material.
4. **Execute** — The script splits the source PDF per the manifest (preserving the OCR text layer), creates each Zotero item via the Web API, uploads its split PDF as an attachment, attaches transcription notes, and runs a verification pass. On a fully clean run it prepends `DONE ` to the batch item's title so finished folders stand out at a glance.

Every new item is tagged **`for review`** and filed into the batch item's deepest collection, with Archive and "Loc. in Archive" fields copied down from the batch item automatically.

## How it handles Zotero metadata

Each new item is assembled from **Zotero's own item template** for its type — the script calls `item_template(itemType)` first, so the item only ever carries fields Zotero recognizes for that type, and the Web API won't reject it for stray fields. Three streams of metadata then flow into that template:

1. **Inherited from the batch item (automatic — never in the manifest).** The `archive` and `archiveLocation` ("Loc. in Archive") fields are copied straight down, and the batch item's tags are carried onto every child, with **`for review`** always appended. This is what keeps provenance consistent across an entire folder without you retyping it.
2. **Supplied per document (the manifest).** Claude fills in `itemType`, `title`, `creators`, and `date` for each document from what it *sees* on the page. Creators use Zotero's structured shape — `{"creatorType": "...", "firstName": "...", "lastName": "..."}` (or a single `"name"`) — so a letter can carry both an `author` and a `recipient`.
3. **Filing.** The item is placed only into the manifest's `collections` (the batch item's deepest subcollection), not its parents.

A few deliberate rules make the metadata clean rather than just present:

- **Item-type mapping.** `letter` covers letters *and* memoranda; `newspaperArticle` / `magazineArticle` are for press clippings; `manuscript` is the catch-all default for everything else.
- **Titles vs. letters.** Every item needs a `title` — *except* letters, which may stand on an `author` creator alone. The skill deliberately **never fabricates a title beginning `[Letter…`**, because that bracketed string is Zotero's *auto-generated display title*; it leaves the field empty and lets Zotero render it, so the catalogue stays free of fake titles.
- **Optional type-aware fields.** `letterType`, `publicationTitle`, `place`, `abstractNote`, `manuscriptType`, `numPages`, and `section` are passed through only when you provide them *and* the chosen item type actually has that field — so you never get an `abstractNote` jammed onto a type that has no such slot.
- **The two meanings of "pages."** In the manifest, `pages` is a *list of scan-page numbers* selecting which images make up the document. But several article types also have a *bibliographic* `pages` field that Zotero expects to be a **string** page range (e.g. `"42-48"`). To keep these from colliding, the printed range is given under a separate `bibPages` key and written into Zotero's `pages`; the scan selector stays a list. Mixing them up would make the API reject the item.
- **Verification.** After creating everything, the script re-fetches each new item's children and prints them, so you can confirm the split PDF (and any note) actually attached rather than trusting the create call alone.

## How it handles handwriting

Archival OCR is shaky on cursive and annotations, so the skill **does not trust the embedded OCR layer for handwritten material** — it routes around it entirely:

- **Visual transcription, not OCR.** During the inspection step, Claude is told to read the *image* of any handwritten page and produce the transcription from what it sees, ignoring the OCR preview.
- **Carried in the manifest.** That text lives on the document's `transcription` field — plain text for handwritten documents, `null` for typed ones.
- **Stored as a child note.** On execution, a non-`null` transcription becomes a **Zotero child note** attached to the item: newlines are converted to `<br/>`, the body is wrapped as `<p><b>Transcription</b></p><p>…</p>`, and the note is tagged `for review` like everything else.

The net effect is a clean division of labor: typed documents keep their machine OCR layer (the split preserves it, so they stay full-text searchable in Zotero), while handwritten ones gain a human-grade transcription note that the OCR could never have produced. A handwritten letter therefore lands in your library as three linked pieces — the catalogued item, its split PDF attachment, and the transcription note — all flagged `for review`.

## Assumptions

This skill encodes a particular workflow. None of these are hard technical limits, but the defaults assume:

- **One PDF per physical folder, many documents inside.** The source items are "whole folder" batch scans, typically titled something like `[Folder 42, Box 1]`.
- **PDFs come from [vFlat](https://www.vflat.com/) (or a similar phone scanner).** Each page is expected to hold exactly one embedded JPEG (the camera photo) under an invisible OCR text layer. The script extracts that original photo; if a page doesn't fit the one-image pattern it falls back to rasterizing at 200 dpi.
- **Your Zotero desktop client is installed and synced.** The fast path reads the PDF straight from `~/Zotero/storage/`; if the local copy isn't there it falls back to downloading via the API. A just-scanned item that hasn't synced yet won't be found.
- **Visual inspection happens.** The whole point is that Claude *looks at the pages*. The OCR previews exist only to orient, not to segment from.
- **The conventions are yours to change.** The `for review` tag, the `DONE ` marker, the letter/manuscript/article item-type mapping, and the "deepest collection only" rule are sensible defaults for one archive's workflow — edit `SKILL.md` and the constants in `scripts/zsplit.py` to match your own.

## Safety guarantees

These are enforced in the script, not just documented:

- **Local Zotero storage is treated as read-only.** Nothing is ever written under `~/Zotero/`; the PDF is *copied out* to a temp workdir. Every write goes through the Zotero Web API.
- **The batch item is never deleted and its content is never altered.** The only edit ever made to it is prepending `DONE ` to its title — and only after every document was created successfully. That marker is idempotent (re-runs won't stack `DONE DONE `), and any creation failure leaves the title unmarked, so `DONE` always means "fully split."

## Prerequisites

| Requirement | Notes |
|---|---|
| **A Zotero account + Web API key** | Create a read/write key at <https://www.zotero.org/settings/security>. You also need your library ID (your numeric userID, shown on that same page, or a group ID). |
| **Zotero desktop client, synced** | For the fast local-storage read path. The skill still works via API download if a local file is missing, but expects the item to exist in the synced library. |
| **Python 3.9+** with the deps in [`requirements.txt`](requirements.txt) | `PyMuPDF` (image extraction), `pypdf` (splitting), `pyzotero` (Web API), `httpx`. Developed against Python 3.12. |
| **Claude Code** | The skill is written for Claude Code's skill system. The Python script itself is standalone and can be run by hand, but the workflow assumes Claude is driving and doing the visual inspection. |

## Setup in Claude

Claude Code auto-discovers skills placed in `~/.claude/skills/`. The skill must live in a folder whose name matches the skill (`zotero-split-scans`) and must contain `SKILL.md`.

**1. Install the skill files**

```bash
# Clone, then copy (or symlink) the skill into your Claude skills directory
git clone https://github.com/aaron-freedman/zotero-split-scans.git
mkdir -p ~/.claude/skills/zotero-split-scans
cp -R zotero-split-scans/SKILL.md zotero-split-scans/scripts ~/.claude/skills/zotero-split-scans/
```

> Prefer a symlink (`ln -s "$(pwd)/zotero-split-scans" ~/.claude/skills/zotero-split-scans`) if you want to keep pulling updates from the repo. Either way, Claude looks for `~/.claude/skills/zotero-split-scans/SKILL.md`.

**2. Install the Python dependencies**

```bash
pip install -r zotero-split-scans/requirements.txt
```

Use whatever Python environment `python3` resolves to in your shell — that's the interpreter the skill invokes.

**3. Provide your Zotero credentials**

The script reads three variables from `~/.zotero_env` at runtime. Copy the template and fill in your own values:

```bash
cp zotero-split-scans/.zotero_env.example ~/.zotero_env
# then edit ~/.zotero_env:
#   ZOTERO_LIBRARY_ID=1234567
#   ZOTERO_LIBRARY_TYPE=user      # or "group"
#   ZOTERO_API_KEY=...            # your read/write Web API key
```

No credentials are stored in the repo. `~/.zotero_env` lives in your home directory, outside the skill folder, and is `.gitignore`d here defensively.

**4. Use it**

In Claude Code, just ask in natural language — the skill's description triggers on requests like:

> "Split the batch scan titled `[Folder 42, Box 1]`."
> "Break item `ABCD1234` into separate documents."

Claude will run `locate`, show you the pages it found, propose document boundaries and metadata, write the manifest, and (after you're happy) run `execute` and report back the new item keys, links, and any warnings.

You can also run the script directly, without Claude:

```bash
python3 ~/.claude/skills/zotero-split-scans/scripts/zsplit.py locate "[Folder 42, Box 1]"
# ...inspect the page images, hand-write manifest.json...
python3 ~/.claude/skills/zotero-split-scans/scripts/zsplit.py execute /tmp/zotero-split/<batchKey>/manifest.json
```

## Repository layout

```
zotero-split-scans/
├── SKILL.md             # the skill definition Claude reads (workflow, rules, gotchas)
├── scripts/
│   └── zsplit.py        # the locate/execute implementation
├── requirements.txt     # Python dependencies
├── .zotero_env.example  # credentials template — copy to ~/.zotero_env
├── .gitignore
└── LICENSE              # MIT
```

## License

MIT — see [LICENSE](LICENSE).
