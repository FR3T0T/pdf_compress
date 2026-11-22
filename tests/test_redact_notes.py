"""Redaction completeness (#119), cluster R4: what a run did not look at.

A redaction that verifies has removed what it was asked to remove from
everywhere it can read. It has not read a picture. ``RedactResult.notes``
carries the plain facts about that, one entry per kind, for the app to
word; none of them is a failure:

  no_text       pages a search found no text on at all (a scan)
  text_layer    pages whose text is an invisible layer over a picture (OCR)
  attachments   attachments, which are searched as stored and never opened
  flattened     pages that were turned into pictures
  boxes_only    a run by boxes alone searched for nothing

Every fixture is built here, by hand. Qt-free, like the module it
exercises (see CLAUDE.md).
"""

import dataclasses
import json

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from pdf_ops import RedactionVerificationError, redact_pdf

fitz = pytest.importorskip("fitz")

TERM = "ACME99Z"
PICTURE = b"q 612 0 0 792 0 0 cm /Im1 Do Q\n"

#: What a page can hold, as content. Every page has the font and the picture
#: in its resources; only some draw them.
WORDS = f"BT /F1 14 Tf 72 720 Td (a line about {TERM} here) Tj ET".encode()
OTHER_WORDS = b"BT /F1 14 Tf 72 720 Td (nothing sensitive on this page) Tj ET"
SCAN = PICTURE
LAYER = PICTURE + (f"BT 3 Tr /F1 14 Tf 72 720 Td (a line about {TERM} here) Tj "
                   f"0 -20 Td (and {TERM} again) Tj ET").encode()
LAYER_WITHOUT_IT = PICTURE + b"BT 3 Tr /F1 14 Tf 72 720 Td (words read from the picture) Tj ET"
OUTLINES = b"0 0 1 RG 2 w 72 700 m 300 700 l 300 600 l S"       # no text, no picture: line art
BLANK = b""
#: A page of ordinary type with a letterhead picture and one hidden word.
LETTERHEAD = (b"q 100 0 0 40 72 730 cm /Im1 Do Q\n"
              + f"BT /F1 14 Tf 72 700 Td (a long line of ordinary words about {TERM}) Tj ET\n".encode()
              + b"BT 3 Tr /F1 14 Tf 72 650 Td (x) Tj ET")


def _pdf(path, *pages: bytes, plant=None) -> str:
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    image = pdf.make_stream(b"\xd0" * 64, Type=Name.XObject, Subtype=Name.Image, Width=8,
                            Height=8, ColorSpace=Name.DeviceGray, BitsPerComponent=8)
    for content in pages:
        pdf.pages.append(pikepdf.Page(Dictionary(
            Type=Name.Page, MediaBox=[0, 0, 612, 792],
            Resources=Dictionary(Font=Dictionary(F1=font), XObject=Dictionary(Im1=image)),
            Contents=pdf.make_stream(content))))
    if plant is not None:
        plant(pdf)
    pdf.save(str(path), fix_metadata_version=False)
    pdf.close()
    return str(path)


def _attach(pdf, **files: bytes) -> None:
    """Attachments in the document's list, by name (dots written as "__")."""
    names = Array()
    for name, data in sorted(files.items()):
        name = name.replace("__", ".")
        names.append(String(name))
        names.append(pdf.make_indirect(Dictionary(
            Type=Name.Filespec, F=String(name), UF=String(name),
            EF=Dictionary(F=pdf.make_stream(data, Type=Name.EmbeddedFile)))))
    pdf.Root["/Names"] = Dictionary(EmbeddedFiles=Dictionary(Names=names))


def _note(result, kind: str):
    found = [n for n in result.notes if n["kind"] == kind]
    assert len(found) <= 1, "one entry per kind"
    return found[0] if found else None


BOX = {"page": 0, "x0": 60, "y0": 50, "x1": 400, "y1": 90}


