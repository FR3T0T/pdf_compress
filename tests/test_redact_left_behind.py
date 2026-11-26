"""What a box redacted, left behind (#150).

MuPDF redacts a picture or form a page draws by giving that page a redacted
copy and resources of its own. When pages share one resources dictionary
(one object, or the page tree's: TCPDF and OpenOffice write them so), every
other page still lists the original, and it stays in the file, with what
the box was put over still in it. Saving with clean=True used to drop what a
page lists and does not draw, on every page; #150 stopped that, because the
same step changed text it should leave alone, and the original picture
stayed: it holds no text, so neither the page-code check nor the whole-file
search saw it, and the run verified.

Now a redaction takes off every page without a box each XObject name its
content does not draw, in a copy of the page's own
(pdf_ops._drop_undrawn_xobjects), and a new check, redaction.left_behind,
refuses a run that leaves in the file, undrawn, a picture or form a page
with a box drew. A word in a form that pages share or one only lists is
dropped the same way, and the run saves again as it did before #150.

Every fixture is built here, by hand. Nothing imported at the top is new,
so the file collects against the tree before the change and each test fails
there for its own reason; the guards that pass there say so.

Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import os
import re
import zlib

import pikepdf
import pytest
from pikepdf import Dictionary, Name

import pdf_ops
import pdf_verify
from pdf_ops import RedactionVerificationError, redact_pdf
from pdf_verify import verify_redaction

fitz = pytest.importorskip("fitz")

TERM = "ACME-4471"
RED = bytes([255, 0, 0]) * 64 * 64
BLUE = bytes([0, 0, 255]) * 64 * 64
OVER_THE_PICTURE = {"page": 0, "x0": 50, "y0": 50, "x1": 250, "y1": 250}
LEFT_BEHIND = "redaction.left_behind"


# ── Builders ─────────────────────────────────────────────────────────────


def _image(pdf, data: bytes, size: int = 64):
    return pdf.make_stream(zlib.compress(data), Type=Name.XObject, Subtype=Name.Image, Width=size,
                           Height=size, ColorSpace=Name.DeviceRGB, BitsPerComponent=8,
                           Filter=Name.FlateDecode)


def _font(pdf):
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1,
                                        BaseFont=Name.Helvetica))


def _shared(path, *contents: bytes, xobjects=None, where: str = "object", size=(300, 300)) -> str:
    """Pages of *size* that share one resources dictionary: one indirect
    object every page points at, or (*where* "tree") the page tree's, which
    every page takes. *xobjects* {name: builder(pdf)} are in it, with
    Helvetica as /F1."""
    pdf = pikepdf.Pdf.new()
    res = pdf.make_indirect(Dictionary(Font=Dictionary(F1=_font(pdf)), XObject=Dictionary(
        {"/" + name: make(pdf) for name, make in (xobjects or {}).items()})))
    for content in contents:
        pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, *size])))
        page = pdf.pages[-1].obj
        page["/Contents"] = pdf.make_stream(content)
        if where == "object":
            page["/Resources"] = res
    if where == "tree":
        # After the pages: adding a page pushes what the tree gives down.
        pdf.Root["/Pages"]["/Resources"] = res
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _the_resources_are_shared(path, where: str) -> bool:
    """Read with PyMuPDF: pikepdf's page list pushes the tree's down."""
    with fitz.open(path) as doc:
        kinds = [doc.xref_get_key(page.xref, "Resources") for page in doc]
        if where == "tree":
            return all(kind == "null" for kind, _ in kinds)
        return len({value for _kind, value in kinds}) == 1 and kinds[0][0] == "xref"


def _with_data(path, data: bytes) -> list:
    """The object ids of the pictures in *path* whose data is *data*."""
    with pikepdf.open(path) as pdf:
        return [x.objgen for x in pdf.objects if isinstance(x, pikepdf.Stream)
                and x.get("/Subtype") == "/Image" and x.read_bytes() == data]


def _listed(path, pno: int) -> list:
    """The /XObject names page *pno* of *path* has, its own or inherited."""
    with fitz.open(path) as doc:
        node = doc[pno].xref
        while doc.xref_get_key(node, "Resources")[0] == "null":
            kind, parent = doc.xref_get_key(node, "Parent")
            if kind != "xref":
                return []
            node = int(parent.split()[0])
        what, value = doc.xref_get_key(node, "Resources/XObject")
        if what == "xref":
            value = doc.xref_object(int(value.split()[0]))
        elif what != "dict":
            return []
        return sorted(re.findall(r"/(\w+)\s*\d+ \d+ R", value))


PICTURE = b"q 200 0 0 200 50 50 cm /Im0 Do Q "
LINE_ONE = b"BT /F1 12 Tf 20 270 Td (page one) Tj ET"
LINE_TWO = b"BT /F1 12 Tf 20 270 Td (page two) Tj ET"


# ── A picture under a box, on pages that share their resources ──────────

@pytest.mark.parametrize("where", ["object", "tree"])
def test_the_original_of_a_picture_under_a_box_goes(tmp_path, where):
    """The owner's repro: page 1 draws the picture under the box, page 2
    only lists it. Before, the original stayed through page 2's listing and
    the run verified."""
    src = _shared(tmp_path / "src.pdf", PICTURE + LINE_ONE, LINE_TWO,
                  xobjects={"Im0": lambda pdf: _image(pdf, RED)}, where=where)
    assert _the_resources_are_shared(src, where) and _with_data(src, RED)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, rects=[OVER_THE_PICTURE])
    assert res.verification["verified"] is True
    assert _with_data(out, RED) == []
    assert _listed(out, 1) == []
    checks = {c["id"]: c for c in res.verification["checks"]}
    assert checks[LEFT_BEHIND]["passed"] is True


def test_a_picture_under_a_search_box(tmp_path):
    """A search's box blanks the pixels of a picture under the word too
    (#143): the same, by term."""
    word = f"BT /F1 24 Tf 60 140 Td ({TERM}) Tj ET ".encode()
    src = _shared(tmp_path / "src.pdf", PICTURE + word + LINE_ONE, LINE_TWO,
                  xobjects={"Im0": lambda pdf: _image(pdf, RED)})
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM])
    assert res.verification["verified"] is True and res.redaction_count == 1
    assert _with_data(out, RED) == []
    assert _listed(out, 1) == []


