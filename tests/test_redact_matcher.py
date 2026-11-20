"""Redaction completeness (#119), cluster R2: one matcher for a page's text.

``pdf_verify.PageText`` reads a page's characters with their boxes and looks
for a term in four views of them (whitespace collapsed; a line-end hyphen
dropped; the break after a line-end hyphen dropped; no whitespace at all).
``redact_pdf`` boxes what it finds, together with search_for's hits, each
widened to the whole words it touches; ``verify_redaction`` uses the same
views to confirm the term is gone.

The cases are the ones search_for gets wrong. On PyMuPDF 1.27 it finds no
term broken across a line after a hyphen, in any mode, and no letter-spaced
one; on 2.1 it finds the typesetter's hyphenation only, and its hit there
stops one letter short of the word. Every test here asserts the outcome, so
it holds on both — run the file under each.

Every fixture is built here, by hand. Nothing imported at the top is new,
so the file collects against the tree before the change and each test fails
there for its own reason; the matcher's own functions are reached through
the module, inside the tests that need them.

Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import os

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

import pdf_verify
from pdf_ops import RedactionVerificationError, redact_pdf
from pdf_verify import verify_redaction

fitz = pytest.importorskip("fitz")

PAGE_TEXT = "redaction.page_text"


# ── Builders ─────────────────────────────────────────────────────────────


def _to_unicode(pdf, extra: dict):
    """A /ToUnicode map: printable ASCII as itself, and each code of *extra*
    as the character given. How a hand-built page gets a soft hyphen, a
    no-break space or a Chinese character into its extracted text."""
    chars = "".join(f"<{code:02X}> <{ord(ch):04X}> " for code, ch in sorted(extra.items()))
    return pdf.make_stream((
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CMapName /Hand def /CMapType 2 def\n"
        "1 begincodespacerange <00> <FF> endcodespacerange\n"
        "1 beginbfrange <20> <7E> <0020> endbfrange\n"
        f"{len(extra)} beginbfchar {chars}endbfchar\n"
        "endcmap CMapName currentdict /CMap defineresource pop end end").encode())


def _pdf(path, *contents: bytes, encoding=None, to_unicode: dict | None = None) -> str:
    """One page per content stream, each with Helvetica as /F1."""
    pdf = pikepdf.Pdf.new()
    font = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)
    if encoding is not None:
        font["/Encoding"] = encoding
    if to_unicode:
        font["/ToUnicode"] = _to_unicode(pdf, to_unicode)
    font = pdf.make_indirect(font)
    for content in contents:
        pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
        page = pdf.pages[-1].obj
        page["/Resources"] = Dictionary(Font=Dictionary(F1=font))
        page["/Contents"] = pdf.make_stream(content)
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _lines(*lines: str, size: int = 12, lead: int = 16) -> bytes:
    """Lines of text, one under the other, from the top left of the page."""
    shown = f" 0 -{lead} Td ".join(f"({line}) Tj" for line in lines)
    return f"BT /F1 {size} Tf 72 720 Td {shown} ET".encode("latin-1")


def _words(path, page: int = 0) -> list:
    with fitz.open(path) as doc:
        return doc[page].get_text().split()


def _check(report, cid):
    for c in report.checks:
        if c.id == cid:
            return c
    raise AssertionError(f"check {cid!r} missing: {[c.id for c in report.checks]}")


def _redact(tmp_path, src, term, **kw):
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[term] if isinstance(term, str) else term, **kw)
    assert res.verification["verified"] is True
    return res, out


#: The ligature case: the font's code 174 is the "fi" ligature.
_LIGATURE = Dictionary(Type=Name.Encoding, BaseEncoding=Name.WinAnsiEncoding,
                       Differences=Array([174, Name("/fi")]))

#: case -> (content, term, the words left on the page, rectangles drawn,
#: font encoding). The seven of the brief; search_for alone redacts the
#: last two of them, on either version.
CASES = {
    "a number broken after its own hyphen": (
        _lines("Account number 4455-", "887 is closed."), "4455-887",
        ["Account", "number", "is", "closed."], 2, None),
    "a number broken after its last hyphen": (
        _lines("The number on file is 123-45-", "6789 as given."), "123-45-6789",
        ["The", "number", "on", "file", "is", "as", "given."], 2, None),
    "a name hyphenated by the typesetter": (
        _lines("This was signed by Dana Whit-", "field on the day."), "Dana Whitfield",
        ["This", "was", "signed", "by", "on", "the", "day."], 2, None),
    "a hyphenated word (2.1 left its last letter)": (
        _lines("This was signed by Whit-", "field on the day."), "Whitfield",
        ["This", "was", "signed", "by", "on", "the", "day."], 2, None),
    "a letter-spaced heading, spaces typed": (
        _lines("C O N F I D E N T I A L", "body text follows", size=14, lead=30), "CONFIDENTIAL",
        ["body", "text", "follows"], 1, None),
    "a letter-spaced heading, by character spacing": (
        b"BT /F1 18 Tf 9 Tc 72 720 Td (CONFIDENTIAL) Tj 0 Tc 0 -30 Td (body text follows) Tj ET",
        "CONFIDENTIAL", ["body", "text", "follows"], 1, None),
    "a letter-spaced heading, glyph by glyph": (
        b"BT /F1 18 Tf 72 720 Td [(C) -700 (O) -700 (N) -700 (F) -700 (I) -700 (D) -700 (E) -700 "
        b"(N) -700 (T) -700 (I) -700 (A) -700 (L)] TJ 0 -30 Td (body text follows) Tj ET",
        "CONFIDENTIAL", ["body", "text", "follows"], 1, None),
    "a hyphenated name, the next line indented with spaces": (
        _lines("    This was signed by Dana Whit-  ", "    field on the day."), "Dana Whitfield",
        ["This", "was", "signed", "by", "on", "the", "day."], 2, None),
    "a term split across two lines": (
        _lines("This was signed by Dana", "Whitfield on the day."), "Dana Whitfield",
        ["This", "was", "signed", "by", "on", "the", "day."], 2, None),
    "a ligature": (
        _lines("This was signed by Dana Whit\256eld on the day."), "Whitfield",
        ["This", "was", "signed", "by", "Dana", "on", "the", "day."], 1, _LIGATURE),
}


# ── The cases of the brief ───────────────────────────────────────────────


@pytest.mark.integration
class TestTheCasesSearchForGetsWrong:
    @pytest.mark.parametrize("case", list(CASES))
    def test_the_term_is_redacted_and_nothing_else(self, tmp_path, case):
        content, term, left, boxes, encoding = CASES[case]
        src = _pdf(tmp_path / "src.pdf", content, encoding=encoding)
        res, out = _redact(tmp_path, src, term)
        assert _words(out) == left
        assert res.redaction_count == boxes and res.pages_affected == 1

    @pytest.mark.parametrize("case", list(CASES))
    def test_the_verifier_finds_it_in_a_file_that_still_has_it(self, tmp_path, case):
        """The same views, on the other side: left unredacted, each of
        these fails the page-text check. Before, the hyphenated numbers and
        the letter-spaced headings passed it."""
        content, term, _left, _boxes, encoding = CASES[case]
        src = _pdf(tmp_path / "src.pdf", content, encoding=encoding)
        check = _check(verify_redaction(src, [term]), PAGE_TEXT)
        assert check.passed is False and check.pages == [1]
        assert f"term '{term}' is still extractable from page text on page 1" in check.evidence

    def test_a_page_without_the_term_passes(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _lines("This was signed by Dana Whit-", "more on the day."))
        assert _check(verify_redaction(src, ["Dana Whitfield"]), PAGE_TEXT).passed is True


# ── Folding ──────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestFolding:
    """Both sides are folded the same way before they are compared."""

    @pytest.mark.parametrize("text, folded", [
        ("\ufb01eld", "field"),                       # a ligature is its letters
        ("\uff24\uff41\uff4e\uff41", "dana"),         # full-width letters
        ("Dana\u00a0Whitfield", "dana whitfield"),    # a no-break space is a space
        ("Whit\u00adfield", "whitfield"),             # a soft hyphen takes no room
        ("Whit\u200bfield\u200d", "whitfield"),       # nor a zero-width space or joiner
        ("123\u201045\u20116789", "123-45-6789"),     # the hyphen characters are a hyphen
        ("Stra\u00dfe", "strasse"),                   # case folding, not lower-casing
        ("e\u0301", "\u00e9"),                        # a letter and its accent are one letter
    ], ids=["ligature", "full width", "no-break space", "soft hyphen", "zero width", "hyphens",
            "sharp s", "combining accent"])
    def test_what_is_the_same_text(self, text, folded):
        assert pdf_verify._fold_text(text, False) == folded

    def test_case_is_kept_for_a_case_sensitive_search(self):
        assert pdf_verify._fold_text("Dana\u00a0Whit\ufb01eld", True) == "Dana Whitfield"

    @pytest.mark.parametrize("shown, why", [
        ("Signed by Dana\001Whitfield today", "a no-break space"),
        ("Signed by Dana Whit\002field today", "a soft hyphen inside the word"),
    ])
    def test_a_no_break_space_and_a_soft_hyphen_on_the_page(self, tmp_path, shown, why):
        src = _pdf(tmp_path / "src.pdf", _lines(shown), to_unicode={1: "\u00a0", 2: "\u00ad"})
        with fitz.open(src) as doc:                     # the fixture is what it claims
            assert ("\u00a0" in doc[0].get_text()) != ("\u00ad" in doc[0].get_text())
        _res, out = _redact(tmp_path, src, "Dana Whitfield")
        assert _words(out) == ["Signed", "by", "today"]

    def test_a_soft_hyphen_that_ends_a_line(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _lines("Signed by Dana Whit\002", "field today"),
                   to_unicode={2: "\u00ad"})
        _res, out = _redact(tmp_path, src, "Whitfield")
        assert _words(out) == ["Signed", "by", "Dana", "today"]

    def test_a_case_sensitive_search_takes_the_one_in_that_case(self, tmp_path):
        """Lines set close, as text is: the rectangle of a hit reaches into
        the line below. Asked what was "under" the upper-case hit, the page
        used to answer with the next line too, where the case is right."""
        src = _pdf(tmp_path / "src.pdf", _lines(
            "one dana whitfield", "two DANA WHITFIELD", "three Dana Whitfield", "four Dana",
            "Whitfield"))
        res, out = _redact(tmp_path, src, "Dana Whitfield", case_sensitive=True)
        assert _words(out) == ["one", "dana", "whitfield", "two", "DANA", "WHITFIELD", "three",
                               "four"]
        assert res.redaction_count == 3                 # one line, and one across two

    def test_without_it_every_case_goes(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _lines(
            "one dana whitfield", "two DANA WHITFIELD", "three Dana Whitfield"))
        res, out = _redact(tmp_path, src, "Dana Whitfield")
        assert _words(out) == ["one", "two", "three"] and res.redaction_count == 3


# ── Whole words ──────────────────────────────────────────────────────────


@pytest.mark.integration
class TestWholeWords:
    """A hit is widened to the whole words it touches: what is left of a
    word says what was taken out of it."""

    def test_part_of_a_word_takes_the_word(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _lines("Signed by Dana Whitfield, today."))
        res, out = _redact(tmp_path, src, "itfie")
        assert _words(out) == ["Signed", "by", "Dana", "today."]
        assert res.redaction_count == 1

    def test_an_address_goes_whole(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _lines("Write to dana.whitfield@example.com soon."))
        _res, out = _redact(tmp_path, src, "whitfield")
        assert _words(out) == ["Write", "to", "soon."]

    def test_two_hits_in_one_word_are_one_rectangle(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _lines("code ABAB-ABAB here"))
        res, out = _redact(tmp_path, src, "AB")
        assert _words(out) == ["code", "here"] and res.redaction_count == 1

    def test_writing_without_spaces_is_not_widened(self, tmp_path):
        """Eight Chinese characters in a row are not one word. The term is
        the second and third; it is there twice."""
        chars = dict(zip(range(0x61, 0x69), "\u6771\u4eac\u90fd\u5927\u962a\u5e9c\u4eac\u90fd",
                         strict=True))
        src = _pdf(tmp_path / "src.pdf", _lines("abcdefgh"), to_unicode=chars)
        res, out = _redact(tmp_path, src, "\u4eac\u90fd")
        assert "".join(_words(out)) == "\u6771\u5927\u962a\u5e9c"
        assert res.redaction_count == 2

    def test_the_count_is_of_rectangles_one_for_each_line_of_a_hit(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _lines(
            "MARK here MARK there MARK", "everywhere, and Dana", "Whitfield too"))
        res, _out = _redact(tmp_path, src, ["MARK", "Dana Whitfield"])
        assert res.redaction_count == 5


# ── The view with no whitespace at all ───────────────────────────────────


@pytest.mark.integration
class TestWithNoWhitespaceAtAll:
    """Accepted only as whole words run together: the match starts at the
    first letter or digit of a word and ends at the last of one."""

    @pytest.mark.parametrize("shown, term, left", [
        (["Card 4111 1111 1111 1111 on file."], "4111111111111111", ["Card", "on", "file."]),
        (["Card 4111111111111111 on file."], "4111 1111 1111 1111", ["Card", "on", "file."]),
        (["Signed (DanaWhitfield), today."], "Dana Whitfield", ["Signed", "today."]),
        (["C O N F I D E N T I A L:", "body text"], "confidential", ["body", "text"]),
        (["Write to dana.whitfield@exam", "ple.com soon."], "dana.whitfield@example.com",
         ["Write", "to", "soon."]),
    ], ids=["a number printed in groups", "a term typed in groups", "two words printed as one",
            "a heading with a colon after it", "an address broken with no hyphen"])
    def test_whole_words_run_together_are_found(self, tmp_path, shown, term, left):
        src = _pdf(tmp_path / "src.pdf", _lines(*shown))
        _res, out = _redact(tmp_path, src, term)
        assert _words(out) == left

    @pytest.mark.parametrize("shown, term", [
        ("values 1.0000 0.2000 end", "00000"),       # a real file's table of numbers
        ("the order 12 345 67 stands", "2345"),
        ("station ahead", "ionah"),
    ], ids=["digits across two numbers", "the middle of a grouped number",
            "the end of one word and the start of the next"])
    def test_the_middle_of_a_word_is_not(self, tmp_path, shown, term):
        src = _pdf(tmp_path / "src.pdf", _lines(shown))
        with pytest.raises(ValueError, match="No matching content"):
            redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[term])
        assert _check(verify_redaction(src, [term]), PAGE_TEXT).passed is True

    def test_two_words_that_spell_the_term_between_them_are_taken(self, tmp_path):
        """The cost of this view, on the record: "now here" is redacted for
        the term "nowhere". Nothing on the page tells a wide gap in one
        word from the gap between two."""
        src = _pdf(tmp_path / "src.pdf", _lines("it is now here at last"))
        res, out = _redact(tmp_path, src, "nowhere")
        assert _words(out) == ["it", "is", "at", "last"] and res.redaction_count == 1


# ── search_for beside the matcher ────────────────────────────────────────


@pytest.mark.integration
class TestSearchForBesideTheMatcher:
    def test_text_a_clip_hides_is_still_text(self, tmp_path):
        """A stamp cut off by its clipping path shows "APPRO". The whole
        word is in the content stream, and is what any other program reads
        from the file."""
        src = _pdf(tmp_path / "src.pdf",
                   b"q 60 700 50 40 re W n BT /F1 12 Tf 72 720 Td (APPROVED) Tj ET Q "
                   b"BT /F1 12 Tf 72 680 Td (body text) Tj ET")
        assert _words(src) == ["APPRO", "body", "text"]     # the fixture is what it claims
        assert _check(verify_redaction(src, ["APPROVED"]), PAGE_TEXT).passed is False
        _res, out = _redact(tmp_path, src, "APPROVED")
        assert _words(out) == ["body", "text"]
        with pikepdf.open(out) as pdf:
            assert b"APPROVED" not in pdf.pages[0].obj["/Contents"].read_bytes()

    def test_two_hits_side_by_side_are_two_rectangles(self, tmp_path):
        """search_for gives one rectangle for both; the matcher finds each."""
        src = _pdf(tmp_path / "src.pdf", _lines("Kappa Kappa Gamma"))
        res, out = _redact(tmp_path, src, "Kappa")
        assert _words(out) == ["Gamma"] and res.redaction_count == 2

    def test_a_hit_in_slanted_text_is_one_rectangle(self, tmp_path):
        """The rectangle round a slanted hit takes in whatever is printed
        inside it (that is as it was). What it takes in is not the hit, and
        is not widened into rectangles of its own: the far ends of the line
        that crosses the name are still there."""
        src = _pdf(tmp_path / "src.pdf",
                   b"BT /F1 24 Tf 0.7071 0.7071 -0.7071 0.7071 200 300 Tm (Dana Whitfield) Tj ET "
                   b"BT /F1 9 Tf 150 380 Td (some other words printed across the slanted name "
                   b"and well past it on both sides) Tj ET")
        res, out = _redact(tmp_path, src, "Whitfield")
        assert res.redaction_count == 1
        left = _words(out)
        assert "Whitfield" not in left and left[-3:] == ["on", "both", "sides"]
        assert "some" in left

    def test_every_rectangle_is_a_rectangle_fitz_can_use(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _lines("Signed by Dana Whit-", "field, today."))
        with fitz.open(src) as doc:
            page = doc[0]
            boxes = pdf_verify.term_boxes(page, ["Whitfield"])
            assert len(boxes) == 2 and all(isinstance(b, fitz.Rect) and not b.is_empty
                                           for b in boxes)
            # The second covers "field," to its comma: the whole word.
            (whole,) = [w for w in page.get_text("words") if w[4] == "field,"]
            assert abs(boxes[1].x0 - whole[0]) < 0.01 and abs(boxes[1].x1 - whole[2]) < 0.01
            assert pdf_verify.term_boxes(page, ["absent"]) == []
            assert pdf_verify.term_boxes(page, []) == []


# ── What removal leaves behind ───────────────────────────────────────────


@pytest.mark.integration
class TestWhatRemovalLeavesBehind:
    def test_neighbours_that_now_read_as_the_term_are_taken_too(self, tmp_path):
        """With the middle two words gone, the two that are left read "New
        York". The verifier has always said so, and the run was refused.
        The page is searched again, until it has nothing to find."""
        src = _pdf(tmp_path / "src.pdf", _lines("in New New York York today"))
        res, out = _redact(tmp_path, src, "New York")
        assert _words(out) == ["in", "today"]
        assert res.redaction_count >= 2                 # the first, and what it left

    def test_what_cannot_be_removed_is_still_refused(self, tmp_path, monkeypatch):
        """A rectangle that removes nothing is not drawn again and again:
        the search stops, and the verifier refuses."""
        src = _pdf(tmp_path / "src.pdf", _lines("Signed by Dana Whitfield today"))
        applied = []
        monkeypatch.setattr(fitz.Page, "apply_redactions",
                            lambda self, *a, **kw: applied.append(1) or True)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=["Whitfield"])
        assert len(applied) == 1 and ei.value.flatten_pages == [1]
        assert not os.path.exists(out)


# ── A file MuPDF repairs while it saves ──────────────────────────────────


def _damaged_on_save(path, *lines: str) -> str:
    """A page of *lines*, in a file with one wrong entry in its cross-
    reference table: an object only the catalog's private /Stash key leads
    to is filed at another object's address. Nothing reads it to open the
    file, show the page or redact it. Writing the file out reads it."""
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[-1].obj
    page["/Resources"] = Dictionary(Font=Dictionary(F1=pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))))
    page["/Contents"] = pdf.make_stream(_lines(*lines))
    pdf.Root["/Stash"] = pdf.make_indirect(Dictionary(Note=pikepdf.String("kept on the side")))
    pdf.save(str(path), object_stream_mode=pikepdf.ObjectStreamMode.disable)
    pdf.close()
    with open(path, "rb") as f:
        raw = f.read()
    number = int(raw.split(b"/Stash ")[1].split(b" ")[0])
    first = raw.index(b"xref\n0 ") + len(b"xref\n0 ")
    first = raw.index(b"\n", first) + 1                 # the entry of object 0; 20 bytes each
    mine, other = first + 20 * number, first + 20 * 1
    assert raw[mine + 17:mine + 18] == b"n"             # an object in use
    with open(path, "wb") as f:
        f.write(raw[:mine] + raw[other:other + 10] + raw[mine + 10:])
    return str(path)


def _dark_share(path, rect, page: int = 0) -> float:
    """How much of *rect* is dark when the page is drawn: 1.0 under a
    redaction's black box, a fraction of that under a line of text."""
    with fitz.open(path) as doc:
        pix = doc[page].get_pixmap(clip=fitz.Rect(rect), colorspace=fitz.csGRAY, alpha=False)
    return sum(1 for value in pix.samples if value < 128) / max(1, len(pix.samples))


