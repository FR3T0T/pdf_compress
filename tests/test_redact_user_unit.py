"""The black of a redaction on a page with a user unit is the box (#134).

A page with a /UserUnit above 1 is drawn that many times larger than its
numbers say. PyMuPDF's own redaction fill was drawn at the box's size as
written, from its lower left corner: twice the box each way at user unit 2,
three times at 3, in box mode, search mode and with OCR, upright and
turned, on PyMuPDF 1.27.2.3 and 2.1 alike. What it removed was right; the
black reached past it. The fill is now written into the page by redact_pdf
itself, at the size of the box.

Every page is built here. The black is measured on the page as shown, with
the box put on that page the way the app shows it (page_to_picture_matrix).
Qt-free (see CLAUDE.md).
"""

import zlib

import numpy as np
import pikepdf
import pytest
from pikepdf import Dictionary, Name
from PIL import Image

import pdf_ocr
import pdf_verify
from pdf_ops import redact_pdf

fitz = pytest.importorskip("fitz")

TERM = "ACME-4471"
W, H = 306, 396                 # the page as written; it is shown UU times larger
DPI = 144                       # what the black is measured at: 2 pixels a point


def _font(pdf):
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1,
                                        BaseFont=Name.Helvetica))


def _text_page(path, uu, rotate):
    """Three lines of type, the term on the middle one, and a grey block."""
    pdf = pikepdf.Pdf.new()
    content = (b"BT /F1 9 Tf 30 330 Td (A line above the reference, left alone.) Tj ET\n"
               b"BT /F1 9 Tf 30 316 Td (The reference ACME-4471 is here.) Tj ET\n"
               b"BT /F1 9 Tf 30 302 Td (A line below the reference, left alone.) Tj ET\n"
               b"q 0.8 0.8 0.8 rg 30 120 200 60 re f Q")
    page = Dictionary(Type=Name.Page, MediaBox=[0, 0, W, H], UserUnit=uu,
                      Resources=Dictionary(Font=Dictionary(F1=_font(pdf))),
                      Contents=pdf.make_stream(content))
    if rotate:
        page.Rotate = rotate
    pdf.pages.append(pikepdf.Page(page))
    pdf.save(str(path))
    return str(path)


def _scan_page(path, uu, rotate, rgb=False):
    """A picture of two lines of type, the term in the first, filling the
    page: grey, or in RGB."""
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text((30, 70), f"The reference {TERM} is here.", fontname="helv", fontsize=12)
    page.insert_text((30, 90), "A line below it, left alone.", fontname="helv", fontsize=12)
    pix = page.get_pixmap(dpi=150 if uu == 2 else 200, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    if rgb:
        img = img.convert("RGB")
    pdf = pikepdf.Pdf.new()
    image = pdf.make_stream(zlib.compress(img.tobytes()), Type=Name.XObject, Subtype=Name.Image,
                            Width=img.width, Height=img.height, BitsPerComponent=8,
                            ColorSpace=Name.DeviceRGB if rgb else Name.DeviceGray,
                            Filter=Name.FlateDecode)
    d = Dictionary(Type=Name.Page, MediaBox=[0, 0, W, H], UserUnit=uu,
                   Resources=Dictionary(XObject=Dictionary(Im0=image)),
                   Contents=pdf.make_stream(f"q {W} 0 0 {H} 0 0 cm /Im0 Do Q".encode()))
    if rotate:
        d.Rotate = rotate
    pdf.pages.append(pikepdf.Page(d))
    pdf.save(str(path))
    return str(path)


def _shown(path):
    with fitz.open(path) as doc:
        pix = doc[0].get_pixmap(dpi=DPI, colorspace=fitz.csGRAY, annots=False)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()


def _new_black(src, out):
    """The bounds of what turned black, in points of the page as shown."""
    new = (_shown(out) < 40) & (_shown(src) >= 40)
    assert new.any(), "nothing turned black"
    ys, xs = np.nonzero(new)
    z = DPI / 72
    return fitz.Rect(xs.min() / z, ys.min() / z, (xs.max() + 1) / z, (ys.max() + 1) / z)


def _as_shown(path, boxes):
    with fitz.open(path) as doc:
        m = pdf_verify.page_to_picture_matrix(doc[0])
    bounds = fitz.Rect()
    for box in boxes:
        bounds |= (fitz.Rect(box) * m).normalize()
    return bounds


def _assert_is_the_box(black, box):
    """Within a pixel of the box on every side (the edge is drawn half on)."""
    slack = 1.0
    assert box.x0 - slack <= black.x0 and black.x1 <= box.x1 + slack, (black, box)
    assert box.y0 - slack <= black.y0 and black.y1 <= box.y1 + slack, (black, box)
    assert black.width >= box.width - 2 * slack and black.height >= box.height - 2 * slack, (black, box)


SHAPES = [(uu, rotate) for uu in (2, 3) for rotate in (0, 90)]


@pytest.mark.parametrize("uu, rotate", SHAPES, ids=[f"uu{u}-turned{r}" for u, r in SHAPES])
def test_a_box_drawn_by_hand(tmp_path, uu, rotate):
    src = _text_page(tmp_path / "src.pdf", uu, rotate)
    with fitz.open(src) as doc:
        grey = doc[0].get_drawings()[0]["rect"]
    box = fitz.Rect(grey.x0 + grey.width / 4, grey.y0 + grey.height / 4,
                    grey.x1 - grey.width / 4, grey.y1 - grey.height / 4)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, rects=[{"page": 0, "x0": box.x0, "y0": box.y0,
                                       "x1": box.x1, "y1": box.y1}])
    assert res.verification["verified"] is True
    _assert_is_the_box(_new_black(src, out), _as_shown(src, [box]))


