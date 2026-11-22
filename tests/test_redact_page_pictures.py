"""Redaction completeness (#119), R4: a box drawn on the picture of a page.

The app's Draw-boxes mode shows each page as a picture (the bridge's
getPageImages renders page.get_pixmap) and sends a box as it lies on that
picture. redact_pdf and the verifier work in PyMuPDF's page space: the page
as it is before any /Rotate, from the top left of its crop box. On a page
turned by /Rotate the two differ, and the box was applied somewhere else:
the word under it stayed and the run verified.

``pdf_ops.rects_from_page_pictures`` puts such boxes into page space, and
the redact slot calls it before redact_pdf. These tests go through it the
way the slot does. The box is found on the picture itself — the bounding
box of the red ink of one word on the page as rendered — so that "where the
app shows it" is measured, not assumed.

Every fixture is built here, by hand. The new name is reached through the
module, so this file collects against a tree that does not have it.
Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import hashlib
import math
import pathlib

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

import pdf_ops
from pdf_verify import verify_redaction

fitz = pytest.importorskip("fitz")

#: What getPageImages renders at, and boxesToRects' margin in points.
DPI, MARGIN = 150, 1
WORD = "Whitfield"
#: Three lines; the word in the middle one is drawn in red.
LINES = (b"BT /F1 14 Tf 100 600 Td (first line stays) Tj ET "
         b"BT /F1 14 Tf 100 570 Td (the ) Tj 1 0 0 rg (Whitfield) Tj 0 g ( word) Tj ET "
         b"BT /F1 14 Tf 100 540 Td (last line stays) Tj ET")
STAYS = ["first", "line", "stays", "the", "word", "last", "line", "stays"]
#: A word written from the left edge of the page, in red, and a line beside it.
AT_THE_EDGE = (b"BT /F1 14 Tf 1 0 0 rg 2 500 Td (Whitfield) Tj ET "
               b"BT /F1 14 Tf 0 g 200 500 Td (a line that stays) Tj ET")
CROP = [40, 300, 560, 760]


def _pdf(path, content: bytes = LINES, *, rotate: int = 0, crop=None, password=None) -> str:
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[0].obj
    page["/Resources"] = Dictionary(Font=Dictionary(F1=pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))))
    page["/Contents"] = pdf.make_stream(content)
    if rotate:
        page["/Rotate"] = rotate
    if crop:
        page["/CropBox"] = crop
    kwargs = {}
    if password:
        kwargs["encryption"] = pikepdf.Encryption(user=password, owner=password + "-owner")
    pdf.save(str(path), **kwargs)
    pdf.close()
    return str(path)


def _on_the_picture(path, password=None) -> tuple:
    """Where the red ink is on the page as getPageImages renders it, in
    points on that picture."""
    zoom = DPI / 72
    with fitz.open(path) as doc:
        if password:
            doc.authenticate(password)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    samples, n, width = pix.samples, pix.n, pix.width
    xs, ys = [], []
    for i in range(0, len(samples), n):
        r, g, b = samples[i], samples[i + 1], samples[i + 2]
        if r > 180 and g < 90 and b < 90:
            xs.append((i // n) % width)
            ys.append((i // n) // width)
    return min(xs) / zoom, min(ys) / zoom, (max(xs) + 1) / zoom, (max(ys) + 1) / zoom


def _as_the_app_sends(box: tuple, page: int = 0) -> dict:
    """boxesToRects (web-react RedactPage.tsx): rounded outward, plus a
    point all round."""
    x0, y0, x1, y1 = box
    return {"page": page, "x0": math.floor(x0) - MARGIN, "y0": math.floor(y0) - MARGIN,
            "x1": math.ceil(x1) + MARGIN, "y1": math.ceil(y1) + MARGIN}


def _words(path, password=None) -> list:
    with fitz.open(path) as doc:
        if password:
            doc.authenticate(password)
        return doc[0].get_text().split()


def _redact(src, out, rects, password=None):
    """What the redact slot does with the boxes the app sends."""
    page_space = pdf_ops.rects_from_page_pictures(src, rects, password)
    return pdf_ops.redact_pdf(src, out, rects=page_space, password=password)


@pytest.mark.integration
class TestABoxOnTheWordAsTheAppShowsIt:

    @pytest.mark.parametrize("crop", [None, CROP], ids=["whole page", "a moved crop box"])
    @pytest.mark.parametrize("rotate", [0, 90, 180, 270])
    def test_the_word_goes_and_the_other_lines_stay(self, tmp_path, rotate, crop):
        src = _pdf(tmp_path / "src.pdf", rotate=rotate, crop=crop)
        out = str(tmp_path / "out.pdf")
        res = _redact(src, out, [_as_the_app_sends(_on_the_picture(src))])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert _words(out) == STAYS

    @pytest.mark.parametrize("rotate", [0, 90, 180, 270])
    def test_a_box_half_off_the_edge_of_a_turned_page(self, tmp_path, rotate):
        """The word starts at the page's edge; the box is drawn from beyond
        it, as a drag that runs off the picture does."""
        src = _pdf(tmp_path / "src.pdf", AT_THE_EDGE, rotate=rotate)
        x0, y0, x1, y1 = _on_the_picture(src)
        with fitz.open(src) as doc:
            width, height = doc[0].rect.width, doc[0].rect.height
        # Out past whichever edge of the picture the word is nearest.
        nearest = min((x0, "left"), (y0, "top"), (width - x1, "right"), (height - y1, "bottom"))[1]
        x0, y0, x1, y1 = {"left": (-30, y0, x1, y1), "top": (x0, -30, x1, y1),
                          "right": (x0, y0, width + 30, y1),
                          "bottom": (x0, y0, x1, height + 30)}[nearest]
        out = str(tmp_path / "out.pdf")
        res = _redact(src, out, [_as_the_app_sends((x0, y0, x1, y1))])
        assert res.verification["verified"] is True
        assert _words(out) == ["a", "line", "that", "stays"]

    def test_a_password_protected_file(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", rotate=90, password="pw")
        out = str(tmp_path / "out.pdf")
        res = _redact(src, out, [_as_the_app_sends(_on_the_picture(src, "pw"))], password="pw")
        assert res.verification["verified"] is True
        assert WORD not in _words(out, "pw")


@pytest.mark.integration
class TestWhatTheConversionLeavesAlone:

    def test_an_upright_page_is_as_it_was(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf")
        box = {"page": 0, "x0": 10.5, "y0": 20, "x1": 30, "y1": 40.25}
        assert pdf_ops.rects_from_page_pictures(src, [box]) == [box]

    def test_on_a_turned_page_only_the_corners_change(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", rotate=90)
        box = {"page": 0, "x0": 100, "y0": 50, "x1": 200, "y1": 80, "id": "kept"}
        (got,) = pdf_ops.rects_from_page_pictures(src, [box])
        with fitz.open(src) as doc:
            want = fitz.Rect(100, 50, 200, 80) * doc[0].derotation_matrix
        want.normalize()
        assert got["page"] == 0 and got["id"] == "kept"
        assert (got["x0"], got["y0"], got["x1"], got["y1"]) == pytest.approx(tuple(want))

    def test_what_cannot_be_read_is_passed_on_for_the_verifier_to_refuse(self, tmp_path):
        """A malformed box, an inverted one, or one for a page the file does
        not have, is handed on as it came: redact_pdf skips it and the
        verifier fails the run for it, as before."""
        src = _pdf(tmp_path / "src.pdf", rotate=90)
        odd = [{"page": 3, "x0": 1, "y0": 1, "x1": 9, "y1": 9}, {"page": 0, "x0": "left"},
               {"x0": 1, "y0": 1, "x1": 9, "y1": 9},
               {"page": 0, "x0": 90, "y0": 90, "x1": 10, "y1": 10}]
        assert pdf_ops.rects_from_page_pictures(src, odd) == odd
        good = _as_the_app_sends(_on_the_picture(src))
        with pytest.raises(pdf_ops.RedactionVerificationError) as ei:
            _redact(src, str(tmp_path / "out.pdf"), [good, odd[0]])
        (check,) = [c for c in ei.value.report["checks"] if not c["passed"]]
        assert check["id"] == "redaction.rect_regions"
        assert check["evidence"] == ["rect targets out-of-range page 4 and could not be verified"]

    def test_no_boxes(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", rotate=90)
        assert pdf_ops.rects_from_page_pictures(src, None) is None
        assert pdf_ops.rects_from_page_pictures(src, []) == []


def test_the_redact_slot_converts_the_boxes_it_is_sent():
    """The bridge cannot be imported here (it pulls in Qt). Its source is
    read instead: the slot hands the app's boxes to the conversion before
    redact_pdf sees them."""
    source = (pathlib.Path(__file__).resolve().parents[1] / "ui" / "bridge.py").read_text(
        encoding="utf-8")
    slot = source[source.index("def startRedact"):]
    slot = slot[:slot.index("self._run_in_thread")]
    assert "rects_from_page_pictures(" in slot
    assert slot.index("rects_from_page_pictures(") < slot.index("redact_pdf(")


# ── A page box that does not start at 0 0 (#119, R4) ─────────────────────

#: The pages: 612 by 792, the word in 24 point at 200 400, and the page box
#: where it is or moved: a crop box inside the media box, or a media box
#: that does not start at 0 0.
PAGE_BOXES = {"at 0 0": {}, "a moved crop box": {"CropBox": [99, 90, 520, 700]},
              "a moved media box": {"MediaBox": [60, 45, 612, 792]}}
OVER_THE_WORD = [195, 392, 305, 424]           # a stamp's /Rect over the word
FAR_AWAY = [380, 600, 490, 632]                 # well away from it


def _corner(boxes: dict) -> tuple:
    box = boxes.get("CropBox") or boxes.get("MediaBox") or [0, 0]
    return box[0], box[1]


def _elsewhere(boxes: dict) -> list:
    """Where an annotation went wrong: /Rect less the page box's corner.
    With the box at 0 0 that is the word itself, so somewhere far off."""
    x, y = _corner(boxes)
    if (x, y) == (0, 0):
        return FAR_AWAY
    return [OVER_THE_WORD[0] - x, OVER_THE_WORD[1] - y, OVER_THE_WORD[2] - x, OVER_THE_WORD[3] - y]


def _turned(path, rotate: int, boxes: dict, *, over=True, elsewhere=True, under=None,
            on_the_tree=False) -> str:
    """The word, a line that stays, a stamp over the word (filled blue), and
    a stamp and a link elsewhere. *under* adds, over the word, a link, a
    form field, or a stamp written into /Annots itself."""
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[0].obj
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    page["/Resources"] = Dictionary(Font=Dictionary(F1=font))
    page["/Contents"] = pdf.make_stream(
        b"BT /F1 24 Tf 1 0 0 rg 200 400 Td (Whitfield) Tj ET "
        b"BT /F1 14 Tf 0 g 200 500 Td (a line that stays) Tj ET")
    for key, value in boxes.items():
        page["/" + key] = value
    if rotate and on_the_tree:
        pdf.Root["/Pages"]["/Rotate"] = rotate
    elif rotate:
        page["/Rotate"] = rotate

    def stamp(rect):
        return Dictionary(Type=Name.Annot, Subtype=Name.Stamp, Rect=rect, AP=Dictionary(
            N=pdf.make_stream(b"0 0 1 rg 0 0 110 32 re f", Type=Name.XObject,
                              Subtype=Name.Form, BBox=[0, 0, 110, 32])))
    annots = []
    if over:
        annots.append(pdf.make_indirect(stamp(OVER_THE_WORD)))
    if elsewhere:
        annots.append(pdf.make_indirect(stamp(_elsewhere(boxes))))
        annots.append(pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                                   Rect=_elsewhere(boxes),
                                                   Dest=Array([0, Name.Fit]))))
    if under == "a link":
        annots.append(pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                                   Rect=OVER_THE_WORD, Dest=Array([0, Name.Fit]))))
    elif under == "a form field":
        field = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("who"),
            V=String("Harbour Ltd"), Rect=OVER_THE_WORD, DA=String("/Helv 12 Tf 0 g"),
            P=page))
        pdf.Root["/AcroForm"] = Dictionary(Fields=Array([field]), DA=String("/Helv 12 Tf 0 g"),
                                           DR=Dictionary(Font=Dictionary(Helv=font)))
        annots.append(field)
    elif under == "a stamp written into /Annots":
        annots.append(stamp(OVER_THE_WORD))
    if annots:
        page["/Annots"] = Array(annots)
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _word_on_the_picture(path, grow: float = 2) -> tuple:
    """The word's rectangle on the page as shown (page space times the
    rotation matrix), grown all round: a box drawn round it in the app."""
    with fitz.open(path) as doc:
        page = doc[0]
        (word,) = [w for w in page.get_text("words") if w[4] == WORD]
        r = fitz.Rect(word[:4]) * page.rotation_matrix
    r.normalize()
    return r.x0 - grow, r.y0 - grow, r.x1 + grow, r.y1 + grow


def _dark_on_the_picture(path, rect, below: int = 64) -> float:
    """How much of *rect*, on the page as shown, is black when it is drawn."""
    zoom = 2
    with fitz.open(path) as doc:
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY,
                                alpha=False, clip=fitz.Rect(rect))
    return sum(1 for v in pix.samples if v < below) / max(1, len(pix.samples))


def _check(report, cid):
    (check,) = [c for c in report.checks if c.id == cid]
    return check


def _annots_left(path) -> list:
    with pikepdf.open(path) as pdf:
        return [str(a.get("/Subtype")) for a in pdf.pages[0].obj.get("/Annots", [])]


ROTATIONS = [0, 90, 180, 270]


@pytest.mark.integration
class TestAPageBoxThatDoesNotStartAtTheCorner:
    """On a turned page, PyMuPDF's page.transformation_matrix leaves out
    where the page box starts. An annotation's own rectangle was put in the
    wrong place with it: a stamp over the box stayed, painted over the
    black, and one elsewhere was taken off; the verifier placed them the
    same way and passed. And PyMuPDF draws a redaction's black fill with
    it: the text went, the black bar landed beside it. Upright, or with
    the page box at 0 0, all of it was right."""

    @pytest.mark.parametrize("boxes", list(PAGE_BOXES), ids=list(PAGE_BOXES))
    @pytest.mark.parametrize("rotate", ROTATIONS)
    def test_a_box_drawn_by_hand(self, tmp_path, rotate, boxes):
        src = _turned(tmp_path / "src.pdf", rotate, PAGE_BOXES[boxes])
        pic = _word_on_the_picture(src)
        out = str(tmp_path / "out.pdf")
        res = _redact(src, out, [_as_the_app_sends(pic)])
        assert res.verification["verified"] is True
        assert WORD not in _words(out) and "stays" in _words(out)
        assert _annots_left(out) == ["/Stamp", "/Link"]          # the ones elsewhere
        assert res.surface_counts.get("annotations_removed") == 1
        assert "links_removed" not in res.surface_counts
        assert _dark_on_the_picture(out, pic) > 0.95                # black, and nothing over it

    @pytest.mark.parametrize("boxes", list(PAGE_BOXES), ids=list(PAGE_BOXES))
    @pytest.mark.parametrize("rotate", ROTATIONS)
    def test_a_search(self, tmp_path, rotate, boxes):
        src = _turned(tmp_path / "src.pdf", rotate, PAGE_BOXES[boxes], over=False)
        pic = _word_on_the_picture(src, grow=-1)
        out = str(tmp_path / "out.pdf")
        res = pdf_ops.redact_pdf(src, out, search_terms=[WORD])
        assert res.verification["verified"] is True
        assert WORD not in _words(out) and _annots_left(out) == ["/Stamp", "/Link"]
        assert _dark_on_the_picture(out, pic) > 0.95

    @pytest.mark.parametrize("under", ["a link", "a form field", "a stamp written into /Annots"])
    @pytest.mark.parametrize("boxes", list(PAGE_BOXES), ids=list(PAGE_BOXES))
    @pytest.mark.parametrize("rotate", ROTATIONS)
    @pytest.mark.filterwarnings("ignore:This document has 1 form widget")
    def test_what_is_under_the_box_goes(self, tmp_path, rotate, boxes, under):
        src = _turned(tmp_path / "src.pdf", rotate, PAGE_BOXES[boxes], over=False, under=under)
        out = str(tmp_path / "out.pdf")
        res = _redact(src, out, [_as_the_app_sends(_word_on_the_picture(src))])
        assert res.verification["verified"] is True
        assert _annots_left(out) == ["/Stamp", "/Link"]

    @pytest.mark.parametrize("boxes", list(PAGE_BOXES), ids=list(PAGE_BOXES))
    @pytest.mark.parametrize("rotate", ROTATIONS)
    def test_the_verifier_places_annotations_where_they_are_drawn(self, tmp_path, rotate, boxes):
        src = _turned(tmp_path / "src.pdf", rotate, PAGE_BOXES[boxes])
        rects = pdf_ops.rects_from_page_pictures(src, [_as_the_app_sends(_word_on_the_picture(src))])
        check = _check(verify_redaction(src, rects=rects), "redaction.rect_annotations")
        assert check.passed is False
        assert check.evidence == ["a Stamp annotation still lies over a redaction rectangle on page 1"]
        alone = _turned(tmp_path / "alone.pdf", rotate, PAGE_BOXES[boxes], over=False)
        assert _check(verify_redaction(alone, rects=rects), "redaction.rect_annotations").passed

    @pytest.mark.parametrize("rotate", [90, 270])
    def test_a_turn_the_page_tree_gives_is_shown_as_before(self, tmp_path, rotate):
        """The page has no /Rotate of its own; it takes its turn from the
        page tree. The output is shown turned as before, with the black
        where the box was drawn."""
        src = _turned(tmp_path / "src.pdf", rotate, PAGE_BOXES["a moved crop box"], on_the_tree=True)
        with fitz.open(src) as doc:                   # the fixture is what it claims
            assert doc.xref_get_key(doc[0].xref, "Rotate")[0] == "null"
            assert doc[0].rotation == rotate
        pic = _word_on_the_picture(src)
        out = str(tmp_path / "out.pdf")
        res = _redact(src, out, [_as_the_app_sends(pic)])
        assert res.verification["verified"] is True and WORD not in _words(out)
        with fitz.open(out) as doc:
            assert doc[0].rotation == rotate
        assert _dark_on_the_picture(out, pic) > 0.95

    @pytest.mark.parametrize("rotate", [90, 180, 270])
    def test_a_page_s_own_turn_is_kept_as_it_was(self, tmp_path, rotate):
        src = _turned(tmp_path / "src.pdf", rotate, PAGE_BOXES["a moved media box"])
        out = str(tmp_path / "out.pdf")
        _redact(src, out, [_as_the_app_sends(_word_on_the_picture(src))])
        with fitz.open(out) as doc:
            assert doc.xref_get_key(doc[0].xref, "Rotate") == ("int", str(rotate))

    def test_nothing_the_verifier_does_to_a_page_reaches_a_file(self, tmp_path):
        src = _turned(tmp_path / "src.pdf", 90, PAGE_BOXES["a moved crop box"])
        before = hashlib.sha256(pathlib.Path(src).read_bytes()).hexdigest()
        rects = pdf_ops.rects_from_page_pictures(src, [_as_the_app_sends(_word_on_the_picture(src))])
        verify_redaction(src, rects=rects)
        verify_redaction(src, terms=[WORD])
        assert hashlib.sha256(pathlib.Path(src).read_bytes()).hexdigest() == before
        with fitz.open(src) as doc:
            assert doc[0].rotation == 90


# ── A turned page whose size is not its crop box's (#119, R4) ────────────

#: A user unit of 2 draws the page twice as large; a crop box that is not
#: inside the media box is cut to it. PyMuPDF's rotation matrix is made from
#: the crop box as written, so on a turned page it puts the picture of the
#: page somewhere MuPDF does not draw it.
ODD_SIZES = {"a user unit of 2": {"UserUnit": 2},
             "a user unit of 2 and a moved crop box": {"UserUnit": 2, "CropBox": [99, 90, 520, 700]},
             "a crop box larger than the media box": {"MediaBox": [60, 45, 612, 792],
                                                      "CropBox": [0, 0, 700, 900]},
             "a crop box partly outside the media box": {"CropBox": [-50, 100, 500, 900]}}


def _odd(path, rotate: int, entries: dict) -> str:
    """LINES (the middle word in red) on a page with *entries*."""
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[0].obj
    page["/Resources"] = Dictionary(Font=Dictionary(F1=pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))))
    page["/Contents"] = pdf.make_stream(LINES)
    for key, value in entries.items():
        page["/" + key] = value
    if rotate:
        page["/Rotate"] = rotate
    pdf.save(str(path))
    pdf.close()
    return str(path)


@pytest.mark.integration
class TestATurnedPageWhoseSizeIsNotItsCropBox:

    @pytest.mark.parametrize("entries", list(ODD_SIZES), ids=list(ODD_SIZES))
    @pytest.mark.parametrize("rotate", [0, 90, 180, 270])
    def test_a_box_on_the_word_as_the_app_shows_it(self, tmp_path, rotate, entries):
        """The box is found on the rendered page, from the red ink."""
        src = _odd(tmp_path / "src.pdf", rotate, ODD_SIZES[entries])
        out = str(tmp_path / "out.pdf")
        res = _redact(src, out, [_as_the_app_sends(_on_the_picture(src))])
        assert res.verification["verified"] is True
        assert _words(out) == STAYS

    @pytest.mark.parametrize("entries", list(ODD_SIZES), ids=list(ODD_SIZES))
    @pytest.mark.parametrize("rotate", [90, 270])
    def test_a_flattened_page_is_black_where_the_box_was(self, tmp_path, monkeypatch, rotate, entries):
        """Removal switched off: the page is made a picture, and the box is
        filled on it where it was drawn."""
        monkeypatch.setattr(fitz.Page, "apply_redactions", lambda self, *a, **k: True)
        src = _odd(tmp_path / "src.pdf", rotate, ODD_SIZES[entries])
        ink = _on_the_picture(src)
        out = str(tmp_path / "out.pdf")
        res = pdf_ops.redact_pdf(
            src, out, rects=pdf_ops.rects_from_page_pictures(src, [_as_the_app_sends(ink)]),
            flatten_pages=[1])
        assert res.flattened_pages == [1]
        inner = (ink[0] + 1, ink[1] + 1, ink[2] - 1, ink[3] - 1)
        assert _dark_on_the_picture(out, inner) > 0.95
