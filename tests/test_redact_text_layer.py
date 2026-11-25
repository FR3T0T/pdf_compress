"""Redact: the pictures under the search's boxes, and pages with an OCR text
layer (#143, #140).

  * A search's box blanks the picture under it (it did before this, in
    every shape of picture tried); redaction.search_image now checks it, and
    fails when the blanking is skipped.
  * A searchable scan's layer can have a word wrong, or in the wrong place:
    its words smaller than the picture's and shifted. The search's box over
    the layer's word then covers only part of the word in the picture. With
    OCR ticked such a page is read too, and a place OCR finds that the
    search does not have covered is blacked out, even where it touches the
    search's box (pdf_ops._left_to_the_search), and counted once.
  * A hidden layer over a stencil-mask picture (a black-and-white scan, as
    JBIG2 and CCITT are) is a text layer too.

Every page is built here. Whether the word in the picture is gone is judged
without the code under test: the same picture with only that word drawn
says where its ink is. Qt-free; a test that reads pictures needs the OCR
language file (the tessdata fixture).
"""

import dataclasses
import io
import zlib

import numpy as np
import pikepdf
import pytest
from pikepdf import Dictionary, Name

import pdf_ocr
import pdf_ops
import pdf_verify
from pdf_ops import RedactionVerificationError, redact_pdf

fitz = pytest.importorskip("fitz")

TERM = "SECRET99"
# B: the words of the picture, and the layer's, which is smaller and lower.
LINES = ["Statement for the month of March", f"Account {TERM} is overdue now",
         "Please pay the balance by Friday", "Thank you for banking with us"]
PAGE = (600, 400)


def _picture(lines, *, only=None, size=PAGE, at=(30, 96), font=20, gap=30, zoom=2):
    """A grey picture of *lines* drawn on a page of *size*, *zoom* pixels to
    the point: every word, or only those in *only*, each where it stands."""
    doc = fitz.open()
    page = doc.new_page(width=size[0], height=size[1])
    for i, line in enumerate(lines):
        x = at[0]
        for word in line.split(" "):
            if only is None or word in only:
                page.insert_text((x, at[1] + i * gap), word, fontname="helv", fontsize=font)
            x += fitz.get_text_length(word + " ", fontname="helv", fontsize=font)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    doc.close()
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()


def _flate(pdf, grey: np.ndarray, *, bits=8, stencil=False):
    h, w = grey.shape
    if bits == 1:
        packed = np.packbits(grey > 128, axis=1)
        extra = {"ImageMask": True} if stencil else {"ColorSpace": Name.DeviceGray}
        return pdf.make_stream(zlib.compress(packed.tobytes()), Type=Name.XObject,
                               Subtype=Name.Image, Width=w, Height=h, BitsPerComponent=1,
                               Filter=Name.FlateDecode, **extra)
    return pdf.make_stream(zlib.compress(grey.tobytes()), Type=Name.XObject, Subtype=Name.Image,
                           Width=w, Height=h, ColorSpace=Name.DeviceGray, BitsPerComponent=8,
                           Filter=Name.FlateDecode)


def _page(path, content: bytes, *, xobjects=None, size=PAGE):
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, *size])))
    page = pdf.pages[-1].obj
    page["/Resources"] = Dictionary(Font=Dictionary(F1=font),
                                    XObject=Dictionary(xobjects(pdf) if xobjects else {}))
    page["/Contents"] = pdf.make_stream(content)
    pdf.save(str(path))
    return str(path)


def _hidden(lines, *, x=34, top=72, gap=60, font=15, height=PAGE[1]) -> bytes:
    """*lines* in the invisible mode, the way an OCR layer is drawn."""
    return "".join(f"\nBT 3 Tr /F1 {font} Tf {x} {height - (top + gap * i)} Td ({line}) Tj ET"
                   for i, line in enumerate(lines)).encode()


