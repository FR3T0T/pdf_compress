"""Redaction completeness (#119), clusters R3 and R4: what a redaction still
left to see, and what a box drawn by hand is taken to cover.

Each of these finished "verified" — some only after the page had been made
a picture, with the word plainly readable in it:

  1. Nothing is drawn above a box. An annotation paints over the page, and
     redaction left every annotation but its own marks alone.
  2. A page flattened to a picture is blacked out first, on the picture.
  3. A tagged file's description of what a box removed goes with it.
  4. A page's thumbnail is a picture of the page as it was.

And for a box drawn by hand (the sections further down): line art under
it, a straight line that only runs through it, a letter on its edge, and
what is counted when an annotation or a form field leaves the page.

Every fixture is built here, by hand. Nothing imported at the top is new,
so the file collects against the tree before the change and each test fails
there for its own reason.

Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import os

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

import pdf_ops
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
        assert res.surface_counts == {"page_content": 1, "annotations_removed": 1}
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
        assert res.verification["verified"] is True
        assert res.surface_counts == {"annotations_removed": 1}
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
        assert res.surface_counts.get("annotations_removed") == 3   # the stamp, two replies
        assert _subtypes(out) == ["/Text", "/Popup"]          # the other note keeps its popup
        with pikepdf.open(out) as pdf:
            note, popup = pdf.pages[0].obj["/Annots"]
            assert note["/Popup"].objgen == popup.objgen

    def test_what_draws_no_term_is_left_alone(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(
            pdf, *_with_popup_and_replies(pdf, _stamp(pdf, "approved"))), body=f"about {TERM}")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True
        assert res.surface_counts == {"page_content": 1}
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
        assert res.surface_counts == {"page_content": 1, "annotations_removed": 1}
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
        assert _subtypes(out) == [] and res.surface_counts.get("annotations_removed") == 1

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
        assert res.surface_counts.get("annotations_removed") == 3   # the stamp, two replies

    @pytest.mark.parametrize("rotate", [90, 180, 270])
    def test_on_a_turned_page(self, tmp_path, rotate):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(pdf, _stamp(pdf)), rotate=rotate)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True and _subtypes(out) == []

    @pytest.mark.parametrize("kinds", [
        ["Highlight"], ["Ink"], ["Square"], ["FileAttachment"], ["FreeText"],
        ["Highlight", "Ink", "FileAttachment", "FreeText"]], ids=" and ".join)
    @pytest.mark.parametrize("rotate", [0, 90])
    def test_one_that_is_nothing_but_a_rectangle(self, tmp_path, kinds, rotate):
        """A highlight with no quads, an ink note with no strokes: PyMuPDF
        has no rectangle for such an annotation until it has redrawn the
        page. It is placed by its own /Rect, and goes like any other."""
        def plant(pdf):
            _on_page(pdf, *[pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name("/" + kind), Rect=STAMP_RECT,
                Contents=String("x"))) for kind in kinds])
        src = _pdf(tmp_path / "src.pdf", plant, rotate=rotate)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True and _subtypes(out) == []
        assert res.surface_counts == {"page_content": 1, "annotations_removed": len(kinds)}

    @pytest.mark.parametrize("rotate", [0, 90])
    def test_links_under_the_box_go_and_the_others_stay(self, tmp_path, rotate):
        """A link half under the box goes like one wholly under it. (The
        page's links are read in one pass: asking PyMuPDF for them one by
        one took minutes on a page with thousands.)"""
        def plant(pdf):
            def link(rect):
                return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                                    Rect=rect, Dest=Array([0, Name.Fit])))
            _on_page(pdf, link([100, 590, 200, 600]), link([380, 585, 480, 605]),
                     *[link([40 + 50 * (k % 10), 100 + 12 * (k // 10), 80 + 50 * (k % 10),
                             108 + 12 * (k // 10)]) for k in range(200)])
        src = _pdf(tmp_path / "src.pdf", plant, rotate=rotate)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True
        assert _subtypes(out) == ["/Link"] * 200
        assert "annotations_removed" not in res.surface_counts      # a link is not counted

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


# ── 9. Two counts for annotations ────────────────────────────────────────


@pytest.mark.integration
class TestAnnotationCounts:
    """"annotations" is text scrubbed inside an annotation, as it has been.
    An annotation taken off its page is counted as "annotations_removed"."""

    def test_the_two_are_counted_apart(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _on_page(
            pdf, _stamp(pdf), _note(pdf, Contents=String(f"ask {TERM} first"))))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.surface_counts == {"page_content": 1, "annotations": 1,
                                      "annotations_removed": 1}
        assert _subtypes(out) == ["/Text"]

    def test_an_appearance_dropped_off_the_page_is_a_scrub_not_a_removal(self, tmp_path):
        def plant(pdf):
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Annot, P=root,
                K=Dictionary(Type=Name.OBJR, Obj=_stamp(pdf))))])
            pdf.Root["/StructTreeRoot"] = root
        src = _pdf(tmp_path / "src.pdf", plant, body=f"about {TERM}")
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert res.surface_counts.get("annotations") == 1
        assert "annotations_removed" not in res.surface_counts


# ── 8. An annotation that is not an object of its own ────────────────────


def _written_in_the_list(pdf, text: str = f"approved by {TERM}") -> None:
    """A stamp whose dictionary stands in the page's /Annots array itself,
    and a note that is an object of its own, elsewhere on the page."""
    pdf.pages[0].obj["/Annots"] = Array([
        Dictionary(Type=Name.Annot, Subtype=Name.Stamp, Rect=STAMP_RECT,
                   AP=Dictionary(N=_drawn(pdf, text))),
        _note(pdf, Contents=String("an unrelated note"))])


@pytest.mark.integration
class TestAnAnnotationWrittenIntoThePageList:
    """PyMuPDF's list of a page's annotations leaves out one that has no
    object number. It is on the page all the same."""

    def test_the_fixture_is_what_it_claims(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _written_in_the_list)
        with pikepdf.open(src) as pdf:
            stamp, note = pdf.pages[0].obj["/Annots"]
            assert not stamp.is_indirect and note.is_indirect

    def test_under_a_box_it_leaves_the_page(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _written_in_the_list(pdf, "approved"))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert res.surface_counts == {"page_content": 1, "annotations_removed": 1}
        assert _subtypes(out) == ["/Text"]

    def test_drawing_a_term_it_leaves_the_page(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _written_in_the_list)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True
        assert res.surface_counts == {"page_content": 1, "annotations_removed": 1}
        assert _subtypes(out) == ["/Text"] and not _holds(out)

    def test_one_that_draws_no_term_and_is_under_no_box_stays(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _written_in_the_list(pdf, "approved"),
                   body=f"about {TERM}")
        out = str(tmp_path / "out.pdf")
        redact_pdf(src, out, search_terms=[TERM], rects=[OVER_NOTHING])
        assert _subtypes(out) == ["/Stamp", "/Text"]

    @pytest.mark.filterwarnings("ignore:This document has 1 form widget")     # the fixture's own
    @pytest.mark.parametrize("rotate", [0, 90])
    def test_a_form_field_s_widget_written_into_the_list_goes_under_a_box(self, tmp_path, rotate):
        """PyMuPDF 1.27 does not delete such a widget when asked to (2.1
        does): it was left under the box, and the run was refused."""
        def plant(pdf):
            pdf.pages[0].obj["/Annots"] = Array([Dictionary(
                Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("who"),
                V=String(f"{TERM} Ltd"), Rect=STAMP_RECT, DA=String("/Helv 12 Tf 0 g"))])
        src = _pdf(tmp_path / "src.pdf", plant, rotate=rotate)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert _subtypes(out) == [] and not _holds(out)
        assert res.surface_counts == {"page_content": 1, "fields_removed": 1}     # R4, on both

    def test_the_verifier_reads_its_rectangle(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _written_in_the_list(pdf, "approved"))
        check = _check(verify_redaction(src, rects=[OVER_THE_STAMP]), "redaction.rect_annotations")
        assert check.passed is False
        assert check.evidence == ["a Stamp annotation still lies over a redaction rectangle on page 1"]
        assert _check(verify_redaction(src, rects=[OVER_NOTHING]),
                      "redaction.rect_annotations").passed is True


# ── 5. Line art under a box drawn by hand ────────────────────────────────

#: A box, in PyMuPDF's coordinates (from the top of the 792-point page),
#: and drawings in the page's own (from the bottom).
ART_BOX = {"page": 0, "x0": 90, "y0": 142, "x1": 210, "y1": 202}        # y 590..650 from the bottom
CURVE = b"0 0 1 RG 1 w 100 600 m 150 660 150 660 200 600 c S"           # runs inside, control points above
INSIDE = b"0 0 1 RG 2 w 110 620 m 190 620 l S"                          # wholly inside
RULE = b"0 0 1 RG 1 w 50 615 m 400 615 l S"                             # a long rule through the box
FRAME = b"0 0 1 RG 1 w 80 580 140 80 re S"                              # a frame round the box
EDGE = b"0 0 1 RG 1 w 90 590 m 210 590 l S"                             # a rule along the box's lower edge
BLACK = b"0 g 100 600 40 20 re f"                                       # a solid black bar inside
ENDS_UNDER = b"0 0 1 RG 1 w 50 615 m 150 615 l S"                       # a rule that ends under the box
BENT = b"0 0 1 RG 1 w 50 615 m 150 615 l 150 700 l S"                   # two rules that meet under it
CORNER = b"0 0 1 RG 1 w 150 620 300 300 re S"                           # a frame with one corner under it
CURVE_THROUGH = b"0 0 1 RG 1 w 50 600 m 150 640 250 640 350 600 c S"    # a curve, both ends outside
ROW = b"0.9 g 50 600 400 30 re f"                                       # a shaded row through the box
BORDERS = b"0 0 1 RG 1 w 150 500 m 150 700 l S 50 620 m 400 620 l S"    # two borders that cross under it
LINE_ART = "redaction.rect_line_art"


def _art(*drawings: bytes):
    def plant(pdf):
        page = pdf.pages[0].obj
        page["/Contents"] = pdf.make_stream(
            page["/Contents"].read_bytes() + b"\n" + b"\n".join(drawings))
    return plant


def _edges_inside(path, box: dict) -> int:
    """How many drawings of the page still have a point of their path
    strictly inside *box* — a look of this file's own, by sampling."""
    inside = 0
    with fitz.open(path) as doc:
        for d in doc[box["page"]].get_drawings():
            if d.get("fill") == (0.0, 0.0, 0.0) and all(it[0] == "re" for it in d["items"]):
                continue                                    # the redaction's own fill
            points = []
            for it in d["items"]:
                if it[0] == "l":
                    a, b = it[1], it[2]
                    points += [(a.x + (b.x - a.x) * t / 40, a.y + (b.y - a.y) * t / 40)
                               for t in range(41)]
                elif it[0] == "c":
                    p = it[1:5]
                    for t in (n / 40 for n in range(41)):
                        u = 1 - t
                        w = (u ** 3, 3 * u * u * t, 3 * u * t * t, t ** 3)
                        points.append((sum(k * q.x for k, q in zip(w, p, strict=True)),
                                       sum(k * q.y for k, q in zip(w, p, strict=True))))
                elif it[0] == "re":
                    r = it[1]
                    points += [(r.x0 + (r.x1 - r.x0) * t / 40, y) for t in range(41)
                               for y in (r.y0, r.y1)]
                    points += [(x, r.y0 + (r.y1 - r.y0) * t / 40) for t in range(41)
                               for x in (r.x0, r.x1)]
            if any(box["x0"] + 0.5 < x < box["x1"] - 0.5 and box["y0"] + 0.5 < y < box["y1"] - 0.5
                   for x, y in points):
                inside += 1
    return inside


