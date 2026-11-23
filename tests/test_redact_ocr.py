"""Redact reads scanned pages with OCR when asked (#128, Part A).

``redact_pdf(..., ocr=True)`` reads every page a search has no text to read
on (pdf_ops._search_fact says "no_text") with PyMuPDF's built-in Tesseract,
finds the term on what it read with the matcher the rest of Redact uses,
and redacts what it found through the box path, with the pixels under each
box blanked. Its own checks then look at what the page shows, at the
picture data under each box, and at what OCR reads on the page now.

Every scan is built here: lines of type drawn with PyMuPDF's Helvetica,
rendered to a picture, turned, skewed, encoded and placed on a page by
hand. Whether a word was covered is judged without the code under test: a
second scan of the same page with only that word drawn goes through the
same steps, and its dark pixels are where the word's ink is; in the output
every one of them must be black.

These tests read pictures, so they need the OCR language file (the
``tessdata`` fixture in conftest.py). Qt-free (see CLAUDE.md).
"""

import io
import pathlib
import re
import threading
import time
import zlib

import numpy as np
import pikepdf
import pytest
from pikepdf import Dictionary, Name
from PIL import Image

import pdf_ocr
import pdf_ops
import pdf_verify
from pdf_ops import RedactionVerificationError, redact_pdf

fitz = pytest.importorskip("fitz")

DPI = 200                 # the scanner's resolution
SIZE = (396, 306)         # a small page, as read, in points: quick to read
LINES = ["Account summary for the year",
         "The account number is 4455-",
         "8870 and it is due today.",
         "Please keep this letter safe."]
TERM = "4455-8870"
NEIGHBOUR = "letter"      # a word two lines below the term


# ── Building scans ──────────────────────────────────────────────────────

def _words(only=None, lines=LINES, size=SIZE, font=14, top=60, gap=28):
    """The page as it is read, as a grey picture: every word of *lines*, or
    only those in *only*, each where it stands in the whole page."""
    doc = fitz.open()
    page = doc.new_page(width=size[0], height=size[1])
    y = top
    for line in lines:
        x = 40
        for word in line.split(" "):
            if only is None or word in only:
                page.insert_text((x, y), word, fontname="helv", fontsize=font)
            x += fitz.get_text_length(word + " ", fontname="helv", fontsize=font)
        y += gap
    pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _scanned(img, *, turn=0, skew=0.0, rgb=True):
    """*img* as a scanner gives it: skewed a little, turned clockwise by
    *turn* (a sideways or upside-down scan), in colour or grey."""
    if skew:
        img = img.rotate(skew, resample=Image.BICUBIC, fillcolor=255)
    if turn:
        img = img.rotate(-turn, expand=True)
    return img.convert("RGB") if rgb else img


def _image_stream(pdf, img, jpeg):
    if jpeg:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        data, filt = buf.getvalue(), Name.DCTDecode
    else:
        data, filt = zlib.compress(img.tobytes()), Name.FlateDecode
    return pdf.make_stream(data, Type=Name.XObject, Subtype=Name.Image, Width=img.width,
                           Height=img.height, BitsPerComponent=8, Filter=filt,
                           ColorSpace=Name.DeviceRGB if img.mode == "RGB" else Name.DeviceGray)


def _pdf(path, img, *, jpeg=True, rotate=0, crop=False, user_unit=None, pages=1):
    """A PDF whose pages show *img* (the page as shown) filling the page.
    With *rotate*, the picture is stored turned back and the page's /Rotate
    turns it to *img*; with *crop*, the media box is larger and the crop box
    is the picture; with *user_unit*, the page is written that many times
    smaller. *pages* pages show the one picture."""
    stored = img.rotate(rotate, expand=True) if rotate else img
    w, h = stored.width * 72 / DPI, stored.height * 72 / DPI
    pdf = pikepdf.Pdf.new()
    image = _image_stream(pdf, stored, jpeg)
    for _ in range(pages):
        page = Dictionary(Type=Name.Page, Resources=Dictionary(XObject=Dictionary(Im0=image)))
        if crop:
            page.MediaBox = [0, 0, w + 80, h + 120]
            page.CropBox = [40, 60, 40 + w, 60 + h]
            draw = f"q {w} 0 0 {h} 40 60 cm /Im0 Do Q"
        elif user_unit:
            page.MediaBox = [0, 0, w / user_unit, h / user_unit]
            page.UserUnit = user_unit
            draw = f"q {w / user_unit} 0 0 {h / user_unit} 0 0 cm /Im0 Do Q"
        else:
            page.MediaBox = [0, 0, w, h]
            draw = f"q {w} 0 0 {h} 0 0 cm /Im0 Do Q"
        if rotate:
            page.Rotate = rotate
        page.Contents = pdf.make_stream(draw.encode())
        pdf.pages.append(pikepdf.Page(page))
    pdf.save(str(path))
    return str(path)


def _scan_pair(tmp_path, name, **shape):
    """The scan, and the oracle for the term and for its neighbour: the same
    page with only that word drawn, through the same steps, kept lossless."""
    scan_kw = {k: shape.pop(k) for k in ("turn", "skew", "rgb") if k in shape}
    oracle_kw = {**scan_kw, "rgb": False}
    page_kw = {k: v for k, v in shape.items() if k != "jpeg"}
    src = _pdf(tmp_path / f"{name}.pdf", _scanned(_words(), **scan_kw), **shape)
    term = _pdf(tmp_path / f"{name}-term.pdf", _scanned(_words(only={"4455-", "8870"}), **oracle_kw),
                jpeg=False, **page_kw)
    near = _pdf(tmp_path / f"{name}-near.pdf", _scanned(_words(only={NEIGHBOUR}), **oracle_kw),
                jpeg=False, **page_kw)
    return src, term, near


