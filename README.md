# PDF Compress

Fully offline PDF compressor with DPI-aware image recompression, smart format selection, font subsetting, duplicate font merging, content stream optimization, and PDF structure cleanup. No cloud services, no accounts, no tracking.

**v3.0.0**

---

## Install

```bash
pip install -r requirements.txt
```

Or manually:

```bash
pip install pikepdf pillow PySide6
```

**Optional dependencies:**
- [Ghostscript](https://www.ghostscript.com/releases/gsdnld.html) — font subsetting (auto-detected if on PATH)
- [NumPy](https://numpy.org/) — improves photo vs diagram detection accuracy (`pip install numpy`)

---

## Usage

**GUI** — double-click `compress_pdf.bat` or run `python app.py`. Drag and drop PDFs or folders onto the window.

**CLI:**

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

## Keyboard shortcuts (GUI)

| Shortcut | Action |
|----------|--------|
| Ctrl+O | Add files |
| Ctrl+Enter | Compress |
| Escape | Clear files |
| Ctrl+T | Toggle light/dark theme |
| Ctrl+, | About |

---

## Quality presets

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
- **PDF magic validation** — rejects files without valid `%PDF-` headers before processing
- **Decompression bomb protection** — images exceeding 200 million pixels are skipped
- **Content stream size limit** — streams over 16 MB are skipped to prevent pathological parsing
- **File size limit** — inputs over 2 GB are rejected
- **Ghostscript sandboxing** — `-dSAFER` flag restricts file system access; `--` separator prevents argument injection; paths are sanitized
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
| `app.py` | PySide6 desktop GUI (v3.0.0) |
| `engine.py` | Compression engine (shared by GUI and CLI) |
| `compress_pdf.py` | Command-line interface |
| `compress_pdf.bat` | Windows launcher (no console window) |
| `requirements.txt` | Python dependencies |

---

## License

MIT — FR3T0T © 2026
