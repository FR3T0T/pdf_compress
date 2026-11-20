"""Redaction completeness (#119), cluster R3: what a redaction still left to see.

Each of these finished "verified" — some only after the page had been made
a picture, with the word plainly readable in it:

  1. Nothing is drawn above a box. An annotation paints over the page, and
     redaction left every annotation but its own marks alone.
  2. A page flattened to a picture is blacked out first, on the picture.
  3. A tagged file's description of what a box removed goes with it.
  4. A page's thumbnail is a picture of the page as it was.

Every fixture is built here, by hand. Nothing imported at the top is new,
so the file collects against the tree before the change and each test fails
there for its own reason.

Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import os

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from pdf_ops import RedactionVerificationError, redact_pdf
from pdf_verify import verify_redaction

fitz = pytest.importorskip("fitz")

TERM = "ACME99Z"


# ── Builders ─────────────────────────────────────────────────────────────


def _font(pdf):
    return pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))


def _pdf(path, plant=None, *, body: str = "nothing sensitive on this page",
         pages: int = 1, rotate: int = 0) -> str:
    """*pages* pages, each one line of *body* at the top, with *plant*
    applied to the document."""
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
        page = pdf.pages[-1].obj
        page["/Resources"] = Dictionary(Font=Dictionary(F1=_font(pdf)))
        page["/Contents"] = pdf.make_stream(f"BT /F1 14 Tf 72 720 Td ({body}) Tj ET".encode())
        if rotate:
            page["/Rotate"] = rotate
    if plant is not None:
        plant(pdf)
    pdf.save(str(path), fix_metadata_version=False)
    pdf.close()
    return str(path)


def _drawn(pdf, text: str):
    """An appearance stream: a yellow panel with a red border and *text* in
    red, 18 point."""
    return pdf.make_stream(
        f"1 1 0 rg 1 0 0 RG 0 0 300 30 re B BT /F1 18 Tf 1 0 0 rg 10 8 Td ({text}) Tj ET".encode(),
        Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 300, 30],
        Resources=Dictionary(Font=Dictionary(F1=_font(pdf))))


#: Where _stamp puts its annotation, as the page has it and as PyMuPDF
#: counts it (from the top of the page).
STAMP_RECT = [72, 580, 372, 610]
OVER_THE_STAMP = {"page": 0, "x0": 60, "y0": 170, "x1": 400, "y1": 225}


def _stamp(pdf, text: str = f"approved by {TERM}", **entries):
    return pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Stamp, Rect=STAMP_RECT,
        **{"AP": Dictionary(N=_drawn(pdf, text)), **entries}))


def _note(pdf, at=(450, 300), **entries):
    return pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Text, Rect=[at[0], at[1], at[0] + 20, at[1] + 20], **entries))


def _on_page(pdf, *annots, page: int = 0) -> None:
    obj = pdf.pages[page].obj
    if "/Annots" not in obj:
        obj["/Annots"] = Array()
    for a in annots:
        obj["/Annots"].append(a)


def _with_popup_and_replies(pdf, annot) -> list:
    """Give *annot* a popup, a reply and a reply to the reply; returns all
    four, for the page."""
    popup = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Popup,
                                         Rect=[400, 650, 560, 740], Parent=annot))
    annot["/Popup"] = popup
    reply = _note(pdf, (500, 200), Contents=String("agreed"), IRT=annot)
    again = _note(pdf, (530, 200), Contents=String("so do I"), IRT=reply)
    return [annot, popup, reply, again]


def _subtypes(path, page: int = 0) -> list:
    with pikepdf.open(path) as pdf:
        return [str(a.get("/Subtype")) for a in pdf.pages[page].obj.get("/Annots", [])]


def _check(report, cid):
    checks = report.checks if hasattr(report, "checks") else None
    for c in checks if checks is not None else report["checks"]:
        if (c.id if checks is not None else c["id"]) == cid:
            return c
    raise AssertionError(f"check {cid!r} missing")


def _everything(path) -> bytes:
    """Every string and every decodable stream in the file — a look that
    shares nothing with the verifier."""
    out = []

    def visit(obj, depth=0):
        if depth > 80 or not isinstance(obj, pikepdf.Object):
            return
        if isinstance(obj, pikepdf.String):
            out.append(bytes(obj))
        elif isinstance(obj, pikepdf.Array):
            for v in obj:
                if isinstance(v, pikepdf.Object) and not v.is_indirect:
                    visit(v, depth + 1)
        elif isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            if isinstance(obj, pikepdf.Stream):
                try: out.append(obj.read_bytes())
                except Exception: out.append(obj.read_raw_bytes())
            for k in obj.keys():
                v = obj[k]
                if isinstance(v, pikepdf.Object) and not v.is_indirect:
                    visit(v, depth + 1)

    with pikepdf.open(path) as pdf:
        visit(pdf.trailer)
        for obj in pdf.objects:
            visit(obj)
    return b"\n".join(out)


def _holds(path, word: str = TERM) -> bool:
    blob = _everything(path)
    return word.encode() in blob or word.lower().encode() in blob.lower()


def _dark_share(path, rect, page: int = 0, below: int = 128) -> float:
    """How much of *rect* (as PyMuPDF counts it on the page before any
    rotation) is dark when the page is drawn: 1.0 under a black box. Red
    ink on a yellow panel is the grey 129 on 248, hence *below*."""
    with fitz.open(path) as doc:
        pg = doc[page]
        clip = fitz.Rect(rect) * pg.rotation_matrix
        clip.normalize()
        pix = pg.get_pixmap(clip=clip, colorspace=fitz.csGRAY, alpha=False)
    return sum(1 for value in pix.samples if value < below) / max(1, len(pix.samples))


def _word_rect(path, word: str, page: int = 0) -> tuple:
    with fitz.open(path) as doc:
        (found,) = [w for w in doc[page].get_text("words") if w[4] == word]
    return tuple(found[:4])


# ── 1. Nothing is drawn above a box ──────────────────────────────────────


@pytest.mark.integration
class TestAnAnnotationThatDrawsATerm:
    """Search mode. An annotation whose appearance draws a term is taken
    off its page, with its popup and whatever replies to it. It used to be
    refused, and then verified as a picture with the word still in it."""

    def test_a_stamp_that_draws_the_term_leaves_its_page(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(
            pdf, _stamp(pdf), _note(pdf, Contents=String("an unrelated note"))))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True and res.flattened_pages == []
        # The page search reads what a shown annotation draws, so the word
        # also gets its box on the page, where it was.
        assert res.surface_counts == {"page_content": 1, "annotations": 1}
        assert _subtypes(out) == ["/Text"] and not _holds(out)
        with fitz.open(out) as doc:
            assert TERM not in doc[0].get_text()

    @pytest.mark.parametrize("where", ["rollover", "down, one state of two", "hidden"])
    def test_any_appearance_of_it_counts(self, tmp_path, where):
        def plant(pdf):
            quiet = _drawn(pdf, "approved")
            if where == "rollover":
                annot = _stamp(pdf, "approved", AP=Dictionary(N=quiet, R=_drawn(pdf, f"by {TERM}")))
            elif where == "hidden":
                annot = _stamp(pdf, f"by {TERM}", F=2)
            else:
                annot = _stamp(pdf, "approved", AP=Dictionary(N=quiet, D=Dictionary(
                    On=_drawn(pdf, f"by {TERM}"), Off=_drawn(pdf, "nobody"))))
            _on_page(pdf, annot)
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True and res.surface_counts == {"annotations": 1}
        assert _subtypes(out) == [] and not _holds(out)

    def test_a_term_the_page_search_cannot_join_up_is_found_in_it_too(self, tmp_path):
        """The matcher reads the appearance, not a byte search: the word is
        broken across two lines of the stamp."""
        def plant(pdf):
            split = pdf.make_stream(
                b"BT /F1 12 Tf 10 18 Td (signed by Dana Whit-) Tj 0 -12 Td (field today) Tj ET",
                Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 300, 30],
                Resources=Dictionary(Font=Dictionary(F1=_font(pdf))))
            _on_page(pdf, _stamp(pdf, AP=Dictionary(N=split)))
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Dana Whitfield"])
        assert res.verification["verified"] is True and _subtypes(out) == []
        assert not _holds(out, "Whit-")

    def test_its_popup_and_its_replies_go_with_it(self, tmp_path):
        def plant(pdf):
            _on_page(pdf, *_with_popup_and_replies(pdf, _stamp(pdf)),
                     *_with_popup_and_replies(pdf, _note(pdf, Contents=String("unrelated")))[:2])
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True
        assert res.surface_counts.get("annotations") == 3     # the stamp and the two replies
        assert _subtypes(out) == ["/Text", "/Popup"]          # the other note keeps its popup
        with pikepdf.open(out) as pdf:
            note, popup = pdf.pages[0].obj["/Annots"]
            assert note["/Popup"].objgen == popup.objgen

    def test_what_draws_no_term_is_left_alone(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(
            pdf, *_with_popup_and_replies(pdf, _stamp(pdf, "approved"))), body=f"about {TERM}")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True and "annotations" not in res.surface_counts
        assert _subtypes(out) == ["/Stamp", "/Popup", "/Text", "/Text"]

    def test_a_tagged_file_does_not_keep_what_it_drew(self, tmp_path):
        """Off its page, the annotation is still what the structure tree
        points to. Its appearance goes; the reference is the tree's own."""
        def plant(pdf):
            stamp = _stamp(pdf)
            _on_page(pdf, stamp)
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Annot, P=root,
                K=Dictionary(Type=Name.OBJR, Obj=stamp)))])
            pdf.Root["/StructTreeRoot"] = root
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True and _subtypes(out) == []
        assert not _holds(out)

    def test_a_form_field_keeps_the_path_it_had(self, tmp_path):
        """A widget is not taken off the page for what its appearance
        draws: its value is emptied and the appearance dropped, as before."""
        def plant(pdf):
            field = pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("who"),
                V=String(f"{TERM} Ltd"), Rect=[0, 0, 0, 0], F=2,
                AP=Dictionary(N=_drawn(pdf, f"{TERM} Ltd"))))
            _on_page(pdf, field)
            pdf.Root["/AcroForm"] = Dictionary(Fields=Array([field]))
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True
        assert res.surface_counts == {"form_fields": 1} and _subtypes(out) == ["/Widget"]

    def test_one_that_is_on_no_page_loses_its_appearance(self, tmp_path):
        def plant(pdf):
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Annot, P=root,
                K=Dictionary(Type=Name.OBJR, Obj=_stamp(pdf))))])
            pdf.Root["/StructTreeRoot"] = root
        src = _pdf(tmp_path / "src.pdf", plant, body=f"about {TERM}")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True and not _holds(out)