def _coloured(path, page: int = 0) -> list:
    """The drawings of a page that are not a solid black fill."""
    with fitz.open(path) as doc:
        return [d for d in doc[page].get_drawings() if d.get("fill") != (0.0, 0.0, 0.0)]


@pytest.mark.integration
class TestLineArtUnderABoxDrawnByHand:
    """A stroked path under a box stays in the page's content, under the
    fill, unless it lies wholly inside the box as PyMuPDF measures that."""

    def test_a_curve_that_runs_inside_the_box_is_removed(self, tmp_path):
        """Its control points are outside the box, so the default leaves it.
        Nothing else is near: everything that touches the box can go."""
        src = _pdf(tmp_path / "src.pdf", _art(CURVE))
        assert _edges_inside(src, ART_BOX) == 1             # the fixture is what it claims
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[ART_BOX])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert _edges_inside(out, ART_BOX) == 0

    def test_a_frame_round_the_box_is_never_the_price(self, tmp_path):
        """With a frame round the box, removing whatever touches it would
        take the frame. The curve stays, and the run is refused for the
        page instead; flattened, the box is black on the picture."""
        src = _pdf(tmp_path / "src.pdf", _art(CURVE, FRAME))
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, rects=[ART_BOX])
        failed = [c["id"] for c in ei.value.report["checks"] if not c["passed"]]
        assert failed == [LINE_ART] and ei.value.flatten_pages == [1]
        assert not os.path.exists(out)
        res = redact_pdf(src, out, rects=[ART_BOX], flatten_pages=[1])
        assert res.verification["verified"] is True and res.flattened_areas == {1: 1}
        assert _dark_share(out, [ART_BOX[k] for k in ("x0", "y0", "x1", "y1")]) > 0.99

    def test_a_rule_that_ends_under_the_box_is_refused_not_deleted(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _art(ENDS_UNDER))
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, str(tmp_path / "out.pdf"), rects=[ART_BOX])
        assert ei.value.flatten_pages == [1]
        (check,) = [c for c in ei.value.report["checks"] if not c["passed"]]
        assert check["id"] == LINE_ART
        assert check["evidence"] == ["a drawing still has an edge inside a redaction "
                                     "rectangle on page 1"]

    def test_a_rule_through_the_box_hides_nothing_and_is_left(self, tmp_path):
        """It runs in one side and out another: the part under the box is
        fixed by the parts outside it (R4)."""
        src = _pdf(tmp_path / "src.pdf", _art(RULE))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[ART_BOX])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert _check(res.verification, LINE_ART)["passed"] is True
        assert len(_coloured(out)) == 1                     # the rule, whole

    def test_borders_and_a_shaded_row_through_the_box_are_left(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _art(ROW, BORDERS))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[ART_BOX])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert len(_coloured(out)) == 3                     # the row and both borders

    def test_a_rule_through_the_box_is_never_the_price_either(self, tmp_path):
        """The rule is not under the box, but removing whatever touches the
        box would take it, all of it, with the curve. The curve stays and
        the run is refused for the page."""
        src = _pdf(tmp_path / "src.pdf", _art(CURVE, RULE))
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, rects=[ART_BOX])
        failed = [c["id"] for c in ei.value.report["checks"] if not c["passed"]]
        assert failed == [LINE_ART] and ei.value.flatten_pages == [1]
        assert not os.path.exists(out)

    def test_what_lies_wholly_inside_goes_and_the_rule_through_stays(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _art(INSIDE, RULE))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[ART_BOX])
        assert res.verification["verified"] is True
        (rule,) = _coloured(out)
        assert rule["rect"].width > 300

    def test_what_lies_wholly_inside_goes_and_the_frame_stays(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _art(INSIDE, FRAME, EDGE))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[ART_BOX])
        assert res.verification["verified"] is True and _edges_inside(out, ART_BOX) == 0
        with fitz.open(out) as doc:
            kept = [d for d in doc[0].get_drawings() if d.get("fill") != (0.0, 0.0, 0.0)]
        assert len(kept) == 2                               # the frame and the rule on the edge

    def test_a_term_box_is_not_given_that_power(self, tmp_path):
        """Box mode only: a rule under a word found by search is as it was."""
        def plant(pdf):
            page = pdf.pages[0].obj
            page["/Contents"] = pdf.make_stream(
                f"BT /F1 14 Tf 72 720 Td (a line about {TERM} here) Tj ET\n".encode()
                + b"0 0 1 RG 1 w 60 722 m 400 722 l S")
        src = _pdf(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True
        assert LINE_ART not in [c["id"] for c in res.verification["checks"]]
        with fitz.open(out) as doc:
            assert any(d["rect"].width > 300 for d in doc[0].get_drawings())


@pytest.mark.integration
class TestTheVerifierLooksForLineArtInsideABox:
    @pytest.mark.parametrize("drawing, passes", [
        (CURVE, False), (INSIDE, False), (ENDS_UNDER, False), (BENT, False), (CORNER, False),
        (CURVE_THROUGH, False),
        (RULE, True), (ROW, True), (BORDERS, True), (FRAME, True), (EDGE, True), (BLACK, True),
    ], ids=["a curve", "a line inside", "a rule that ends under it", "two rules that meet under it",
            "a corner under it", "a curve through it",
            "a rule through", "a shaded row through", "borders that cross under it",
            "a frame round it", "a rule along its edge", "a solid black bar"])
    def test_an_edge_inside_fails_it_and_nothing_else_does(self, tmp_path, drawing, passes):
        """A straight line both of whose ends are outside the box is not
        under it: what the box covers of it is fixed by what it does not
        (R4). A line that ends under the box, a corner, any curve, and
        anything wholly inside are."""
        src = _pdf(tmp_path / "src.pdf", _art(drawing))
        report = verify_redaction(src, rects=[ART_BOX])
        check = _check(report, LINE_ART)
        assert check.passed is passes
        if not passes:
            assert check.pages == [1] and report.flatten_target_pages() is not None

    def test_another_box_s_own_fill_is_not_line_art(self, tmp_path):
        """Two boxes that overlap, and a term's box inside one drawn by
        hand: each fill has an edge inside the other."""
        src = _pdf(tmp_path / "src.pdf", body=f"a line about {TERM} here")
        word = _word_rect(src, TERM)
        over = {"page": 0, "x0": word[0] - 40, "y0": word[1] - 20, "x1": word[2] - 10,
                "y1": word[3] + 20}
        lower = dict(over, y0=over["y0"] + 15, y1=over["y1"] + 30)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM], rects=[over, lower])
        assert res.verification["verified"] is True


