"""Tests for pdf_translate.py — the offline translation line router.

Centred on TRN-03: ``translate_line`` must send real words in ANY script
to the translator (not just Latin/Cyrillic) while still skipping fragments
that are purely separators/digits/punctuation. Pure logic driven by a stub
``translate_fn`` — no model, no network, Qt-free.
"""

from pdf_translate import translate_line


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