@pytest.mark.integration
class TestAnAnnotationUnderABox:
    """Box mode. Whatever annotation lies over a box drawn by hand is taken
    off the page: it paints above the box."""

    def test_a_stamp_under_the_box_leaves_the_page(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(
            pdf, _stamp(pdf), _note(pdf, Contents=String("an unrelated note"))))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert res.surface_counts == {"page_content": 1, "annotations": 1}
        assert _subtypes(out) == ["/Text"] and not _holds(out)
        box = [OVER_THE_STAMP[k] for k in ("x0", "y0", "x1", "y1")]
        assert _dark_share(out, box) > 0.99                  # nothing is drawn over the box

    def test_one_that_draws_no_text_goes_too(self, tmp_path):
        """A picture of a signature has no text for any check to find. It
        was left painted over the box, and the output verified."""
        def plant(pdf):
            scrawl = pdf.make_stream(b"0 0 1 RG 3 w 10 5 m 80 25 150 0 290 20 c S",
                                     Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 300, 30])
            _on_page(pdf, _stamp(pdf, AP=Dictionary(N=scrawl)))
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True
        assert _subtypes(out) == [] and res.surface_counts.get("annotations") == 1

    def test_popup_and_replies_go_and_what_is_elsewhere_stays(self, tmp_path):
        def plant(pdf):
            _on_page(pdf, *_with_popup_and_replies(pdf, _stamp(pdf)),
                     *_with_popup_and_replies(pdf, _note(pdf, Contents=String("unrelated")))[:2],
                     pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                                  Rect=[100, 590, 200, 600], Dest=Array([0, Name.Fit]))),
                     pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                                  Rect=[100, 300, 200, 320], Dest=Array([0, Name.Fit]))))
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True
        assert _subtypes(out) == ["/Text", "/Popup", "/Link"]
        assert res.surface_counts.get("annotations") == 3     # the stamp and its two replies

    @pytest.mark.parametrize("rotate", [90, 180, 270])
    def test_on_a_turned_page(self, tmp_path, rotate):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(pdf, _stamp(pdf)), rotate=rotate)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True and _subtypes(out) == []

    def test_a_hidden_one_goes_too(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(pdf, _stamp(pdf, F=2)))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True and _subtypes(out) == [] and not _holds(out)