def _sloppy(tmp_path, *, stencil=False):
    """B: a picture of LINES filling the page, and a hidden layer of the same
    lines in smaller type, shifted: the search's box over the layer's word
    lies over part of the word in the picture only."""
    grey = _picture(LINES)
    draw = b"q 600 0 0 400 0 0 cm /Im Do Q"
    if stencil:
        draw = b"q 0 0 0 rg 600 0 0 400 0 0 cm /Im Do Q"
    return _page(tmp_path / "sloppy.pdf", draw + _hidden(LINES),
                 xobjects=lambda pdf: {"/Im": _flate(pdf, grey, bits=1 if stencil else 8,
                                                     stencil=stencil)})


def _ink_left(out) -> tuple[int, int]:
    """(dark pixels of the term's ink still dark in the picture of *out*,
    dark pixels of its ink): read from the picture's own data."""
    ink = _picture(LINES, only={TERM}) < 128
    with fitz.open(out) as doc:
        pix = fitz.Pixmap(doc, doc[0].get_images()[0][0])
        if pix.n != 1:
            pix = fitz.Pixmap(fitz.csGRAY, pix)
        data = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    return int((data[ink] < 128).sum()), int(ink.sum())


def _note(res, kind):
    return next((n for n in res.notes if n["kind"] == kind), None)


def _lines(res) -> list:
    """The result's notes in words, without the one PyMuPDF 2.x adds."""
    plain = dataclasses.replace(res, notes=[n for n in res.notes if n["kind"] != "text_may_move"])
    return pdf_ops.redaction_summary(plain)["notes"]


def _check(res, cid):
    return next(c for c in res.verification["checks"] if c["id"] == cid)


# ── B: the layer has the word in the wrong place ────────────────────────

def test_with_ocr_the_word_in_the_picture_is_gone(tmp_path, tessdata):
    """The search's box covers part of the word in the picture; OCR finds
    all of it, touching that box, and its place is blacked out too."""
    src = _sloppy(tmp_path)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    left, ink = _ink_left(out)
    assert ink > 500 and left == 0
    assert _check(res, "redaction.search_image")["passed"]
    assert _check(res, "redaction.ocr_outside")["passed"]


def test_without_ocr_part_of_it_stays_and_the_result_says_so(tmp_path):
    src = _sloppy(tmp_path)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM])
    assert res.verification["verified"] is True
    left, ink = _ink_left(out)
    assert 0 < left < ink                       # the search's box took part of it
    assert _note(res, "text_layer") == {"kind": "text_layer", "pages": [1], "hits": {1: 1},
                                        "ocr_asked": False}
    (line,) = _lines(res)
    assert line == ("Page 1 is a scan with a hidden text layer. The search read that layer and "
                    "found 1 match there. If the layer misread a word, or put it in the wrong "
                    "place, that word is still in the picture. Tick “Also read scanned pages "
                    "with OCR” to read the picture too.")


WIDENED_ONE = ("Page 1 is a scan with a hidden text layer. The search read that layer and found "
               "1 match there, and OCR read the picture too and blacked out the rest of 1 word "
               "the search had covered only in part. OCR can miss words: check it by eye.")
CHECKED_PAGE_1 = ("Checked before saving: a search of the new file no longer finds what you "
                  "asked to remove, and OCR no longer reads it on page 1.")


def test_a_place_that_touches_the_search_box_is_counted_once(tmp_path, tessdata):
    """Not counted as a new place, but told of: it blacked out the rest of
    the word (#143 review: the note said "found nothing more to black out",
    and the checked line left the page out)."""
    src = _sloppy(tmp_path)
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM], ocr=True)
    assert res.redaction_count == 1
    assert res.surface_counts.get("page_content") == 1
    assert "ocr_page_content" not in res.surface_counts
    assert _note(res, "text_layer_read") == {"kind": "text_layer_read", "pages": [1],
                                             "hits": {1: 1}, "ocr_hits": {1: 0},
                                             "ocr_widened": {1: 1}}
    assert _note(res, "text_layer") is None and _note(res, "ocr") is None
    assert _lines(res) == [WIDENED_ONE]
    assert pdf_ops.redaction_summary(res)["checked"] == CHECKED_PAGE_1


