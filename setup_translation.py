#!/usr/bin/env python3
"""
setup_translation.py — provision the offline translation & OCR models.

This is the ONE step that uses the network.  It downloads the Argos
Translate language packages (and tells you how to add Tesseract OCR data)
so that, afterwards, the PDF Toolkit can translate completely offline.
The app's network kill-switch is unaffected: it sandboxes the embedded
web UI, while this script is a separate, explicit, user-run provisioning
tool.

Usage
-----
    python setup_translation.py --status
    python setup_translation.py --list
    python setup_translation.py --install all
    python setup_translation.py --install de ru da        # specific languages

Each requested language X is wired to/from English (en<->X); Argos pivots
through English, so installing a handful of languages lets you translate
between any of them.
"""

from __future__ import annotations

import sys

try:
    from pdf_translate import SUPPORTED_LANGUAGES, LANG_BY_CODE, translation_status
except Exception as exc:  # pragma: no cover
    print(f"Could not import pdf_translate: {exc}")
    sys.exit(1)


# OS-specific hints for Tesseract OCR data (cannot be pip-installed).
_OCR_HINTS = """\
Tesseract OCR language data (needed to read text from images/scans) is a
system package, installed separately per OS:

  Debian/Ubuntu : sudo apt install tesseract-ocr tesseract-ocr-{pack}
  macOS (brew)  : brew install tesseract tesseract-lang
  Windows       : install the UB-Mannheim Tesseract build and tick the
                  desired languages, or drop the *.traineddata files into
                  the tessdata folder.

Tesseract pack codes for the supported languages:
"""


def _print_status() -> None:
    st = translation_status()
    print(f"Argos translation library installed : {st['argosAvailable']}")
    print(f"Tesseract OCR available             : {st['ocrAvailable']}")
    print(f"OCR language packs                  : {', '.join(st['ocrLangs']) or '(none)'}")
    print(f"Installed translation pairs         : {', '.join(st['argosPairs']) or '(none)'}")
    print()
    print(f"{'Language':22} {'OCR':>4} {'→ to':>5} {'from →':>7}")
    print("-" * 42)
    for l in st["languages"]:
        print(f"{l['name']:22} {('yes' if l['ocr'] else '—'):>4} "
              f"{('yes' if l['translateTo'] else '—'):>5} "
              f"{('yes' if l['translateFrom'] else '—'):>7}")


def _print_list() -> None:
    print("Supported languages (code — name — Tesseract OCR pack):\n")
    for l in SUPPORTED_LANGUAGES:
        print(f"  {l.code:4} {l.name:22} OCR pack: {l.tesseract}")
    print("\n" + _OCR_HINTS)
    for l in SUPPORTED_LANGUAGES:
        print(f"  {l.name:22} {l.tesseract}")


def _install(codes: list[str]) -> int:
    try:
        import argostranslate.package as package
    except Exception:
        print("argostranslate is not installed. Run:\n    pip install argostranslate")
        return 1

    # Expand 'all'
    if "all" in codes:
        codes = [l.code for l in SUPPORTED_LANGUAGES]

    # Validate
    unknown = [c for c in codes if c not in LANG_BY_CODE]
    if unknown:
        print(f"Unknown language code(s): {', '.join(unknown)}")
        print("Run  python setup_translation.py --list  to see valid codes.")
        return 1

    # Build the set of en<->X pairs needed (skip English itself).
    pairs: set[tuple[str, str]] = set()
    for c in codes:
        argos = LANG_BY_CODE[c].argos
        if argos == "en":
            continue
        pairs.add(("en", argos))
        pairs.add((argos, "en"))

    if not pairs:
        print("Nothing to install (English needs no translation model).")
        return 0

    print("Updating Argos package index (network)…")
    try:
        package.update_package_index()
        available = package.get_available_packages()
    except Exception as exc:
        print(f"Could not reach the Argos package index: {exc}")
        return 1

    ok, failed = 0, 0
    for from_code, to_code in sorted(pairs):
        match = next((p for p in available
                      if p.from_code == from_code and p.to_code == to_code), None)
        if match is None:
            print(f"  [skip] no package published for {from_code}->{to_code}")
            failed += 1
            continue
        try:
            print(f"  [get ] {from_code}->{to_code} …", end=" ", flush=True)
            path = match.download()
            try:
                match.install()             # newer Argos
            except Exception:
                package.install_from_path(path)  # older Argos
            print("installed")
            ok += 1
        except Exception as exc:
            print(f"failed ({exc})")
            failed += 1

    print(f"\nDone. {ok} package(s) installed, {failed} skipped/failed.")
    print("Translation now runs fully offline. For reading text from images, "
          "make sure the matching Tesseract OCR packs are installed "
          "(python setup_translation.py --list).")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if "--status" in argv:
        _print_status(); return 0
    if "--list" in argv:
        _print_list(); return 0
    if "--install" in argv:
        codes = argv[argv.index("--install") + 1:]
        if not codes:
            print("Specify language codes, e.g.  --install de ru da   (or  --install all)")
            return 1
        return _install(codes)
    print("Unrecognized arguments. Use --help.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