@pytest.mark.integration
class TestTheVerifierLooksForAnnotationsOverABox:
    CHECK = "redaction.rect_annotations"

    def test_an_annotation_over_a_box_fails_it(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(pdf, _stamp(pdf, "approved")))
        report = verify_redaction(src, rects=[OVER_THE_STAMP])
        check = _check(report, self.CHECK)
        assert check.passed is False and check.pages == [1]
        assert check.evidence == ["a Stamp annotation still lies over a redaction rectangle on page 1"]
        assert report.flatten_target_pages() == [1]

    def test_none_over_it_passes(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(
            pdf, _note(pdf, Contents=String("elsewhere"))))
        assert _check(verify_redaction(src, rects=[OVER_THE_STAMP]), self.CHECK).passed is True

    def test_a_popup_is_judged_with_its_note_not_by_its_own_rectangle(self, tmp_path):
        def plant(pdf):
            note = _note(pdf, Contents=String("elsewhere"))
            popup = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Popup,
                                                 Rect=STAMP_RECT, Parent=note))
            note["/Popup"] = popup
            _on_page(pdf, note, popup)
        src = _pdf(tmp_path / "src.pdf", plant)
        assert _check(verify_redaction(src, rects=[OVER_THE_STAMP]), self.CHECK).passed is True

    def test_it_is_not_made_for_a_search(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf")
        assert self.CHECK not in [c.id for c in verify_redaction(src, [TERM]).checks]

    def test_a_rectangle_it_cannot_read_fails_it(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(pdf, pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Stamp, Rect=String("not a rectangle")))))
        check = _check(verify_redaction(src, rects=[OVER_THE_STAMP]), self.CHECK)
        assert check.passed is False and "could not be read" in check.evidence[0]