def test_a_box_ocr_put_on_a_scan(tmp_path, tessdata):
    """A two-page file whose pages share their resources: page 1 is a scan
    with the word in its pixels, page 2 is typed. OCR boxes the word; the
    original scan, with the word in it, does not stay in the file."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((30, 80), f"Account {TERM} is closed.", fontname="helv", fontsize=16)
    pix = page.get_pixmap(dpi=200, colorspace=fitz.csRGB)
    doc.close()
    scan = bytes(pix.samples)

    def picture(pdf):
        return pdf.make_stream(zlib.compress(scan), Type=Name.XObject, Subtype=Name.Image,
                               Width=pix.width, Height=pix.height, ColorSpace=Name.DeviceRGB,
                               BitsPerComponent=8, Filter=Name.FlateDecode)

    src = _shared(tmp_path / "src.pdf", b"q 300 0 0 200 0 0 cm /Im0 Do Q", LINE_TWO,
                  xobjects={"Im0": picture}, size=(300, 200))
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], ocr=True)
    assert res.verification["verified"] is True and res.surface_counts["ocr_page_content"] >= 1
    assert _with_data(out, scan) == []
    assert _listed(out, 1) == []


def test_without_the_drop_the_check_refuses(tmp_path, monkeypatch):
    """With the listings left (the drop switched off), the original stays,
    and the run is refused: a leak, not of a page, so no page is offered as
    a picture. The check fails closed."""
    monkeypatch.setattr(pdf_ops, "_drop_undrawn_xobjects", lambda pdf, boxed: 0)
    src = _shared(tmp_path / "src.pdf", PICTURE + LINE_ONE, LINE_TWO,
                  xobjects={"Im0": lambda pdf: _image(pdf, RED)})
    out = str(tmp_path / "out.pdf")
    with pytest.raises(RedactionVerificationError) as caught:
        redact_pdf(src, out, rects=[OVER_THE_PICTURE])
    failed = [c for c in caught.value.report["checks"] if not c["passed"]]
    assert [c["id"] for c in failed] == [LEFT_BEHIND]
    assert failed[0]["evidence"] == ["a picture drawn on page 1 before the redaction is still in "
                                     "the file, and nothing draws it (document-level)"]
    assert caught.value.detail["found"] == [
        "A picture from page 1 is still in the file, though no page shows it any more. It could "
        "be taken out of the file."]
    assert caught.value.detail["headline"].startswith("Redaction refused: some of what you")
    assert caught.value.flatten_pages is None and caught.value.document_level is True
    assert not os.path.exists(out)


def test_a_file_pikepdf_cannot_open(tmp_path, monkeypatch):
    """Some files MuPDF mends as it opens them and pikepdf cannot open at
    all ("unable to find /Root dictionary", in pdf.js's test files). What
    the pages with a box drew is then read from a copy MuPDF writes of the
    file before anything changes, and the copy is not left behind."""
    src = _shared(tmp_path / "src.pdf", PICTURE + LINE_ONE, LINE_TWO,
                  xobjects={"Im0": lambda pdf: _image(pdf, RED)})
    real = pikepdf.open

    def refuse(path, *args, **kwargs):
        if os.path.abspath(str(path)) == os.path.abspath(src):
            raise pikepdf.PdfError("unable to find /Root dictionary")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(pikepdf, "open", refuse)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, rects=[OVER_THE_PICTURE])
    assert res.verification["verified"] is True
    monkeypatch.setattr(pikepdf, "open", real)
    assert _with_data(out, RED) == []
    assert sorted(os.listdir(tmp_path)) == ["out.pdf", "src.pdf"]


def test_the_copy_of_an_encrypted_file_pikepdf_cannot_open_stays_encrypted(tmp_path, monkeypatch):
    """The same, for a file with a password: the copy keeps the file's
    encryption, so the unredacted file is never on disk without it, even
    for the length of a run (or after a crash). It is read with the
    password, and the run saves and verifies."""
    plain = _shared(tmp_path / "plain.pdf", PICTURE + LINE_ONE, LINE_TWO,
                    xobjects={"Im0": lambda pdf: _image(pdf, RED)})
    src = str(tmp_path / "src.pdf")
    with pikepdf.open(plain) as pdf:
        pdf.save(src, encryption=pikepdf.Encryption(user="user-pw", owner="owner-pw", R=6))
    os.unlink(plain)
    real_open, real_copy = pikepdf.open, pdf_ops._pristine_copy
    copies = []

    def refuse(path, *args, **kwargs):
        if os.path.abspath(str(path)) == os.path.abspath(src):
            raise pikepdf.PdfError("unable to find /Root dictionary")
        return real_open(path, *args, **kwargs)

    def copy(*args, **kwargs):
        path = real_copy(*args, **kwargs)
        assert path is not None
        with pytest.raises(pikepdf.PasswordError):        # as it is on disk
            real_open(path).close()
        with real_open(path, password="user-pw") as pdf:
            copies.append(pdf.encryption.R)
        return path

    monkeypatch.setattr(pikepdf, "open", refuse)
    monkeypatch.setattr(pdf_ops, "_pristine_copy", copy)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, rects=[OVER_THE_PICTURE], password="user-pw")
    monkeypatch.setattr(pikepdf, "open", real_open)
    assert copies == [6]
    assert res.verification["verified"] is True
    checks = {c["id"]: c for c in res.verification["checks"]}
    assert checks[LEFT_BEHIND]["passed"] is True                 # read, not "could not be checked"
    assert _with_data(out, RED) == []
    assert sorted(os.listdir(tmp_path)) == ["out.pdf", "src.pdf"]


def test_content_with_bytes_that_are_no_operator(tmp_path):
    """A damaged content stream can hold an operator that is not text
    (pdf.js's poppler-90-0-fuzzed.pdf). It draws nothing, and is passed
    over: page 2's list is still cut, and nothing is left."""
    src = _shared(tmp_path / "src.pdf", PICTURE + LINE_ONE, LINE_TWO + b" \xe3\x80",
                  xobjects={"Im0": lambda pdf: _image(pdf, RED)})
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, rects=[OVER_THE_PICTURE])
    assert res.verification["verified"] is True
    assert _with_data(out, RED) == [] and _listed(out, 1) == []


def test_a_picture_another_page_draws_is_left(tmp_path):
    """Three pages share a red and a blue picture: page 1 draws the red one
    under the box, page 2 draws the blue one, page 3 draws the red one. Page
    3 still shows the red one, as it was; page 2 no longer lists it, and
    draws what it drew."""
    src = _shared(tmp_path / "src.pdf", PICTURE + LINE_ONE,
                  b"q 200 0 0 200 50 50 cm /Im1 Do Q " + LINE_TWO, PICTURE + LINE_TWO,
                  xobjects={"Im0": lambda pdf: _image(pdf, RED),
                            "Im1": lambda pdf: _image(pdf, BLUE)})
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, rects=[OVER_THE_PICTURE])
    assert res.verification["verified"] is True
    assert len(_with_data(out, RED)) == 1                 # page 3's, which it still draws
    with fitz.open(out) as doc:
        drawn = {p: [doc.xref_stream(i["xref"]) for i in doc[p].get_image_info(xrefs=True)]
                 for p in (1, 2)}
    assert drawn == {1: [BLUE], 2: [RED]}
    assert (_listed(out, 1), _listed(out, 2)) == (["Im1"], ["Im0"])
    with fitz.open(src) as a, fitz.open(out) as b:
        for pno in (1, 2):
            assert pdf_verify.page_digest(a, pno) == pdf_verify.page_digest(b, pno)