def _sample(name):
    """A function of tests/manual/make_redact_samples.py, the script that
    writes the app checklist's samples."""
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).parent / "manual" / "make_redact_samples.py"
    spec = importlib.util.spec_from_file_location("make_redact_samples", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name), module.TERM


def test_sample_26_says_what_ocr_blacked_out(tmp_path, tessdata):
    """The app checklist's sample 26, with OCR on: the whole word in the
    picture goes, and the result says OCR blacked out the rest of it."""
    make, term = _sample("sloppy_text_layer")
    make(str(tmp_path))
    src = str(tmp_path / "26_scan_with_a_sloppy_text_layer.pdf")
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[term], ocr=True)
    assert res.verification["verified"] is True
    assert res.redaction_count == 1
    assert _note(res, "text_layer_read") == {"kind": "text_layer_read", "pages": [1],
                                             "hits": {1: 1}, "ocr_hits": {1: 0},
                                             "ocr_widened": {1: 1}}
    assert _lines(res) == [WIDENED_ONE]
    assert pdf_ops.redaction_summary(res)["checked"] == CHECKED_PAGE_1


def test_a_new_place_and_a_widened_one_on_the_same_page(tmp_path, tessdata):
    """The picture has the term twice, the layer once (in the wrong place):
    OCR widens the one the search found and finds the other."""
    extra = "Copy to SECRET99 on file"
    grey = _picture(LINES + [extra])
    src = _page(tmp_path / "both.pdf", b"q 600 0 0 400 0 0 cm /Im Do Q" + _hidden(LINES),
                xobjects=lambda pdf: {"/Im": _flate(pdf, grey)})
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    assert res.redaction_count == 2
    assert res.surface_counts.get("ocr_page_content") == 1
    assert _note(res, "text_layer_read") == {"kind": "text_layer_read", "pages": [1],
                                             "hits": {1: 1}, "ocr_hits": {1: 1},
                                             "ocr_widened": {1: 1}}
    assert _lines(res) == [
        "Page 1 is a scan with a hidden text layer. The search read that layer and found 1 "
        "match there, and OCR read the picture too: it found 1 more place to black out, and "
        "blacked out the rest of 1 word the search had covered only in part. OCR can miss "
        "words: check it by eye."]


def test_a_page_that_is_mostly_a_picture_says_what_ocr_widened(tmp_path, tessdata):
    """The "ocr" note too: a scan whose only hidden words are the term, in
    the wrong place, under a typed line (more letters shown than hidden, so
    it is mostly a picture, not a text layer)."""
    hidden = _hidden([f"Account {TERM}"], top=132)
    footer = b"\nBT 0 Tr /F1 12 Tf 30 20 Td (Scanned at the front desk on a Friday) Tj ET"
    src = _page(tmp_path / "mostly.pdf", b"q 600 0 0 400 0 0 cm /Im Do Q" + hidden + footer,
                xobjects=lambda pdf: {"/Im": _flate(pdf, _picture(LINES))})
    with fitz.open(src) as doc:
        assert pdf_ops._search_fact(doc[0]) == "mostly_picture"
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    assert _ink_left(out)[0] == 0
    assert res.redaction_count == 1
    assert _note(res, "ocr") == {"kind": "ocr", "pages": [1], "hits": {1: 0}, "widened": {1: 1}}
    assert _lines(res) == [
        "OCR read page 1 and blacked out the rest of 1 word the search had covered only in "
        "part. OCR can miss words: check it by eye."]
    assert pdf_ops.redaction_summary(res)["checked"] == CHECKED_PAGE_1


# ── A word the layer misread ────────────────────────────────────────────