def _shown(path, pno=0, dpi=100):
    """Page *pno* as shown (turned, cut to its crop box), grey, as an array."""
    with fitz.open(path) as doc:
        pix = doc[pno].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()


def _halo(ink, width=2):
    """The pixels within *width* of *ink*, *ink* itself left out: the paper
    round each letter, light in the scan whether or not the letter is
    there."""
    grown = ink.copy()
    for _ in range(width):
        step = grown.copy()
        step[1:] |= grown[:-1]
        step[:-1] |= grown[1:]
        step[:, 1:] |= grown[:, :-1]
        step[:, :-1] |= grown[:, 1:]
        grown = step
    return grown & ~ink


def _covered(out, oracle, pno=0):
    """How much of the word is blacked out in *out*: (black, all), counted
    on the paper round its letters. Ink is dark whether it is covered or
    not; the paper round it turns black only under a box."""
    round_it = _halo(_shown(oracle, pno) < 160)
    return int((_shown(out, pno)[round_it] < 40).sum()), int(round_it.sum())


def _blackened(out, src, oracle, pno=0):
    """How much more of the area round a word is black in *out* than in
    *src*: 0 for a word left alone, near 1 for a word blacked out."""
    ink = _shown(oracle, pno) < 160
    ys, xs = np.nonzero(ink)
    box = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    return float((_shown(out, pno)[box] < 40).mean() - (_shown(src, pno)[box] < 40).mean())


def _ocr_read(path, tessdata, pno=0):
    """Everything OCR reads on page *pno* of *path*, at every turn, with no
    spaces or line ends (the term is split across a line)."""
    with fitz.open(path) as doc:
        page = doc[pno]
        return "".join("".join(pdf_ocr.read_text(page, tessdata, turn).split())
                       for turn in (0, 90, 180, 270))


def _note(res, kind):
    return next((n for n in res.notes if n["kind"] == kind), None)


# ── Reading a scan, in every shape ─────────────────────────────────────

SHAPES = {
    "rgb jpeg": {},
    "grey jpeg": {"rgb": False},
    "rgb lossless": {"jpeg": False},
    "grey lossless": {"rgb": False, "jpeg": False},
    "page turned 90": {"rotate": 90},
    "page turned 180": {"rotate": 180},
    "page turned 270": {"rotate": 270},
    "sideways scan": {"turn": 90},
    "upside-down scan": {"turn": 180},
    "sideways scan on a page turned 270": {"turn": 90, "rotate": 270},
    "crop box moved": {"crop": True},
    "crop box moved, page turned 90": {"crop": True, "rotate": 90},
    "user unit 2": {"user_unit": 2},
    "user unit 2, page turned 90": {"user_unit": 2, "rotate": 90},
    "skewed 1.5 degrees": {"skew": 1.5},
    "skewed 0.8 degrees, grey": {"skew": -0.8, "rgb": False},
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_term_in_a_scan_is_found_and_blacked_out(tmp_path, tessdata, shape):
    src, term, near = _scan_pair(tmp_path, "scan", **dict(SHAPES[shape]))
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    black, ink = _covered(out, term)
    assert ink > 100 and black == ink, f"{ink - black} of {ink} ink pixels of the term left"
    assert _blackened(out, src, near) < 0.25            # the word two lines down is left
    assert TERM not in _ocr_read(out, tessdata)
    assert res.surface_counts.get("ocr_page_content") == 2      # "4455-" and "8870"
    assert res.redaction_count == 2 and res.pages_affected == 1
    assert _note(res, "ocr") == {"kind": "ocr", "pages": [1], "hits": {1: 2}}
    assert _note(res, "no_text") is None


#: Lines 16 points apart, with a word right under the term.
TIGHT = ["Notes from the visit made in May",
         "after the vote on 4455-8870 today",
         "under the oak by 5566-7788 here",
         "and then we all went back home"]


@pytest.mark.parametrize("skew", [0.0, 1.5])
def test_the_margin_stops_at_the_next_word(tmp_path, tessdata, skew):
    """A box grows by 2 points and a quarter of its height, but the quarter
    goes no further than the edge of another word OCR read: on lines set
    close, it took a third or more of the paper round the word below (#128,
    measured 0.35, and 0.48 tilted). The term is still covered whole."""
    scan = _scanned(_words(lines=TIGHT, gap=16), skew=skew)
    src = _pdf(tmp_path / "tight.pdf", scan)
    term = _pdf(tmp_path / "term.pdf", _scanned(_words(only={"4455-8870"}, lines=TIGHT, gap=16),
                                                skew=skew, rgb=False), jpeg=False)
    below = _pdf(tmp_path / "below.pdf", _scanned(_words(only={"5566-7788"}, lines=TIGHT, gap=16),
                                                  skew=skew, rgb=False), jpeg=False)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    black, ink = _covered(out, term)
    assert ink > 100 and black == ink
    harmed, near = _covered(out, below)
    assert harmed / near < 0.25, f"{harmed} of {near} pixels round the word below blacked"


def test_a_scan_read_at_150_dpi(tmp_path, tessdata, monkeypatch):
    monkeypatch.setattr(pdf_ocr, "DPI", 150)
    src, term, _near = _scan_pair(tmp_path, "scan")
    out = str(tmp_path / "out.pdf")
    redact_pdf(src, out, search_terms=[TERM], ocr=True)
    black, ink = _covered(out, term)
    assert black == ink


def test_one_picture_on_two_pages_is_read_and_redacted_on_each(tmp_path, tessdata):
    src, term, _near = _scan_pair(tmp_path, "two", pages=2)
    with pikepdf.open(src) as pdf:
        assert pdf.pages[0].Resources.XObject.Im0.objgen == pdf.pages[1].Resources.XObject.Im0.objgen
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    for pno in (0, 1):
        black, ink = _covered(out, term, pno)
        assert ink > 100 and black == ink
        assert TERM not in _ocr_read(out, tessdata, pno)
    assert _note(res, "ocr")["hits"] == {1: 2, 2: 2}
    # No picture in the output holds the term any longer: each page's own.
    with fitz.open(out) as doc:
        for xref in {info["xref"] for p in doc for info in p.get_image_info(xrefs=True)}:
            pix = fitz.Pixmap(doc, xref)
            if pix.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            one = fitz.open()
            page = one.new_page(width=pix.width, height=pix.height)
            page.insert_image(page.rect, pixmap=pix)
            assert TERM not in "".join(pdf_ocr.read_text(page, tessdata, 0).split())


def test_a_scan_stored_as_an_inline_picture(tmp_path, tessdata):
    """Some scanners write the picture into the page's content itself (BI
    ... ID ... EI): it has no object of its own, and the check of the
    picture's data reads it from the page."""
    img = _scanned(_words(), rgb=False)
    w, h = img.width * 72 / DPI, img.height * 72 / DPI
    pdf = pikepdf.Pdf.new()
    content = (f"q {w} 0 0 {h} 0 0 cm BI /W {img.width} /H {img.height} /CS /G /BPC 8 /F /Fl ID ".encode()
               + zlib.compress(img.tobytes()) + b" EI Q")
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, w, h],
                                             Resources=Dictionary(), Contents=pdf.make_stream(content))))
    src = str(tmp_path / "inline.pdf")
    pdf.save(src)
    term = _pdf(tmp_path / "inline-term.pdf", _scanned(_words(only={"4455-", "8870"}), rgb=False), jpeg=False)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    black, ink = _covered(out, term)
    assert black == ink
    image = next(c for c in res.verification["checks"] if c["id"] == "redaction.ocr_image")
    assert image["passed"] and not image["unevaluated"]


