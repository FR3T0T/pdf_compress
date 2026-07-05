# Changelog
## v4.20

Major release: the entire frontend rebuilt in React, plus new tools, a redaction overhaul, and a security-hardening pass.

### React frontend (complete rewrite)
- The whole web UI is migrated from vanilla JS to **React + Vite + TypeScript** — all 22 tools, the sidebar/router shell, and shared components rebuilt as typed React components.
- **Security-console aesthetic** with full **light and dark themes** (WCAG-AA contrast), toggle persisted across restarts.
- The built bundle is committed to `web-react/dist/`, so the app runs with no Node build step. Developers modifying the frontend need Node + `npm install` + `npm run build` in `web-react/`.
- The Python bridge is unchanged; async operations (progress/cancel/done) are centralized in a single `useOperation` hook.

### New tools
- **Analyze Document** — offline privacy/security audit (`pdf_analyze.py`): flags embedded JavaScript, auto-run/launch actions, external trackers, embedded files, hidden layers, invisible text, and identifying metadata, with a one-click sanitizer. Auto-run findings now describe *when* each trigger fires and *what* it does.
- **Translate** — offline translation (`pdf_translate.py`) of PDF text and text inside images/photos/scans (Argos + Tesseract OCR), 12 languages. Includes proper-noun/separator protection (prevents place-name and separator corruption), a user "keep these words untranslated" field, and **image-preserving PDF output** using a bundled DejaVu font.

### Redaction (rewritten — true content destruction)
- Replaced the old content-stream regex approach (which silently missed most text) with **PyMuPDF `apply_redactions()`** — text and images under a redaction are permanently destroyed and unrecoverable.
- Added a **visual box-drawing mode**: render each page, draw boxes over content, true-redact those regions.
- **Fixed a critical data leak**: form-field (AcroForm) values survived redaction — a black box was drawn but the underlying value (e.g. an SSN on a fillable form) remained fully extractable. Overlapping form fields are now neutralized before redaction.
- Fixed box coordinate mapping (display-vs-natural image scaling) and removed an incorrect Y-axis inversion that sent boxes to the backend flipped.

### Watermark
- Added a **tiled/diagonal mode** — the watermark repeats across the whole page (staggered, translucent) so it can't be trivially cropped out. Single mode retained.

### Security & hardening
- **Network kill-switch** — `QWebEngineUrlRequestInterceptor` blocks every non-local request; CSP forbids all network egress. The app provably cannot phone home.
- **`.epdf` header authentication (v2)** — cipher/KDF/salt/nonce bound as Associated Data; tampering and downgrade attacks are detected on decrypt. v1 files still decrypt.
- **Output-path containment** — user-supplied names/templates can no longer escape the chosen folder (blocks `../` traversal and absolute-path override).
- **`openFile`/`openFolder` validation** — only real local paths are opened; URLs and protocol handlers are refused.
- Upgraded **stanza** to resolve **CVE-2026-54499** (critical RCE via unsafe pickle deserialization in model loading).

### Fixes
- **Translate page-load freeze** — a synchronous `argostranslate` import on tool open froze the whole window ~5s; provisioning status now runs off the UI thread with a loading state.
- **Progress/results not updating** — async tools read `data.tool`/`data.percent` while the bridge sent `toolKey`/`pct`/nested `results`; corrected across all tools.
- **Watermark opacity** — was sent on a 1–100 scale where the engine expects 0–1 (watermarks rendered fully opaque).
- **`getPresets`** — called with zero args where the slot requires one (preset dropdowns fell back silently).
- **Sidebar collapse persistence** and **`loadSetting` double-encoding** — settings now restore correctly.

### Notes
- No change for end users beyond installing dependencies — pull and run.
- New files: the `web-react/` React project (source + committed `dist/`), `assets/fonts/DejaVuSans.ttf`. The legacy `web/` vanilla frontend is retained but no longer the active UI.

---
## v4.10

Privacy/security hardening and offline translation — four additions, all fully offline.

### Analyze Document (new tool)
- **`pdf_analyze.py`** — an offline privacy/security audit engine (pikepdf, with optional PyMuPDF). Detects identifying metadata (/Info + XMP), embedded JavaScript, auto-run actions (/OpenAction, /AA), launch actions, external URI links / trackers, remote GoTo, embedded files/attachments, form submit/import actions, XFA/AcroForm, optional-content layers, and invisible text (the classic failed-redaction tell). Returns risk-graded findings and an overall risk level.
- **One-click sanitizer** — `sanitize_pdf()` writes a cleaned copy with the selected categories stripped (JavaScript, launch/auto actions, embedded files, submit actions, and optionally external links and metadata). The original file is never modified; writes are atomic.
- Wired into the web UI as a new **Analyze Document** tool (shield icon, Repair & Analysis category): drop a PDF → risk report → optional sanitize. New bridge slots `analyzeDocument`, `getSanitizeDefaults`, `sanitizeDocument`.

