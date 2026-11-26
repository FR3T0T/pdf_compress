"""Text outside the boxes (#150).

MuPDF's content filter, which apply_redactions runs over the page it redacts
(and which doc.save(clean=True) ran over every page of the file), writes the
text state only inside a text object and rewrites each form a page draws as
if it starts from the default text state (MuPDF 1.27.2 and 1.28.6). So a
visible footer drawn by a form after a scan's hidden text layer turned
invisible; text a page hid ("3 Tr /Fm0 Do") started showing; a font set by a
graphics state was lost; and all of it on every page of the file, boxes or
not. Every such run verified.

Now a page no box touches is saved as it was read; around every
apply_redactions the text state is made explicit before and put back to the
default where a form is drawn after (pdf_ops._apply); and a new check,
redaction.text_outside, compares every character outside the boxes with what
the page drew before, refusing the run as damage where it changed.

Every fixture is built here, by hand. Nothing imported at the top is new,
so the file collects against the tree before the change and each test fails
there for its own reason (the leading test passes there on PyMuPDF 1.27 and
fails on 2.1; it is named so). Characters are read here independently of
the code under test: get_texttrace on a copy of the page without its
annotations.

Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import os
import re
from collections import Counter

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

import pdf_ops
from pdf_ops import RedactionVerificationError, redact_pdf
from pdf_verify import DAMAGE_HEADLINE, verify_redaction

fitz = pytest.importorskip("fitz")

TERM = "ACME-4471"
#: The term alone on its line: removing it moves nothing after it, on any
#: version of the library.
WORD = f"BT /F1 12 Tf 72 720 Td ({TERM}) Tj ET BT /F1 12 Tf 72 700 Td (stays where it is) Tj ET "
HIDDEN_LAYER = b"BT /F1 12 Tf 3 Tr 72 600 Td (hidden layer words) Tj ET "
FOOTER = b"BT /F1 10 Tf 72 40 Td (Footer line shown on every page) Tj ET"
OUTSIDE = "redaction.text_outside"


# ── Builders ─────────────────────────────────────────────────────────────


def _pdf(path, *pages: bytes, forms: dict | None = None, gs: dict | None = None,
         tree_resources: bool = False) -> str:
    """One page per content stream, each with Helvetica as /F1 and Times as
    /F2. *forms* {name: content, or (content, inner forms)} are the same
    form objects on every page; a form's own resources hold the two fonts
    (and its inner forms). *gs* {name: font size}: graphics states that set
    /F1's font. With *tree_resources* the pages take their resources from
    the page tree."""
    pdf = pikepdf.Pdf.new()
    helvetica = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1,
                                             BaseFont=Name.Helvetica))
    times = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1,
                                         BaseFont=Name("/Times-Roman")))

    def make(spec):
        out = Dictionary()
        for name, value in (spec or {}).items():
            body, inner = value if isinstance(value, tuple) else (value, None)
            res = Dictionary(Font=Dictionary(F1=helvetica, F2=times))
            if inner:
                res["/XObject"] = make(inner)
            out["/" + name] = pdf.make_stream(body, Type=Name.XObject, Subtype=Name.Form,
                                              BBox=[0, 0, 612, 792], Resources=res)
        return out

    resources = Dictionary(Font=Dictionary(F1=helvetica, F2=times))
    if forms:
        resources["/XObject"] = make(forms)
    if gs:
        resources["/ExtGState"] = Dictionary({"/" + name: Dictionary(
            Type=Name.ExtGState, Font=Array([helvetica, size])) for name, size in gs.items()})
    for content in pages:
        pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
        page = pdf.pages[-1].obj
        if not tree_resources:
            page["/Resources"] = resources
        page["/Contents"] = pdf.make_stream(content)
    if tree_resources:
        # After the pages: adding a page pushes what the tree gives down.
        pdf.Root["/Pages"]["/Resources"] = pdf.make_indirect(resources)
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _chars(path, pno: int = 0) -> Counter:
    """(character, origin to a tenth of a point, hidden) of what page *pno*
    draws, white space aside."""
    with fitz.open(path) as doc:
        one = fitz.open()
        one.insert_pdf(doc, from_page=pno, to_page=pno, links=False, annots=False, widgets=False)
        found = Counter()
        for span in one[0].get_texttrace():
            for c in span["chars"]:
                if c[0] > 32:
                    found[(chr(c[0]), round(c[2][0], 1), round(c[2][1], 1),
                           span["type"] == 3)] += 1
        one.close()
    return found


def _shown(path, pno: int = 0, hidden: bool = False) -> str:
    """The letters page *pno* shows (or hides), in the order it draws them."""
    with fitz.open(path) as doc:
        one = fitz.open()
        one.insert_pdf(doc, from_page=pno, to_page=pno, links=False, annots=False, widgets=False)
        text = "".join(chr(c[0]) for span in one[0].get_texttrace()
                       if (span["type"] == 3) is hidden for c in span["chars"] if c[0] > 32)
        one.close()
    return text


def _redact(tmp_path, src, **kw):
    out = str(tmp_path / "out.pdf")
    kw.setdefault("search_terms", [TERM])
    res = redact_pdf(src, out, **kw)
    assert res.verification["verified"] is True
    return res, out


def _only_the_term_went(src, out, pno: int = 0, term: str = TERM) -> None:
    before, after = _chars(src, pno), _chars(out, pno)
    gone, new = before - after, after - before
    assert sorted(c for (c, *_rest), n in gone.items() for _ in range(n)) == sorted(term), \
        f"gone: {sorted(gone)}"
    assert not new, f"new: {sorted(new)}"


def _check(report, cid):
    checks = report["checks"] if isinstance(report, dict) else [
        {"id": c.id, "passed": c.passed, "evidence": c.evidence} for c in report.checks]
    for c in checks:
        if c["id"] == cid:
            return c
    raise AssertionError(f"check {cid!r} missing: {[c['id'] for c in checks]}")


# ── 1: the text state at a form call ─────────────────────────────────────

#: case -> (page content after WORD, forms, what the page shows after, what it hides after).
STATE_CASES = {
    "the footer after a hidden layer, reset in q (the #150 file)": (
        HIDDEN_LAYER + b"q 0 Tr /Fm0 Do Q", {"Fm0": FOOTER},
        "stayswhereitisFooterlineshownoneverypage", "hiddenlayerwords"),
    "reset outside a text object, without q": (
        HIDDEN_LAYER + b"0 Tr /Fm0 Do", {"Fm0": FOOTER},
        "stayswhereitisFooterlineshownoneverypage", "hiddenlayerwords"),
    "reset inside a text object": (
        HIDDEN_LAYER + b"BT 0 Tr ET /Fm0 Do", {"Fm0": FOOTER},
        "stayswhereitisFooterlineshownoneverypage", "hiddenlayerwords"),
    "the form sets its own 0 Tr": (
        HIDDEN_LAYER + b"/Fm0 Do", {"Fm0": b"0 Tr " + FOOTER},
        "stayswhereitisFooterlineshownoneverypage", "hiddenlayerwords"),
    "the page hides a form": (
        b"3 Tr /Fm0 Do 0 Tr", {"Fm0": FOOTER},
        "stayswhereitis", "Footerlineshownoneverypage"),
    "an inherited 3 Tr with the form's own 0": (
        b"3 Tr /Fm0 Do", {"Fm0": b"BT /F1 10 Tf 72 60 Td (hidden in form) Tj ET 0 Tr " + FOOTER},
        "stayswhereitisFooterlineshownoneverypage", "hiddeninform"),
    "a form with no font of its own": (
        b"BT /F2 14 Tf 72 600 Td (times) Tj ET /Fm0 Do",
        {"Fm0": b"BT 72 40 Td (Footer with no font of its own) Tj ET"},
        "stayswhereitistimesFooterwithnofontofitsown", ""),
}


@pytest.mark.parametrize("case", list(STATE_CASES))
def test_the_text_state_at_a_form_call(tmp_path, case):
    """A form drawn where the text state is not the default draws as it did."""
    content, forms, shown, hidden = STATE_CASES[case]
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + content, forms=forms)
    _res, out = _redact(tmp_path, src)
    assert (_shown(out), _shown(out, hidden=True)) == (shown, hidden)
    _only_the_term_went(src, out)


def test_leading_a_form_inherits__fails_on_main_on_pymupdf_2_only(tmp_path):
    """T* in a form moves by the leading the page set (TD sets it). MuPDF
    1.28 writes TD for every line, so it changed under 2.1; 1.27 kept it."""
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + b"BT /F1 12 Tf 72 600 Td (lead) Tj 0 -30 TD "
               b"(two) Tj ET /Fm0 Do",
               forms={"Fm0": b"BT /F1 10 Tf 72 60 Td (one) Tj T* (two) Tj ET"})
    _res, out = _redact(tmp_path, src)
    _only_the_term_went(src, out)


def test_by_a_box_drawn_by_hand_too(tmp_path):
    """The same with a box that covers nothing, in a corner."""
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + HIDDEN_LAYER + b"q 0 Tr /Fm0 Do Q",
               forms={"Fm0": FOOTER})
    _res, out = _redact(tmp_path, src, search_terms=None,
                        rects=[{"page": 0, "x0": 0, "y0": 0, "x1": 0.5, "y1": 0.5}])
    assert _chars(out) == _chars(src)


# ── 2: a font set by a graphics state ────────────────────────────────────

def test_a_font_set_by_a_graphics_state(tmp_path):
    """Lost before: the line was drawn in another font, and moved."""
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + b"/GS1 gs BT 72 500 Td "
               b"(Text in a font set by a graphics state) Tj ET", gs={"GS1": 24})
    _res, out = _redact(tmp_path, src)
    _only_the_term_went(src, out)


def test_a_font_set_by_a_graphics_state_with_resources_from_the_page_tree(tmp_path):
    """The page takes its resources from the page tree, which the second page
    shares: the font is given a name in a copy the first page has of its
    own, and the second page is not touched."""
    content = b"/GS1 gs BT 72 500 Td (Text in a font set by a graphics state) Tj ET "
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + content, content, gs={"GS1": 24},
               tree_resources=True)
    def fonts(path, pno):
        """The names in the /Font of the resources page *pno* draws with,
        its own or the tree's, read with PyMuPDF (pikepdf's page list
        pushes what the tree gives down to the pages)."""
        with fitz.open(path) as doc:
            node = doc[pno].xref
            while doc.xref_get_key(node, "Resources")[0] == "null":
                node = int(doc.xref_get_key(node, "Parent")[1].split()[0])
            return node == doc[pno].xref, sorted(
                re.findall(r"/(\w+)\s*\d+ \d+ R", doc.xref_get_key(node, "Resources/Font")[1]))

    assert fonts(src, 0) == fonts(src, 1) == (False, ["F1", "F2"])   # as it claims
    _res, out = _redact(tmp_path, src)
    _only_the_term_went(src, out)
    assert _chars(out, 1) == _chars(src, 1)
    assert fonts(out, 1)[1] == ["F1", "F2"]


# ── A page no box touched ────────────────────────────────────────────────

def test_a_page_no_box_touched_is_saved_as_it_was(tmp_path):
    """The term is on the first page only; the same footer form is drawn on
    both, after hidden text. The second page gets no box: before, it was
    filtered anyway (clean=True) and its footer turned invisible."""
    second = b"BT /F1 12 Tf 72 700 Td (no term here) Tj ET " + HIDDEN_LAYER + b"q 0 Tr /Fm0 Do Q"
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + HIDDEN_LAYER + b"q 0 Tr /Fm0 Do Q", second,
               forms={"Fm0": FOOTER})
    _res, out = _redact(tmp_path, src)
    _only_the_term_went(src, out)
    assert _chars(out, 1) == _chars(src, 1)
    assert "Footerlineshownoneverypage" in _shown(out, 1)
    with pikepdf.open(src) as a, pikepdf.open(out) as b:
        assert b.pages[1].obj["/Contents"].read_bytes() == a.pages[1].obj["/Contents"].read_bytes()
        form = b.pages[1].obj["/Resources"]["/XObject"]["/Fm0"]
        assert form.read_bytes() == FOOTER


# ── Forms inside forms, a form drawn twice, a picture in the content ─────

def test_a_form_inside_a_form(tmp_path):
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + HIDDEN_LAYER + b"q 0 Tr /Fm0 Do Q",
               forms={"Fm0": (b"q 1 0 0 1 0 0 cm /Inner Do Q", {"Inner": FOOTER})})
    _res, out = _redact(tmp_path, src)
    assert "Footerlineshownoneverypage" in _shown(out)
    _only_the_term_went(src, out)


def test_the_state_set_inside_a_form_for_the_form_inside_it(tmp_path):
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + b"/Fm0 Do",
               forms={"Fm0": (b"BT /F1 12 Tf 3 Tr 72 600 Td (hidden in outer) Tj ET "
                              b"q 0 Tr /Inner Do Q", {"Inner": FOOTER})})
    _res, out = _redact(tmp_path, src)
    assert (_shown(out), _shown(out, hidden=True)) == (
        "stayswhereitisFooterlineshownoneverypage", "hiddeninouter")
    _only_the_term_went(src, out)


def test_one_form_drawn_twice_in_two_states(tmp_path):
    """Hidden the first time, shown the second (a hundred points higher)."""
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + b"3 Tr /Fm0 Do 0 Tr q 1 0 0 1 0 100 cm "
               b"/Fm0 Do Q", forms={"Fm0": FOOTER})
    _res, out = _redact(tmp_path, src)
    assert (_shown(out), _shown(out, hidden=True)) == (
        "stayswhereitisFooterlineshownoneverypage", "Footerlineshownoneverypage")
    _only_the_term_went(src, out)


def test_a_picture_written_into_the_content_and_marked_content_survive(tmp_path):
    """The content is parsed and written again to make the state explicit:
    a picture written into it (BI ... ID ... EI) and a marked-content
    sequence come through."""
    picture = b"q 20 0 0 20 300 300 cm BI /W 2 /H 2 /CS /G /BPC 8 ID \x00\xff\xff\x00 EI Q "
    marked = b"/Span <</ActualText (layer)>> BDC " + HIDDEN_LAYER + b"EMC "
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + picture + marked + b"q 0 Tr /Fm0 Do Q",
               forms={"Fm0": FOOTER})
    _res, out = _redact(tmp_path, src)
    _only_the_term_went(src, out)
    with fitz.open(src) as a, fitz.open(out) as b:
        def pictures(page):
            return [(tuple(round(v, 1) for v in i["bbox"]), i["width"], i["height"])
                    for i in page.get_image_info()]
        assert pictures(b[0]) == pictures(a[0]) == [((300.0, 472.0, 320.0, 492.0), 2, 2)]
        assert b[0].get_pixmap(clip=fitz.Rect(300, 472, 320, 492)).samples == \
            a[0].get_pixmap(clip=fitz.Rect(300, 472, 320, 492)).samples
        assert b"/ActualText" in b[0].read_contents()


# ── The check ────────────────────────────────────────────────────────────

def _without_the_fix(monkeypatch):
    monkeypatch.setattr(pdf_ops, "_text_state_before", lambda page: None)
    monkeypatch.setattr(pdf_ops, "_text_state_after", lambda page, new_from: None)


@pytest.mark.parametrize("content, forms, line", [
    (HIDDEN_LAYER + b"q 0 Tr /Fm0 Do Q", {"Fm0": FOOTER},
     "Text on page 1 outside the black boxes would have stopped showing."),
    (b"3 Tr /Fm0 Do 0 Tr", {"Fm0": FOOTER},
     "Hidden text on page 1 outside the black boxes would have started showing."),
])
def test_the_check_refuses_when_the_state_is_not_made_explicit(tmp_path, monkeypatch, content,
                                                              forms, line):
    """With the text state left to MuPDF, the run is refused as damage, and
    nothing is saved: the check fails closed."""
    _without_the_fix(monkeypatch)
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + content, forms=forms)
    out = str(tmp_path / "out.pdf")
    with pytest.raises(RedactionVerificationError) as caught:
        redact_pdf(src, out, search_terms=[TERM])
    assert str(caught.value) == DAMAGE_HEADLINE
    assert caught.value.detail == {"headline": DAMAGE_HEADLINE, "found": [line],
                                   "nextStep": "The file was left as it was."}
    assert caught.value.flatten_pages is None and not os.path.exists(out)
    failed = [c["id"] for c in caught.value.report["checks"] if not c["passed"]]
    assert failed == [OUTSIDE]


@pytest.mark.parametrize("content", [
    b"/F1 12 Tf 1 0 0 1 72 300 Tm (Outside a text object) Tj",
    b"/F1 12 Tf 72 300 Td (Outside a text object) Tj",
])
def test_text_outside_a_text_object_is_refused(tmp_path, content):
    """MuPDF draws it, and its filter moves it: nothing here puts it back, so
    the run is refused, on every page that has a box."""
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + content)
    out = str(tmp_path / "out.pdf")
    with pytest.raises(RedactionVerificationError) as caught:
        redact_pdf(src, out, search_terms=[TERM])
    assert caught.value.detail["headline"] == DAMAGE_HEADLINE
    assert caught.value.detail["found"] == [
        "Text on page 1 outside the black boxes would have moved or gone."]
    assert not os.path.exists(out)


def test_a_clean_page_passes_the_check(tmp_path):
    src = _pdf(tmp_path / "src.pdf", WORD.encode(), b"BT /F1 12 Tf 72 700 Td (page two) Tj ET")
    res, out = _redact(tmp_path, src)
    assert _check(res.verification, OUTSIDE) == {
        "id": OUTSIDE, "description": "Text outside the boxes shows as it did", "passed": True,
        "evidence": [], "pages": [], "isDocumentLevel": False, "unevaluated": False,
        "objectPaths": []}


def test_the_verifier_on_a_page_it_is_told_no_box_touched(tmp_path):
    """Given a page's digest from before: the same page passes; another
    digest, with the characters the page drew before, is compared by them;
    without them, it could not be checked."""
    src = _pdf(tmp_path / "src.pdf", WORD.encode(), b"BT /F1 12 Tf 72 700 Td (page two) Tj ET")
    _res, out = _redact(tmp_path, src)
    import pdf_verify
    with fitz.open(out) as doc:
        same = pdf_verify.page_digest(doc, 1)
        chars = pdf_verify.drawn_characters(doc, 1)
    moved = [(c[0], c[1] + 5, c[2], c[3] + 5, c[4], c[5] + 5, *c[6:]) for c in chars]

    def check(entry):
        return _check(verify_redaction(out, [TERM], text_before={1: entry}), OUTSIDE)

    assert check({"digest": same})["passed"] is True
    assert check({"digest": "0" * 64, "chars": chars})["passed"] is True
    assert check({"digest": "0" * 64, "chars": moved})["evidence"] == [
        "7 characters outside the boxes on page 2 moved or went missing"]
    assert check({"digest": "0" * 64})["evidence"] == [
        "the text on page 2, which no box touched, changed and could not be compared, so it "
        "could not be checked"]


# ── PyMuPDF 2.x: text after a removed word moved along its line ──────────
#
# 2.x (MuPDF 1.28) can move the rest of a line along it when a word is
# removed (a one-letter word, a word that ends at a TJ spacing step); the
# run says so in the text_may_move note, as main does. On 2.x only, a letter
# that moved along the line of one the run removed is not refused. Shown,
# hidden, gone, or moved off its line still is, on every version.

KEEP = "BT /F1 12 Tf 72 700 Td (keep ACME-4471 these words) Tj ET"


def _moved_page(tmp_path, dx: float = 0.0, dy: float = 0.0) -> tuple:
    """(the source, what a redaction of the term would write if the rest of
    the line moved by *dx*, *dy*, the term's box): the second built by hand,
    with "keep" where it was and "these words" moved."""
    src = _pdf(tmp_path / "src.pdf", KEEP.encode())
    with fitz.open(src) as doc:
        box = doc[0].search_for(TERM)[0]
    at = 72 + fitz.get_text_length(f"keep {TERM} ", fontname="helv", fontsize=12)
    out = _pdf(tmp_path / f"out_{dx}_{dy}.pdf", (
        f"BT /F1 12 Tf 72 700 Td (keep) Tj ET "
        f"BT /F1 12 Tf {at + dx:.3f} {700 + dy:.3f} Td (these words) Tj ET").encode())
    return src, out, box


def _outside_check(src, out, box, along_line):
    import pdf_verify
    with fitz.open(src) as doc:
        before = pdf_verify.drawn_characters(doc, 0)
    report = verify_redaction(out, [TERM], text_before={0: {"chars": before}},
                              search_boxes=[{"page": 0, "x0": box.x0, "y0": box.y0,
                                             "x1": box.x1, "y1": box.y1}],
                              text_along_line=along_line)
    return _check(report, OUTSIDE)


def test_on_2x_text_moved_along_the_line_of_a_removed_word_is_not_refused(tmp_path):
    src, out, box = _moved_page(tmp_path, dx=-8)
    assert _outside_check(src, out, box, along_line=True)["passed"] is True
    assert _outside_check(src, out, box, along_line=False)["evidence"] == [
        "10 characters outside the boxes on page 1 moved or went missing"]


@pytest.mark.parametrize("dx, dy", [(-8, -3), (0, -14)])
def test_on_2x_a_shift_off_the_line_is_still_refused(tmp_path, dx, dy):
    src, out, box = _moved_page(tmp_path, dx=dx, dy=dy)
    assert _outside_check(src, out, box, along_line=True)["evidence"] == [
        "10 characters outside the boxes on page 1 moved or went missing"]


def test_on_2x_a_line_no_word_was_removed_from_is_still_refused(tmp_path):
    """Moved along its line, but nothing was removed from that line."""
    src = _pdf(tmp_path / "src.pdf", KEEP.encode() + b" BT /F1 12 Tf 72 650 Td (another line) Tj ET")
    with fitz.open(src) as doc:
        box = doc[0].search_for(TERM)[0]
    out = _pdf(tmp_path / "out.pdf", (
        "BT /F1 12 Tf 72 700 Td (keep) Tj ET BT /F1 12 Tf "
        f"{72 + fitz.get_text_length(f'keep {TERM} ', fontname='helv', fontsize=12):.3f} 700 Td "
        "(these words) Tj ET BT /F1 12 Tf 64 650 Td (another line) Tj ET").encode())
    assert _outside_check(src, out, box, along_line=True)["evidence"] == [
        "11 characters outside the boxes on page 1 moved or went missing"]


@pytest.mark.parametrize("content, forms, line", [
    (HIDDEN_LAYER + b"q 0 Tr /Fm0 Do Q", {"Fm0": FOOTER},
     "Text on page 1 outside the black boxes would have stopped showing."),
    (b"/F1 12 Tf 1 0 0 1 72 300 Tm (Outside a text object) Tj", None,
     "Text on page 1 outside the black boxes would have moved or gone."),
])
def test_on_2x_hidden_shown_or_moved_off_the_line_is_still_refused(tmp_path, monkeypatch,
                                                                     content, forms, line):
    """As run on 2.x (the version _check_library notes): the footer the
    filter hides, and text outside a text object, which moves to another
    line, are refused as on every version."""
    monkeypatch.setattr(pdf_ops, "_library_version", lambda: "2.1")
    if forms:
        _without_the_fix(monkeypatch)
    src = _pdf(tmp_path / "src.pdf", WORD.encode() + content, forms=forms)
    out = str(tmp_path / "out.pdf")
    with pytest.raises(RedactionVerificationError) as caught:
        redact_pdf(src, out, search_terms=[TERM])
    assert caught.value.detail["found"] == [line]
    assert not os.path.exists(out)


def test_a_word_that_ends_at_a_spacing_step(tmp_path):
    """On 2.1 the rest of the line moves 8 pt along it when the word goes;
    the run is saved, with the note, as on main. On 1.27 it does not move."""
    src = _pdf(tmp_path / "src.pdf",
               b"BT /F1 12 Tf 72 700 Td [(keep ACME)-120(4471 these words)] TJ ET")
    res, out = _redact(tmp_path, src, search_terms=["ACME4471"])
    assert _check(res.verification, OUTSIDE)["passed"] is True