@pytest.mark.parametrize("uu, rotate", SHAPES, ids=[f"uu{u}-turned{r}" for u, r in SHAPES])
def test_a_search_term(tmp_path, uu, rotate):
    src = _text_page(tmp_path / "src.pdf", uu, rotate)
    with fitz.open(src) as doc:
        hits = pdf_verify.term_boxes(doc[0], [TERM])
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM])
    assert res.verification["verified"] is True and res.redaction_count == 1
    _assert_is_the_box(_new_black(src, out), _as_shown(src, hits))
    with fitz.open(out) as doc:                          # the lines around it are kept
        text = doc[0].get_text()
        assert "A line above the reference" in text and "A line below the reference" in text


@pytest.mark.parametrize("uu, rotate", [(2, 0), (3, 90)], ids=["uu2-turned0", "uu3-turned90"])
def test_a_place_ocr_found(tmp_path, tessdata, uu, rotate):
    src = _scan_page(tmp_path / "scan.pdf", uu, rotate)
    with fitz.open(src) as doc:
        boxes = pdf_ocr.read_page(doc[0], [TERM], False, tessdata).boxes
    assert boxes
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    _assert_is_the_box(_new_black(src, out), _as_shown(src, boxes))
    outside = next(c for c in res.verification["checks"] if c["id"] == "redaction.ocr_outside")
    assert outside["passed"]


def test_a_page_without_a_user_unit_is_as_before(tmp_path):
    src = _text_page(tmp_path / "src.pdf", 1, 0)
    with fitz.open(src) as doc:
        hits = pdf_verify.term_boxes(doc[0], [TERM])
    out = str(tmp_path / "out.pdf")
    redact_pdf(src, out, search_terms=[TERM])
    _assert_is_the_box(_new_black(src, out), _as_shown(src, hits))


def test_black_past_the_box_on_a_user_unit_page_is_damage(tmp_path, tessdata):
    """The check that nothing changed outside the places OCR found no longer
    puts black past the boxes down to the fill: on a page with a user unit,
    a fill drawn too large, the way PyMuPDF draws its own, is refused."""
    src = _scan_page(tmp_path / "scan.pdf", 2, 0)
    with fitz.open(src) as doc:
        reading = pdf_ocr.read_page(doc[0], [TERM], False, tessdata)
        before = pdf_ocr.fingerprint(doc[0])
        for box in reading.boxes:
            doc[0].add_redact_annot(box, fill=(0, 0, 0))        # PyMuPDF's own fill
        doc[0].apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
        out = str(tmp_path / "too-large.pdf")
        doc.save(out, garbage=4, deflate=True)
    report = pdf_verify.verify_redaction(
        out, [TERM], rects=[{"page": 0, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]}
                            for b in reading.boxes],
        ocr_pages=[{"page": 0, "boxes": [list(b) for b in reading.boxes], "dpi": reading.dpi,
                    "turns": reading.hit_turns, "before": before}], tessdata=tessdata)
    outside = next(c for c in report.checks if c.id == "redaction.ocr_outside")
    assert not outside.passed