def test_a_picture_left_unblanked_inline_is_caught(tmp_path, tessdata):
    """The same, with the black box drawn over an inline picture whose
    pixels were not blanked."""
    img = _scanned(_words(), rgb=False)
    w, h = img.width * 72 / DPI, img.height * 72 / DPI
    pdf = pikepdf.Pdf.new()
    content = (f"q {w} 0 0 {h} 0 0 cm BI /W {img.width} /H {img.height} /CS /G /BPC 8 /F /Fl ID ".encode()
               + zlib.compress(img.tobytes()) + b" EI Q")
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, w, h],
                                             Resources=Dictionary(), Contents=pdf.make_stream(content))))
    src = str(tmp_path / "inline.pdf")
    pdf.save(src)
    boxes = _term_boxes(src, tessdata)
    out = _redacted_by_hand(tmp_path, src, boxes, blank=False)
    _report, checks = _ocr_checks(out, boxes, tessdata)
    assert not checks["redaction.ocr_image"].passed
    assert not checks["redaction.ocr_image"].unevaluated


# ── Pages OCR cannot read ──────────────────────────────────────────────

def _photo():
    rng = np.random.default_rng(7)
    y, x = np.mgrid[0:612, 0:792]
    img = np.zeros((612, 792, 3))
    for _ in range(12):
        cy, cx, r = rng.uniform(0, 612), rng.uniform(0, 792), rng.uniform(40, 200)
        img += np.exp(-((y - cy) ** 2 + (x - cx) ** 2) / (2 * r * r))[..., None] * rng.uniform(0, 160, 3)
    img += (x / 792 * 60)[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def _noise(seed=11):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (612, 792), dtype=np.uint8), "L").convert("RGB")


def _blank():
    return Image.new("RGB", (792, 612), (255, 255, 255))


#: Noise pictures OCR reads a few common words in, at one turn or another,
#: enough for the share rule as it was: seeds 2, 13, 14, 17 and 23 on
#: PyMuPDF 1.27.2.3 and 0, 14, 15 and 17 on 2.1 (Windows), and 11 on 2.1 on
#: Linux (CI) (#128).
NOISE_SEEDS = (0, 2, 11, 13, 14, 15, 17, 23)
PICTURES = {"photo": _photo, "blank": _blank,
            **{f"noise {seed}": (lambda seed=seed: _noise(seed)) for seed in NOISE_SEEDS}}


def _with_text_page(tmp_path, img):
    """A file with a page of ordinary type holding the term, so that the run
    has something to remove, and then the picture page."""
    path = tmp_path / "pic.pdf"
    _pdf(path, img)
    with fitz.open(str(path)) as doc:
        page = doc.new_page(0, width=400, height=300)
        page.insert_text((40, 60), f"Reference {TERM} in type", fontname="helv", fontsize=12)
        out = str(tmp_path / "mixed.pdf")
        doc.save(out)
    return out


@pytest.mark.parametrize("picture", list(PICTURES))
def test_a_picture_with_no_words_is_noted_as_not_read(tmp_path, tessdata, picture):
    src = _with_text_page(tmp_path, PICTURES[picture]())
    before = _shown(src, 1)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    assert _note(res, "ocr_unread") == {"kind": "ocr_unread", "pages": [2], "too_large": []}
    assert _note(res, "ocr") is None and _note(res, "no_text") is None
    assert "ocr_page_content" not in res.surface_counts
    assert np.array_equal(_shown(out, 1), before)          # nothing on it was touched


