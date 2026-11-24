# PDF Toolkit

Fully offline PDF toolkit with 22 professional tools — compress, merge, split, convert, protect, redact, watermark, and more. DPI-aware image recompression, AES-256 encryption, and PDF/A compliance. No cloud services, no accounts, no tracking.

**v4.23**

---

## Install

```bash
pip install -r requirements.txt
```

Or manually (core):

```bash
pip install pikepdf pillow PySide6 "PyMuPDF==1.27.2.3" argon2-cffi pycryptodome cryptography
```

**The PDF library is pinned to PyMuPDF 1.27.2.3.** Redaction is tested on
that version and supported on it alone. PyMuPDF 1.28 removes other text from
a page when it redacts — in some files, thousands of words that had nothing
to do with what was redacted — so Redact refuses to run on it. On 2.x Redact
runs, but the text that follows a removed word on its line can move along
it, and the result says so. The test suite also runs on 2.1 as an early
warning.

**Optional dependencies:**
- [Ghostscript](https://www.ghostscript.com/releases/gsdnld.html) — font subsetting. The app looks for it only where its installer puts it: on Windows `Program Files\gs\gs<version>\bin\gswin64c.exe` (or `Program Files (x86)\gs\...\gswin32c.exe`), newest version first; on macOS `/opt/homebrew/bin/gs`, `/usr/local/bin/gs` or `/opt/local/bin/gs`; on Linux `/usr/bin/gs` or `/usr/local/bin/gs`. Installed elsewhere, set `PDFTOOLKIT_GHOSTSCRIPT` to the full path of the program. It never runs one found on PATH or in the current folder
- **Offline translation** (Translate tool) — set up on demand: open the Translate tool, pick your languages, and it downloads what they need, once. In the packaged app that starts with the translation engine itself: about 48 MB (19 pinned wheels, each checked against `translate_runtime_lock.json`), about 150 MB once unpacked. Each language then adds two Argos language packs, English to it and back (about 160 MB each once installed, for German), and a small model that splits its sentences (about 0.2 MB; 0.6 MB for Chinese), pinned with its SHA-256 in `translate_sbd_lock.json`. English's model comes with the first language. After setup, translation makes no network connection at all: if a sentence model is missing, it stops and says "Run translation setup again" instead of fetching one. A setup made before the sentence models were needed shows the setup card again until it has been run once more. Reading text from images and scans also needs the [Tesseract](https://github.com/tesseract-ocr/tesseract) program, which the app looks for only where its installer puts it: on Windows `Program Files\Tesseract-OCR\tesseract.exe` (or under `Program Files (x86)`), on macOS `/opt/homebrew/bin`, `/usr/local/bin` or `/opt/local/bin`, on Linux `/usr/bin` or `/usr/local/bin`. Installed elsewhere, set `PDFTOOLKIT_TESSERACT` to the full path of the program; one on PATH or in the current folder is never used. From a source checkout you can set translation up by hand:

  ```bash
  pip install --no-deps argostranslate==1.11.0
  pip install "ctranslate2>=4.0,<5" minisbd packaging "sacremoses>=0.0.53,<0.2" "sentencepiece>=0.2.0,<0.3"
  pip install pytesseract
  python setup_translation.py --install de fr    # the languages you need, or: all
  ```

  `--no-deps` leaves out two things argostranslate asks for that the app never uses: stanza 1.10.1, which has known security flaws and brings PyTorch with it, and spaCy. The second line installs the rest of what argostranslate needs. Setting up translation, here or in the app, is one of the few things that go online; see [What goes online](#what-goes-online).
- [NumPy](https://numpy.org/) — improves photo vs diagram detection accuracy, and is needed for Redact's OCR (`pip install numpy`)
- **OCR for Redact** (reading scanned pages for search words) — the packaged app carries it. From a source checkout, fetch its pinned English language file once with `python setup_ocr.py --install` (about 4 MB; `--status` checks it). It uses PyMuPDF's own built-in Tesseract, so the Tesseract program is not needed. This is another explicit setup download; a redaction never downloads anything.
- [python-docx](https://python-docx.readthedocs.io/) — PDF to Word conversion (`pip install python-docx`)

---

## Frontend

The GUI is a **React + Vite + TypeScript** app (`web-react/`), with its built bundle committed to `web-react/dist/` — end users just pull and run, **no Node.js required**.

If you're modifying the frontend, you'll need Node.js + npm:

```bash
cd web-react
npm install
npm run build
```

The original vanilla-JS frontend has been fully retired now that the React app is at feature parity.

---

## Building the Windows installer

Two steps, wrapped in one script:

```bat
build_installer.bat
```

That runs the PyInstaller one-dir build (`dist\PDFToolkit\`) and then compiles
[installer.iss](installer.iss) into `dist\PDFToolkit-Setup-<version>.exe` — an
all-users installer (Program Files, Start Menu + optional Desktop shortcut,
automatic uninstaller). Requirements:

- `pip install pyinstaller`
- [Inno Setup 6.3+](https://jrsoftware.org/isinfo.php) (free) — `iscc.exe` is
  found on PATH or at the default `C:\Program Files (x86)\Inno Setup 6\`

To build just the app without the installer, use `build.bat` (or
`python -m PyInstaller pdf_toolkit.spec --noconfirm`). Uninstalling removes the
program files only; per-user data (settings/logs, the on-demand translation
runtime, Argos language packs) is left in place so upgrades keep your setup.

---

## Development

Tests and linting run in CI on a matrix of Linux + Windows, Python 3.10 and 3.12:

```bash
pip install -r requirements-dev.txt   # pytest, pytest-cov, ruff
pytest                                 # run the test suite
ruff check .                           # lint (import order, unused code, bugbear)
```

Pure, unit-testable logic is kept in **top-level modules that import no Qt/PySide6**
(e.g. `compress_paths.py`, or `pdf_analyze.sanitize_payload` for what the Sanitize
slot answers), so the suite runs headless without a graphics stack.
Importing anything under `ui/` pulls in the Qt GUI stack (`ui/__init__` → `web_shell`
→ QtWebEngine), which a headless CI runner can't load — so tests import the pure
helpers directly instead of through `ui.bridge`.

---

## Usage

**GUI** — double-click `compress_pdf.bat` or run `python app.py`. Select any tool from the dashboard or sidebar.

**CLI (compression only):**

```bash
python compress_pdf.py document.pdf                   # default (standard preset)
python compress_pdf.py document.pdf -p ebook          # specific preset
python compress_pdf.py document.pdf --linearize       # web-optimized output
python compress_pdf.py document.pdf --gs              # Ghostscript font subsetting
python compress_pdf.py document.pdf --backup          # create .backup before overwriting
python compress_pdf.py *.pdf -o compressed/            # batch to output directory
python compress_pdf.py document.pdf --log             # write the activity log (no file names)
python compress_pdf.py document.pdf --detailed-log    # the detailed log, which names files
```

---

## Tools

### Compress & Optimize
- **Compress PDF** — reduce file size with smart image recompression

### Merge & Split
- **Merge PDFs** — combine multiple PDFs into one document
- **Split PDF** — divide a PDF into separate files

### Convert
- **PDF to Images** — export pages as PNG or JPEG
- **Images to PDF** — convert images into a PDF document
- **PDF to Word** — extract text to a Word document
- **Translate** — offline translation of PDF text and text inside images/photos/scans (top global languages plus German and Danish), via local Argos models and Tesseract OCR; nothing is uploaded

### Security
- **Protect PDF** — add password and set permissions (AES-256)
- **Unlock PDF** — remove password protection
- **Redact PDF** — permanently remove sensitive text via search terms (optionally reading scanned pages with OCR) or a visual box-drawing mode; true content destruction (including form-field values, metadata, bookmarks, annotations, link URLs, and attachment names), not a cosmetic overlay. **Fail-closed:** every redaction is verified *before* it is saved and *refused* if any target is still extractable — a page-content leak can be force-removed by flattening the affected page(s) to image; nothing is ever saved with a residual, and a refused run leaves the destination untouched (including the original, when redacting in place). The output gets a new content-derived file identifier, so it cannot be matched to its source through the PDF `/ID`. What it removes, what it reports and its known limits: [Redaction](#redaction)
- **Enhanced encryption** — custom `.epdf` format with ChaCha20-Poly1305, AES-256-GCM, or Camellia-256 and Argon2id key derivation

### Page Operations
- **Rotate & Reorder** — rotate, reorder, or delete pages
- **Crop Pages** — trim page margins
- **Flatten PDF** — remove annotations and form fields
- **N-up Layout** — arrange multiple pages per sheet

### Content & Watermark
- **Add Watermark** — overlay text or image watermark, with an optional tiled/diagonal mode that repeats across the page so it can't be trivially cropped out
- **Add Page Numbers** — insert page numbering
- **Edit Metadata** — view and edit PDF properties

### Extract
- **Extract Images** — pull all images from a PDF
- **Extract Text** — export text content to a file

### Repair & Analysis
- **Repair PDF** — fix corrupted PDF files
- **Compare PDFs** — find differences between two PDFs
- **Analyze Document** — offline privacy & security audit; finds embedded JavaScript (including scripts on bookmarks and form fields), auto-run/launch actions, external trackers, embedded files and references to external files, hidden layers, invisible text, and identifying metadata (document, page or object level), and says so when part of a file could not be checked. One-click sanitizing writes a clean copy that is verified before it is saved: it reports what it removed and what the ticked options left alone, or refuses and says what is still there

---

## Redaction

Redact works in one of two ways. **Search terms**: every place a word or
phrase stands is found and removed, on the pages and everywhere else in the
file. **Draw boxes**: what lies under each box you draw on a page is
removed, and nothing is searched for.

**What it removes.**
- *On the page*: the text of each hit or under each box, the pixels of a
  picture under it, and a drawing under a box (a straight line that only
  passes through a box hides nothing and stays). Each place is filled black.
- *Over the page*: notes, stamps and other annotations lying over a box, or
  whose own appearance shows the term, with their pop-ups and replies; links
  under a hit or a box; a form field shown under a hit or a box.
- *Elsewhere in the file* (search terms): the document's details and XMP
  metadata, bookmark titles, the text a tagged file keeps for screen readers
  (`/Alt`, `/ActualText`), layer names, an application's private data
  (`/PieceInfo`), metadata streams, scripts, named destinations, page-label
  prefixes, form-field text (values, defaults, tooltips, options, captions,
  names), rich text, link addresses, attachments that hold the term,
  signatures that hold it, and an XFA form's data.
- *Always*: page thumbnails, a tagged file's description of content that
  was removed, anything the structure tree still points at after it left
  its page, and the file's identifier (a new one is written).

**Checked before saving.** The new file is written beside the destination
and checked there: the page text, the page code, annotations, form fields,
links, the document's details, bookmarks, attachments, the structure tree,
thumbnails, what is under each box you drew, and a search of every string
and stream in the file. It is saved only if every check passes. Otherwise
nothing is saved, and the original is untouched, even when redacting in
place.

**What it reports.** When the run is saved, the Redact page says how many
places on how many pages were redacted, what else was removed, and what is
good to know: pages with no text to search (a scan or a picture: any words
in it were not searched), pages that are mostly a picture with a few words
of text (only those words were searched), the pages OCR read and what it
found on each (see *Reading scanned pages*), a scan's hidden text layer that was searched (a
word the layer misread is still in the picture), attachments, which are
searched as they are stored and never opened (a zip or a Word file is
packed, so nothing inside it is searched), pages turned into pictures, a
run by boxes alone, a form field under a box that is still shown in another
place, and, on PyMuPDF 2.x, that text after a removed word may have moved.
When the run is refused, the page says what was found and where, and what
can be done. When what is left is drawn on a page, that page can be turned
into a picture: it keeps its look, with what was found blacked out first,
but its text can no longer be selected or searched. When it is not on a page
(the document's details, the file's identifier), the page says that turning
pages into pictures would not help. A form stored only as XFA that holds a
search term, and PyMuPDF 1.28 (see Install), are refused before anything is
written. A search that finds nothing is not an error: the page says so in a
note, with the pages it could not read, and that nothing was saved. Neither
is Cancel: the page says the run stopped and nothing was saved.

**The black is the box.** Each place is filled black, with an edge half a
point past it, the same on a page with a user unit (a page drawn larger than
its numbers say), where PyMuPDF's own fill used to reach two or three times
the box.

**Supported PDF library.** PyMuPDF 1.27.2.3. Redact refuses to run on 1.28,
and runs on 2.x with a note (see Install).

**Reading scanned pages (OCR).** Tick *Also read scanned pages with OCR
(slower)* under the search words. It is off by default and needs search
words. A search then also reads, with OCR, every page it has no text to read
on (a scan, a picture), and every page that is mostly a picture with a few
words of text on it: pictures cover half the page or more, and the search
reads 200 characters or fewer there (a scan with a typed page or Bates
number, a stamp, a "Scanned with" line or a note on it). Where OCR finds a
search word, a black box covers it with a small margin, and the picture's own
pixels under the box are blanked, so the word is not in the file for anyone
who takes the picture out. Where OCR reads the word in such a page's typed
text, the search has found it there already and removes it the usual way. A
page that is sideways or upside down is read at each quarter turn. Before
saving, these places are checked too: each shows only black, the picture
holds nothing there any more, OCR no longer reads the word on the page, and
nothing else on the page changed. The result names every page OCR read, so
that you can check them by eye.

OCR uses PyMuPDF's own built-in Tesseract (the Tesseract program is not
needed) and one language file: English, `eng.traineddata` from
tesseract-ocr/tessdata_fast, pinned by commit and SHA-256 in
`ocr_data_lock.json`. The packaged app carries the file. From source, fetch
it once with `python setup_ocr.py --install` (`--status` says whether OCR is
ready); OCR also needs numpy. Nothing is downloaded during a redaction.
Without the file the option is greyed out with one line saying why, and a run
asked to use OCR goes ahead without it and says so.

Known limits of OCR:
- OCR misses words. In the owner's tests it found 99 of 117; the misses were
  on 7 pages, and the result told the user to check each of them by eye. It
  never says a page is clear.
- English only: a word with accented letters may not be found (OCR can read
  "impôt" as "impot").
- The black can take parts of words on the lines above and below, more on a
  tilted scan. A box grows 2 points round what OCR found, and by a quarter of
  its height more, but that quarter stops at the edge of any other word OCR
  read. Measured on real pages made into scans, a word beside the search word
  lost a third or more of the paper round it on 4 of 66 straight pages at 300
  dpi, and on 26 of 64 pages tilted 1.5 degrees.
- It is slow: a page took about 20 seconds in the owner's tests, and up to
  two and a half minutes on a large one. Cancel waits for the reading in
  progress.
- A redacted scan's picture is saved without loss, so the file grows: 1.3 to
  1.6 times for a typical scan, up to about 15 times on a real page (13 times
  for a large JPEG, 14.5 times for a JPEG 2000 picture).
- A page with an OCR text layer (a "searchable" scan) is searched through
  that layer and not read again; a word the layer misread is still in the
  picture, and the result says so.
- A page with more than a few words of text on its picture (type over a
  background picture, a form with a scanned signature) is searched as text
  and not read with OCR.
- A page too large to read at 150 dpi within 25 million pixels (about A0 and
  up) is not read, and the result says so.

**Known limits.** Each of these is refused, reported in the result, or as
described here.
- Text outside the visible page, and a word that is only a PDF name in a
  page's code (`/Subtype /Watermark`), have no text on the page to box: the
  run is refused and the page offered as a picture.
- Characters drawn out of reading order are not put back in order, so a word
  drawn that way may not be found (no match, or a refusal; never passed).
- Slanted text is boxed by its bounding rectangle, which takes in what is
  printed near it.
- Where a box changes content inside a form XObject, the redacted copy is
  not put back in the structure tree: that content is no longer tagged.
- A form field under a hit is removed whole, with all of its text.
- A form field under a box you draw that is also shown elsewhere keeps its
  value there. The run says so.
- Lines printed over each other by more than half their height lose
  letters to a term box: the box is trimmed to its own line, but never past
  the middle half of its height.
- Text running up or down the page is not trimmed, so a term box there can
  take letters from the next column.
- Two words that spell the term between them are taken as the term ("now
  here" for "nowhere").
- A box you draw that reaches a fifth of the way into a letter of the next
  line takes that letter, in a font whose widths in the PDF differ from the
  font's own.
- In a font whose widths are all zero, a letter is judged by the point it is
  drawn from: a box over the letter's shape that misses that point leaves
  its text in the file, under the black.
- Text hidden under a picture drawn over it (not in the invisible mode) is
  not noticed as a text layer, so the run does not warn that a misread word
  may still be in the picture.
- A straight line written as a curve is treated as a curve: a box over it
  is refused and the page offered as a picture.
- On PyMuPDF 2.x the text after a removed word can move along its line. The
  run says so.
- Words inside a picture are not read, unless OCR is ticked (see *Reading
  scanned pages*). Attachments are searched as stored and never opened. A few places have no rule yet (web-capture names, an
  output intent's strings, certificates in the document security store):
  a term there is refused.

---

## Keyboard shortcuts (GUI)

| Shortcut | Action | Scope |
|----------|--------|-------|
| Ctrl+T | Toggle light/dark theme | Global |
| Ctrl+Home | Dashboard | Global |
| Ctrl+O | Add files | Compress, Merge, Split, Watermark |
| Ctrl+Enter | Run the tool | Compress, Merge, Split, Watermark |
| Escape | Clear selected files | Compress, Merge, Split, Watermark |

---

## Quality presets (compression)

| Preset | Color DPI | Mono DPI | JPEG | Metadata | Use case |
|--------|-----------|----------|------|----------|----------|
| Screen | 72 | 150 | 35% | Stripped | On-screen viewing only |
| E-book | 120 | 200 | 55% | Stripped | Tablets, laptops, e-readers |
| Standard | 150 | 150 | 65% | Kept | Lecture notes, reports, datasheets |
| High quality | 200 | 200 | 80% | Kept | Good prints, detailed diagrams |
| Prepress | 300 | 300 | 90% | Kept | Professional printing |

Each preset has per-image-type DPI targets (color, grayscale, monochrome). Your last-used preset, theme, output options, and naming template are remembered between sessions.

---

## Features

### Compression engine
- **Smart image format selection** — photographs get JPEG; diagrams and screenshots get lossless Flate; B&W scans get 1-bit encoding
- **DPI-aware downscaling** — uses CTM (Current Transformation Matrix) tracking to calculate true rendered DPI, not just pixel dimensions
- **Grayscale preservation** — single-channel JPEG saves ~66% vs RGB
- **Transparency handling** — composites soft-masked images against white before JPEG encoding
- **Duplicate font merging** — SHA-256 hash deduplication of identical embedded font streams
- **Content stream optimization** — removes redundant save/restore (q/Q) operator pairs
- **Font subsetting** — via Ghostscript, removes unused glyphs from embedded fonts (typically 20-60% savings on font-heavy documents)
- **PDF structure cleanup** — removes JavaScript, PieceInfo, empty AcroForm, page thumbnails
- **Metadata stripping** — XMP metadata, document info dict, and accessibility trees (lower presets only)
- **Object stream compression** — all PDF streams are Flate-compressed with object stream generation
- **Smart skip logic** — tiny images, already-compressed images, and images below the quality threshold are left untouched to avoid generation loss

### GUI features
- **Dashboard home** — searchable grid of all 22 tools organized by category
- **Sidebar navigation** — collapsible sidebar with quick access to all tools
- **Light and dark themes** — toggle with Ctrl+T, preference remembered
- **Compress file cards** — each added PDF shows a page-1 thumbnail, size/pages/image chips, image DPI with a downscale warning, and estimated savings for the selected preset (updates as you switch presets)
- **Keyboard shortcuts** — Ctrl+O add files, Ctrl+Enter run, Esc clear on the main tool pages (see the Keyboard shortcuts table above)
- **Invalid PDF detection** — files without valid `%PDF-` headers are flagged and skipped
- **Encrypted PDF support** — prompts for a password on protected files
- **Custom naming templates** — on tools that write named output (e.g. split, protect, watermark), configure filenames with template variables like `{name}`
- **Linearization** — produce web-optimized PDFs for fast online viewing
- **Per-file progress bars** — individual progress per file during batch operations
- **Results panel** — per-file before/after sizes and total savings after a batch
- **Cancellation** — cancel a running batch at any time without leaving partial files
- **System tray notifications** — notified when a long operation finishes while minimized
- **Folder drag-and-drop** — drop a folder to add all PDFs recursively
- **Background analysis** — files are analyzed in a background thread without freezing the UI
- **Programmatic app icon** — no external icon file needed

### CLI features
- All five quality presets
- Batch processing with glob patterns
- `--linearize` for web-optimized output
- `--gs` for Ghostscript font subsetting
- `--backup` for backup creation
- `--log` for the activity log, `--detailed-log` for the detailed one (see [What the app keeps on your computer](#what-the-app-keeps-on-your-computer))
- Progress bar with per-image status

---

## How it works

1. Validates PDF magic bytes (`%PDF-` header)
2. Parses content streams to extract each image's transformation matrix (CTM tracking)
3. Calculates true rendered DPI from the matrix
4. Classifies images as photographic, diagram/screenshot, or monochrome using color variance analysis
5. Selects optimal encoding per image: JPEG for photos, Flate for diagrams, 1-bit for B&W
6. Downscales only images exceeding the target DPI for their type
7. Preserves grayscale images as single-channel
8. Composites transparent images (soft masks) against white before JPEG encoding
9. Skips tiny images and already-compressed images to avoid generation loss
10. Deduplicates shared XObjects — images used on multiple pages are processed once
11. Merges duplicate embedded fonts by SHA-256 hash
12. Optimizes content streams (removes empty q/Q pairs)
13. Optionally subsets fonts via Ghostscript
14. Removes JavaScript, PieceInfo, empty AcroForm, page thumbnails
15. Compresses all PDF streams and removes unreferenced resources
16. Writes to a temp file first — a crash never corrupts your original
17. Uses atomic file replacement (`os.replace`) for safe in-place overwrites
18. Picks the best result between pikepdf and Ghostscript passes (if both enabled)

Text and vector graphics are never modified.

---

## Security and privacy

- **Fully offline** — no telemetry, no cloud dependencies, no accounts. Nothing goes online unless you start one of the setup steps in [What goes online](#what-goes-online)
- **Network kill-switch for the interface** — the app's interface runs in an embedded web view. A `QWebEngineUrlRequestInterceptor` blocks every non-local request it makes (anything but `file`/`qrc`/`data`/`blob`/`about`) at the engine level, and a restrictive Content-Security-Policy (`connect-src 'none'`) backs it up, so the interface cannot reach the network. The kill-switch does not cover the Python side of the app, where the setup downloads below run
- **Document audit** — the Analyze Document tool inspects a PDF for trackers, scripts, and hidden data entirely on-device
- **PDF magic validation** — rejects files without valid `%PDF-` headers before processing
- **Decompression bomb protection** — images exceeding 200 million pixels are skipped
- **Content stream size limit** — streams over 16 MB are skipped to prevent pathological parsing
- **File size limit** — inputs over 2 GB are rejected
- **Ghostscript sandboxing** — `-dSAFER` flag restricts file system access; `--` separator prevents argument injection; paths are sanitized; 5-minute process timeout
- **Enhanced encryption** — `.epdf` format with ChaCha20-Poly1305, AES-256-GCM, or Camellia-256 ciphers; Argon2id key derivation; AEAD authentication. The `.epdf` header (cipher, KDF parameters, salt, nonce) is bound as Associated Data, so tampering or downgrade attacks on the header are detected on decrypt (format v2; v1 files still readable)
- **Encrypted PDF handling** — password-protected files prompt for credentials. A password you type is used for that operation and not kept: it is not written to the log (detailed or not), the settings, temp files or the results the interface gets back, and the files written hold only what the PDF or `.epdf` format derives from it. Python cannot promise to wipe it from memory, so it stays in the app's memory until that memory is reused or the app quits
- **Atomic file I/O** — temp file + `os.replace()`; original file is never corrupted on failure
- **Verify, then commit (Redact)** — the redacted candidate is written to a temp file beside the destination, verified there, and renamed into place only if verification passes. A refused redaction never creates, replaces, or deletes the destination
- **No identifier carry-over (Redact, Sanitize)** — PDF libraries preserve the trailer `/ID` across saves so revisions of a document can be recognised; for a redacted or sanitized copy that is a link back to the original. Both tools drop it and write a new one derived from the output's own content as their last step, and verification refuses an output that still shares an identifier with its source. The verification report records the SHA-256 of both files
- **Verify, then commit (Sanitize)** — the cleaned copy is written to a temp file beside the destination and audited again there, twice over: by the analyzer, and by an independent sweep of every object in the file. It is renamed into place only if everything the ticked options promise to remove is gone, every one of those checks could be carried out, and the copy shares no identifier with its source. Otherwise Sanitize refuses, says what is still present or what could not be checked, and has not created, replaced, or deleted the destination. What the ticked options do not cover is listed as remaining, not hidden
- **"Could not check" is not "clean" (Analyze)** — a structure the audit cannot read does not end it, and does not pass silently either: the findings it could have produced are reported as not checked (`audit.incomplete`), and Analyze never answers "no concerns" for a file it could not fully read
- **Known limits (Sanitize)** — three, each needing a file built for the purpose. A trailer `/Info` that points at a dictionary with another job and nothing but strings, names, numbers and booleans in it (and no `/Type`, `/Subtype` or `/S`) is emptied by the metadata option, taking that other job's entries with it. An `/Info` dictionary that is also held under a second key and declares a `/Type`, or holds an array or a dictionary, is only unhooked from the trailer: its entries stay in the file under the other key and the copy still verifies. And the identifier check compares the elements of the two trailer `/ID`s, so an identifier hidden inside a nested array in the source's `/ID` is not seen
- **Backup system** — optional `.backup` copy with rotation before overwriting originals
- **Thread-safe cancellation** — cancel at any point without leaving partial output files
- **Activity log without file names** — the app logs what it does (the tool, counts, sizes, the outcome) to `pdfcompress.log` in its log folder (5 MB, three older copies), never the names or folders of your files. Anything path-like that still reaches a line, such as a file name in an error message, is replaced before the line is written; a traceback keeps the app's module names and line numbers. Other libraries add only their warnings and errors (the translation library logs the text it translates otherwise). For troubleshooting, **Detailed log (includes file names)** on the home screen turns on the full log, file names, folders and all; **Clear logs** deletes the log and its copies
- **PDF/A awareness** — detects PDF/A conformance and warns before breaking compliance
- **No silent failures** — all exceptions are logged with appropriate severity levels
- **Path validation** — `os.startfile` calls are guarded with directory validation

### What goes online

The app never connects to the network on its own. These are the only parts of
the toolkit that do, and each runs only when you start it:

| What | When | Where it connects |
|------|------|-------------------|
| Translation setup: the translation engine (packaged app only) | Translate tool, *Download & set up*, the first time | files.pythonhosted.org, for the pinned wheels (each checked against its SHA-256) |
| Translation setup: sentence models | the same button, or `python setup_translation.py --install` | github.com, which hands over to release-assets.githubusercontent.com (the MiniSBD v0.0.1 release; size and SHA-256 checked) |
| Translation setup: language packs | the same | raw.githubusercontent.com for the Argos package index, then the addresses the index gives for the packs (argos-net.com in our checks) |
| Redact's OCR language file (source checkout) | `python setup_ocr.py --install` | raw.githubusercontent.com (tessdata_fast at a pinned commit; size and SHA-256 checked) |
| Regenerating the translation lock (developers) | `python translate_runtime.py --make-lockfile` | PyPI (pypi.org, files.pythonhosted.org), through pip |

After setup, translating, reading text from images and redacting make no
network connection. A translation whose sentence model is missing stops and
says so; it does not fetch the model.

### What the app keeps on your computer

| What | Where | What's in it, and when it goes |
|------|-------|--------------------------------|
| Activity log | Windows `%APPDATA%\PDFCompress\logs`, macOS `~/Library/Logs/PDFCompress`, Linux `~/.local/share/PDFCompress/logs` | `pdfcompress.log` and up to three older copies of 5 MB each, and a marker file (`old-logs-removed`). No file names unless you turn on the detailed log. **Clear logs** on the home screen deletes the logs. The first start of this version deleted the logs earlier versions wrote, which named files and folders |
| Settings | Windows registry `HKEY_CURRENT_USER\Software\PDFCompress\PDFCompress`, macOS `~/Library/Preferences/com.pdfcompress.PDFCompress.plist`, Linux `~/.config/PDFCompress/PDFCompress.conf` | Theme, sidebar, the detailed-log switch (`diagnostics/detailedLog`) and each tool's last choices: `merge/outputDir`, `merge/outputName`, `split/outputDir`, `split/nameTemplate`, `split/mode`, `split/everyN`, `watermark/outputDir` and the other watermark options, `protect/mode`, `protect/cipher`, `protect/kdf`, `protect/naming`. The `outputDir` keys hold the last output folder you chose. No passwords |
| Workspace | a `pdfcompress_workspace_*` folder in your temp folder | The working document while you pass it from tool to tool. Deleted when the app quits; one left by a crash is deleted at the next start (or after a day, if an older version made it) |
| Translation engine | Windows `%LOCALAPPDATA%\PDFToolkit\translate-runtime`, elsewhere `~/.local/share/pdftoolkit/translate-runtime` | What translation setup installed (packaged app). Replaced by the next setup |
| Language packs and sentence models | `~/.local/share/argos-translate` (`packages`, `minisbd`), with Argos Translate's cache and settings in `~/.local/cache/argos-translate` and `~/.config/argos-translate` | What translation setup downloaded |
| OCR language file | Windows `%LOCALAPPDATA%\PDFToolkit\ocr-data`, elsewhere `~/.local/share/pdftoolkit/ocr-data` | `eng.traineddata`, from `python setup_ocr.py --install` (the packaged app carries its own) |
| Backups | beside the original: `<name>.pdf.backup`, and the one before it as `.backup.old` | A copy of the original, made before Compress saves over it: in the app whenever it saves over the original (it normally writes a new file beside it instead), on the command line with `--backup`. Kept until you delete it |
| Web view | nothing on disk | The interface runs in an off-the-record profile: its cache is kept in memory and it stores no cookies or web storage. Qt may create an empty graphics cache folder under `%LOCALAPPDATA%\PDFCompress` |

Temporary files written while a tool runs sit beside the output (or in the
temp folder, for a page read with OCR during translation) and are deleted
when the tool finishes, whether it worked or not.

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Application entry point (window icon, logging, Qt bootstrap) |
| `engine.py` | Compression engine (shared by GUI and CLI) |
| `compress_paths.py` | Pure compression output-path resolution (Qt-free; unit-tested by `tests/test_bridge.py`) |
| `pdf_ops.py` | PDF operations (merge, split, protect, watermark, etc.) |
| `epdf_crypto.py` | Enhanced encryption engine (.epdf format — ChaCha20, AES-256, Camellia; header-authenticated v2) |
| `pdf_analyze.py` | Offline privacy/security audit + sanitizer engine (verify, then commit); also `sanitize_payload`, the Qt-free answer the bridge gives the Sanitize panel |
| `pdf_verify.py` | Post-operation verification loop — independently re-checks redacted/sanitized output and reports proof of removal (Qt-free) |
| `pdf_translate.py` | Offline translation + OCR engine (Argos + Tesseract) |
| `translate_runtime.py` | On-demand provisioning of the translation runtime (frozen builds leave the translation stack out; this downloads pinned wheels into a user dir once) |
| `translate_runtime_lock.json` | Pinned wheel manifest for that runtime, and what it leaves out of argostranslate's requirements (stanza, spaCy) and why (regenerate: `python translate_runtime.py --make-lockfile`) |
| `translate_sbd_lock.json` | The pinned sentence models (MiniSBD v0.0.1: file, size, SHA-256) that translation setup fetches, and their licence |
| `app_log.py` | The activity log: no file names by default, the scrubbing safety net, the detailed log, Clear logs (Qt-free) |
| `workspace_dir.py` | The workspace's temp folder: made with an owner file, deleted at quit, stale ones swept at start (Qt-free) |
| `trusted_programs.py` | Where Ghostscript, Tesseract and the file manager are found: a setting or their install folders, never PATH or the current folder (Qt-free) |
| `setup_translation.py` | CLI provisioning of offline translation/OCR models (source checkouts) |
| `pdf_ocr.py` | Redact's OCR: reading scanned pages with PyMuPDF's built-in Tesseract, its language file and whether it is ready (Qt-free) |
| `ocr_data_lock.json` | The pinned OCR language file (commit, size, SHA-256) and its licence |
| `setup_ocr.py` | Fetches the pinned OCR language file once, for a source checkout (`--install`, `--status`) |
| `licenses/tessdata_fast-LICENSE` | Apache-2.0 licence of the OCR language file, carried beside it in the packaged app |
| `compress_pdf.py` | Command-line interface for compression |
| `compress_pdf.bat` | Windows launcher (no console window) |
| `pdf_toolkit.ico` | Application icon (16–256 px) — embedded in the .exe, window icon, installer/shortcuts |
| `pdf_toolkit.spec` | PyInstaller build configuration (one-dir frozen app) |
| `build.bat` | PyInstaller build script |
| `installer.iss` | Inno Setup script — Windows installer (see *Building the Windows installer*) |
| `build_installer.bat` | PyInstaller build + Inno Setup compile in one step |
| `ui/` | GUI package — web shell, bridge, theme |
| `ui/web_shell.py` | Main window (QWebEngineView + QWebChannel) |
| `ui/bridge.py` | Python-to-JavaScript communication bridge |
| `ui/net_guard.py` | Network kill-switch — blocks all non-local web-engine requests |
| `ui/tool_registry.py` | Centralized tool metadata and categories |
| `web-react/` | Frontend — React + Vite + TypeScript (source + committed `dist/` build) |
| `assets/fonts/` | Bundled fonts (DejaVu Sans) for image-preserving PDF translation output |
| `tests/` | Pytest suite (pure-Python, no Qt import required) |
| `.github/workflows/ci.yml` | CI — ruff lint + pytest matrix (Linux/Windows, py3.10/3.12) |
| `requirements.txt` | Python runtime dependencies |
| `requirements-dev.txt` | Dev/test dependencies (pytest, pytest-cov, ruff) |

---

## License

MIT — FR3T0T © 2026

The OCR language file Redact reads scans with (`eng.traineddata`, from
tesseract-ocr/tessdata_fast) is Apache-2.0. Its licence is in
`licenses/tessdata_fast-LICENSE`, and the packaged app carries it beside the file.

Translation is downloaded at setup, not shipped with the app. Argos Translate
is MIT. It splits sentences with MiniSBD (LibreTranslate/MiniSBD), whose code
is AGPL-3.0. Its models come from that project's v0.0.1 release, which names no
separate licence for them; MiniSBD's README describes them as Stanza's tokenizer
models converted to ONNX.
