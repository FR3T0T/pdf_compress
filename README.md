# PDF Toolkit

Fully offline PDF toolkit with 22 professional tools — compress, merge, split, convert, protect, redact, watermark, and more. DPI-aware image recompression, AES-256 encryption, and PDF/A compliance. No cloud services, no accounts, no tracking.

**v4.20**

---

## Install

```bash
pip install -r requirements.txt
```

Or manually (core):

```bash
pip install pikepdf pillow PySide6 PyMuPDF argon2-cffi pycryptodome cryptography
```

**Optional dependencies:**
- [Ghostscript](https://www.ghostscript.com/releases/gsdnld.html) — font subsetting (auto-detected if on PATH)
- **Offline translation** (Translate tool) — `pip install argostranslate pytesseract langdetect`, the [Tesseract](https://github.com/tesseract-ocr/tesseract) binary, then `python setup_translation.py --install all` to download the language models. This download is the only step that uses the network; translation itself is fully offline.
- [NumPy](https://numpy.org/) — improves photo vs diagram detection accuracy (`pip install numpy`)
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

The legacy vanilla JS frontend (`web/`) is retained as a fallback — set `PDF_TOOLKIT_UI=legacy` to force it — but is no longer the active UI.

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
python compress_pdf.py document.pdf --log             # enable diagnostic logging
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
- **Redact PDF** — permanently remove sensitive text via search terms or a visual box-drawing mode; true content destruction (including form-field values), not a cosmetic overlay
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
- **Analyze Document** — offline privacy & security audit; finds embedded JavaScript, auto-run/launch actions, external trackers, embedded files, hidden layers, invisible text, and identifying metadata, with one-click sanitizing

---

## Keyboard shortcuts (GUI)

| Shortcut | Action |
|----------|--------|
| Ctrl+O | Add files |
| Ctrl+Enter | Compress |
| Ctrl+T | Toggle light/dark theme |
| Ctrl+, | About |
| Ctrl+Home | Dashboard |
| Escape | Clear files |

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
- **Space audit** — click the info button on any file to see a breakdown of images, fonts, and other content
- **PDF/A detection** — badges on PDF/A-compliant files with warnings when metadata stripping would break compliance
- **Invalid PDF detection** — files without valid `%PDF-` headers are flagged and skipped
- **Encrypted PDF support** — prompts for password, re-analyzes after unlock
- **Custom naming templates** — configure output filenames with `{name}`, `{preset}`, `{dpi}` variables
- **Backup on replace** — optional `.backup` copy before overwriting originals
- **Linearization** — produce web-optimized PDFs for fast online viewing
- **Replace original** — compress in-place with a safety confirmation dialog
- **Per-file progress bars** — individual progress per file during batch compression
- **Sortable file list** — sort by name, size, or page count
- **Batch summary dialog** — sortable results table after batch compression
- **Recent files** — quickly re-add previously compressed files
- **System tray notifications** — notified when compression finishes while minimized
- **Folder drag-and-drop** — drop a folder to add all PDFs recursively
- **Windows context menu** — register "Compress with PDF Compress" in the Explorer right-click menu (via About dialog)
- **Cancellation** — cancel a running batch at any time without leaving partial files
- **Background analysis** — files are analyzed in a background thread without freezing the UI
- **Programmatic app icon** — no external icon file needed

### CLI features
- All five quality presets
- Batch processing with glob patterns
- `--linearize` for web-optimized output
- `--gs` for Ghostscript font subsetting
- `--backup` for backup creation
- `--log` for diagnostic file logging
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

- **Fully offline** — no network access, no telemetry, no cloud dependencies, no accounts
- **Network kill-switch** — a `QWebEngineUrlRequestInterceptor` blocks every non-local request (anything but `file`/`qrc`/`data`/`blob`/`about`) at the engine level, plus a restrictive Content-Security-Policy (`connect-src 'none'`). The app provably cannot phone home even if a future change tried to
- **Document audit** — the Analyze Document tool inspects a PDF for trackers, scripts, and hidden data entirely on-device
- **PDF magic validation** — rejects files without valid `%PDF-` headers before processing
- **Decompression bomb protection** — images exceeding 200 million pixels are skipped
- **Content stream size limit** — streams over 16 MB are skipped to prevent pathological parsing
- **File size limit** — inputs over 2 GB are rejected
- **Ghostscript sandboxing** — `-dSAFER` flag restricts file system access; `--` separator prevents argument injection; paths are sanitized; 5-minute process timeout
- **Enhanced encryption** — `.epdf` format with ChaCha20-Poly1305, AES-256-GCM, or Camellia-256 ciphers; Argon2id key derivation; AEAD authentication. The `.epdf` header (cipher, KDF parameters, salt, nonce) is bound as Associated Data, so tampering or downgrade attacks on the header are detected on decrypt (format v2; v1 files still readable)
- **Encrypted PDF handling** — password-protected files prompt for credentials; passwords are cleared from memory after use
- **Atomic file I/O** — temp file + `os.replace()`; original file is never corrupted on failure
- **Backup system** — optional `.backup` copy with rotation before overwriting originals
- **Thread-safe cancellation** — cancel at any point without leaving partial output files
- **Diagnostic logging** — rotating file logs (5 MB, 3 backups) to platform-appropriate directories
- **PDF/A awareness** — detects PDF/A conformance and warns before breaking compliance
- **No silent failures** — all exceptions are logged with appropriate severity levels
- **Path validation** — `os.startfile` calls are guarded with directory validation

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Application entry point and icon generation |
| `engine.py` | Compression engine (shared by GUI and CLI) |
| `pdf_ops.py` | PDF operations (merge, split, protect, watermark, etc.) |
| `epdf_crypto.py` | Enhanced encryption engine (.epdf format — ChaCha20, AES-256, Camellia; header-authenticated v2) |
| `pdf_analyze.py` | Offline privacy/security audit + sanitizer engine |
| `pdf_translate.py` | Offline translation + OCR engine (Argos + Tesseract) |
| `setup_translation.py` | One-time provisioning of offline translation/OCR models |
| `compress_pdf.py` | Command-line interface for compression |
| `compress_pdf.bat` | Windows launcher (no console window) |
| `ui/` | GUI package — web shell, bridge, theme |
| `ui/web_shell.py` | Main window (QWebEngineView + QWebChannel) |
| `ui/bridge.py` | Python-to-JavaScript communication bridge |
| `ui/net_guard.py` | Network kill-switch — blocks all non-local web-engine requests |
| `ui/tool_registry.py` | Centralized tool metadata and categories |
| `web-react/` | Active frontend — React + Vite + TypeScript (source + committed `dist/` build) |
| `web/` | Legacy vanilla JS frontend — retained as a fallback (`PDF_TOOLKIT_UI=legacy`), no longer active by default |
| `assets/fonts/` | Bundled fonts (DejaVu Sans) for image-preserving PDF translation output |
| `requirements.txt` | Python dependencies |

---

## License

MIT — FR3T0T © 2026