# ── 2. A flattened page is blacked out first ─────────────────────────────


def _no_removal(monkeypatch) -> None:
    """Make apply_redactions remove nothing and draw nothing, so that what
    the flattened page shows depends on the flatten alone."""
    monkeypatch.setattr(fitz.Page, "apply_redactions", lambda self, *a, **kw: True)


@pytest.mark.integration
class TestAFlattenedPageIsBlackedOutFirst:
    """A page made a picture has no text left for any check to find, so
    whatever is still drawn on it is in the picture for good. Every place
    the matcher still finds a term is filled black on the picture itself —
    not on the page, where something could be painted over it — and so are
    the user's boxes."""

    LINE = "Signed by Dana Whitfield today"

    def test_a_term_that_could_not_be_removed_is_not_in_the_picture(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "src.pdf", body=self.LINE)
        word, other = _word_rect(src, "Whitfield"), _word_rect(src, "Signed")
        _no_removal(monkeypatch)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=["Whitfield"])
        assert ei.value.flatten_pages == [1] and not os.path.exists(out)

        res = redact_pdf(src, out, search_terms=["Whitfield"], flatten_pages=[1])
        assert res.verification["verified"] is True and res.flattened_pages == [1]
        assert res.flattened_areas == {1: 1}
        assert _dark_share(out, word) > 0.95
        assert 0.02 < _dark_share(out, other) < 0.5         # the rest is still to be read

    def test_a_box_drawn_by_hand_is_filled_on_the_picture(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "src.pdf", body=self.LINE)
        word = _word_rect(src, "Whitfield")
        box = {"page": 0, "x0": word[0] - 3, "y0": word[1] - 3, "x1": word[2] + 3, "y1": word[3] + 3}
        _no_removal(monkeypatch)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[box], flatten_pages=[1])
        assert res.verification["verified"] is True and res.flattened_areas == {1: 1}
        assert _dark_share(out, [box[k] for k in ("x0", "y0", "x1", "y1")]) > 0.99

    def test_what_an_annotation_draws_is_covered_too(self, tmp_path, monkeypatch):
        """With the rule that takes such an annotation off the page switched
        off, so that this stands on its own: the stamp stays, the run is
        refused for the page, and the picture used to show the stamp."""
        import pdf_ops
        monkeypatch.setattr(pdf_ops, "_lift_annotations_drawing", lambda *a, **kw: 0,
                            raising=False)
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(pdf, _stamp(pdf)))
        word = _word_rect(src, TERM)
        assert 0.02 < _dark_share(src, word, below=160) < 0.6  # the fixture shows the word
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=[TERM])
        assert ei.value.flatten_pages == [1]
        res = redact_pdf(src, out, search_terms=[TERM], flatten_pages=[1])
        assert res.verification["verified"] is True and res.flattened_areas == {1: 1}
        assert _dark_share(out, word) > 0.95
    @pytest.mark.parametrize("rotate", [90, 180, 270])
    def test_on_a_turned_page_the_black_is_where_the_word_was(self, tmp_path, monkeypatch, rotate):
        src = _pdf(tmp_path / "src.pdf", body=self.LINE, rotate=rotate)
        word, other = _word_rect(src, "Whitfield"), _word_rect(src, "Signed")
        _no_removal(monkeypatch)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"], flatten_pages=[1])
        assert res.verification["verified"] is True and res.flattened_areas == {1: 1}
        # The picture is the page as shown: upright, so not turned again.
        with fitz.open(src) as doc:
            shown = [fitz.Rect(r) * doc[0].rotation_matrix for r in (word, other)]
        assert _dark_share(out, shown[0]) > 0.95 and 0.02 < _dark_share(out, shown[1]) < 0.5

    def test_only_the_pages_asked_for_and_a_count_for_each(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "src.pdf", body=self.LINE, pages=3)
        word = _word_rect(src, "Whitfield")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"], flatten_pages=[2])
        # Removal worked here: the page that became a picture had nothing left to cover.
        assert res.flattened_pages == [2] and res.flattened_areas == {2: 0}
        assert _dark_share(out, word, page=1) > 0.95           # the redaction's own box
        with fitz.open(out) as doc:
            assert [bool(p.get_text().strip()) for p in doc] == [True, False, True]

    def test_nothing_is_recorded_when_nothing_is_flattened(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", body=self.LINE)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["Whitfield"])
        assert res.flattened_areas == {}

# ── 3. A tagged file's description of what a box removed ─────────────────

#: A box over the picture _tagged puts on page 1, and one over nothing.
OVER_THE_PICTURE = {"page": 0, "x0": 90, "y0": 180, "x1": 310, "y1": 400}
OVER_NOTHING = {"page": 0, "x0": 400, "y0": 600, "x1": 500, "y1": 700}
SECRET = "the Harbour Lane depot"


def _described(**entries):
    """What a structure element can say of its content in words."""
    return {"Alt": String(f"photo of {SECRET}"), "ActualText": String(f"{SECRET}, from the air"),
            "E": String(f"{SECRET} (expanded)"), "T": String(f"Figure 1: {SECRET}"), **entries}


def _tagged(pdf, *, kid="number", by_name: bool = False, section: bool = False) -> dict:
    """Page 1 as a tagged page: a picture (marked content 0), a paragraph
    that stays (1) and a line about TERM (2), each with its structure
    element. *kid* is how the picture's element refers to its content: by
    "number", by a marked-content "reference", or both in an "array".
    Returns the elements by name."""
    page = pdf.pages[0].obj
    image = pdf.make_stream(b"\x40" * 64, Type=Name.XObject, Subtype=Name.Image, Width=8,
                            Height=8, ColorSpace=Name.DeviceGray, BitsPerComponent=8)
    page["/Resources"]["/XObject"] = Dictionary(Im1=image)
    figure_tag = b"/Figure <</MCID 0>> BDC"
    if by_name:
        page["/Resources"]["/Properties"] = Dictionary(MC0=Dictionary(MCID=0))
        figure_tag = b"/Figure /MC0 BDC"
    page["/Contents"] = pdf.make_stream(
        figure_tag + b" q 200 0 0 200 100 400 cm /Im1 Do Q EMC\n"
        b"/P <</MCID 1>> BDC BT /F1 14 Tf 72 720 Td (a paragraph that stays) Tj ET EMC\n"
        + f"/P <</MCID 2>> BDC BT /F1 14 Tf 72 690 Td (a line about {TERM} here) Tj ET EMC".encode())
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    top = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    kids = {"number": 0, "reference": Dictionary(Type=Name.MCR, Pg=page, MCID=0),
            "array": Array([Dictionary(Type=Name.MCR, Pg=page, MCID=0), 7])}[kid]
    figure = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, P=top, Pg=page,
                                          K=kids, **_described()))
    stays = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=top, Pg=page, K=1,
                                         Alt=String("a description that stays")))
    line = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=top, Pg=page, K=2,
                                        ActualText=String("the line, as it is spoken")))
    elems = {"figure": figure, "stays": stays, "line": line, "top": top}
    if section:
        sect = pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name.Sect, P=top, K=Array([figure]),
            Alt=String(f"a section showing {SECRET}"), T=String("Section 1")))
        figure["/P"] = sect
        elems["section"] = sect
        top["/K"] = Array([sect, stays, line])
    else:
        top["/K"] = Array([figure, stays, line])
    root["/K"] = Array([top])
    pdf.Root["/StructTreeRoot"] = root
    pdf.Root["/MarkInfo"] = Dictionary(Marked=True)
    return elems