# ── 6. A term box takes only its own characters ──────────────────────────

#: Three lines of 12-point type 13 points apart: each line's characters
#: reach 3.5 points into the next line's.
TIGHT = (b"BT /F1 12 Tf 72 720 Td (gypsy Algy jumpy) Tj 0 -13 Td (above Whitfield below) Tj "
         b"0 -13 Td (TALL Hdlb CAPS) Tj ET")
NEIGHBOURS = ["gypsy", "Algy", "jumpy", "above", "below", "TALL", "Hdlb", "CAPS"]


def _content(data: bytes):
    return lambda pdf: pdf.pages[0].obj.__setitem__("/Contents", pdf.make_stream(data))


def _page_words(path) -> list:
    with fitz.open(path) as doc:
        return doc[0].get_text().split()


@pytest.mark.integration
class TestATermBoxTakesOnlyItsOwnCharacters:
    """A character goes if its box overlaps a redaction rectangle at all,
    and the box of a word is as tall as its line. In type set close, the
    words above and below lost letters, or went."""

    def test_the_lines_above_and_below_are_left_whole(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _content(TIGHT))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"])
        assert res.verification["verified"] is True and res.redaction_count == 1
        assert _page_words(out) == NEIGHBOURS

    def test_the_black_box_is_still_the_whole_box(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _content(TIGHT))
        word = _word_rect(src, "Whitfield")
        out = str(tmp_path / "out.pdf")
        redact_pdf(src, out, search_terms=["Whitfield"])
        assert _dark_share(out, word) > 0.99

    def test_the_pixels_of_a_scan_are_covered_by_the_whole_box(self, tmp_path):
        """An image under the text, as on a scanned page with its text
        layer: its pixels are blanked under the full box, as before."""
        def plant(pdf):
            page = pdf.pages[0].obj
            page["/Resources"]["/XObject"] = Dictionary(Im1=pdf.make_stream(
                b"\xd0" * 64, Type=Name.XObject, Subtype=Name.Image, Width=8, Height=8,
                ColorSpace=Name.DeviceGray, BitsPerComponent=8))
            page["/Contents"] = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im1 Do Q\n" + TIGHT)
        src = _pdf(tmp_path / "src.pdf", plant)
        word = _word_rect(src, "Whitfield")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"])
        assert res.verification["verified"] is True and _page_words(out) == NEIGHBOURS
        with fitz.open(out) as doc:
            (xref,) = [img[0] for img in doc[0].get_images()]
            pix = fitz.Pixmap(doc, xref)
            # The pixels the box touches are blanked in the image itself
            # (the black fill is drawn on the page, over them); the rest stay.
            assert 0xFF in pix.samples and 0xD0 in pix.samples
        assert _dark_share(out, word) > 0.99

    def test_trimming_never_costs_a_removal(self, tmp_path, monkeypatch):
        """Make the trimmed pass remove nothing. The page is searched
        again, and what is still found gets the whole box, text and all."""
        real = fitz.Page.apply_redactions

        def only_whole_boxes(self, *args, **kw):
            if kw.get("images") == fitz.PDF_REDACT_IMAGE_NONE:
                for annot in list(self.annots(types=[fitz.PDF_ANNOT_REDACT]) or []):
                    self.delete_annot(annot)
                return True
            return real(self, *args, **kw)

        monkeypatch.setattr(fitz.Page, "apply_redactions", only_whole_boxes)
        src = _pdf(tmp_path / "src.pdf", _content(TIGHT))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"])
        assert res.verification["verified"] is True and res.redaction_count == 1
        assert "Whitfield" not in _page_words(out)