def test_a_picture_a_form_draws_with_the_page_s_resources_is_kept(tmp_path):
    """A form with no /Resources of its own draws with the page's: a picture
    it draws is drawn by the page, though the page's own content only draws
    the form. It stays listed, the original of page 1's picture does not,
    and page 2 draws what it drew."""
    def form(pdf):
        return pdf.make_stream(b"q 100 0 0 100 100 100 cm /Im1 Do Q", Type=Name.XObject,
                               Subtype=Name.Form, BBox=[0, 0, 300, 300])
    src = _shared(tmp_path / "src.pdf", PICTURE + LINE_ONE, b"/Fm0 Do " + LINE_TWO,
                  xobjects={"Im0": lambda pdf: _image(pdf, RED),
                            "Im1": lambda pdf: _image(pdf, BLUE), "Fm0": form})
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, rects=[OVER_THE_PICTURE])
    assert res.verification["verified"] is True
    assert _listed(out, 1) == ["Fm0", "Im1"]
    with fitz.open(src) as a, fitz.open(out) as b:
        assert pdf_verify.page_digest(a, 1) == pdf_verify.page_digest(b, 1)
        assert b[1].get_pixmap().samples == a[1].get_pixmap().samples


def test_the_digest_of_a_page_covers_the_pictures_it_draws(tmp_path):
    """A page with no box must draw what it drew: a picture it draws that
    changed is a change, and one it only lists is not part of it."""
    a = _shared(tmp_path / "a.pdf", PICTURE + LINE_TWO, xobjects={
        "Im0": lambda pdf: _image(pdf, RED), "Im9": lambda pdf: _image(pdf, BLUE)})
    b = _shared(tmp_path / "b.pdf", PICTURE + LINE_TWO, xobjects={
        "Im0": lambda pdf: _image(pdf, BLUE)})
    c = _shared(tmp_path / "c.pdf", PICTURE + LINE_TWO, xobjects={
        "Im0": lambda pdf: _image(pdf, RED)})
    with fitz.open(a) as da, fitz.open(b) as db, fitz.open(c) as dc:
        assert pdf_verify.page_digest(da, 0) != pdf_verify.page_digest(db, 0)
        assert pdf_verify.page_digest(da, 0) == pdf_verify.page_digest(dc, 0)


