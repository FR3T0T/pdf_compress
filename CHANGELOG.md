# Changelog

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