# ── R4. A letter on the edge of a box drawn by hand ──────────────────────

#: The same three lines 16 points apart: a line's characters reach half a
#: point into the next line's.
LOOSE = TIGHT.replace(b"-13 Td", b"-16 Td")
REGIONS = "redaction.rect_regions"


def _around(rect, by: float = 0.0, page: int = 0) -> dict:
    return {"page": page, "x0": rect[0] - by, "y0": rect[1] - by,
            "x1": rect[2] + by, "y1": rect[3] + by}


def _wide(text: str):
    """One line of 12-point type in a font whose /Widths give every
    character two ems. Its text box is 24 points wide; the letter is drawn
    in the first few of them, and that is all MuPDF's redaction measures."""
    def plant(pdf):
        page = pdf.pages[0].obj
        page["/Resources"] = Dictionary(Font=Dictionary(F1=pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
            FirstChar=32, LastChar=126, Widths=Array([2000] * 95)))))
        page["/Contents"] = pdf.make_stream(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode())
    return plant


def _right_part_of(path, letter: str, share: float = 0.6) -> dict:
    """A box over the right-hand *share* of the text box of *letter*."""
    with fitz.open(path) as doc:
        (box,) = [ch["bbox"] for block in doc[0].get_text("rawdict")["blocks"]
                  for line in block.get("lines", ()) for span in line["spans"]
                  for ch in span["chars"] if ch["c"] == letter]
    return {"page": 0, "x0": box[2] - share * (box[2] - box[0]), "y0": box[1],
            "x1": box[2], "y1": box[3]}


