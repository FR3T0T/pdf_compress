"""
pdf_translate — Offline translation & OCR engine.

Translates PDF text and text inside images (photos, scans) entirely
on-device:

  * Translation  — Argos Translate (CTranslate2 / OpenNMT models).  Runs
                   fully offline once the language packages are installed.
  * OCR          — Tesseract (via pytesseract) for pulling text out of
                   images and scanned pages, with per-language trained data.
  * Detection    — langdetect for source-language auto-detection.

Nothing here makes a network request.  The language models and OCR data
are provisioned separately and explicitly by ``setup_translation.py`` (the
one online step); after that, everything runs locally.

The module is import-safe: heavy optional libraries (argostranslate,
pytesseract, langdetect, PyMuPDF, python-docx) are imported lazily so the
rest of the toolkit keeps working even when translation isn't provisioned
yet.  Functions that need a missing piece raise :class:`TranslationError`
with a clear, actionable message.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB, matches the rest of the toolkit


# ════════════════════════════════════════════════════════════════════
#  Errors
# ════════════════════════════════════════════════════════════════════

class TranslationError(Exception):
    """Base error for translation/OCR problems."""


class ModelMissingError(TranslationError):
    """A required language model or OCR pack is not installed."""


# ════════════════════════════════════════════════════════════════════
#  Supported languages
#
#  The genuine global top-10 by total speakers, plus German and Danish
#  (both explicitly requested).  Each entry maps our stable code to the
#  Argos translation code and the Tesseract OCR code.
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Language:
    code: str        # our stable id (ISO 639-1 where possible)
    name: str        # English display name
    native: str      # endonym
    argos: str       # Argos Translate language code
    tesseract: str   # Tesseract OCR language code


SUPPORTED_LANGUAGES: list[Language] = [
    Language("en", "English",            "English",     "en", "eng"),
    Language("zh", "Chinese (Mandarin)", "中文",         "zh", "chi_sim"),
    Language("hi", "Hindi",              "हिन्दी",        "hi", "hin"),
    Language("es", "Spanish",            "Español",     "es", "spa"),
    Language("ar", "Arabic",             "العربية",      "ar", "ara"),
    Language("fr", "French",             "Français",    "fr", "fra"),
    Language("bn", "Bengali",            "বাংলা",        "bn", "ben"),
    Language("pt", "Portuguese",         "Português",   "pt", "por"),
    Language("ru", "Russian",            "Русский",     "ru", "rus"),
    Language("id", "Indonesian",         "Indonesia",   "id", "ind"),
    Language("de", "German",             "Deutsch",     "de", "deu"),
    Language("da", "Danish",             "Dansk",       "da", "dan"),
]

LANG_BY_CODE: dict[str, Language] = {l.code: l for l in SUPPORTED_LANGUAGES}
_ARGOS_TO_CODE = {l.argos: l.code for l in SUPPORTED_LANGUAGES}

# langdetect emits a few codes that need normalising to ours.
_LANGDETECT_FIXUP = {
    "zh-cn": "zh", "zh-tw": "zh", "zh": "zh",
}


def supported_languages() -> list[dict]:
    """Return the supported languages as plain dicts (for the UI)."""
    return [
        {"code": l.code, "name": l.name, "native": l.native}
        for l in SUPPORTED_LANGUAGES
    ]


# ════════════════════════════════════════════════════════════════════
#  Lazy backends
# ════════════════════════════════════════════════════════════════════

def _argos():
    try:
        import argostranslate.translate as t
        import argostranslate.package as p
        return t, p
    except Exception as exc:  # not installed
        raise ModelMissingError(
            "Offline translation isn't provisioned yet. Install it with:\n"
            "    pip install argostranslate\n"
            "then run:  python setup_translation.py --install all"
        ) from exc


def _pytesseract():
    try:
        import pytesseract
        return pytesseract
    except Exception as exc:
        raise ModelMissingError(
            "OCR isn't available — pytesseract is not installed "
            "(pip install pytesseract) and Tesseract must be on the system."
        ) from exc


# ════════════════════════════════════════════════════════════════════
#  Provisioning status
# ════════════════════════════════════════════════════════════════════

def _installed_ocr_langs() -> list[str]:
    """Tesseract OCR language codes available on this machine."""
    try:
        pt = _pytesseract()
        return sorted(pt.get_languages(config=""))
    except Exception:
        return []


def _installed_argos_pairs() -> list[tuple[str, str]]:
    """Installed Argos (from_code, to_code) pairs."""
    try:
        t, p = _argos()
        return sorted((pkg.from_code, pkg.to_code)
                      for pkg in p.get_installed_packages())
    except Exception:
        return []


def translation_status() -> dict:
    """Report what is provisioned so the UI can guide the user.

    Returns target languages reachable from English (the Argos pivot) and
    which OCR packs are present — never raises.
    """
    ocr = set(_installed_ocr_langs())
    pairs = set(_installed_argos_pairs())

    langs = []
    for l in SUPPORTED_LANGUAGES:
        to_ok = l.argos == "en" or ("en", l.argos) in pairs
        from_ok = l.argos == "en" or (l.argos, "en") in pairs
        langs.append({
            "code": l.code,
            "name": l.name,
            "native": l.native,
            "ocr": l.tesseract in ocr,        # can read this language from images
            "translateTo": to_ok,             # can translate INTO this language
            "translateFrom": from_ok,         # can translate FROM this language
        })

    try:
        import argostranslate  # noqa: F401
        argos_available = True
    except Exception:
        argos_available = False

    return {
        "argosAvailable": argos_available,
        "ocrAvailable": bool(ocr),
        "ocrLangs": sorted(ocr),
        "argosPairs": [f"{a}->{b}" for a, b in sorted(pairs)],
        "languages": langs,
    }


# ════════════════════════════════════════════════════════════════════
#  Language detection
# ════════════════════════════════════════════════════════════════════

def detect_language(text: str) -> Optional[str]:
    """Best-effort source-language detection → our code (or None)."""
    sample = (text or "").strip()
    if len(sample) < 3:
        return None
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0  # deterministic
        raw = detect(sample)
    except Exception:
        return None
    raw = _LANGDETECT_FIXUP.get(raw, raw)
    return raw if raw in LANG_BY_CODE else None


# ════════════════════════════════════════════════════════════════════
#  Text translation
# ════════════════════════════════════════════════════════════════════

def _resolve_source(text: str, source: str) -> str:
    if source and source != "auto":
        if source not in LANG_BY_CODE:
            raise TranslationError(f"Unsupported source language: {source}")
        return source
    detected = detect_language(text)
    if not detected:
        raise TranslationError(
            "Could not auto-detect the source language — please select it.")
    return detected


def translate_text(text: str, target: str, source: str = "auto") -> dict:
    """Translate a block of text. Returns {translated, source, target}.

    Argos pivots through English automatically when a direct model pair
    isn't installed (e.g. de→en→ru).
    """
    if not text or not text.strip():
        return {"translated": "", "source": source, "target": target}
    if target not in LANG_BY_CODE:
        raise TranslationError(f"Unsupported target language: {target}")

    src = _resolve_source(text, source)
    if src == target:
        return {"translated": text, "source": src, "target": target}

    t, _p = _argos()
    from_code = LANG_BY_CODE[src].argos
    to_code = LANG_BY_CODE[target].argos
    try:
        translated = t.translate(text, from_code, to_code)
    except Exception as exc:
        raise ModelMissingError(
            f"No offline model for {LANG_BY_CODE[src].name} → "
            f"{LANG_BY_CODE[target].name}. Install it with:\n"
            f"    python setup_translation.py --install {src} {target}"
        ) from exc
    return {"translated": translated, "source": src, "target": target}


# ════════════════════════════════════════════════════════════════════
#  OCR (image / photo text)
# ════════════════════════════════════════════════════════════════════

def ocr_image(image_path: str, source: str = "auto") -> dict:
    """Pull text out of an image with Tesseract. Returns {text, ocrLang}.

    ``source`` selects the OCR trained-data pack; "auto" tries every
    installed pack the toolkit knows about (less accurate than naming it).
    """
    if not os.path.isfile(image_path):
        raise FileNotFoundError(image_path)
    pt = _pytesseract()
    from PIL import Image

    installed = set(_installed_ocr_langs())
    if not installed:
        raise ModelMissingError(
            "No Tesseract OCR language data is installed. "
            "See setup_translation.py for the install commands.")

    if source and source != "auto":
        if source not in LANG_BY_CODE:
            raise TranslationError(f"Unsupported source language: {source}")
        ocr_code = LANG_BY_CODE[source].tesseract
        if ocr_code not in installed:
            raise ModelMissingError(
                f"OCR data for {LANG_BY_CODE[source].name} ({ocr_code}) "
                f"is not installed. See setup_translation.py.")
    else:
        # Use every known installed pack at once (Tesseract supports '+').
        known = [l.tesseract for l in SUPPORTED_LANGUAGES if l.tesseract in installed]
        ocr_code = "+".join(known) if known else "eng"

    try:
        with Image.open(image_path) as img:
            text = pt.image_to_string(img, lang=ocr_code)
    except Exception as exc:
        raise TranslationError(f"OCR failed: {exc}") from exc

    return {"text": text.strip(), "ocrLang": ocr_code}


def translate_image(image_path: str, target: str, source: str = "auto") -> dict:
    """OCR an image then translate the extracted text.

    Returns {sourceText, translatedText, source, target, ocrLang}.
    """
    ocr = ocr_image(image_path, source)
    src_text = ocr["text"]
    if not src_text:
        return {
            "sourceText": "", "translatedText": "",
            "source": source, "target": target, "ocrLang": ocr["ocrLang"],
            "note": "No text was found in the image.",
        }
    result = translate_text(src_text, target, source)
    return {
        "sourceText": src_text,
        "translatedText": result["translated"],
        "source": result["source"],
        "target": target,
        "ocrLang": ocr["ocrLang"],
    }


# ════════════════════════════════════════════════════════════════════
#  PDF translation
# ════════════════════════════════════════════════════════════════════

def _extract_pages(path: str, ocr_fallback_source: str = "auto") -> list[str]:
    """Return the text of each page. OCRs pages that have no text layer."""
    import fitz  # PyMuPDF
    pages: list[str] = []
    doc = fitz.open(path)
    try:
        for page in doc:
            txt = page.get_text("text").strip()
            if not txt:
                # Scanned page — rasterize and OCR it.
                try:
                    import tempfile
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    fd, tmp = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    pix.save(tmp)
                    try:
                        txt = ocr_image(tmp, ocr_fallback_source)["text"]
                    finally:
                        if os.path.exists(tmp):
                            os.unlink(tmp)
                except ModelMissingError:
                    raise
                except Exception as exc:
                    log.debug("page OCR fallback failed: %s", exc)
                    txt = ""
            pages.append(txt)
    finally:
        doc.close()
    return pages


def translate_pdf(input_path: str, output_path: str, target: str,
                  source: str = "auto",
                  progress: Optional[Callable[[int, int], None]] = None,
                  should_cancel: Optional[Callable[[], bool]] = None) -> dict:
    """Translate a PDF's text and write the result.

    Output format follows the ``output_path`` extension:
      * .txt  — page-delimited plain text (default, universal)
      * .docx — a Word document with a heading per page (needs python-docx)

    Plain-text/Word output is used deliberately: it renders every script
    (CJK, Cyrillic, Arabic, Devanagari) with the system's own fonts and
    avoids the heavy, error-prone job of embedding fonts for a re-laid-out
    PDF.  Returns a summary dict.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)
    size = os.path.getsize(input_path)
    if size > MAX_FILE_SIZE:
        raise TranslationError("File too large (max 2 GB).")
    if target not in LANG_BY_CODE:
        raise TranslationError(f"Unsupported target language: {target}")

    page_texts = _extract_pages(input_path, source)
    total = len(page_texts)
    translated_pages: list[str] = []
    detected_source = None

    for i, ptext in enumerate(page_texts):
        if should_cancel and should_cancel():
            raise InterruptedError("Cancelled")
        if not ptext.strip():
            translated_pages.append("")
        else:
            res = translate_text(ptext, target, source)
            detected_source = detected_source or res["source"]
            translated_pages.append(res["translated"])
        if progress:
            try:
                progress(i + 1, total)
            except Exception:
                pass

    ext = os.path.splitext(output_path)[1].lower()
    if ext == ".docx":
        _write_docx(translated_pages, output_path)
    else:
        _write_txt(translated_pages, output_path)

    return {
        "output": output_path,
        "outputSize": os.path.getsize(output_path),
        "pages": total,
        "source": detected_source or source,
        "target": target,
        "files": [output_path],
        "output_dir": os.path.dirname(os.path.abspath(output_path)),
    }