def _said(path) -> dict:
    """{structure type: the describing entries its element still has}, for
    the elements of a file _tagged built."""
    found, texts = {}, ("/Alt", "/ActualText", "/E", "/T")
    with pikepdf.open(path) as pdf:
        stack = [pdf.Root["/StructTreeRoot"]]
        while stack:
            elem = stack.pop()
            kids = elem.get("/K")
            for k in (kids if isinstance(kids, pikepdf.Array) else [kids]):
                if isinstance(k, pikepdf.Dictionary) and "/S" in k:
                    stack.append(k)
            if "/S" in elem:
                name = str(elem["/S"]).lstrip("/") + ("" if str(elem["/S"]) != "/P" else str(elem["/K"]))
                found[name] = sorted(t for t in texts if t in elem)
    return found


@pytest.mark.integration
class TestWhatATaggedFileSaysOfRemovedContent:
    """A structure element whose own content a box removed loses what it
    said of that content in words: /Alt, /ActualText, /E and /T."""

    @pytest.mark.parametrize("kid", ["number", "reference", "array"])
    def test_a_picture_under_a_box_takes_its_description(self, tmp_path, kid):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _tagged(pdf, kid=kid))
        assert _holds(src, SECRET)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_PICTURE])
        assert res.verification["verified"] is True
        assert res.surface_counts == {"page_content": 1, "structure_text": 4}
        assert not _holds(out, SECRET)
        said = _said(out)
        assert said["Figure"] == [] and said["P1"] == ["/Alt"] and said["P2"] == ["/ActualText"]

    def test_marked_content_named_through_the_page_s_properties(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _tagged(pdf, by_name=True))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_PICTURE])
        assert res.surface_counts.get("structure_text") == 4 and not _holds(out, SECRET)

    def test_a_term_box_does_the_same(self, tmp_path):
        """The line about the term is removed from the page; what its
        element said of it goes, though it does not hold the term."""
        src = _pdf(tmp_path / "src.pdf", _tagged)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True
        assert res.surface_counts == {"page_content": 1, "structure_text": 1}
        said = _said(out)
        assert said["P2"] == [] and said["P1"] == ["/Alt"] and len(said["Figure"]) == 4

    def test_what_stands_above_it_loses_its_description_and_keeps_its_title(self, tmp_path):
        """/Alt, /ActualText and /E speak for an element and everything
        under it. A title is the element's own."""
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _tagged(pdf, section=True))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_PICTURE])
        assert res.surface_counts.get("structure_text") == 5 and not _holds(out, SECRET)
        assert _said(out)["Sect"] == ["/T"]

    def test_a_box_over_nothing_removes_no_description(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _tagged)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_NOTHING])
        assert res.surface_counts == {"page_content": 1}
        assert len(_said(out)["Figure"]) == 4

    def test_content_on_a_page_without_a_box_is_not_touched(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _tagged, pages=2)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[dict(OVER_THE_PICTURE, page=1)])
        assert res.surface_counts == {"page_content": 1} and len(_said(out)["Figure"]) == 4

    def test_a_form_s_content_is_followed_into_the_form(self, tmp_path):
        """The element refers to marked content inside a form XObject. The
        page paints a redacted copy of the form; the two are compared."""
        def plant(pdf):
            page = pdf.pages[0].obj
            def form(text, number):
                return pdf.make_stream(
                    f"/P <</MCID 0>> BDC BT /F1 14 Tf 10 20 Td ({text}) Tj ET EMC".encode(),
                    Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 300, 40],
                    StructParents=number, Resources=Dictionary(Font=Dictionary(F1=_font(pdf))))
            under, clear = form("a figure under the box", 0), form("a figure left alone", 1)
            page["/Resources"]["/XObject"] = Dictionary(X1=under, X2=clear)
            page["/Contents"] = pdf.make_stream(
                b"q 1 0 0 1 72 600 cm /X1 Do Q q 1 0 0 1 72 300 cm /X2 Do Q")
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Figure, P=root, Pg=page,
                K=Dictionary(Type=Name.MCR, MCID=0, Stm=stream), Alt=String(alt)))
                for stream, alt in ((under, f"plan of {SECRET}"), (clear, "an ordinary chart"))])
            pdf.Root["/StructTreeRoot"] = root
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[{"page": 0, "x0": 60, "y0": 150, "x1": 400, "y1": 200}])
        assert res.verification["verified"] is True and not _holds(out, SECRET)
        assert res.surface_counts.get("structure_text") == 1
        with pikepdf.open(out) as pdf:
            first, second = pdf.Root["/StructTreeRoot"]["/K"]
            assert "/Alt" not in first and str(second["/Alt"]) == "an ordinary chart"

    def test_an_annotation_taken_from_under_a_box_takes_its_description(self, tmp_path):
        def plant(pdf):
            stamp = _stamp(pdf, "approved")
            _on_page(pdf, stamp)
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Annot, P=root, Alt=String(f"stamp of {SECRET}"),
                K=Dictionary(Type=Name.OBJR, Obj=stamp)))])
            pdf.Root["/StructTreeRoot"] = root
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True and not _holds(out, SECRET)
        assert res.surface_counts.get("structure_text") == 1

    def test_what_cannot_be_compared_is_taken_as_removed(self, tmp_path, monkeypatch):
        """If a page's content cannot be read, before or after, every
        element with content on a boxed page loses its description."""
        import pdf_ops
        monkeypatch.setattr(pdf_ops, "_page_marks", lambda page: None, raising=False)
        src = _pdf(tmp_path / "src.pdf", _tagged)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_NOTHING])
        said = _said(out)
        assert said["Figure"] == [] and said["P1"] == [] and said["P2"] == []
        assert res.surface_counts.get("structure_text") == 6