@pytest.mark.integration
class TestALetterOnTheEdgeOfABoxDrawnByHand:
    """MuPDF leaves a character a box reaches less than a tenth into, and
    the check asked for no text at all in the box: a box a point too tall
    was refused for a letter of the next line. One rule for both sides now:
    a character is under the box when its centre is inside it, or a fifth
    or more of its text box is."""

    def test_a_box_a_point_into_the_next_lines_verifies(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _content(LOOSE))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[_around(_word_rect(src, "Whitfield"), 1)])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert _page_words(out) == NEIGHBOURS

    @pytest.mark.parametrize("share, passes", [
        (0.05, True), (0.1, True), (0.15, True), (0.19, True),
        (0.21, False), (0.5, False), (0.9, False)])
    def test_a_fifth_of_a_character_is_under_the_box(self, tmp_path, share, passes):
        """A box that comes up from below into a line: at a tenth of the
        line's height it is on the tails of g, y and p, and the old check,
        which went by the drawn shape, failed there. Nothing half under a
        box may pass."""
        src = _pdf(tmp_path / "src.pdf", body="one gypsy line")
        x0, y0, x1, y1 = _word_rect(src, "gypsy")
        rect = {"page": 0, "x0": x0, "y0": y1 - share * (y1 - y0), "x1": x1, "y1": y1 + 40}
        report = verify_redaction(src, rects=[rect])
        check = _check(report, REGIONS)
        assert check.passed is passes
        if not passes:
            assert check.evidence == ["text still extractable inside redaction rect on "
                                      "page 1: 'gypsy'"]
            assert report.flatten_target_pages() == [1]

    def test_a_text_box_with_no_width_is_measured_by_its_height(self, tmp_path):
        """A font whose /Widths are all nought: a character's text box is
        the upright line it is written from (the W here from x=100, 33
        points tall). A box that line passes through is over it if it has
        a fifth of the line."""
        def plant(pdf):
            page = pdf.pages[0].obj
            page["/Resources"] = Dictionary(Font=Dictionary(F1=pdf.make_indirect(Dictionary(
                Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                FirstChar=32, LastChar=126, Widths=Array([0] * 95)))))
            page["/Contents"] = pdf.make_stream(
                b"BT /F1 24 Tf 100 700 Td [(W) -700 (h) -600 (o)] TJ ET")
        src = _pdf(tmp_path / "src.pdf", plant)
        a_third = {"page": 0, "x0": 90, "y0": 88, "x1": 110, "y1": 140}
        a_tenth = dict(a_third, y0=96)
        beside = {"page": 0, "x0": 300, "y0": 70, "x1": 400, "y1": 100}
        assert _check(verify_redaction(src, rects=[a_third]), REGIONS).evidence == [
            "text still extractable inside redaction rect on page 1: 'W'"]
        assert _check(verify_redaction(src, rects=[a_tenth]), REGIONS).passed is True
        assert _check(verify_redaction(src, rects=[beside]), REGIONS).passed is True
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[a_third])
        assert res.verification["verified"] is True and _page_words(out) == ["h", "o"]

    def test_a_character_whose_centre_is_in_the_box_is_under_it(self, tmp_path):
        """However little of it the box covers; and a box as small that
        misses the centre is not over it."""
        src = _pdf(tmp_path / "src.pdf", _content(b"BT /F1 144 Tf 72 500 Td (H) Tj ET"))
        x0, y0, x1, y1 = _word_rect(src, "H")
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        on_it = {"page": 0, "x0": cx - 2, "y0": cy - 2, "x1": cx + 2, "y1": cy + 2}
        beside = {"page": 0, "x0": x0 + 4, "y0": y0 + 4, "x1": x0 + 12, "y1": y0 + 12}
        assert _check(verify_redaction(src, rects=[on_it]), REGIONS).passed is False
        assert _check(verify_redaction(src, rects=[beside]), REGIONS).passed is True

    @pytest.mark.parametrize("letter", ["H", "i"])
    def test_a_letter_the_first_pass_left_under_the_box_is_removed(self, tmp_path, letter):
        """More than half of its text box is in the box and MuPDF, which
        measures the letter itself, left it. It is boxed and removed: by
        the middle of its text box, and if that misses the letter (the i),
        by all of it."""
        src = _pdf(tmp_path / "src.pdf", _wide(f"stay {letter} go"))
        box = _right_part_of(src, letter)
        assert _check(verify_redaction(src, rects=[box]), REGIONS).passed is False
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[box])
        assert res.verification["verified"] is True and res.flattened_pages == []
        assert _page_words(out) == ["stay", "go"]

    def test_only_a_letter_that_was_under_the_box_to_begin_with(self, tmp_path):
        """Removing text can move the text after it on the line (PyMuPDF 2.1
        does not always keep its place). A letter that comes under a box
        that way is not removed for it: taking it would move the next one
        in. So the second pass is given what was under the boxes before
        anything was redacted, and touches nothing else."""
        src = _pdf(tmp_path / "src.pdf", _wide("stay H go"))
        box = fitz.Rect(*[_right_part_of(src, "H")[k] for k in ("x0", "y0", "x1", "y1")])
        with fitz.open(src) as doc:
            page = doc[0]
            were_under = pdf_ops._under_keys(page, [box])
            assert [c for c, _box in were_under] == ["H"]
            pdf_ops._redact_chars_left_under(page, [box], set())
            assert "H" in page.get_text()
            pdf_ops._redact_chars_left_under(page, [box], were_under)
            assert "H" not in page.get_text()

    def test_one_that_cannot_be_removed_is_refused(self, tmp_path, monkeypatch):
        real = fitz.Page.apply_redactions

        def no_text_only_passes(self, *args, **kw):
            if kw.get("images") == fitz.PDF_REDACT_IMAGE_NONE:
                for annot in list(self.annots(types=[fitz.PDF_ANNOT_REDACT]) or []):
                    self.delete_annot(annot)
                return True
            return real(self, *args, **kw)

        monkeypatch.setattr(fitz.Page, "apply_redactions", no_text_only_passes)
        src = _pdf(tmp_path / "src.pdf", _wide("stay H go"))
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, rects=[_right_part_of(src, "H")])
        (check,) = [c for c in ei.value.report["checks"] if not c["passed"]]
        assert check["id"] == REGIONS and ei.value.flatten_pages == [1]
        assert check["evidence"] == ["text still extractable inside redaction rect on page 1: 'H'"]
        assert not os.path.exists(out)