@pytest.mark.integration
class TestPagesWithNoText:
    def test_a_scan_among_pages_of_text_is_named(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, SCAN, OTHER_WORDS, SCAN)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert _note(res, "no_text") == {"kind": "no_text", "pages": [2, 4]}

    def test_text_drawn_as_outlines_is_no_text_either(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, OUTLINES)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert _note(res, "no_text") == {"kind": "no_text", "pages": [2]}

    def test_an_empty_page_is_not_named(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, BLANK)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert _note(res, "no_text") is None

    def test_only_the_pages_that_were_searched(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, SCAN, SCAN)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM], pages=[0, 1])
        assert _note(res, "no_text") == {"kind": "no_text", "pages": [2]}

    def test_a_file_of_ordinary_pages_has_no_such_note(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, OTHER_WORDS)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert res.notes == []

    def test_a_search_that_finds_nothing_says_which_pages_it_could_not_read(self, tmp_path):
        """The run ends without a result, so the fact goes in the message."""
        src = _pdf(tmp_path / "src.pdf", OTHER_WORDS, SCAN, SCAN, OTHER_WORDS, SCAN)
        with pytest.raises(ValueError, match="No matching content found to redact") as ei:
            redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert str(ei.value) == ("No matching content found to redact. Pages 2-3, 5 have no "
                                 "text: a search cannot read words in a scan or a picture.")

    def test_and_says_nothing_more_when_every_page_had_text(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", OTHER_WORDS)
        with pytest.raises(ValueError) as ei:
            redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert str(ei.value) == "No matching content found to redact."


@pytest.mark.integration
class TestATextLayerOverAPicture:
    def test_the_pages_and_the_hits_on_them(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", LAYER, WORDS, LAYER_WITHOUT_IT)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert res.redaction_count == 3
        assert _note(res, "text_layer") == {"kind": "text_layer", "pages": [1, 3],
                                            "hits": {1: 2, 3: 0}}
        assert _note(res, "no_text") is None

    def test_ordinary_type_beside_a_picture_is_not_a_text_layer(self, tmp_path):
        """A letterhead and one hidden word do not make the page a scan:
        most of its text is there to be seen."""
        src = _pdf(tmp_path / "src.pdf", LETTERHEAD)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert res.notes == []

    def test_a_hidden_layer_with_no_picture_is_not_one(self, tmp_path):
        hidden = f"BT 3 Tr /F1 14 Tf 72 720 Td (a line about {TERM} here) Tj ET".encode()
        src = _pdf(tmp_path / "src.pdf", hidden)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert _note(res, "text_layer") is None


@pytest.mark.integration
class TestAttachments:
    def test_they_are_named_and_the_packed_ones_marked(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, plant=lambda pdf: _attach(
            pdf, notes__txt=b"plain words", bundle__zip=b"PK\x03\x04" + b"\x00" * 40,
            report__docx=b"PK\x03\x04" + b"\x00" * 40, scan__gz=b"\x1f\x8b\x08" + b"\x00" * 20,
            inner__pdf=b"%PDF-1.7\n"))
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert _note(res, "attachments") == {
            "kind": "attachments",
            "names": ["bundle.zip", "inner.pdf", "notes.txt", "report.docx", "scan.gz"],
            "compressed": ["bundle.zip", "inner.pdf", "report.docx", "scan.gz"]}

    def test_one_that_was_removed_for_holding_the_term_is_not(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, plant=lambda pdf: _attach(
            pdf, kept__txt=b"plain words", gone__txt=f"about {TERM}".encode()))
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert res.surface_counts.get("embedded_files") == 1
        assert _note(res, "attachments") == {"kind": "attachments", "names": ["kept.txt"],
                                             "compressed": []}

    def test_a_file_with_none_has_no_such_note(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert _note(res, "attachments") is None


@pytest.mark.integration
class TestPagesTurnedIntoPictures:
    def test_the_pages_and_the_areas_filled_on_each(self, tmp_path, monkeypatch):
        """Removal switched off, as for a term that cannot be removed: the
        run is refused, the page is offered, and the second run says what
        it did."""
        monkeypatch.setattr(fitz.Page, "apply_redactions", lambda self, *a, **k: True)
        src = _pdf(tmp_path / "src.pdf", WORDS, OTHER_WORDS)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=[TERM])
        assert ei.value.flatten_pages == [1]
        res = redact_pdf(src, out, search_terms=[TERM], flatten_pages=[1])
        assert _note(res, "flattened") == {"kind": "flattened", "pages": [1], "areas": {1: 1}}
        assert res.flattened_pages == [1] and res.flattened_areas == {1: 1}

    def test_a_run_that_flattened_nothing_has_no_such_note(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        assert _note(res, "flattened") is None


@pytest.mark.integration
class TestARunByBoxesOnly:
    def test_it_says_that_nothing_was_searched_for(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, WORDS, SCAN,
                   plant=lambda pdf: _attach(pdf, notes__txt=b"plain words"))
        res = redact_pdf(src, str(tmp_path / "out.pdf"), rects=[BOX, dict(BOX, page=2)])
        assert res.notes == [{"kind": "boxes_only", "pages": [1, 3]}]

    def test_a_run_by_terms_and_boxes_did_search(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", WORDS, OTHER_WORDS)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM],
                         rects=[dict(BOX, page=1)])
        assert _note(res, "boxes_only") is None


@pytest.mark.integration
class TestTheNotesReachTheApp:
    def test_they_survive_what_the_bridge_does_to_a_result(self, tmp_path):
        """The bridge turns a result into JSON with dataclasses.asdict and
        json.dumps (ui/bridge.py, _serialize); nothing else is done to it.
        A page number used as a key arrives as text, as in flattened_areas."""
        src = _pdf(tmp_path / "src.pdf", LAYER, SCAN, plant=lambda pdf: _attach(
            pdf, bundle__zip=b"PK\x03\x04" + b"\x00" * 40))
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        sent = json.loads(json.dumps(dataclasses.asdict(res), ensure_ascii=False))
        assert sent["notes"] == [
            {"kind": "no_text", "pages": [2]},
            {"kind": "text_layer", "pages": [1], "hits": {"1": 2}},
            {"kind": "attachments", "names": ["bundle.zip"], "compressed": ["bundle.zip"]}]

    def test_every_note_is_plain_data(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", LAYER, SCAN)
        res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=[TERM])
        for note in res.notes:
            assert set(note) <= {"kind", "pages", "hits", "names", "compressed", "areas"}
            assert all(isinstance(p, int) and p >= 1 for p in note.get("pages", []))