def test_a_page_too_large_to_read_is_not_drawn(tmp_path, tessdata, monkeypatch):
    path = tmp_path / "huge.pdf"
    _pdf(path, _blank())
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.pages[0].MediaBox = [0, 0, 14400, 14400]        # 200 by 200 inches
        pdf.pages[0].Contents = pdf.make_stream(b"q 14400 0 0 14400 0 0 cm /Im0 Do Q")
        pdf.save(path)
    src = _with_text_page(tmp_path, _blank())
    with fitz.open(src) as doc, fitz.open(str(path)) as huge:
        doc.insert_pdf(huge)
        both = str(tmp_path / "both.pdf")
        doc.save(both)
    drawn = []
    real = pdf_ocr._render
    monkeypatch.setattr(pdf_ocr, "_render", lambda page, *a, **k: (drawn.append(page.rect.width), real(page, *a, **k))[1])
    said = []
    res = redact_pdf(both, str(tmp_path / "out.pdf"), search_terms=[TERM], ocr=True,
                     on_status=said.append)
    assert 14400 not in drawn
    assert said == ["Reading page 2 of 3 with OCR"]           # page 3 is not read
    assert pdf_ocr.reading_dpi(fitz.open(both)[2]) is None
    assert _note(res, "ocr_unread") == {"kind": "ocr_unread", "pages": [2, 3], "too_large": [3]}


def test_the_rule_for_what_looks_like_words():
    assert pdf_ocr.looks_like_words("The account number is due today, and the letter is "
                                    "about the year that has been.")
    assert pdf_ocr.looks_like_words("HOW BIG MEDIA USES TECHNOLOGY AND THE LAW")
    # What OCR reads in an upside-down page, a photo, and nothing at all:
    assert not pdf_ocr.looks_like_words("sinords uoywusofuy sdadpg suiysoy AUSIOATULE pue")
    assert not pdf_ocr.looks_like_words("Sees ste Lees RMAC leas Suge Ceres Ene " * 40 + "the and for")
    assert not pdf_ocr.looks_like_words("")
    assert not pdf_ocr.looks_like_words("an in to is it of on at by or")     # two letters say nothing
    # Noise read sideways on PyMuPDF 2.1: three common words, seven times,
    # among 115 tokens (6.1%).
    gibberish = " ".join(["see", "each", "the", "see", "each", "see", "the"] + ["Vr4"] * 108)
    assert not pdf_ocr.looks_like_words(gibberish)


# ── When OCR runs, and when it does not ────────────────────────────────

def test_ocr_is_off_unless_asked(tmp_path, tessdata, monkeypatch):
    src = _with_text_page(tmp_path, _scanned(_words()))
    monkeypatch.setattr(pdf_ocr, "read_page", lambda *a, **k: pytest.fail("OCR ran"))
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM])
    assert _note(res, "no_text") == {"kind": "no_text", "pages": [2]}
    assert TERM in _ocr_read(out, tessdata, 1)       # as before #128


def test_boxes_alone_never_use_ocr(tmp_path, tessdata, monkeypatch):
    src, _term, _near = _scan_pair(tmp_path, "scan")
    monkeypatch.setattr(pdf_ocr, "read_page", lambda *a, **k: pytest.fail("OCR ran"))
    res = redact_pdf(src, str(tmp_path / "out.pdf"), ocr=True,
                     rects=[{"page": 0, "x0": 10, "y0": 10, "x1": 60, "y1": 30}])
    assert res.verification["verified"] is True


def test_a_page_with_a_text_layer_is_not_read_again(tmp_path, tessdata, monkeypatch):
    src, _term, _near = _scan_pair(tmp_path, "scan")
    with fitz.open(src) as doc:
        doc[0].insert_text((30, 30), "hidden layer words", render_mode=3, fontsize=10)
        doc[0].insert_text((30, 50), "Reference ABC-123", render_mode=3, fontsize=10)
        layered = str(tmp_path / "layered.pdf")
        doc.save(layered)
    monkeypatch.setattr(pdf_ocr, "read_page", lambda *a, **k: pytest.fail("OCR ran"))
    res = redact_pdf(layered, str(tmp_path / "out.pdf"), search_terms=["ABC-123"], ocr=True)
    assert _note(res, "text_layer")["pages"] == [1]


def test_ocr_asked_for_but_not_set_up(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_ocr, "ocr_status", lambda: {
        "ready": False, "tessdata": None, "file": None, "source": None, "pinned": False,
        "problem": "OCR is not set up"})
    monkeypatch.setattr(pdf_ocr, "read_page", lambda *a, **k: pytest.fail("OCR ran"))
    src = _with_text_page(tmp_path, _scanned(_words()))
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    assert _note(res, "no_text") == {"kind": "no_text", "pages": [2]}
    assert _note(res, "ocr_not_ready") == {"kind": "ocr_not_ready"}


def test_nothing_found_says_ocr_can_miss_words(tmp_path, tessdata):
    src, _term, _near = _scan_pair(tmp_path, "scan")
    with pytest.raises(ValueError) as info:
        redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["ZEBRA-0001"], ocr=True)
    words = str(info.value)
    assert words.startswith("No matching content found to redact.")
    assert "Page 1 was read by OCR, which can miss words" in words
    assert "check" in words


# ── Pages that are mostly a picture, with a little text on them ────────

def _with_a_few_words(tmp_path, how):
    """The scan with a little text the search can read on it: "Page 1 of 1"
    typed under the picture's words, or a FreeText note saying "Received"."""
    src, term, near = _scan_pair(tmp_path, "scan")
    with fitz.open(src) as doc:
        page = doc[0]
        if how == "typed":
            page.insert_text((page.rect.width - 90, page.rect.height - 14), "Page 1 of 1",
                             fontname="helv", fontsize=9)
        else:
            page.add_freetext_annot(fitz.Rect(page.rect.width - 110, 8, page.rect.width - 10, 30),
                                    "Received", fontsize=11)
        out = str(tmp_path / f"{how}.pdf")
        doc.save(out)
    return out, term, near


