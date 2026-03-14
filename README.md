# PDF Compress

Fully offline PDF compressor with DPI-aware image recompression. No Ghostscript, no cloud services, no accounts, no tracking.

---

## Install

```bash
pip install pikepdf pillow PySide6
```

---

## Usage

**GUI** — double-click `compress_pdf.bat` or `python app.py`. Drag and drop PDFs onto the window.

**CLI** — `python compress_pdf.py document.pdf -p standard`

**CLI (web-optimized)** — `python compress_pdf.py document.pdf --linearize`

---

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+O | Add files |
| Ctrl+Enter | Compress |
| Escape | Clear files |
| Ctrl+T | Toggle light/dark theme |
| Ctrl+, | About |

---

## Quality presets

| Preset | DPI | JPEG | Metadata | Use case |
|--------|-----|------|----------|----------|
| Screen | 72 | 35% | Stripped | On-screen viewing only |
| E-book | 120 | 55% | Stripped | Tablets, laptops, e-readers |
| Standard | 150 | 65% | Kept | Lecture notes, reports, datasheets |
| High quality | 200 | 80% | Kept | Good prints, detailed diagrams |
| Prepress | 300 | 90% | Kept | Professional printing |

Your last-used preset and theme are remembered between sessions.

---

## How it works

1. Parses the PDF content stream to find each image's transformation matrix
2. Calculates true rendered DPI from the matrix (not just pixel dimensions)
3. Downscales only images that exceed the target DPI
4. Preserves grayscale images as single-channel JPEG
5. Composites transparent images (soft masks) against white before JPEG encoding
6. Skips tiny images (logos, icons, thumbnails)
7. Detects already-compressed images to avoid generation loss
8. Deduplicates shared XObjects — images used on multiple pages are processed once
9. Optionally strips XMP metadata, document info, and page thumbnails
10. Compresses PDF streams and removes unreferenced resources
11. Writes to a temp file first — a crash never corrupts your original
12. Preserves original file permissions on the output

Text, fonts, and vector graphics are never modified.

---

## Security and robustness

- **Decompression bomb protection** — images exceeding 200 million pixels are skipped
- **Content stream size limit** — streams over 16 MB are skipped to prevent pathological parsing
- **Encrypted PDF detection** — password-protected files are identified and reported clearly
- **Safe file I/O** — temp file + atomic rename; original file permissions preserved
- **No network access** — fully offline, no telemetry, no cloud dependencies

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | PySide6 desktop GUI |
| `engine.py` | Compression engine |
| `compress_pdf.py` | Command-line interface |
| `compress_pdf.bat` | Windows launcher (no console) |

> **Note:** `pdf_compress_gui.py` is a legacy tkinter GUI from v1.x. It uses an older
> compression engine without DPI awareness, deduplication, or transparency handling.
> Use `app.py` instead.

---

## License

MIT — FR3T0T © 2026