@pytest.mark.integration
class TestNoStreamIsKeptByTheStructureTreeAlone:
    """The check for what the removal of stale references fixes: without
    it, nothing would notice if that removal stopped working under a box."""

    CHECK = "redaction.structure_streams"

    def _stale(self, pdf):
        root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        gone = pdf.make_stream(b"BT /F1 14 Tf 10 20 Td (the original) Tj ET", Type=Name.XObject,
                               Subtype=Name.Form, BBox=[0, 0, 300, 40])
        root["/K"] = Array([pdf.make_indirect(Dictionary(
            Type=Name.StructElem, S=Name.P, P=root, K=Dictionary(Type=Name.MCR, MCID=0, Stm=gone)))])
        pdf.Root["/StructTreeRoot"] = root

    @pytest.mark.parametrize("mode", ["a search", "a box"])
    def test_a_stream_only_the_tree_leads_to_fails_it(self, tmp_path, mode):
        src = _pdf(tmp_path / "src.pdf", self._stale)
        kw = {"terms": [TERM]} if mode == "a search" else {"rects": [OVER_NOTHING]}
        report = verify_redaction(src, **kw)
        check = _check(report, self.CHECK)
        assert check.passed is False and check.is_document_level
        assert report.flatten_target_pages() is None

    def test_a_tagged_file_with_nothing_stale_passes(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _tagged)
        assert _check(verify_redaction(src, rects=[OVER_NOTHING]), self.CHECK).passed is True

    def test_a_redaction_that_left_one_is_refused(self, tmp_path, monkeypatch):
        import pdf_ops
        monkeypatch.setattr(pdf_ops, "_drop_stale_structure_refs", lambda pdf: 0)
        def plant(pdf):
            page = pdf.pages[0].obj
            form = pdf.make_stream(
                f"/P <</MCID 0>> BDC BT /F1 14 Tf 10 20 Td (drawn by {TERM}) Tj ET EMC".encode(),
                Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 300, 40], StructParents=0,
                Resources=Dictionary(Font=Dictionary(F1=_font(pdf))))
            page["/Resources"]["/XObject"] = Dictionary(X1=form)
            page["/Contents"] = pdf.make_stream(b"q 1 0 0 1 72 600 cm /X1 Do Q")
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.P, P=root, Pg=page,
                K=Dictionary(Type=Name.MCR, MCID=0, Stm=form)))])
            pdf.Root["/StructTreeRoot"] = root
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, rects=[{"page": 0, "x0": 60, "y0": 150, "x1": 400, "y1": 200}])
        failed = [c["id"] for c in ei.value.report["checks"] if not c["passed"]]
        assert failed == [self.CHECK] and ei.value.flatten_pages is None
        assert not os.path.exists(out)


