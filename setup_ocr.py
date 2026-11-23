#!/usr/bin/env python3
"""
setup_ocr.py — fetch the language file Redact reads scanned pages with.

This is the one step of OCR that uses the network, and it runs only when
you run it. It fetches tessdata_fast's eng.traineddata (about 4 MB,
Apache-2.0) at the commit pinned in ocr_data_lock.json, checks its size
and SHA-256, and keeps it outside the repository (PDFTOOLKIT_OCR_DATA
names another folder). The packaged app carries the file already. An
installed Tesseract with eng.traineddata in its tessdata folder is used
without this step.

Usage
-----
    python setup_ocr.py --status     # exit code 0 when OCR is ready
    python setup_ocr.py --install
"""

from __future__ import annotations

import sys

import pdf_ocr


def _print_status() -> int:
    st = pdf_ocr.ocr_status()
    if not st["ready"]:
        print("OCR: not set up.")
        print(st["problem"] or "Run  python setup_ocr.py --install")
        return 1
    where = {"bundled": "carried by the app", "installed": "fetched by setup_ocr.py",
             "tesseract": "an installed Tesseract's"}[st["source"]]
    print("OCR: ready.")
    print(f"  Language file : {st['file']} ({where})")
    try:
        from pathlib import Path
        print(f"  SHA-256       : {pdf_ocr._file_sha256(Path(st['file']))}")
    except OSError:
        pass
    print(f"  Pinned file   : {'yes' if st['pinned'] else 'no'}")
    return 0


def _install() -> int:
    def _progress(done: int, total: int, label: str) -> None:
        print(f"\r  {label}: {done // 1024:,} of {total // 1024:,} KB", end="", flush=True)

    try:
        res = pdf_ocr.install_language_data(progress=_progress)
    except (pdf_ocr.OcrSetupError, OSError) as exc:
        print(f"\n{exc}")
        return 1
    print(f"\nInstalled {res['file']} ({res['size']:,} bytes, SHA-256 {res['sha256']}).")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if "--status" in argv:
        return _print_status()
    if "--install" in argv:
        return _install()
    print("Unrecognized arguments. Use --help.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