@pytest.mark.integration
class TestAFileRepairedWhileItIsSaved:
    """MuPDF checks an object when it comes to read it. Finding one damaged,
    it goes back to the file and rebuilds everything from there — and what
    had been changed in memory, the redaction, is gone without a word. A
    real file's redaction was undone that way as it was being written, and
    the page offered for flattening would have been drawn from the
    unredacted file."""

    def _word(self, src, word: str = "Whitfield") -> tuple:
        """The rectangle of *word* on the fixture's page."""
        with fitz.open(src) as doc:
            (found,) = [w for w in doc[0].get_text("words") if w[4] == word]
        return tuple(found[:4])

    def test_the_fixture_is_damaged_where_only_saving_looks(self, tmp_path):
        src = _damaged_on_save(tmp_path / "src.pdf", "Signed by Dana Whitfield today")
        with fitz.open(src) as doc:
            assert doc.is_repaired is False and doc[0].get_text().split()[3] == "Whitfield"
            doc.tobytes(garbage=4)
            assert doc.is_repaired is True

    def test_a_term_is_still_redacted(self, tmp_path):
        src = _damaged_on_save(tmp_path / "src.pdf", "Signed by Dana Whitfield today")
        res, out = _redact(tmp_path, src, "Whitfield")
        assert _words(out) == ["Signed", "by", "Dana", "today"]
        assert res.redaction_count == 1 and res.pages_affected == 1
        assert _dark_share(out, self._word(src)) > 0.95

    def test_so_is_a_box_drawn_by_hand(self, tmp_path):
        src = _damaged_on_save(tmp_path / "src.pdf", "Signed by Dana Whitfield today")
        out = str(tmp_path / "out.pdf")
        box = dict(zip(("x0", "y0", "x1", "y1"), self._word(src), strict=True))
        res = redact_pdf(src, out, rects=[{"page": 0, **box}])
        assert res.verification["verified"] is True
        assert _words(out) == ["Signed", "by", "Dana", "today"]

    def test_a_flattened_page_is_drawn_from_the_redacted_file(self, tmp_path):
        """The page as an image has no text for a check to find. What it
        shows is the black box, not the word."""
        src = _damaged_on_save(tmp_path / "src.pdf", "Signed by Dana Whitfield today")
        word = self._word(src)
        assert _dark_share(src, word) < 0.5                 # the word itself: mostly paper
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"], flatten_pages=[1])
        assert res.verification["verified"] is True and res.flattened_pages == [1]
        assert _words(out) == [] and _dark_share(out, word) > 0.95

    def test_a_sound_file_is_not_done_twice(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "src.pdf", _lines("Signed by Dana Whitfield today"))
        saves = []
        real = fitz.Document.save
        monkeypatch.setattr(fitz.Document, "save",
                            lambda self, *a, **kw: saves.append(1) or real(self, *a, **kw))
        _redact(tmp_path, src, "Whitfield")
        assert len(saves) == 1


# ── The verifier ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestTheVerifierNeverPassesWhatItCouldNotRead:
    def test_characters_that_cannot_be_read_fail_the_page(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "src.pdf", _lines("nothing here"), _lines("nor here"))

        def broken(self, page, case_sensitive=False):
            raise RuntimeError("no characters today")

        monkeypatch.setattr(pdf_verify.PageText, "__init__", broken)
        report = verify_redaction(src, ["Whitfield"])
        check = _check(report, PAGE_TEXT)
        assert check.passed is False and check.pages == [1, 2]
        assert check.evidence[0] == ("page text on page 1 could not be read character by "
                                     "character for verification (RuntimeError)")
        assert report.flatten_target_pages() == [1, 2]

    def test_each_page_is_read_once_however_many_terms(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "src.pdf", _lines("nothing here"), _lines("nor here"))
        built = []
        real = pdf_verify.PageText.__init__
        monkeypatch.setattr(pdf_verify.PageText, "__init__",
                            lambda self, page, cs=False: built.append(1) or real(self, page, cs))
        assert verify_redaction(src, ["one", "two", "three"]).verified is True
        assert len(built) == 2
