"""Tests for pdf_translate.py — the offline translation line router.

Covers TRN-03 (``translate_line`` must send real words in ANY script to the
translator, not just Latin/Cyrillic, while still skipping pure
separators/digits/punctuation) and TRN-01 (PDF→PDF translation must not
abort the whole document when one block fails language auto-detection).
The TRN-03 tests are pure logic driven by a stub ``translate_fn``; the
TRN-01 test needs PyMuPDF + a real PDF and is gated + monkeypatched so it
runs offline and deterministically.
"""

import os

import pytest

import pdf_translate
from pdf_translate import _translate_pdf_to_pdf, translate_line

try:
    import fitz  # noqa: F401  (PyMuPDF — required by _translate_pdf_to_pdf)
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False


class _Recorder:
    """Stub translate_fn: records every fragment it's asked to translate and
    returns it with a marker, so a caller can tell a real translation from a
    verbatim passthrough."""

    def __init__(self):
        self.calls = []

    def __call__(self, s: str) -> str:
        self.calls.append(s)
        return f"[T]{s}"


class TestTranslateLineScriptGate:
    # -- Non-Latin/Cyrillic scripts must now reach the translator (TRN-03) --

    def test_chinese_is_translated(self):
        rec = _Recorder()
        out = translate_line("你好世界", rec)
        assert rec.calls == ["你好世界"]
        assert out == "[T]你好世界"

    def test_arabic_is_translated(self):
        rec = _Recorder()
        out = translate_line("مرحبا", rec)
        assert rec.calls == ["مرحبا"]
        assert out == "[T]مرحبا"

    def test_hindi_is_translated(self):
        rec = _Recorder()
        out = translate_line("नमस्ते", rec)
        assert rec.calls == ["नमस्ते"]
        assert out == "[T]नमस्ते"

    def test_bengali_is_translated(self):
        rec = _Recorder()
        out = translate_line("নমস্কার", rec)
        assert rec.calls == ["নমস্কার"]
        assert out == "[T]নমস্কার"

    def test_latin_still_translated(self):
        # No regression for the scripts that already worked.
        rec = _Recorder()
        out = translate_line("hello", rec)
        assert rec.calls == ["hello"]
        assert out == "[T]hello"

    def test_mixed_letters_and_digits_translated(self):
        rec = _Recorder()
        out = translate_line("abc123", rec)
        assert rec.calls == ["abc123"]
        assert out == "[T]abc123"

    # -- Original intent preserved: letter-free fragments stay skipped -----

    def test_pure_punctuation_skipped(self):
        for frag in ("---", "!!!"):
            rec = _Recorder()
            out = translate_line(frag, rec)
            assert rec.calls == []       # translator never invoked
            assert out == frag           # returned verbatim

    def test_pure_number_skipped(self):
        for frag in ("2024", "123"):
            rec = _Recorder()
            out = translate_line(frag, rec)
            assert rec.calls == []
            assert out == frag


# ═══════════════════════════════════════════════════════════════════
#  PDF→PDF block resilience (TRN-01)
# ═══════════════════════════════════════════════════════════════════


def _two_block_pdf(path) -> str:
    """One-page PDF with two separate text blocks: a normal sentence and a
    bare number (the kind that fails auto-detection)."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world this is a full sentence here")
    page.insert_text((72, 400), "2024")   # far apart → its own block
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestTranslatePdfBlockResilience:
    def test_undetectable_block_does_not_abort_document(self, tmp_path, monkeypatch):
        # The regression: a block that fails detect_language (a bare number,
        # under source='auto') raised out of translate_text and aborted the
        # whole document, so out.save() was never reached. Monkeypatch
        # translate_text to raise on the number and translate the sentence,
        # then assert the document still saves and both blocks survive.
        src = _two_block_pdf(tmp_path / "in.pdf")
        out = str(tmp_path / "out.pdf")

        def _stub(text, target, source, protect_terms=None):
            if "2024" in text:
                raise pdf_translate.TranslationError("undetectable short block")
            return {"translated": "XLATED", "source": "en", "target": target}

        monkeypatch.setattr(pdf_translate, "translate_text", _stub)

        result = _translate_pdf_to_pdf(src, out, target="es", source="auto",
                                       progress=None, should_cancel=None)

        # Core regression: the document completed and an output file exists.
        assert os.path.isfile(out)
        assert result["output"] == out
        assert result["pages"] == 1

        with fitz.open(out) as doc:
            combined = "".join(page.get_text() for page in doc)
        assert "XLATED" in combined        # sentence block was translated
        assert "2024" in combined          # undetectable block preserved, not dropped