def _searchable_scan(tmp_path, tessdata, misread: bool):
    """A scan given its text layer by MuPDF's own OCR, as OCR programs do;
    with *misread*, the layer's first reference reads "...447l"."""
    term = "ACME-4471"
    doc = fitz.open()
    page = doc.new_page(width=420, height=300)
    for i, row in enumerate(["The ledger for the year.", f"The account {term} is open.",
                             "Nothing else is due.", f"Please quote {term} when you write."]):
        page.insert_text((40, 60 + 34 * i), row, fontname="helv", fontsize=14)
    pix = page.get_pixmap(dpi=200)                         # RGB: OCR reads nothing on grey
    doc.close()
    buf = io.BytesIO(pix.tobytes("jpeg", jpg_quality=85))
    shot = fitz.Pixmap(buf.getvalue())
    shot.set_dpi(200, 200)
    pdf = pikepdf.Pdf.open(io.BytesIO(shot.pdfocr_tobytes(language=pdf_ocr.LANGUAGE,
                                                          tessdata=tessdata)))
    if misread:
        content = pdf.pages[0].obj["/Contents"]
        data = content.read_bytes()
        right = "".join(f"{ord(c):04x}" for c in term).encode()
        assert right in data.lower(), "OCR did not read the reference as one word"
        at = data.lower().index(right)
        content.write(data[:at] + "".join(f"{ord(c):04x}" for c in term[:-1] + "l").encode()
                      + data[at + len(right):])
    path = str(tmp_path / f"searchable-{misread}.pdf")
    pdf.save(path)
    return path, term


def test_ocr_finds_a_word_the_layer_misread_and_it_is_counted(tmp_path, tessdata):
    src, term = _searchable_scan(tmp_path, tessdata, misread=True)
    with fitz.open(src) as doc:
        assert pdf_ops._search_fact(doc[0]) == "text_layer"
        assert doc[0].get_text().count(term) == 1         # the layer has only one of the two
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[term], ocr=True)
    assert res.verification["verified"] is True
    assert res.redaction_count == 2
    assert res.surface_counts.get("page_content") == 1
    assert res.surface_counts.get("ocr_page_content") == 1
    assert _note(res, "text_layer_read") == {"kind": "text_layer_read", "pages": [1],
                                             "hits": {1: 1}, "ocr_hits": {1: 1},
                                             "ocr_widened": {1: 0}}
    assert _check(res, "redaction.ocr_reread")["passed"]
    summary = pdf_ops.redaction_summary(res)
    assert _lines(res) == [
        "Page 1 is a scan with a hidden text layer. The search read that layer and found 1 "
        "match there, and OCR read the picture too and found 1 more place to black out. OCR "
        "can miss words: check it by eye."]
    assert summary["checked"] == ("Checked before saving: a search of the new file no longer "
                                  "finds what you asked to remove, and OCR no longer reads it "
                                  "on page 1.")


def test_where_the_layer_has_the_word_right_the_search_has_it_covered(tmp_path, tessdata):
    """OCR reads the word where the layer has it: the search's box takes
    it, and OCR adds nothing (its place is dropped)."""
    src, term = _searchable_scan(tmp_path, tessdata, misread=False)
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[term], ocr=True)
    assert res.verification["verified"] is True
    assert res.redaction_count == 2 and "ocr_page_content" not in res.surface_counts
    assert _note(res, "text_layer_read")["ocr_hits"] == {1: 0}
    assert _note(res, "text_layer_read")["ocr_widened"] == {1: 0}
    assert pdf_ops.redaction_summary(res)["checked"] == (
        "Checked before saving: a search of the new file no longer finds what you asked to "
        "remove.")


# ── Which way a searchable scan is read ─────────────────────────────────
# Its layer says which way the lines run: the page is read at that turn
# only. A page OCR cannot read as English (another language, say) was read
# at all four turns, each a full reading (pdf.js's issue12963.pdf, a
# Russian scan: 17 to 33 seconds a page).

def _turned_layer(tmp_path, text_rotate: int, page_rotate: int) -> str:
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.draw_rect(page.rect, fill=(0.95, 0.95, 0.95))                   # stands for the scan
    page.insert_image(fitz.Rect(0, 0, 40, 40), pixmap=fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 8, 8)))
    origin = {0: (40, 200), 90: (200, 360), 180: (360, 200), 270: (200, 40)}[text_rotate]
    page.insert_text(origin, "Zbrt kvlm drosh plin vashko", fontsize=12, rotate=text_rotate,
                     render_mode=3)
    if page_rotate:
        page.set_rotation(page_rotate)
    path = str(tmp_path / f"turned-{text_rotate}-{page_rotate}.pdf")
    doc.save(path)
    return path