def test_a_picture_left_in_a_form_is_refused(tmp_path):
    """The drop takes names off pages, never off a form. A form page 2
    draws, whose resources are the shared ones, still lists the original:
    the run is refused."""
    def form(pdf):
        return pdf.make_stream(b"BT /F1 12 Tf 20 250 Td (a form) Tj ET", Type=Name.XObject,
                               Subtype=Name.Form, BBox=[0, 0, 300, 300])
    src = _shared(tmp_path / "src.pdf", PICTURE + LINE_ONE, b"/Fm0 Do " + LINE_TWO,
                  xobjects={"Im0": lambda pdf: _image(pdf, RED), "Fm0": form})
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        shared = pdf.pages[0].obj["/Resources"]
        shared["/XObject"]["/Fm0"]["/Resources"] = shared
        pdf.save(src)
    out = str(tmp_path / "out.pdf")
    with pytest.raises(RedactionVerificationError) as caught:
        redact_pdf(src, out, rects=[OVER_THE_PICTURE])
    assert [c["id"] for c in caught.value.report["checks"] if not c["passed"]] == [LEFT_BEHIND]
    assert not os.path.exists(out)


# ── A word in a form that pages share, or only list ──────────────────────

def _form_with(text: str):
    def make(pdf):
        return pdf.make_stream(f"BT /F1 12 Tf 72 150 Td ({text}) Tj ET".encode(),
                               Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 300, 300],
                               Resources=Dictionary(Font=Dictionary(F1=_font(pdf))))
    return make