### Network kill-switch (provable offline)
- **`ui/net_guard.py`** — a `QWebEngineUrlRequestInterceptor` installed on the web profile blocks every request whose scheme isn't local (`file`/`qrc`/`data`/`blob`/`about`); it fails closed. Any tracker, beacon, web font, or stray `fetch()` is dropped before a byte leaves the machine.
- **Content-Security-Policy** added to `index.html` (`connect-src 'none'`, `object-src 'none'`, `base-uri 'none'`, restrictive `default-src`) as defense-in-depth; kept permissive for bundled local assets so rendering is unaffected.
- Hardened web settings: clipboard access and window-opening disabled for the page's JavaScript.

### Encryption header authentication (.epdf v2)
- The `.epdf` header (cipher, KDF parameters, salt, nonce) is now bound as **Associated Data**: AEAD ciphers authenticate it directly, and the Camellia HMAC now covers `header + iv + ciphertext`. Tampering with — or downgrading — the header is detected on decrypt instead of being silently accepted.
- Format version bumped to **v2**; existing **v1** files still decrypt unchanged.

### Translate (new tool)
- **`pdf_translate.py`** — an offline translation/OCR engine. Translates PDF text and the text inside images/photos/scans, entirely on-device: Argos Translate (CTranslate2 models) for translation, Tesseract for OCR, and langdetect for source auto-detection. No network calls.
- **Languages** — the global top-10 by speakers (English, Mandarin Chinese, Hindi, Spanish, Arabic, French, Bengali, Portuguese, Russian, Indonesian) plus **German** and **Danish** (12 total). Argos pivots through English, so a handful of installed languages covers translation between any of them.
- **Image translation** — drop a photo or scan; the tool OCRs the text, detects the language, and translates it (source text + translation shown side by side, copyable). Scanned PDF pages with no text layer are OCR'd automatically.
- **PDF translation** — runs on a background thread with progress and cancel; writes a `.txt` (page-delimited) or `.docx`. Text/Word output is used deliberately so every script (CJK, Cyrillic, Arabic, Devanagari) renders with the system's own fonts, avoiding fragile font embedding.
- Wired into the web UI as a new **Translate** tool (globe icon, Convert category) with From/To language pickers and a provisioning-status banner. New bridge slots `getTranslationStatus`, `translateText`, `translateImage`, and async `startTranslatePdf`.

### Model provisioning
- **`setup_translation.py`** — the one explicit online step. `--status` / `--list` show what's installed; `--install all` (or specific codes) downloads the Argos language packages and prints the per-OS commands for the Tesseract OCR packs. The app's network kill-switch is unaffected — it sandboxes the embedded web UI, while provisioning is a separate, user-run tool. After setup, translation is fully offline.

### Notes
- Translation dependencies are **optional**; the rest of the toolkit works without them. The Translate tool degrades gracefully and tells the user exactly what to install if a model is missing.
- No new dependencies for the analyze/kill-switch/encryption additions (PyMuPDF was already optional; it enables the invisible-text check).
- New files: `pdf_analyze.py`, `pdf_translate.py`, `setup_translation.py`, `ui/net_guard.py`, `web/js/pages/analyze.js`, `web/js/pages/translate.js`; edits to `epdf_crypto.py`, `ui/web_shell.py`, `ui/bridge.py`, `ui/tool_registry.py`, `web/index.html`, `web/js/bridge.js`, `web/js/app.js`, `web/js/icons.js`, `requirements.txt`.

## v4.0.1

Code quality, testing, documentation, and build hardening pass.

### Test suite (new)
- **76 automated tests** across 4 test files covering engine, PDF operations, encryption, and CLI
- `tests/test_engine.py` — compression presets, path sanitization, PDF validation, backup, cancellation
- `tests/test_pdf_ops.py` — merge, split, page operations, metadata, text extraction, flatten, repair
- `tests/test_epdf_crypto.py` — encrypt/decrypt round-trips for all 3 ciphers, wrong-password handling, format detection
- `tests/test_cli.py` — help flag, default compression, invalid file handling
- Shared fixtures in `tests/conftest.py` generate sample PDFs, encrypted PDFs, and invalid files on-the-fly

### Build & distribution (new)
- **PyInstaller spec** (`pdf_toolkit.spec`) — builds standalone `PDFToolkit.exe` with bundled frontend assets
- **build.bat** — one-click Windows build script
- **pyproject.toml** — project metadata, dependency declarations, pytest config
- **requirements-dev.txt** — dev dependencies (pytest, pytest-cov)