@pytest.mark.parametrize("how", ["typed", "note"])
def test_a_scan_with_a_few_words_on_it_is_noted_without_ocr(tmp_path, tessdata, how, monkeypatch):
    src, _term, _near = _with_a_few_words(tmp_path, how)
    monkeypatch.setattr(pdf_ocr, "read_page", lambda *a, **k: pytest.fail("OCR ran"))
    # Something the search can find, so that the run finishes with a result.
    with fitz.open(src) as doc:
        doc.new_page(-1, width=300, height=200).insert_text((20, 40), f"Ref {TERM}", fontsize=11)
        both = str(tmp_path / "both.pdf")
        doc.save(both)
    res = redact_pdf(both, str(tmp_path / "out.pdf"), search_terms=[TERM])
    assert _note(res, "mostly_picture") == {"kind": "mostly_picture", "pages": [1]}
    assert _note(res, "no_text") is None
    # And with nothing found at all, the message says so.
    with pytest.raises(ValueError) as info:
        redact_pdf(src, str(tmp_path / "out2.pdf"), search_terms=["ZEBRA-0001"])
    assert "Page 1 is mostly a picture" in str(info.value)


@pytest.mark.parametrize("how", ["typed", "note"])
def test_a_scan_with_a_few_words_on_it_is_read_with_ocr(tmp_path, tessdata, how):
    src, term, near = _with_a_few_words(tmp_path, how)
    with fitz.open(src) as doc:
        before_words = doc[0].get_text().split()
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    black, ink = _covered(out, term)
    assert ink > 100 and black == ink
    assert _blackened(out, src, near) < 0.25
    assert res.surface_counts.get("ocr_page_content") == 2
    assert _note(res, "ocr") == {"kind": "ocr", "pages": [1], "hits": {1: 2}}
    assert _note(res, "mostly_picture") is None
    with fitz.open(out) as doc:                          # the few words are still there
        assert doc[0].get_text().split() == before_words


def test_on_such_a_page_a_term_in_its_text_is_left_to_the_search(tmp_path, tessdata):
    """The term typed on the scan as well as in it: the search takes the
    typed one by its characters, OCR the one in the picture; the typed one,
    which OCR reads too, is not taken twice. The search's box does not
    count as damage."""
    src, term, _near = _scan_pair(tmp_path, "scan")
    with fitz.open(src) as doc:
        page = doc[0]
        page.insert_text((page.rect.width - 150, page.rect.height - 14), f"Ref {TERM}",
                         fontname="helv", fontsize=12)
        typed = str(tmp_path / "typed.pdf")
        doc.save(typed)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(typed, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True
    assert res.surface_counts.get("page_content") == 1
    assert res.surface_counts.get("ocr_page_content") == 2
    with fitz.open(out) as doc:
        assert "Ref" in doc[0].get_text() and TERM not in doc[0].get_text()
    black, ink = _covered(out, term)
    assert black == ink


def test_a_redaction_with_ocr_uses_no_network(tmp_path, tessdata, monkeypatch):
    import socket
    import urllib.request

    def refuse(*_a, **_k):
        raise AssertionError("the network was used")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    src, _term, _near = _scan_pair(tmp_path, "scan")
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True


# ── What the window is told ─────────────────────────────────────────────

_NOT_READY = {"ready": False, "tessdata": None, "file": None, "source": None, "pinned": False,
              "problem": "The OCR language file /opt/tools/ocr-data/eng.traineddata does not "
                         "match the pinned file."}


def test_the_window_is_told_whether_ocr_is_ready_and_nothing_more(monkeypatch):
    """{ready, reason} only, in plain words: never the language file's path,
    which sits in the user's own folder (#128)."""
    monkeypatch.setattr(pdf_ocr, "ocr_status", lambda: dict(_NOT_READY))
    assert pdf_ocr.ocr_readiness(frozen=True) == {
        "ready": False,
        "reason": "The OCR language file is missing from this copy of PDF Toolkit: reinstall it."}
    assert pdf_ocr.ocr_readiness(frozen=False) == {
        "ready": False,
        "reason": "OCR isn't set up: run python setup_ocr.py --install, then restart the app."}
    monkeypatch.setattr(pdf_ocr, "ocr_status", lambda: {
        **_NOT_READY, "problem": "OCR needs numpy to check what it redacted, and numpy is not "
                                 "installed."})
    assert pdf_ocr.ocr_readiness(frozen=False) == {
        "ready": False,
        "reason": "OCR isn't set up: it needs numpy (pip install numpy), then restart the app."}
    monkeypatch.setattr(pdf_ocr, "ocr_status", lambda: {
        **_NOT_READY, "ready": True, "tessdata": "/opt/tools/ocr-data", "source": "installed",
        "file": "/opt/tools/ocr-data/eng.traineddata", "pinned": True, "problem": None})
    assert pdf_ocr.ocr_readiness(frozen=False) == {"ready": True, "reason": None}


def test_the_bridge_passes_ocr_on_and_tells_the_window_only_readiness():
    bridge = (pathlib.Path(__file__).resolve().parents[1] / "ui" / "bridge.py").read_text(encoding="utf-8")
    slot = bridge[bridge.index("def startRedact"):]
    slot = slot[:slot.index("self._run_in_thread")]
    assert 'ocr=bool(p.get("ocr"' in slot and "on_status=" in slot
    status = bridge[bridge.index("def getOcrStatus"):]
    end = re.search(r"\n(    @Slot|    def |    # ──|\S)", status[1:])
    status = status[:end.start() + 1] if end else status
    assert "ocr_readiness()" in status
    assert "tessdata" not in status and '["file"]' not in status and "log." not in status


def test_progress_says_when_a_page_is_read_with_ocr(tmp_path, tessdata):
    src, _term, _near = _scan_pair(tmp_path, "scan", pages=2)
    said = []
    redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM], ocr=True,
               on_status=said.append)
    assert said == ["Reading page 1 of 2 with OCR", "Reading page 2 of 2 with OCR"]