def _write_txt(pages: list[str], output_path: str) -> None:
    import tempfile
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    fd, tmp = tempfile.mkstemp(suffix=".txt", dir=out_dir)
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            for i, txt in enumerate(pages):
                fh.write(f"--- Page {i + 1} ---\n")
                fh.write(txt + "\n\n")
        os.replace(tmp, output_path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_docx(pages: list[str], output_path: str) -> None:
    try:
        from docx import Document
    except Exception as exc:
        raise TranslationError(
            "Word output needs python-docx (pip install python-docx). "
            "Use a .txt output path instead.") from exc
    doc = Document()
    for i, txt in enumerate(pages):
        doc.add_heading(f"Page {i + 1}", level=2)
        for para in (txt.split("\n") if txt else [""]):
            doc.add_paragraph(para)
    doc.save(output_path)


# ════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import json as _json
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("usage:\n"
              "  python pdf_translate.py --status\n"
              "  python pdf_translate.py --text 'Hello' --to de [--from en]\n"
              "  python pdf_translate.py --image photo.png --to en [--from de]\n"
              "  python pdf_translate.py --pdf in.pdf out.txt --to en [--from auto]")
        raise SystemExit(0)

    def _opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    if "--status" in args:
        print(_json.dumps(translation_status(), indent=2, ensure_ascii=False))
    elif "--text" in args:
        print(translate_text(_opt("--text"), _opt("--to"),
                             _opt("--from", "auto"))["translated"])
    elif "--image" in args:
        print(_json.dumps(translate_image(_opt("--image"), _opt("--to"),
                          _opt("--from", "auto")), indent=2, ensure_ascii=False))
    elif "--pdf" in args:
        i = args.index("--pdf")
        print(_json.dumps(translate_pdf(args[i + 1], args[i + 2], _opt("--to"),
                          _opt("--from", "auto")), indent=2, ensure_ascii=False))