@pytest.mark.parametrize("where", ["object", "tree"])
def test_a_word_in_a_form_another_page_only_lists(tmp_path, where):
    """Page 1 draws Fm1, which holds the word; page 2 draws Fm2 and lists
    Fm1. Before #150 the save dropped Fm1's listing; on #150 as first
    built the run was refused for page 2's code. It saves again."""
    src = _shared(tmp_path / "src.pdf", b"/Fm1 Do " + LINE_ONE, b"/Fm2 Do " + LINE_TWO,
                  xobjects={"Fm1": _form_with(f"the account {TERM}"),
                            "Fm2": _form_with("nothing to see")}, where=where)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM])
    assert res.verification["verified"] is True
    assert _listed(out, 1) == ["Fm2"]


def test_a_word_in_a_form_a_page_lists_and_never_draws(tmp_path):
    """Page 2 lists a form that holds the word and draws nothing of it."""
    pdf = pikepdf.Pdf.new()
    font = _font(pdf)
    for content in (f"BT /F1 12 Tf 72 200 Td ({TERM}) Tj ET".encode(), LINE_TWO):
        pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 300, 300])))
        pdf.pages[-1].obj["/Resources"] = Dictionary(Font=Dictionary(F1=font))
        pdf.pages[-1].obj["/Contents"] = pdf.make_stream(content)
    pdf.pages[1].obj["/Resources"]["/XObject"] = Dictionary(Unused=pdf.make_stream(
        f"BT /F1 12 Tf 72 150 Td ({TERM}) Tj ET".encode(), Type=Name.XObject, Subtype=Name.Form,
        BBox=[0, 0, 300, 300], Resources=Dictionary(Font=Dictionary(F1=font))))
    src = str(tmp_path / "src.pdf")
    pdf.save(src)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM])
    assert res.verification["verified"] is True
    assert _listed(out, 1) == []


def test_a_word_in_a_comment_on_a_page_with_no_box_is_still_refused(tmp_path):
    """A known limit, and a guard (it passes before this change too): the
    page's code is left as it is, so a word in a comment there stays, and
    the run is refused, with nothing saved."""
    pdf = pikepdf.Pdf.new()
    font = _font(pdf)
    for content in (f"BT /F1 12 Tf 72 200 Td ({TERM}) Tj ET".encode(),
                    LINE_TWO + f"\n% {TERM}\n".encode()):
        pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 300, 300])))
        pdf.pages[-1].obj["/Resources"] = Dictionary(Font=Dictionary(F1=font))
        pdf.pages[-1].obj["/Contents"] = pdf.make_stream(content)
    src = str(tmp_path / "src.pdf")
    pdf.save(src)
    out = str(tmp_path / "out.pdf")
    with pytest.raises(RedactionVerificationError) as caught:
        redact_pdf(src, out, search_terms=[TERM])
    assert {c["id"] for c in caught.value.report["checks"] if not c["passed"]} == {
        "redaction.raw_content", "redaction.term_sweep"}
    assert not os.path.exists(out)


def test_the_verifier_on_its_own(tmp_path):
    """Given what the pages with a box drew: a picture still drawn passes,
    one left undrawn fails, and an account that could not be taken could
    not be checked."""
    src = _shared(tmp_path / "src.pdf", PICTURE + LINE_ONE, LINE_TWO,
                  xobjects={"Im0": lambda pdf: _image(pdf, RED)})
    with pikepdf.open(src) as pdf:
        drew = pdf_verify.drawn_before(pdf, [0])
    assert list(drew.values()) == [{"page": 0, "kind": "image"}]

    def check(path, before):
        report = verify_redaction(path, rects=[OVER_THE_PICTURE], drawn_before=before)
        return next(c for c in report.checks if c.id == LEFT_BEHIND)

    assert check(src, drew).passed is True                  # page 1 draws it
    lists_only = _shared(tmp_path / "lists.pdf", LINE_ONE, LINE_TWO,
                         xobjects={"Im0": lambda pdf: _image(pdf, RED)})
    assert check(lists_only, drew).evidence == [
        "a picture drawn on page 1 before the redaction is still in the file, and nothing draws "
        "it (document-level)"]
    unread = check(src, {"unread": True})
    assert unread.passed is False and unread.unevaluated is True