@pytest.mark.parametrize("text_rotate, page_rotate, turn", [
    (0, 0, 0), (90, 0, 90), (180, 0, 180), (270, 0, 270),
    (0, 90, 270), (90, 90, 0), (180, 90, 90), (270, 90, 180)])
def test_the_layer_says_which_way_to_read(tmp_path, text_rotate, page_rotate, turn):
    """The turn agrees with the one read_page picks reading all four (checked
    with real OCR on pages whose picture and layer run each of the four ways,
    with and without a /Rotate of their own)."""
    with fitz.open(_turned_layer(tmp_path, text_rotate, page_rotate)) as doc:
        assert pdf_ocr.layer_turn(doc[0]) == turn


def _stub_ocr(monkeypatch, text="Zbrt kvlm drosh plin vashko"):
    """OCR set up, reading *text* (not English) at any turn; the turns read."""
    asked = []
    monkeypatch.setattr(pdf_ocr, "ocr_status", lambda: {
        "ready": True, "tessdata": "stub", "file": None, "source": "installed", "pinned": True,
        "problem": None})

    def read_at(page, terms, case_sensitive, tessdata, turn, dpi):
        asked.append(turn)
        return pdf_ocr.Reading(turn, dpi, text)
    monkeypatch.setattr(pdf_ocr, "read_at", read_at)
    return asked


@pytest.mark.parametrize("text_rotate, turn", [(0, 0), (90, 90)])
def test_a_searchable_scan_ocr_cannot_read_is_read_once(tmp_path, monkeypatch, text_rotate, turn):
    asked = _stub_ocr(monkeypatch)
    src = _turned_layer(tmp_path, text_rotate, 0)
    with fitz.open(src) as doc:
        assert pdf_ops._search_fact(doc[0]) == "text_layer"
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["vashko"], ocr=True)
    assert asked == [turn]                              # not all four
    assert _note(res, "ocr_unread") == {"kind": "ocr_unread", "pages": [1], "too_large": []}


def test_a_scan_with_no_layer_is_still_read_at_every_turn(tmp_path, monkeypatch):
    asked = _stub_ocr(monkeypatch)
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.insert_image(page.rect, pixmap=fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 8, 8)))
    page2 = doc.new_page(width=400, height=400)
    page2.insert_text((40, 40), "Account SECRET99 here", fontsize=12)
    src = str(tmp_path / "scan.pdf")
    doc.save(src)
    redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM], ocr=True)
    assert asked == [0, 90, 180, 270]


# ── A place the search has covered is still dropped ─────────────────────

def test_a_typed_line_over_a_picture_takes_nothing_from_the_lines_round_it(tmp_path, tessdata):
    """A Bates block typed over a scan, its lines close together: OCR reads
    the term where the search found it, inside the search's box, and its
    place (grown round the word) is dropped. The typed words on the lines
    above and below stay, every letter."""
    paper = np.full((800, 1200), 235, np.uint8)
    paper[100:700, 100:1100] = np.linspace(180, 240, 1000, dtype=np.uint8)[None, :]
    block = ["CONFIDENTIAL MATTER", f"REF {TERM}", "PAGE 0001 OF 0003"]
    typed = "".join(f"\nBT /F1 11 Tf 420 {60 - 12 * i} Td ({line}) Tj ET"
                    for i, line in enumerate(block)).encode()
    src = _page(tmp_path / "bates.pdf", b"q 600 0 0 400 0 0 cm /Im Do Q" + typed,
                xobjects=lambda pdf: {"/Im": _flate(pdf, paper)})
    with fitz.open(src) as doc:
        assert pdf_ops._search_fact(doc[0]) == "mostly_picture"
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    assert res.surface_counts.get("page_content") == 1
    assert "ocr_page_content" not in res.surface_counts
    with fitz.open(out) as doc:
        text = doc[0].get_text()
    assert "CONFIDENTIAL MATTER" in text and "PAGE 0001 OF 0003" in text and "REF" in text
    assert TERM not in text