# ── Progress and cancel ────────────────────────────────────────────────

def test_progress_counts_the_pages_read_by_ocr(tmp_path, tessdata):
    src, _term, _near = _scan_pair(tmp_path, "scan", pages=3)
    seen = []
    redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM], ocr=True,
               on_progress=lambda cur, tot: seen.append((cur, tot)))
    assert seen[:3] == [(1, 3), (2, 3), (3, 3)]


def test_a_cancel_stops_inside_a_page_between_turns(tmp_path, tessdata, monkeypatch):
    """A page that looks like no words is read at four turns; a cancel made
    while the first is read stops it before the second."""
    path = tmp_path / "photo.pdf"
    _pdf(path, _photo())
    cancel = threading.Event()
    calls = []
    real = pdf_ocr._ocr

    def first_then_cancel(*a, **k):
        calls.append(time.perf_counter())
        cancel.set()
        return real(*a, **k)
    monkeypatch.setattr(pdf_ocr, "_ocr", first_then_cancel)
    out = tmp_path / "out.pdf"
    with pytest.raises(InterruptedError, match="Cancelled"):
        redact_pdf(str(path), str(out), search_terms=[TERM], ocr=True, cancel=cancel)
    assert len(calls) == 1 and not out.exists()


# ── The checks of their own ─────────────────────────────────────────────

def test_a_mapping_gone_wrong_is_refused(tmp_path, tessdata, monkeypatch):
    """The verifier cannot see pixels: with the OCR boxes put in the wrong
    place, the term stays readable and every text check passes. The re-read
    finds it; the run is refused, nothing is saved, and no flatten is
    offered (a picture of the page would show the term as well)."""
    real = pdf_ocr._to_page

    def shifted(*a, **k):
        return [fitz.Rect(r) + (0, 60, 0, 60) for r in real(*a, **k)]
    monkeypatch.setattr(pdf_ocr, "_to_page", shifted)
    src, _term, _near = _scan_pair(tmp_path, "scan")
    out = tmp_path / "out.pdf"
    with pytest.raises(RedactionVerificationError) as info:
        redact_pdf(src, str(out), search_terms=[TERM], ocr=True)
    failed = {c["id"] for c in info.value.report["checks"] if not c["passed"]}
    assert "redaction.ocr_reread" in failed
    assert info.value.flatten_pages is None and not out.exists()
    lines = " ".join(info.value.detail["found"])
    assert "page 1" in lines and "OCR" in lines


def _redacted_by_hand(tmp_path, src, boxes, *, fill=True, blank=True, after=b""):
    """*src* with *boxes* redacted by MuPDF directly, as redact_pdf applies
    a box (the page set upright for it, pdf_verify.upright; the black
    written by pdf_ops._fill_black): with or without the fill, with or
    without the picture's pixels blanked, and with *after* drawn on top of
    the page last."""
    with fitz.open(src) as doc:
        page = doc[0]
        for box in boxes:
            page.add_redact_annot(fitz.Rect(box), fill=False)
        with pdf_verify.upright(page):
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS if blank
                                  else fitz.PDF_REDACT_IMAGE_NONE)
        if fill:                     # the black, the size of the box (#134)
            pdf_ops._fill_black(page, [fitz.Rect(b) for b in boxes])
        if after:
            xref = page.get_contents()[-1]
            doc.update_stream(xref, doc.xref_stream(xref) + b"\n" + after)
        out = str(tmp_path / "by-hand.pdf")
        doc.save(out, garbage=4, deflate=True)
    return out


def _term_boxes(src, tessdata):
    with fitz.open(src) as doc:
        return pdf_ocr.read_page(doc[0], [TERM], False, tessdata).boxes


def _found(src, tessdata):
    """Where OCR finds the term on page 1 of *src*, and at which turns."""
    with fitz.open(src) as doc:
        reading = pdf_ocr.read_page(doc[0], [TERM], False, tessdata)
        return reading.boxes, reading.hit_turns


def _ocr_checks(out, boxes, tessdata, turns=(0,), **extra):
    report = pdf_verify.verify_redaction(
        out, [TERM], rects=[{"page": 0, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]} for b in boxes],
        ocr_pages=[{"page": 0, "boxes": [list(b) for b in boxes], "dpi": 300, "turns": list(turns),
                    **extra}],
        tessdata=tessdata)
    return report, {c.id: c for c in report.checks if c.id.startswith("redaction.ocr")}


#: The shapes the checks are shown failing on: upright, and turned by every
#: way a page can be (the check could otherwise look in the wrong place, at
#: blank paper, and pass).
CHECK_SHAPES = ["rgb jpeg", "page turned 90", "sideways scan on a page turned 270",
                "crop box moved, page turned 90", "user unit 2, page turned 90"]


