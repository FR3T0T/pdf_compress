# pdf-compress

A simple, fully offline PDF compressor with a desktop GUI. No Ghostscript, no online tools, no files leaving your machine.

---

## Requirements

- [Python 3.8+](https://www.python.org/downloads/) — tick **"Add to PATH"** during install
- Two Python libraries:

```bash
pip install pikepdf pillow
```

---

## Usage

### GUI (recommended)

Double-click `compress_pdf.bat` to open the app, or drag a PDF onto it to open with that file pre-loaded.

1. Click the file area to select a PDF
2. Adjust the quality slider (1–5)
3. Optionally change the output path
4. Hit **Compress PDF**

### Command Line

```bash
python compress_pdf.py document.pdf
python compress_pdf.py document.pdf -q 1   # smallest
python compress_pdf.py document.pdf -q 5   # best quality
python compress_pdf.py *.pdf               # batch
```

---

## Quality Levels

| Level | DPI | JPEG | Best For |
|-------|-----|------|----------|
| 1 | 72  | 30% | Absolute smallest |
| 2 | 96  | 50% | Small, acceptable quality |
| 3 | 120 | 65% | **Default** — lecture notes & docs |
| 4 | 150 | 80% | Good quality, moderate savings |
| 5 | 200 | 90% | Light compression |

---

## How It Works

1. **Recompresses embedded images** at lower JPEG quality and downscales above the DPI threshold.
2. **Compresses PDF streams** — removes redundant internal data.

Text, fonts, and vectors are untouched. If compression doesn't reduce the file size, the original is kept.

---

## Files

| File | Purpose |
|------|---------|
| `pdf_compress_gui.py` | Desktop GUI application |
| `compress_pdf.py` | Command-line version |
| `compress_pdf.bat` | Launcher (double-click or drag PDF onto it) |
| `requirements.txt` | `pip install -r requirements.txt` |

---

## License

MIT
