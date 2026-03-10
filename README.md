# pdf-compress

A simple, fully offline PDF compressor. No Ghostscript, no online tools, no files leaving your machine. Just Python.

Built for compressing lecture notes, datasheets, and documents before uploading to GitHub, Claude, or anywhere else with a file size limit.

---

## Requirements

- [Python 3.8+](https://www.python.org/downloads/) — tick **"Add to PATH"** during install
- Two Python libraries (installed once):

```bash
pip install pikepdf pillow
```

---

## Usage

### Windows — Drag and Drop

Put `compress_pdf.py` and `compress_pdf.bat` in the same folder.  
Drag any PDF (or multiple PDFs) onto `compress_pdf.bat`. Done.

To change the default quality, open `compress_pdf.bat` in Notepad and edit the `QUALITY=3` line.

---

### Command Line

```bash
# Compress a single file (outputs input_compressed.pdf)
python compress_pdf.py document.pdf

# Set quality level
python compress_pdf.py document.pdf -q 1   # smallest
python compress_pdf.py document.pdf -q 5   # best quality

# Custom output name
python compress_pdf.py document.pdf -o document_small.pdf

# Batch compress multiple files
python compress_pdf.py *.pdf
python compress_pdf.py file1.pdf file2.pdf file3.pdf
```

---

## Quality Levels

| Level | DPI | JPEG Quality | Best For |
|-------|-----|--------------|----------|
| `-q 1` | 72  | 30% | Absolute smallest, rough images |
| `-q 2` | 96  | 50% | Small file, acceptable quality |
| `-q 3` | 120 | 65% | **Default** — good for lecture notes & docs |
| `-q 4` | 150 | 80% | Good quality, moderate compression |
| `-q 5` | 200 | 90% | Light compression, near-original quality |

> If compression doesn't reduce file size (e.g. the PDF is already optimised), the original is kept untouched.

---

## How It Works

The script does two things:

1. **Recompresses embedded images** at a lower JPEG quality and downscales them if they're above the target DPI threshold. This is usually where the bulk of the size is in lecture slides and scanned documents.

2. **Compresses PDF streams** using pikepdf's built-in stream compression and object stream generation, which removes redundant internal data.

Text, fonts, and vector graphics are left completely untouched — only raster images are affected.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| [pikepdf](https://pikepdf.readthedocs.io/) | Read, modify, and save PDF structure |
| [Pillow](https://python-pillow.org/) | Recompress and resize embedded images |

Install both at once:

```bash
pip install pikepdf pillow
```

---

## License

MIT — do whatever you want with it.