# ── 4. Page thumbnails ───────────────────────────────────────────────────


def _thumbs(pdf, *pages: int) -> None:
    for n in pages:
        pdf.pages[n].obj["/Thumb"] = pdf.make_stream(
            b"\x30" * 48, Width=6, Height=8, ColorSpace=Name.DeviceGray, BitsPerComponent=8)


@pytest.mark.integration
class TestPageThumbnails:
    """A thumbnail is a small picture of the page as it was."""

    CHECK = "redaction.thumbnails"

    @pytest.mark.parametrize("mode", ["a search", "a box"])
    def test_every_page_loses_its_thumbnail(self, tmp_path, mode):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _thumbs(pdf, 0, 2), body=f"about {TERM}",
                   pages=3)
        out = str(tmp_path / "out.pdf")
        # The box is on page 1 only; the thumbnails are on pages 1 and 3.
        kw = {"search_terms": [TERM]} if mode == "a search" else {"rects": [OVER_NOTHING]}
        res = redact_pdf(src, out, **kw)
        assert res.verification["verified"] is True and res.surface_counts.get("thumbnails") == 2
        with pikepdf.open(out) as pdf:
            assert not any("/Thumb" in page.obj for page in pdf.pages)

    def test_a_file_without_any_counts_none(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", body=f"about {TERM}")
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert "thumbnails" not in res.surface_counts

    def test_the_verifier_fails_a_file_that_kept_one(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _thumbs(pdf, 1), pages=2)
        check = _check(verify_redaction(src, [TERM]), self.CHECK)
        assert check.passed is False and check.is_document_level
        assert check.evidence == ["page 2 still carries a thumbnail of the page as it was "
                                  "(document-level)"]
        assert _check(verify_redaction(_pdf(tmp_path / "clean.pdf"), [TERM]), self.CHECK).passed
