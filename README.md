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

| Preset | DPI | JPEG | Use case |
|--------|-----|------|----------|
| Screen | 72 | 35% | On-screen viewing only |
| E-book | 120 | 55% | Tablets, laptops, e-readers |
| Standard | 150 | 65% | Lecture notes, reports, datasheets |
| High quality | 200 | 80% | Good prints, detailed diagrams |
| Prepress | 300 | 90% | Professional printing |

Your last-used preset and theme are remembered between sessions.

---

## How it works

1. Parses the PDF content stream to find each image's transformation matrix
2. Calculates true rendered DPI from the matrix (not just pixel dimensions)
3. Downscales only images that exceed the target DPI
4. Preserves grayscale images as single-channel JPEG
5. Skips tiny images (logos, icons, thumbnails)
6. Detects already-compressed images to avoid generation loss
7. Compresses PDF streams and removes unreferenced resources
8. Writes to a temp file first — a crash never corrupts your original

Text, fonts, and vector graphics are never modified.

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | PySide6 desktop GUI |
| `engine.py` | Compression engine |
| `compress_pdf.py` | Command-line interface |
| `compress_pdf.bat` | Windows launcher (no console) |

---

## License

MIT — FR3T0T © 2026