### Fixes
- **Ghostscript timeout** — added 5-minute wall-clock timeout to prevent runaway Ghostscript processes
- **Import ordering** — moved `logging.handlers` and `hashlib` imports to the top of `engine.py` (were deferred/inline, fragile under edge-case import ordering)
- **Deprecated shell.py** — added deprecation notice to the unused widget-based shell (replaced by `web_shell.py` in v4.0.0)

### Documentation
- **README: Files table** — corrected to reference `web_shell.py` (active) instead of `shell.py` (deprecated); added `epdf_crypto.py`, `ui/bridge.py`, and `web/` entries
- **README: Install** — manual install command now includes all required dependencies (PyMuPDF, argon2-cffi, pycryptodome, cryptography)
- **README: Tools** — added enhanced encryption (.epdf format) to the Security tools section
- **README: Security** — added Ghostscript timeout and enhanced encryption details

---

## v4.0.0

Complete rewrite from single-purpose PDF compressor to a full 20-tool PDF toolkit.

### New tools
- **Merge PDFs** — combine multiple PDFs into one document
- **Split PDF** — divide a PDF into separate files by page ranges
- **PDF to Images** — export pages as PNG or JPEG
- **Images to PDF** — convert images into a PDF document
- **PDF to Word** — extract text to a Word document (requires python-docx)
- **Protect PDF** — add password and set permissions (AES-256 encryption)
- **Unlock PDF** — remove password protection
- **Redact PDF** — permanently remove sensitive text
- **Rotate & Reorder** — rotate, reorder, or delete pages
- **Crop Pages** — trim page margins
- **Flatten PDF** — remove annotations and form fields
- **N-up Layout** — arrange multiple pages per sheet
- **Add Watermark** — overlay text or image watermark
- **Add Page Numbers** — insert page numbering
- **Edit Metadata** — view and edit PDF properties
- **Extract Images** — pull all images from a PDF
- **Extract Text** — export text content to a file
- **Repair PDF** — fix corrupted PDF files
- **Compare PDFs** — find differences between two PDFs

### New GUI architecture
- Dashboard home page with searchable tool grid organized by category
- Collapsible sidebar navigation with tool icons
- Stacked page system — each tool has its own dedicated page
- Centralized tool registry (`ui/tool_registry.py`)
- New `pdf_ops.py` module for all non-compression PDF operations
- Modular `ui/pages/` package with a base page class

### Improvements
- App renamed from "PDF Compress" to "PDF Toolkit"
- Ctrl+Home shortcut to return to dashboard
- python-docx added as optional dependency for PDF to Word conversion

---

## v3.0.0

Major engine rewrite with DPI-aware compression, smart format selection, and full GUI overhaul.

### Compression engine
- DPI-aware image downscaling using CTM (Current Transformation Matrix) tracking
- Smart image classification: photographic, diagram/screenshot, monochrome
- Optimal encoding per type: JPEG for photos, Flate for diagrams, 1-bit for B&W
- Grayscale preservation (single-channel JPEG saves ~66% vs RGB)
- Transparency handling — composites soft-masked images against white
- Duplicate font merging by SHA-256 hash
- Content stream optimization (removes empty q/Q pairs)
- Font subsetting via Ghostscript
- PDF structure cleanup: JavaScript, PieceInfo, empty AcroForm, page thumbnails
- Metadata stripping (XMP, document info dict)
- Object stream compression
- Smart skip logic for tiny/already-compressed images
- Decompression bomb protection (200M pixel limit)
- Content stream size limit (16 MB)
- File size limit (2 GB)

### GUI
- Five quality presets: Screen, E-book, Standard, High quality, Prepress
- Light and dark themes with Ctrl+T toggle
- Space audit dialog with image/font/content breakdown
- PDF/A detection with compliance warnings
- Invalid PDF detection (magic byte validation)
- Encrypted PDF support with password prompt
- Custom naming templates (`{name}`, `{preset}`, `{dpi}`)
- Backup on replace with rotation
- Linearization for web-optimized output
- Per-file progress bars
- Sortable file list (name, size, page count)
- Batch summary dialog
- Recent files
- System tray notifications
- Folder drag-and-drop
- Windows Explorer context menu integration
- Cancellation support
- Background file analysis
- Programmatic app icon

### CLI
- All five quality presets
- Batch processing with glob patterns
- `--linearize`, `--gs`, `--backup`, `--log` flags
- Progress bar with per-image status

### Security
- Ghostscript sandboxing (`-dSAFER`, `--` separator, path sanitization)
- Atomic file I/O (temp file + `os.replace()`)
- Thread-safe cancellation
- Diagnostic logging with rotation
- Path validation for `os.startfile`