# ── A stencil-mask scan ─────────────────────────────────────────────────

def test_a_layer_over_a_stencil_mask_is_a_text_layer(tmp_path):
    src = _sloppy(tmp_path, stencil=True)
    with fitz.open(src) as doc:
        assert {e[0] for e in doc[0].get_bboxlog()} == {"fill-imgmask", "ignore-text"}
        assert pdf_ops._search_fact(doc[0]) == "text_layer"
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
    assert _note(res, "text_layer")["pages"] == [1]


# ── A: the picture under the search's own box ───────────────────────────

WORDS = f"Account {TERM} here"


def _a_page(tmp_path, kind: str, typed: str):
    """A picture of WORDS filling a 600 x 200 page, the same words typed
    over it ("hidden" or "shown"), the picture as *kind*."""
    grey = _picture([WORDS], size=(600, 200), at=(60, 100), font=20)
    words = f"BT {3 if typed == 'hidden' else 0} Tr /F1 20 Tf 60 100 Td ({WORDS}) Tj ET".encode()
    size = (600, 200)
    if kind == "inline":
        h, w = grey.shape
        draw = (f"q 600 0 0 200 0 0 cm BI /W {w} /H {h} /CS /G /BPC 8 /F /Fl ID ".encode()
                + zlib.compress(grey.tobytes()) + b" EI Q\n")
        return _page(tmp_path / f"{kind}-{typed}.pdf", draw + words, size=size)
    if kind == "in a form":
        def xobjects(pdf):
            form = pdf.make_stream(b"q 600 0 0 200 0 0 cm /Im Do Q", Type=Name.XObject,
                                   Subtype=Name.Form, BBox=[0, 0, 600, 200],
                                   Resources=Dictionary(XObject=Dictionary(Im=_flate(pdf, grey))))
            return {"/Fm": form}
        return _page(tmp_path / f"form-{typed}.pdf", b"q /Fm Do Q\n" + words, xobjects=xobjects,
                     size=size)
    if kind == "jpeg":
        def xobjects(pdf):
            buf = io.BytesIO()
            from PIL import Image
            Image.fromarray(grey).save(buf, "JPEG", quality=85)
            return {"/Im": pdf.make_stream(buf.getvalue(), Type=Name.XObject, Subtype=Name.Image,
                                           Width=grey.shape[1], Height=grey.shape[0],
                                           ColorSpace=Name.DeviceGray, BitsPerComponent=8,
                                           Filter=Name.DCTDecode)}
    else:
        def xobjects(pdf):
            return {"/Im": _flate(pdf, grey, bits=1 if kind in ("1-bit", "stencil") else 8,
                                  stencil=kind == "stencil")}
    draw = b"q 0 0 0 rg 600 0 0 200 0 0 cm /Im Do Q\n" if kind == "stencil" else \
        b"q 600 0 0 200 0 0 cm /Im Do Q\n"
    return _page(tmp_path / f"{kind}-{typed}.pdf", draw + words, xobjects=xobjects, size=size)


KINDS = ["flate", "jpeg", "1-bit", "stencil", "inline", "in a form"]


@pytest.mark.parametrize("typed", ["hidden", "shown"])
@pytest.mark.parametrize("kind", KINDS)
def test_the_picture_under_a_search_box_is_blanked_and_checked(tmp_path, kind, typed):
    src = _a_page(tmp_path, kind, typed)
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
    assert res.verification["verified"] is True
    check = _check(res, "redaction.search_image")
    assert check["passed"] and not check["unevaluated"]
    assert check["description"] == "The pictures under the search's boxes are blanked there"