@pytest.mark.parametrize("shape", CHECK_SHAPES)
def test_the_checks_on_turned_pages(tmp_path, tessdata, shape):
    src, _term, _near = _scan_pair(tmp_path, "scan", **dict(SHAPES[shape]))
    boxes, turns = _found(src, tessdata)
    assert len(boxes) == 2
    with fitz.open(src) as doc:
        before = pdf_ocr.fingerprint(doc[0])
        pdf_box = (fitz.Rect(boxes[0]) * ~pdf_verify.pdf_to_page_matrix(doc[0])).normalize()
    # Done properly, every check passes.
    out = _redacted_by_hand(tmp_path, src, boxes)
    _report, checks = _ocr_checks(out, boxes, tessdata, turns, before=before)
    assert all(c.passed for c in checks.values()), [c.evidence for c in checks.values()]
    # Something white drawn over a box, last: the page does not show black.
    white = f"q 1 1 1 rg {pdf_box.x0} {pdf_box.y0} {pdf_box.width} {pdf_box.height} re f Q".encode()
    out = _redacted_by_hand(tmp_path, src, boxes, after=white)
    _report, checks = _ocr_checks(out, boxes, tessdata, turns)
    assert not checks["redaction.ocr_fill"].passed
    # The picture's pixels left as they were under the black boxes.
    out = _redacted_by_hand(tmp_path, src, boxes, blank=False)
    _report, checks = _ocr_checks(out, boxes, tessdata, turns)
    assert checks["redaction.ocr_fill"].passed and not checks["redaction.ocr_image"].passed
    # The boxes somewhere else: OCR still reads the term.
    away = [fitz.Rect(b) + (0, 40, 0, 40) if fitz.Rect(b).height < fitz.Rect(b).width
            else fitz.Rect(b) + (40, 0, 40, 0) for b in boxes]
    out = _redacted_by_hand(tmp_path, src, away)
    _report, checks = _ocr_checks(out, away, tessdata, turns)
    assert not checks["redaction.ocr_reread"].passed


def test_the_checks_pass_on_a_proper_redaction(tmp_path, tessdata):
    src, _term, _near = _scan_pair(tmp_path, "scan")
    boxes = _term_boxes(src, tessdata)
    with fitz.open(src) as doc:
        before = pdf_ocr.fingerprint(doc[0])
    out = _redacted_by_hand(tmp_path, src, boxes)
    report, checks = _ocr_checks(out, boxes, tessdata, before=before)
    assert set(checks) == {"redaction.ocr_fill", "redaction.ocr_image", "redaction.ocr_reread",
                           "redaction.ocr_outside"}
    assert all(c.passed for c in checks.values()), [c.evidence for c in checks.values()]


def test_something_drawn_over_a_box_is_caught(tmp_path, tessdata):
    src, _term, _near = _scan_pair(tmp_path, "scan")
    boxes = _term_boxes(src, tessdata)
    b = fitz.Rect(boxes[0])
    with fitz.open(src) as doc:
        h = doc[0].rect.height
    white = f"q 1 1 1 rg {b.x0} {h - b.y1} {b.width} {b.height} re f Q".encode()
    out = _redacted_by_hand(tmp_path, src, boxes, after=white)
    report, checks = _ocr_checks(out, boxes, tessdata)
    assert not checks["redaction.ocr_fill"].passed
    assert checks["redaction.ocr_fill"].pages == [1]
    assert report.flatten_target_pages() == [1]          # a picture of the page fills it black


def test_picture_data_left_under_a_black_box_is_caught(tmp_path, tessdata):
    """A box filled black over a picture whose pixels were not blanked
    shows black, and OCR cannot read under it; the picture still holds the
    term, for anyone who takes it out of the file."""
    src, _term, _near = _scan_pair(tmp_path, "scan")
    boxes = _term_boxes(src, tessdata)
    out = _redacted_by_hand(tmp_path, src, boxes, blank=False)
    report, checks = _ocr_checks(out, boxes, tessdata)
    assert checks["redaction.ocr_fill"].passed and checks["redaction.ocr_reread"].passed
    assert not checks["redaction.ocr_image"].passed
    assert report.flatten_target_pages() == [1]          # the page's picture is replaced


def test_the_term_still_read_on_the_page_is_caught(tmp_path, tessdata):
    src, _term, _near = _scan_pair(tmp_path, "scan")
    boxes = _term_boxes(src, tessdata)
    wrong = [fitz.Rect(b) + (0, 60, 0, 60) for b in boxes]
    out = _redacted_by_hand(tmp_path, src, wrong)
    report, checks = _ocr_checks(out, wrong, tessdata)
    assert not checks["redaction.ocr_reread"].passed
    assert checks["redaction.ocr_reread"].pages == [1]
    assert report.flatten_target_pages() is None


def test_damage_outside_the_boxes_is_caught(tmp_path, tessdata):
    src, _term, _near = _scan_pair(tmp_path, "scan")
    boxes = _term_boxes(src, tessdata)
    with fitz.open(src) as doc:
        before = pdf_ocr.fingerprint(doc[0])
    out = _redacted_by_hand(tmp_path, src, boxes,
                            after=b"q 0 0 0 rg 10 10 60 20 re f Q")
    report, checks = _ocr_checks(out, boxes, tessdata, before=before)
    assert not checks["redaction.ocr_outside"].passed
    assert report.flatten_target_pages() is None


#: Twenty lines of type, one with the term of the clipped-picture tests.
MINUTES = ["The meeting was held on the third floor of the old building.",
           "Everyone who had been asked to come was there by nine.",
           "The minutes of the last meeting were read and agreed.",
           "A question was asked about the Hollyhock fund for the roof.",
           "The treasurer said the work would start in the spring.",
           "There was some talk about the car park and the new gates.",
           "It was agreed that the gates would be painted green.",
           "The chair thanked the volunteers for their hard work.",
           "A letter from the council was read out to the room.",
           "It said that the path by the river would be closed.",
           "Several people asked how long the closure would last.",
           "No one knew, and the chair said she would find out.",
           "The next item was the summer fair and its stalls.",
           "More helpers are needed for the cake stall this year.",
           "The raffle prizes have been given by local shops.",
           "Tickets will be on sale from the first of the month.",
           "Any other business was taken at the end of the meeting.",
           "The date of the next meeting will be sent by post.",
           "The meeting closed at a quarter past ten that evening.",
           "These notes were written up on the following day."]