# ── The colour of the black ─────────────────────────────────────────────
# A page can be drawn in a transparency group with its own colour space,
# /Group << /S /Transparency /CS /DeviceCMYK >>, as print exports often are.
# Black written as grey (0 g) there is converted through that group and
# shows dark grey (about 35, 31, 32); black written as RGB, as PyMuPDF
# writes its own fill (0 0 0 rg), shows as black (about 8, 7, 6).

def _in_a_cmyk_group(path):
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj["/Group"] = Dictionary(S=Name.Transparency, CS=Name.DeviceCMYK)
        pdf.save(path)
    return path


def _rgb_under(path, box, dpi=72):
    """The page as shown, in RGB, inside *box* (page space), a pixel in."""
    with fitz.open(path) as doc:
        page = doc[0]
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False, annots=False)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
        r = (fitz.Rect(box) * pdf_verify.page_to_picture_matrix(page)
             * fitz.Matrix(dpi / 72, dpi / 72)).normalize()
    return a[int(r.y0) + 1:int(r.y1) - 1, int(r.x0) + 1:int(r.x1) - 1]


@pytest.mark.parametrize("uu", [1, 2])
def test_the_black_is_as_dark_as_before_in_a_cmyk_group(tmp_path, uu):
    src = _in_a_cmyk_group(_text_page(tmp_path / "src.pdf", uu, 0))
    with fitz.open(src) as doc:
        grey = doc[0].get_drawings()[0]["rect"]
    box = fitz.Rect(grey.x0 + grey.width / 4, grey.y0 + grey.height / 4,
                    grey.x1 - grey.width / 4, grey.y1 - grey.height / 4)
    out = str(tmp_path / "out.pdf")
    redact_pdf(src, out, rects=[{"page": 0, "x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1}])
    # PyMuPDF's own black on the same page, as main drew it.
    with fitz.open(src) as doc:
        doc[0].add_redact_annot(box, fill=(0, 0, 0))
        doc[0].apply_redactions()
        own = str(tmp_path / "pymupdf.pdf")
        doc.save(own)
    ours, theirs = _rgb_under(out, box), _rgb_under(own, box)
    assert ours.size and int(ours.max()) <= int(theirs.max()) + 2, (ours.max(), theirs.max())
    assert int(ours.max()) <= 16


@pytest.mark.parametrize("uu", [1, 2])
def test_an_ocr_redaction_in_a_cmyk_group_verifies(tmp_path, tessdata, uu):
    """redaction.ocr_fill wants each place OCR found to show only black: in a
    CMYK group, black written as grey showed dark grey, and was refused. (An
    RGB scan, as scanners give them. A grey one in such a group is refused
    by redaction.ocr_outside, on main as well: another matter.)"""
    src = _in_a_cmyk_group(_scan_page(tmp_path / "scan.pdf", uu, 0, rgb=True))
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    fill = next(c for c in res.verification["checks"] if c["id"] == "redaction.ocr_fill")
    assert fill["passed"]


def test_pages_that_share_their_content_get_one_fill_each(tmp_path):
    """Two pages drawing the same content stream: each gets its own black
    box, once. The fill is not written into the stream they share."""
    pdf = pikepdf.Pdf.new()
    shared = pdf.make_stream(b"BT /F1 9 Tf 30 316 Td (The reference ACME-4471 is here.) Tj ET")
    font = _font(pdf)
    for _ in range(2):
        pdf.pages.append(pikepdf.Page(Dictionary(
            Type=Name.Page, MediaBox=[0, 0, W, H], UserUnit=2,
            Resources=Dictionary(Font=Dictionary(F1=font)), Contents=shared)))
    src = str(tmp_path / "shared.pdf")
    pdf.save(src)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM])
    assert res.verification["verified"] is True and res.redaction_count == 2
    with fitz.open(out) as doc:
        for page in doc:
            fills = [d for d in page.get_drawings() if d.get("fill") == (0.0, 0.0, 0.0)]
            assert len(fills) == 1, [d["rect"] for d in fills]