@pytest.mark.parametrize("kind", KINDS)
def test_a_picture_not_blanked_under_a_search_box_is_refused(tmp_path, kind, monkeypatch):
    """The blanking skipped (PDF_REDACT_IMAGE_NONE where the search's box
    asks for its pixels): the black box is over the word, and the picture
    still holds it under the box."""
    src = _a_page(tmp_path, kind, "shown")
    monkeypatch.setattr(fitz, "PDF_REDACT_IMAGE_PIXELS", fitz.PDF_REDACT_IMAGE_NONE)
    with pytest.raises(RedactionVerificationError) as info:
        redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
    failed = [c for c in info.value.report["checks"] if not c["passed"]]
    assert [c["id"] for c in failed] == ["redaction.search_image"]
    assert failed[0]["evidence"] == ["a picture under a search box on page 1 still holds what "
                                     "was under the box"]
    found = " ".join(info.value.detail["found"])
    assert found == ("A picture on page 1 still holds what the search found, under the black "
                     "box. It could be taken out of the file.")
    for jargon in ("/", "xref", "search box", "pixmap", "redaction."):
        assert jargon not in found


def test_a_picture_that_cannot_be_read_could_not_be_checked(tmp_path, monkeypatch):
    src = _a_page(tmp_path, "flate", "shown")
    monkeypatch.setattr(pdf_verify, "_image_pixmaps", lambda *_a: None)
    with pytest.raises(RedactionVerificationError) as info:
        redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
    (check,) = [c for c in info.value.report["checks"] if not c["passed"]]
    assert check["id"] == "redaction.search_image" and check["unevaluated"]
    assert info.value.detail["headline"].startswith("Redaction refused: part of the new file "
                                                    "could not be read")


# ── What search_image does not look at ──────────────────────────────────
# Only what the redaction itself blanks: pictures the page's content draws,
# directly or through forms, and pictures written into it. MuPDF reports
# more than that as pictures under a box (with no object number): a
# shading, text filled with one, an annotation's appearance, a picture
# that is part of a soft mask. Each of these refused files that verified
# before (pdf.js test files, on the owner's corpus).

def _shading(pdf):
    fn = Dictionary(FunctionType=2, Domain=[0, 1], C0=[1, 0.9, 0.6], C1=[0.4, 0.6, 1], N=1)
    return pdf.make_indirect(Dictionary(ShadingType=2, ColorSpace=Name.DeviceRGB,
                                        Coords=[0, 0, 600, 0], Function=fn, Extend=[True, True]))


def _ramp(pdf, w=300, h=100):
    data = bytes(x * 255 // w for _y in range(h) for x in range(w))
    return pdf.make_stream(zlib.compress(data), Type=Name.XObject, Subtype=Name.Image, Width=w,
                           Height=h, ColorSpace=Name.DeviceGray, BitsPerComponent=8,
                           Filter=Name.FlateDecode)


def _not_blanked_by_redaction(tmp_path, kind: str) -> str:
    words = f"BT /F1 20 Tf 60 100 Td (Account {TERM} here) Tj ET".encode()
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 600, 200])))
    page = pdf.pages[-1].obj
    resources = Dictionary(Font=Dictionary(F1=font))
    if kind == "a shading":
        resources["/Shading"] = Dictionary(Sh0=_shading(pdf))
        content = b"q /Sh0 sh Q\n" + words
    elif kind == "text filled with a shading":
        resources["/Pattern"] = Dictionary(P0=pdf.make_indirect(Dictionary(
            Type=Name.Pattern, PatternType=2, Shading=_shading(pdf))))
        content = b"/Pattern cs /P0 scn\n" + words
    elif kind == "a stamp's picture":
        appearance = pdf.make_stream(b"q 300 0 0 100 0 0 cm /Im Do Q", Type=Name.XObject,
                                     Subtype=Name.Form, BBox=[0, 0, 300, 100],
                                     Resources=Dictionary(XObject=Dictionary(Im=_ramp(pdf))))
        page["/Annots"] = pikepdf.Array([pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Stamp, Rect=[40, 60, 340, 160],
            AP=Dictionary(N=appearance)))])
        content = words
    else:                                                  # a picture in a soft mask
        group = pdf.make_stream(b"q 600 0 0 200 0 0 cm /Im Do Q", Type=Name.XObject,
                                Subtype=Name.Form, BBox=[0, 0, 600, 200],
                                Group=Dictionary(S=Name.Transparency, CS=Name.DeviceGray),
                                Resources=Dictionary(XObject=Dictionary(Im=_ramp(pdf, 600, 200))))
        resources["/ExtGState"] = Dictionary(G0=Dictionary(
            Type=Name.ExtGState, SMask=Dictionary(S=Name.Luminosity, G=group)))
        content = b"q /G0 gs 0.2 0.4 0.8 rg 0 0 600 200 re f Q\n" + words
    page["/Resources"] = resources
    page["/Contents"] = pdf.make_stream(content)
    path = str(tmp_path / f"{kind.replace(' ', '-')}.pdf")
    pdf.save(path)
    return path