def _clipped_scan(path, *, size=(2640, 1896), draw=b"q 1320 0 0 948 33 -156 cm /X4 Do Q", turn=90):
    """A JPEG of *size* pixels holding MINUTES in 26-pixel type, turned
    *turn* degrees, near its top left corner; drawn on a 612 x 792 page by
    *draw*, by default at 144 dpi and larger than the page, so that the page
    shows only part of it (the shape of pdf.js's issue12841)."""
    doc = fitz.open()
    width = max(fitz.get_text_length(t, fontname="helv", fontsize=26) for t in MINUTES) + 40
    page = doc.new_page(width=width, height=36 * len(MINUTES) + 40)
    for i, line in enumerate(MINUTES):
        page.insert_text((20, 56 + i * 36), line, fontname="helv", fontsize=26)
    pix = page.get_pixmap(dpi=72, colorspace=fitz.csGRAY)
    block = Image.frombytes("L", (pix.width, pix.height), pix.samples).rotate(turn, expand=True, fillcolor=255)
    img = Image.new("RGB", size, "white")
    img.paste(block.convert("RGB"), (60, 60))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    pdf = pikepdf.Pdf.new()
    image = pdf.make_stream(buf.getvalue(), Type=Name.XObject, Subtype=Name.Image, Width=size[0],
                            Height=size[1], BitsPerComponent=8, ColorSpace=Name.DeviceRGB,
                            Filter=Name.DCTDecode)
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792],
                                             Resources=Dictionary(XObject=Dictionary(X4=image)),
                                             Contents=pdf.make_stream(draw))))
    pdf.save(str(path))
    return str(path)


def test_a_clipped_jpeg_saved_without_loss_is_not_damage(tmp_path, tessdata):
    """A JPEG drawn at less than half its own resolution is decoded small by
    MuPDF, and differs at the edge of every letter from the same pixels
    stored without loss, as a redacted picture is; and what MuPDF has kept
    from drawing the page before changes how it draws it next. Neither is a
    change to the page: the run verifies, and the outside check ran."""
    src = _clipped_scan(tmp_path / "clipped.pdf")
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=["Hollyhock"], ocr=True)
    assert res.verification["verified"] is True
    checks = {c["id"]: c for c in res.verification["checks"]}
    assert checks["redaction.ocr_outside"]["passed"]
    assert res.surface_counts.get("ocr_page_content") == 1


def test_damage_outside_the_boxes_of_a_clipped_jpeg_is_still_caught(tmp_path, tessdata):
    src = _clipped_scan(tmp_path / "clipped.pdf")
    with fitz.open(src) as doc:
        reading = pdf_ocr.read_page(doc[0], ["Hollyhock"], False, tessdata)
        before = pdf_ocr.fingerprint(doc[0])
    boxes = reading.boxes
    assert len(boxes) == 1
    out = _redacted_by_hand(tmp_path, src, boxes, after=b"q 0.6 0.6 0.6 rg 400 40 30 2 re f Q")
    report = pdf_verify.verify_redaction(
        out, ["Hollyhock"], rects=[{"page": 0, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]} for b in boxes],
        ocr_pages=[{"page": 0, "boxes": [list(b) for b in boxes], "dpi": reading.dpi,
                    "turns": reading.hit_turns, "before": before}], tessdata=tessdata)
    outside = next(c for c in report.checks if c.id == "redaction.ocr_outside")
    assert not outside.passed


def test_on_a_page_with_a_user_unit_the_outside_check_is_as_anywhere(tmp_path, tessdata):
    """The black fill is the box on a page with a user unit too (#134): a
    proper redaction passes with nothing put down to the fill, and anything
    else that changed is caught (test_redact_user_unit.py has the fill drawn
    too large)."""
    src, _term, _near = _scan_pair(tmp_path, "uu", user_unit=2)
    boxes = _term_boxes(src, tessdata)
    with fitz.open(src) as doc:
        before = pdf_ocr.fingerprint(doc[0])
    out = _redacted_by_hand(tmp_path, src, boxes)
    _report, checks = _ocr_checks(out, boxes, tessdata, before=before)
    assert checks["redaction.ocr_outside"].passed
    out = _redacted_by_hand(tmp_path, src, boxes, after=b"q 0.5 0.5 0.5 rg 5 5 30 10 re f Q")
    _report, checks = _ocr_checks(out, boxes, tessdata, before=before)
    assert not checks["redaction.ocr_outside"].passed


def test_each_check_is_said_in_plain_words(tmp_path, tessdata):
    src, _term, _near = _scan_pair(tmp_path, "scan")
    boxes = _term_boxes(src, tessdata)
    wrong = [fitz.Rect(b) + (0, 60, 0, 60) for b in boxes]
    out = _redacted_by_hand(tmp_path, src, wrong, blank=False,
                            after=b"q 1 1 1 rg 0 0 5 5 re f Q")
    report, _checks = _ocr_checks(out, wrong, tessdata)
    detail = pdf_verify.redaction_refusal_detail(report, report.flatten_target_pages())
    text = " ".join(detail["found"])
    for jargon in ("/", "xref", "OCR box", "pixmap", "redaction."):
        assert jargon not in text
    assert "page 1" in text