# ── R4. What leaves a page is counted ────────────────────────────────────


def _field(pdf, value: str):
    """A text field shown where _stamp puts its stamp, saying *value*."""
    font = _font(pdf)
    field = pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("who"), V=String(value),
        Rect=STAMP_RECT, DA=String("/Helv 12 Tf 0 g"), P=pdf.pages[0].obj,
        AP=Dictionary(N=_drawn(pdf, value))))
    pdf.Root["/AcroForm"] = Dictionary(Fields=Array([field]), DA=String("/Helv 12 Tf 0 g"),
                                       DR=Dictionary(Font=Dictionary(Helv=font)))
    _on_page(pdf, field)


def _free_text(pdf, text: str):
    """A free-text note where _stamp puts its stamp, with a popup and a
    reply; returns all three, for the page."""
    note = pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.FreeText, Rect=STAMP_RECT, Contents=String(text),
        DA=String("/Helv 12 Tf 0 g"), AP=Dictionary(N=_drawn(pdf, text))))
    popup = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Popup,
                                         Rect=[400, 650, 560, 740], Parent=note))
    note["/Popup"] = popup
    return [note, popup, _note(pdf, (500, 200), Contents=String("agreed"), IRT=note)]


@pytest.mark.integration
@pytest.mark.filterwarnings("ignore:This document has 1 form widget")         # the fixture's own
class TestWhatLeavesAPageIsCounted:
    """A form field taken off a page because it was under a box, drawn by
    hand or a term's, is counted as "fields_removed". And an annotation
    MuPDF takes off the page itself while it redacts (a free-text note any
    box touches) is counted like one this code takes off, with its replies
    — which used to stay behind under a box drawn by hand."""

    def test_a_field_under_a_box_drawn_by_hand(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _field(pdf, "Whitfield and Sons"))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert res.surface_counts == {"page_content": 1, "fields_removed": 1}
        assert _subtypes(out) == []

    def test_a_field_under_a_term_hit(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _field(pdf, f"{TERM} and Sons"))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.surface_counts.get("fields_removed") == 1
        assert _subtypes(out) == [] and not _holds(out)

    def test_a_field_no_box_touches_is_not(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _field(pdf, "Whitfield and Sons"),
                   body=f"about {TERM}")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM], rects=[OVER_NOTHING])
        assert "fields_removed" not in res.surface_counts and _subtypes(out) == ["/Widget"]

    def test_a_note_that_draws_the_term_with_its_reply_in_a_search(self, tmp_path):
        """Note, popup and reply all leave the page; two annotations are
        counted, as under a box. (A popup is not counted in either.)"""
        src = _pdf(tmp_path / "src.pdf",
                   lambda pdf: _on_page(pdf, *_free_text(pdf, f"ask {TERM} first")))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert _subtypes(out) == [] and not _holds(out)
        assert res.surface_counts == {"page_content": 1, "annotations_removed": 2}

    def test_the_same_note_under_a_box_drawn_by_hand(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf",
                   lambda pdf: _on_page(pdf, *_free_text(pdf, "ask them first")))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[OVER_THE_STAMP])
        assert _subtypes(out) == []
        assert res.surface_counts == {"page_content": 1, "annotations_removed": 2}

    def test_a_stamp_with_its_reply_is_counted_the_same_both_ways(self, tmp_path):
        def plant(pdf):
            stamp = _stamp(pdf)
            popup = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Popup,
                                                 Rect=[400, 650, 560, 740], Parent=stamp))
            stamp["/Popup"] = popup
            _on_page(pdf, stamp, popup, _note(pdf, (500, 200), Contents=String("agreed"),
                                              IRT=stamp))
        src = _pdf(tmp_path / "src.pdf", plant)
        by_search = redact_pdf(src, str(tmp_path / "a.pdf"), search_terms=[TERM])
        by_box = redact_pdf(src, str(tmp_path / "b.pdf"), rects=[OVER_THE_STAMP])
        assert by_search.surface_counts["annotations_removed"] == 2
        assert by_box.surface_counts["annotations_removed"] == 2

    def test_a_box_drawn_by_hand_is_not_trimmed(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _content(TIGHT))
        word = _word_rect(src, "Whitfield")
        out = str(tmp_path / "out.pdf")
        redact_pdf(src, out, rects=[dict(zip(("x0", "y0", "x1", "y1"), word, strict=True), page=0)])
        left = _page_words(out)
        assert "Whitfield" not in left and "Algy" not in left and "Hdlb" not in left

    def test_lines_set_apart_are_as_before(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _content(TIGHT.replace(b"-13 Td", b"-20 Td")))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"])
        assert _page_words(out) == NEIGHBOURS and res.redaction_count == 1

    def test_type_set_up_the_page_is_removed_untrimmed(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _content(
            b"BT /F1 12 Tf 0 1 -1 0 300 300 Tm (above Whitfield below) Tj ET"))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"])
        assert res.verification["verified"] is True and _page_words(out) == ["above", "below"]

    def test_the_trimmed_box_keeps_the_middle_of_the_word(self, tmp_path):
        import pdf_verify
        src = _pdf(tmp_path / "src.pdf", _content(TIGHT))
        with fitz.open(src) as doc:
            ((whole, core),) = pdf_verify.term_hits(doc[0], ["Whitfield"])
            (plain,) = pdf_verify.term_boxes(doc[0], ["Whitfield"])
        assert tuple(whole) == tuple(plain) and core.x0 == whole.x0 and core.x1 == whole.x1
        assert whole.y0 < core.y0 < core.y1 < whole.y1
        middle, quarter = (whole.y0 + whole.y1) / 2, whole.height / 4
        assert core.y0 <= middle - quarter + 0.01 and core.y1 >= middle + quarter - 0.01
