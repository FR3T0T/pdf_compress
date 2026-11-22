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

import math
import pathlib

import pikepdf
import pytest
from pikepdf import Dictionary, Name

import pdf_ops

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