@pytest.mark.parametrize("kind", ["a shading", "text filled with a shading", "a stamp's picture",
                                  "a picture in a soft mask"])
def test_what_the_redaction_does_not_blank_is_not_looked_at(tmp_path, kind):
    src = _not_blanked_by_redaction(tmp_path, kind)
    with fitz.open(src) as doc:                            # MuPDF calls it a picture under the box
        box = fitz.Rect(pdf_verify.term_hits(doc[0], [TERM], False)[0][0])
        assert any(fitz.Rect(i["bbox"]).intersects(box) and not i.get("xref")
                   for i in doc[0].get_image_info(xrefs=True))
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
    assert res.verification["verified"] is True
    check = _check(res, "redaction.search_image")
    assert check["passed"] and not check["unevaluated"]


def _inline_over_a_shading(tmp_path) -> str:
    """A shading filling the page, and a picture written into the content
    over it, both under the search's box."""
    grey = _picture([WORDS], size=(600, 200), at=(60, 100), font=20)
    h, w = grey.shape
    words = f"BT /F1 20 Tf 60 100 Td ({WORDS}) Tj ET".encode()
    content = (b"q /Sh0 sh Q\n" + f"q 600 0 0 200 0 0 cm BI /W {w} /H {h} /CS /G /BPC 8 /F /Fl ID ".encode()
               + zlib.compress(grey.tobytes()) + b" EI Q\n" + words)
    return _page_with(tmp_path, content, Shading=lambda pdf: Dictionary(Sh0=_shading(pdf)))


def _page_with(tmp_path, content: bytes, **resources):
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 600, 200])))
    page = pdf.pages[-1].obj
    page["/Resources"] = Dictionary(Font=Dictionary(F1=font))
    for key, make in resources.items():
        page["/Resources"][f"/{key}"] = make(pdf)
    page["/Contents"] = pdf.make_stream(content)
    path = str(tmp_path / "page.pdf")
    pdf.save(path)
    return path


def test_a_picture_written_into_the_content_is_still_looked_at_beside_a_shading(tmp_path, monkeypatch):
    src = _inline_over_a_shading(tmp_path)
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
    assert _check(res, "redaction.search_image")["passed"]
    monkeypatch.setattr(fitz, "PDF_REDACT_IMAGE_PIXELS", fitz.PDF_REDACT_IMAGE_NONE)
    with pytest.raises(RedactionVerificationError) as info:
        redact_pdf(src, str(tmp_path / "out2.pdf"), search_terms=[TERM])
    (check,) = [c for c in info.value.report["checks"] if not c["passed"]]
    assert check["id"] == "redaction.search_image"
    assert check["evidence"] == ["a picture under a search box on page 1 still holds what was "
                                 "under the box"]                   # the picture, and only it


def test_a_page_with_no_picture_under_the_search_box_passes(tmp_path):
    src = _page(tmp_path / "text.pdf", f"BT /F1 14 Tf 72 300 Td (The account {TERM}) Tj ET".encode())
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
    check = _check(res, "redaction.search_image")
    assert check["passed"] and not check["evidence"]
