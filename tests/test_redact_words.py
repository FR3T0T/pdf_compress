"""Redaction completeness (#119), R4: what the app says about a redaction.

A run's result carries plain facts — counts, and notes on what it did not
look at — and a refused run carries its verification report. The words a
person reads are made from those here, in Python, as Sanitize's are
(pdf_verify.sanitize_refusal_detail): in one place, and tested. The window
shows the lines it is given.

  * pdf_ops.redaction_summary(result) — a finished run: the headline, what
    else was removed, and what to know (the notes);
  * pdf_verify.redaction_refusal_detail(report, offer) — a refused run:
    what was found and where, and what can be done, with no promise;
  * pdf_ops.redact_result_payload(result) — the result as the redact slot
    hands it to the window, the summary included.

Plain language, at about an 8th-grade level: no PDF words where an
everyday word will do. A count can be 1 or 32,768, and a list of pages can
be long; the words hold for both. New names are reached through their
modules, so this file collects against a tree that does not have them.
Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import dataclasses
import json
import os
import pathlib

import pikepdf
import pytest
from pikepdf import Dictionary, Name, String

import pdf_ops
import pdf_verify

fitz = pytest.importorskip("fitz")

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _result(count=3, pages=2, counts=None, notes=(), flattened=(), areas=None):
    return pdf_ops.RedactResult(
        input_path="in.pdf", output_path="out.pdf", redaction_count=count,
        pages_affected=pages, surface_counts=dict(counts or {"page_content": count}),
        verification={"verified": True, "checks": []}, flattened_pages=list(flattened),
        flattened_areas=dict(areas or {}), notes=list(notes))


def _summary(**kw):
    return pdf_ops.redaction_summary(_result(**kw))


# ── A finished run ───────────────────────────────────────────────────────


class TestTheHeadline:
    def test_places_and_pages(self):
        s = _summary(count=3, pages=2)
        assert s["headline"] == "Redacted 3 places on 2 pages."
        assert s["checked"] == ("Checked before saving: what you asked to remove is gone "
                                "from the new file.")

    def test_one_place_on_one_page(self):
        assert _summary(count=1, pages=1)["headline"] == "Redacted 1 place on 1 page."

    def test_large_numbers_are_grouped_by_thousands(self):
        assert _summary(count=32768, pages=1200)["headline"] == \
            "Redacted 32,768 places on 1,200 pages."

    def test_nothing_on_the_pages(self):
        s = _summary(count=0, pages=0, counts={"docinfo": 1})
        assert s["headline"] == ("Nothing on the pages matched. What you searched for was "
                                 "removed from other parts of the file.")


class TestWhatElseWasRemoved:
    @pytest.mark.parametrize("key, one, many", [
        ("annotations_removed", "1 note, stamp or other mark taken off a page",
         "32,768 notes, stamps or other marks taken off the pages"),
        ("fields_removed", "1 form field taken off a page",
         "32,768 form fields taken off the pages"),
        ("links_removed", "1 link taken off a page", "32,768 links taken off the pages"),
        ("thumbnails", "1 small preview picture of a page",
         "32,768 small preview pictures of pages"),
        ("structure_text", "1 description of removed content, the kind screen readers read aloud",
         "32,768 descriptions of removed content, the kind screen readers read aloud"),
        ("structure_refs", "1 hidden copy of removed content that the file still pointed to",
         "32,768 hidden copies of removed content that the file still pointed to"),
    ])
    def test_one_and_many(self, key, one, many):
        assert _summary(counts={"page_content": 3, key: 1})["removed"] == [one]
        assert _summary(counts={"page_content": 3, key: 32768})["removed"] == [many]

    def test_the_page_content_count_is_the_headline_not_a_line(self):
        assert _summary(counts={"page_content": 3})["removed"] == []

    def test_every_key_a_run_can_count_has_words(self):
        """No key falls through to its own name: each reads as words."""
        keys = ["annotations_removed", "fields_removed", "links_removed", "thumbnails",
                "structure_text", "structure_refs", "docinfo", "xmp", "metadata",
                "bookmarks", "annotations", "form_fields", "field_text", "field_names",
                "rich_text", "links", "embedded_files", "structure", "layers", "piece_info",
                "scripts", "destinations", "pdftex", "page_labels", "xfa", "signatures"]
        lines = _summary(counts={k: 2 for k in keys})["removed"]
        assert len(lines) == len(keys)
        assert not any("_" in line for line in lines)

    def test_an_unknown_key_still_reads(self):
        assert _summary(counts={"page_content": 1, "new_kind": 4})["removed"] == [
            "4 other items (new kind)"]


def _notes(*notes):
    return _summary(notes=list(notes))["notes"]


class TestWhatToKnow:
    def test_no_text(self):
        assert _notes({"kind": "no_text", "pages": [3]}) == [
            "Page 3 has no text to search. It may be a scan or a picture, so any words in "
            "it were not searched."]
        assert _notes({"kind": "no_text", "pages": [2, 3, 5]}) == [
            "Pages 2, 3 and 5 have no text to search. They may be scans or pictures, so any "
            "words in them were not searched."]

    @pytest.mark.parametrize("hits, said", [(0, "found no matches"), (1, "found 1 match"),
                                            (2, "found 2 matches")])
    def test_a_text_layer(self, hits, said):
        """OCR not asked for: the layer can have a word wrong or in the
        wrong place, and ticking OCR reads the picture too (#140)."""
        assert _notes({"kind": "text_layer", "pages": [1], "hits": {1: hits},
                       "ocr_asked": False}) == [
            f"Page 1 is a scan with a hidden text layer. The search read that layer and "
            f"{said} there. If the layer misread a word, or put it in the wrong place, that "
            f"word is still in the picture. Tick “Also read scanned pages with OCR” to "
            f"read the picture too."]

    def test_a_text_layer_when_ocr_was_asked_for(self):
        """OCR asked for and not set up, or it found no words: another note
        says so, and ticking it is not suggested."""
        assert _notes({"kind": "text_layer", "pages": [1], "hits": {1: 1}, "ocr_asked": True}) == [
            "Page 1 is a scan with a hidden text layer. The search read that layer and found 1 "
            "match there. If the layer misread a word, or put it in the wrong place, that word "
            "is still in the picture."]

    def test_text_layers_on_several_pages(self):
        (line,) = _notes({"kind": "text_layer", "pages": [1, 3], "hits": {1: 2, 3: 1}})
        assert line.startswith("Pages 1 and 3 are scans with a hidden text layer. The search "
                               "read that layer and found 3 matches there.")
        assert line.endswith("to read the pictures too.")

    @pytest.mark.parametrize("more, said", [(0, "found nothing more to black out"),
                                            (1, "found 1 more place to black out"),
                                            (3, "found 3 more places to black out")])
    def test_a_text_layer_that_ocr_read_too(self, more, said):
        assert _notes({"kind": "text_layer_read", "pages": [2], "hits": {2: 1},
                       "ocr_hits": {2: more}}) == [
            f"Page 2 is a scan with a hidden text layer. The search read that layer and found "
            f"1 match there, and OCR read the picture too and {said}. OCR can miss words: "
            f"check it by eye."]

    def test_text_layers_that_ocr_read_on_several_pages(self):
        (line,) = _notes({"kind": "text_layer_read", "pages": [1, 2], "hits": {"1": 0, "2": 0},
                          "ocr_hits": {"1": 1, "2": 1}})
        assert line == ("Pages 1 and 2 are scans with a hidden text layer. The search read that "
                        "layer and found no matches there, and OCR read the pictures too and "
                        "found 2 more places to black out. OCR can miss words: check them by eye.")

    @pytest.mark.parametrize("more, widened, said", [
        # Neither: OCR found nothing past the search's boxes.
        (0, 0, "and OCR read the picture too and found nothing more to black out."),
        # Only new places.
        (2, 0, "and OCR read the picture too and found 2 more places to black out."),
        # Only widened: OCR's place touched the search's box, so it is not a
        # new place, but it blacked out the part of the word the box missed.
        (0, 1, "and OCR read the picture too and blacked out the rest of 1 word the search "
               "had covered only in part."),
        (0, 3, "and OCR read the picture too and blacked out the rest of 3 words the search "
               "had covered only in part."),
        # Both.
        (1, 1, "and OCR read the picture too: it found 1 more place to black out, and blacked "
               "out the rest of 1 word the search had covered only in part.")])
    def test_what_ocr_blacked_out_past_the_search_on_a_text_layer(self, more, widened, said):
        """#143 review: a place OCR found that touches the search's box is
        not counted as a new one, but it did black out more of the word;
        the note says so, and does not say "nothing more"."""
        (line,) = _notes({"kind": "text_layer_read", "pages": [1], "hits": {1: 1},
                          "ocr_hits": {1: more}, "ocr_widened": {"1": widened}})
        assert line == ("Page 1 is a scan with a hidden text layer. The search read that layer "
                        f"and found 1 match there, {said} OCR can miss words: check it by eye.")

    def test_what_ocr_blacked_out_past_the_search_on_several_text_layers(self):
        (line,) = _notes({"kind": "text_layer_read", "pages": [1, 2], "hits": {1: 1, 2: 1},
                          "ocr_hits": {1: 0, 2: 0}, "ocr_widened": {1: 1, 2: 1}})
        assert line == ("Pages 1 and 2 are scans with a hidden text layer. The search read that "
                        "layer and found 2 matches there, and OCR read the pictures too and "
                        "blacked out the rest of 2 words the search had covered only in part. "
                        "OCR can miss words: check them by eye.")

    def test_hits_given_as_text_as_they_come_through_json(self):
        (line,) = _notes({"kind": "text_layer", "pages": [1], "hits": {"1": 2}})
        assert "found 2 matches" in line

    def test_attachments(self):
        assert _notes({"kind": "attachments", "names": ["notes.txt"], "compressed": []}) == [
            "This file has 1 attached file: notes.txt. Attached files were searched as they "
            "are stored, and were not opened."]
        (line,) = _notes({"kind": "attachments", "names": ["a.zip", "b.docx", "c.txt"],
                          "compressed": ["a.zip", "b.docx"]})
        assert line == ("This file has 3 attached files: a.zip, b.docx and c.txt. Attached "
                        "files were searched as they are stored, and were not opened. a.zip "
                        "and b.docx are packed (like a zip or Word file), so nothing inside "
                        "them could be searched.")

    def test_many_attachments(self):
        names = [f"file{n:03d}.txt" for n in range(40)]
        (line,) = _notes({"kind": "attachments", "names": names, "compressed": names})
        assert "file000.txt, file001.txt, file002.txt, file003.txt, file004.txt and 35 more" \
            in line
        assert line.endswith("40 of them are packed (like a zip or Word file), so nothing "
                             "inside them could be searched.")

    def test_flattened(self):
        assert _notes({"kind": "flattened", "pages": [1], "areas": {1: 1}}) == [
            "Page 1 was turned into a picture, with 1 area blacked out on it. Its text can no "
            "longer be selected or searched."]
        assert _notes({"kind": "flattened", "pages": [1, 2], "areas": {"1": 2, "2": 3}}) == [
            "Pages 1 and 2 were turned into pictures, with 5 areas blacked out on them. Their "
            "text can no longer be selected or searched."]

    def test_flattened_with_nothing_left_to_black_out(self):
        """What the search found was removed, and filled black, before the
        page became a picture; what was left is off the visible page. No
        area is blacked out on the picture itself, and "0 areas" is not
        said."""
        assert _notes({"kind": "flattened", "pages": [1], "areas": {1: 0}}) == [
            "Page 1 was turned into a picture. Its text can no longer be selected or "
            "searched."]
        assert _notes({"kind": "flattened", "pages": [1, 2], "areas": {}}) == [
            "Pages 1 and 2 were turned into pictures. Their text can no longer be selected "
            "or searched."]

    def test_boxes_only(self):
        assert _notes({"kind": "boxes_only", "pages": [1, 3]}) == [
            "Only the boxes you drew were redacted. The words under them were not searched "
            "for anywhere else in the file."]

    def test_text_may_move(self):
        assert _notes({"kind": "text_may_move", "version": "2.1"}) == [
            "This run used version 2.1 of the PDF library. With this version, text after a "
            "removed word can shift along its line."]

    @pytest.mark.parametrize("pages, where", [([2], "page 2"), ([1], "page 1"),
                                              ([2, 5], "pages 2 and 5"),
                                              ([2, 3, 4, 9], "pages 2-4 and 9"),
                                              (range(2, 1201), "pages 2-1200")])
    def test_a_field_still_shown(self, pages, where):
        """One page or several, the box's own page included, and a field
        with nothing filled in: the words fit each ("elsewhere", not "in
        another place", which does not fit a field shown on many pages)."""
        assert _notes({"kind": "field_still_shown", "pages": list(pages)}) == [
            f"A form field under one of your boxes is also shown elsewhere, on {where}. It is "
            f"still shown there, and anything filled into it is still in the file."]

    def test_an_unknown_note_is_not_shown_as_code(self):
        assert _notes({"kind": "something_new"}) == []

    def test_every_note_at_once_in_the_order_they_come(self):
        notes = [{"kind": "no_text", "pages": [2]},
                 {"kind": "text_layer", "pages": [1], "hits": {1: 1}},
                 {"kind": "attachments", "names": ["a.txt"], "compressed": []},
                 {"kind": "flattened", "pages": [3], "areas": {3: 1}},
                 {"kind": "field_still_shown", "pages": [4]},
                 {"kind": "boxes_only", "pages": [1]},
                 {"kind": "text_may_move", "version": "2.1"}]
        lines = _notes(*notes)
        assert [line.split()[0] for line in lines] == [
            "Page", "Page", "This", "Page", "A", "Only", "This"]


class TestWhatOcrDid:
    """The notes of a run that read pages with OCR, or was asked to (#128).
    OCR can miss words: every page it read is named, and nothing it did is
    said with certainty."""

    def test_a_page_read_with_ocr(self):
        assert _notes({"kind": "ocr", "pages": [2], "hits": {2: 2}}) == [
            "OCR read page 2 and found 2 places to black out there. OCR can miss words: "
            "check it by eye."]
        assert _notes({"kind": "ocr", "pages": [2], "hits": {"2": 1}}) == [
            "OCR read page 2 and found 1 place to black out there. OCR can miss words: "
            "check it by eye."]
        assert _notes({"kind": "ocr", "pages": [2], "hits": {2: 0}}) == [
            "OCR read page 2 and found nothing to black out there. OCR can miss words: "
            "check it by eye."]

    def test_pages_read_with_ocr(self):
        assert _notes({"kind": "ocr", "pages": [1, 2, 3, 7], "hits": {1: 1, 2: 0, 3: 2, 7: 0}}) == [
            "OCR read pages 1-3 and 7 and found 3 places to black out on them. OCR can miss "
            "words: check them by eye."]

    @pytest.mark.parametrize("hits, widened, line", [
        # Neither, and only new places: as before.
        (0, 0, "OCR read page 2 and found nothing to black out there."),
        (2, 0, "OCR read page 2 and found 2 places to black out there."),
        # Only widened (#143 review): the search found the word in the
        # page's few words of text, and OCR blacked out more of it.
        (0, 1, "OCR read page 2 and blacked out the rest of 1 word the search had covered "
               "only in part."),
        # Both.
        (2, 1, "OCR read page 2: it found 2 places to black out there, and blacked out the "
               "rest of 1 word the search had covered only in part.")])
    def test_what_ocr_blacked_out_past_the_search(self, hits, widened, line):
        assert _notes({"kind": "ocr", "pages": [2], "hits": {2: hits},
                       "widened": {"2": widened}}) == [
            f"{line} OCR can miss words: check it by eye."]

    def test_what_ocr_blacked_out_past_the_search_on_several_pages(self):
        assert _notes({"kind": "ocr", "pages": [1, 3], "hits": {1: 1, 3: 0},
                       "widened": {1: 0, 3: 2}}) == [
            "OCR read pages 1 and 3: it found 1 place to black out on them, and blacked out "
            "the rest of 2 words the search had covered only in part. OCR can miss words: "
            "check them by eye."]

    def test_every_page_ocr_read_is_named(self):
        import re
        pages = list(range(3, 301, 3))
        (line,) = _notes({"kind": "ocr", "pages": pages, "hits": {p: 0 for p in pages}})
        named = line.split(" and found ")[0]
        assert "more" not in named
        assert [int(n) for n in re.findall(r"\d+", named)] == pages

    def test_pages_ocr_could_not_read(self):
        assert _notes({"kind": "ocr_unread", "pages": [4], "too_large": []}) == [
            "OCR found no words it could read on page 4. It may be a photo or a drawing, or "
            "its words may not be in English: check it by eye."]
        assert _notes({"kind": "ocr_unread", "pages": [4, 6], "too_large": []}) == [
            "OCR found no words it could read on pages 4 and 6. They may be photos or "
            "drawings, or their words may not be in English: check them by eye."]

    def test_a_page_too_large_to_read(self):
        assert _notes({"kind": "ocr_unread", "pages": [9], "too_large": [9]}) == [
            "Page 9 is too large to read with OCR, and was not read: check it by eye."]
        assert _notes({"kind": "ocr_unread", "pages": [4, 9], "too_large": [9]}) == [
            "OCR found no words it could read on page 4. It may be a photo or a drawing, or "
            "its words may not be in English: check it by eye. Page 9 is too large to read "
            "with OCR, and was not read: check it by eye."]

    def test_ocr_asked_for_but_not_set_up(self):
        assert _notes({"kind": "no_text", "pages": [2]}, {"kind": "ocr_not_ready"}) == [
            "Page 2 has no text to search. It may be a scan or a picture, so any words in it "
            "were not searched.",
            "OCR, which can read the words in a picture, is not set up, so those pages were "
            "not read."]

    def test_a_page_that_is_mostly_a_picture(self):
        assert _notes({"kind": "mostly_picture", "pages": [3]}) == [
            "Page 3 is mostly a picture with a few words of text. The search read only those "
            "words, so any words in the picture were not searched."]
        assert _notes({"kind": "mostly_picture", "pages": [3, 5]}) == [
            "Pages 3 and 5 are mostly pictures with a few words of text. The search read only "
            "those words, so any words in the pictures were not searched."]

    def test_what_was_checked_on_pages_ocr_read(self):
        s = _summary(count=3, pages=2, counts={"page_content": 1, "ocr_page_content": 2},
                     notes=[{"kind": "ocr", "pages": [2, 4], "hits": {2: 2, 4: 0}}])
        assert s["checked"] == ("Checked before saving: a search of the new file no longer "
                                "finds what you asked to remove, and OCR no longer reads it on "
                                "page 2.")
        assert s["removed"] == []

    def test_what_was_checked_names_the_pages_where_ocr_only_widened(self):
        """#143 review: a page where OCR's only place touched the search's
        box was read again by OCR (redaction.ocr_reread) like any other."""
        s = _summary(count=2, pages=2, counts={"page_content": 2},
                     notes=[{"kind": "ocr", "pages": [2, 4], "hits": {2: 0, 4: 0},
                             "widened": {2: 0, 4: 1}},
                            {"kind": "text_layer_read", "pages": [5, 6], "hits": {5: 1, 6: 1},
                             "ocr_hits": {5: 0, 6: 0}, "ocr_widened": {"5": 1, "6": 0}}])
        assert s["checked"] == ("Checked before saving: a search of the new file no longer "
                                "finds what you asked to remove, and OCR no longer reads it on "
                                "pages 4 and 5.")

    def test_what_was_checked_when_ocr_found_nothing(self):
        s = _summary(count=1, pages=1,
                     notes=[{"kind": "ocr", "pages": [2], "hits": {2: 0}}])
        assert s["checked"] == ("Checked before saving: a search of the new file no longer "
                                "finds what you asked to remove.")
        s = _summary(count=1, pages=1,
                     notes=[{"kind": "ocr_unread", "pages": [2], "too_large": []}])
        assert s["checked"] == ("Checked before saving: a search of the new file no longer "
                                "finds what you asked to remove.")

    def test_every_ocr_note_reads_as_words(self):
        for note in ({"kind": "ocr", "pages": [1], "hits": {1: 1}},
                     {"kind": "ocr", "pages": [1], "hits": {1: 1}, "widened": {1: 1}},
                     {"kind": "text_layer_read", "pages": [1], "hits": {1: 1}, "ocr_hits": {1: 1},
                      "ocr_widened": {1: 1}},
                     {"kind": "ocr_unread", "pages": [1], "too_large": [1]},
                     {"kind": "ocr_not_ready"},
                     {"kind": "mostly_picture", "pages": [1]}):
            (line,) = _notes(note)
            assert "_" not in line and "ocr_" not in line


class TestManyPages:
    def test_a_long_list_of_pages_is_cut_short(self):
        (line,) = _notes({"kind": "no_text", "pages": list(range(3, 301, 3))})
        assert line.startswith("Pages 3, 6, 9, 12, 15, 18 and 94 more have no text to search.")

    def test_runs_of_pages_are_joined(self):
        (line,) = _notes({"kind": "no_text", "pages": list(range(1, 301))})
        assert line.startswith("Pages 1-300 have no text to search.")


# ── A refused run ────────────────────────────────────────────────────────


def _report(*checks):
    report = pdf_verify.VerificationReport(input_path="", output_path="", tool="redaction",
                                           timestamp="")
    report.checks = [pdf_verify.VerificationCheck(id="redaction.readable", description="",
                                                  passed=True), *checks]
    return report


def _failed(cid, *evidence, unevaluated=False):
    return pdf_verify.VerificationCheck(id=cid, description="", passed=False,
                                        evidence=list(evidence), unevaluated=unevaluated)


def _detail(*checks, offer=None):
    return pdf_verify.redaction_refusal_detail(_report(*checks), offer)


class TestWhatWasFound:
    @pytest.mark.parametrize("check, line", [
        (_failed("redaction.page_text", "term 'x' is still extractable from page text on page 2"),
         "What you searched for can still be read in the text of page 2."),
        (_failed("redaction.raw_content", "term 'x' found in a raw content stream on page 1"),
         "What you searched for is still in the hidden page code of page 1 (for example, text "
         "placed outside the visible page)."),
        (_failed("redaction.annotations", "term 'x' found in Text annotation content on page 3"),
         "What you searched for is still in a note or comment on page 3."),
        (_failed("redaction.form_fields", "term 'x' found in a form field value not attached "
                 "to any page widget (document-level)"),
         "What you searched for is still in a form field that is not shown on any page."),
        (_failed("redaction.links", "term 'x' found in link uri on page 1"),
         "What you searched for is still in a link on page 1."),
        (_failed("redaction.docinfo", "term 'x' found in document info field 'title' "
                 "(document-level)"),
         "What you searched for is still in the document's details, such as the title or "
         "author."),
        (_failed("redaction.xmp", "term 'x' found in the XMP metadata packet (document-level)"),
         "What you searched for is still in the document's hidden details (XMP)."),
        (_failed("redaction.bookmarks", "term 'x' found in a bookmark title targeting page 2 "
                 "(document-level)"),
         "What you searched for is still in a bookmark title."),
        (_failed("redaction.embedded_files", "term 'x' found in embedded file name 'x.txt' "
                 "(document-level)"),
         "What you searched for is still in the name of an attached file."),
        (_failed("redaction.rect_regions", "text still extractable inside redaction rect on "
                 "page 1: 'q'"),
         "Text is still under a box you drew on page 1."),
        (_failed("redaction.rect_annotations", "a Stamp annotation still lies over a redaction "
                 "rectangle on page 2"),
         "A note, stamp or other mark still lies over a box you drew on page 2."),
        (_failed("redaction.rect_line_art", "a drawing still has an edge inside a redaction "
                 "rectangle on page 1"),
         "A drawing under a box on page 1 could not be removed without also removing the part "
         "of it that goes on outside the box."),
        (_failed("redaction.thumbnails", "page 2 still carries a thumbnail of the page as it "
                 "was (document-level)"),
         "The file still has a small preview picture of a page, made before redaction."),
        (_failed("redaction.file_id", "the output's trailer /ID still carries the source "
                 "file's identifier ab, which links it to the original (document-level)"),
         "The new file would still carry the original file's ID, which links the two files."),
    ], ids=lambda v: v.id if isinstance(v, pdf_verify.VerificationCheck) else "")
    def test_one_line_per_check(self, check, line):
        assert _detail(check)["found"] == [line]

    def test_pages_are_joined(self):
        check = _failed("redaction.page_text",
                        *[f"term 'x' is still extractable from page text on page {n}"
                          for n in (1, 2, 3, 7)])
        assert _detail(check)["found"] == [
            "What you searched for can still be read in the text of pages 1-3 and 7."]

    def test_the_structure_tree(self):
        streams = _failed("redaction.structure_streams", "1 stream in the file can be reached "
                          "only through the structure tree: the original of content a page "
                          "now has a redacted copy of (document-level)")
        things = _failed("redaction.structure_streams", "2 annotations or form fields in the "
                         "file can be reached only through the structure tree: they are on no "
                         "page (document-level)")
        assert _detail(streams)["found"] == [
            "The file's structure still keeps a hidden copy of content that was removed."]
        assert _detail(things)["found"] == [
            "The file's structure still keeps a note or form field that was taken off its "
            "page."]

    def test_the_whole_file_search_says_where(self):
        check = _failed("redaction.term_sweep",
                        "term 'x' found in a text string in the document catalog, under /T "
                        "(document-level)",
                        "term 'x' found in a stream on page 2, annotation 1, under /Stash",
                        "term 'x' found in a text string in a bookmark (document-level)",
                        "term 'x' found in a text string in a form field (document-level)")
        assert _detail(check)["found"] == [
            "What you searched for is still hidden in the file's own settings.",
            "What you searched for is still hidden in a note or mark on page 2.",
            "What you searched for is still hidden in a bookmark.",
            "And in 1 more place in the file."]

    def test_the_whole_file_search_uses_no_pdf_names(self):
        """The sweep's places are written for the PDF's insides ("a stream",
        "under /Title"); the window gets plain words, and the raw lines stay
        under the technical details."""
        check = _failed("redaction.term_sweep",
                        "term 'x' found in a text string in the document information, under "
                        "/Title (document-level)",
                        "term 'x' found in a text string in the document information, under "
                        "/Author (document-level)",
                        "term 'x' found in a stream on page 4, under /PieceInfo, and on 2 more "
                        "pages",
                        "term 'x' found in a text string in the document's open action, 40 "
                        "steps down a chain of actions (document-level)")
        found = _detail(check)["found"]
        assert found == [
            "What you searched for is still hidden in the document's details.",
            "What you searched for is still hidden on page 4, and on 2 more pages.",
            "What you searched for is still hidden in what the file does when it opens."]
        assert not any("/" in line or "stream" in line or "string" in line for line in found)

    def test_a_place_is_said_once(self):
        """The whole-file search finds what the other checks find; the window
        names each place once, and the search's own line only for a place no
        other check named."""
        checks = (
            _failed("redaction.docinfo", "term 'x' found in document info field 'title' "
                    "(document-level)"),
            _failed("redaction.raw_content", "term 'x' found in raw content stream on page 1"),
            _failed("redaction.annotations", "term 'x' found in Text annotation content on "
                    "page 3"),
            _failed("redaction.term_sweep",
                    "term 'x' found in a text string in the document information, under /Title "
                    "(document-level)",
                    "term 'x' found in a stream on page 1, under /Contents",
                    "term 'x' found in a text string on page 3, annotation 1, under /Contents",
                    "term 'x' found in a text string on page 5, annotation 2, under /Contents"))
        assert _detail(*checks)["found"] == [
            "What you searched for is still in the document's details, such as the title or "
            "author.",
            "What you searched for is still in the hidden page code of page 1 (for example, "
            "text placed outside the visible page).",
            "What you searched for is still in a note or comment on page 3.",
            "What you searched for is still hidden in a note or mark on page 5."]
        # Places past the twenty listed are counted, however few are shown.
        cut = _failed("redaction.term_sweep",
                      "term 'x' found in a stream on page 1, under /Contents",
                      "... and 4,000 more")
        assert _detail(checks[1], cut)["found"][-1] == "And in 4,000 more places in the file."
        # Covered by another check, every place: the search adds nothing.
        assert _detail(*checks[:2], _failed(
            "redaction.term_sweep", "term 'x' found in a stream on page 1, under /Contents"))[
            "found"] == [
            "What you searched for is still in the document's details, such as the title or "
            "author.",
            "What you searched for is still in the hidden page code of page 1 (for example, "
            "text placed outside the visible page)."]

    def test_what_could_not_be_read(self):
        check = _failed("redaction.term_sweep", "the whole-file search did not cover the "
                        "whole file (x); what it did not read cannot be reported free of the "
                        "terms (document-level)", unevaluated=True)
        d = _detail(check)
        assert d["headline"] == ("Redaction refused: part of the new file could not be read, "
                                 "so it could not be checked. Nothing was saved.")
        assert d["found"] == ["Part of the file could not be read, so it could not be checked."]


class TestWhatCanBeDone:
    def test_content_still_present_headline(self):
        d = _detail(_failed("redaction.page_text",
                            "term 'x' is still extractable from page text on page 2"), offer=[2])
        assert d["headline"] == ("Redaction refused: some of what you asked to remove is still "
                                 "in the new file. Nothing was saved.")
        assert d["nextStep"] == (
            "You can turn page 2 into a picture to remove it. The page keeps its look, with "
            "what was found blacked out, but its text can no longer be selected or searched.")

    def test_a_drawing_under_a_box_is_covered(self):
        d = _detail(_failed("redaction.rect_line_art", "a drawing still has an edge inside a "
                            "redaction rectangle on page 1"), offer=[1])
        assert d["nextStep"] == (
            "You can turn page 1 into a picture to cover it. The page keeps its look, with "
            "what was found blacked out, but its text can no longer be selected or searched.")

    def test_several_pages(self):
        d = _detail(_failed("redaction.page_text",
                            "term 'x' is still extractable from page text on page 1",
                            "term 'x' is still extractable from page text on page 3"),
                    offer=[1, 3])
        assert d["nextStep"].startswith("You can turn pages 1 and 3 into pictures to remove "
                                        "it. The pages keep their look, with what was found "
                                        "blacked out, but their text")

    def test_not_on_a_page(self):
        d = _detail(_failed("redaction.docinfo", "term 'x' found in document info field "
                            "'title' (document-level)"))
        assert d["nextStep"] == ("It is not drawn on a page, so turning pages into pictures "
                                 "would not remove it.")

    def test_the_identifier_alone(self):
        d = _detail(_failed("redaction.file_id", "the output's trailer /ID still carries the "
                            "source file's identifier ab, which links it to the original "
                            "(document-level)"))
        assert d["headline"] == ("Redaction refused: the new file would still be linked to "
                                 "the original. Nothing was saved.")
        assert d["nextStep"] == ("The ID is not on any page, so turning pages into pictures "
                                 "cannot change it.")

    def test_after_the_pages_were_already_made_pictures(self):
        """A run with flatten_pages offers nothing more."""
        d = _detail(_failed("redaction.page_text",
                            "term 'x' is still extractable from page text on page 2"), offer=None)
        assert d["nextStep"] == ("Turning the page into a picture did not remove it.")

    def test_damage_alone_is_not_called_a_leak(self):
        """When the only thing wrong is that more of a page would have
        changed than the places OCR found, nothing is "still present": the
        one-line message says what the headline says (#128)."""
        failed = [_failed("redaction.ocr_outside", "792 pixels changed outside the boxes on page 1")]
        headline = _detail(*failed)["headline"]
        assert headline == ("Redaction refused: it would have changed more of the page than the "
                            "places found. Nothing was saved.")
        assert pdf_ops._refusal_message(failed) == headline
        # With something left as well, it is a leak, and said so.
        both = failed + [_failed("redaction.ocr_reread", "OCR still reads a term on page 1, "
                                 "turned 0 degrees (1 place)")]
        assert "still present" in pdf_ops._refusal_message(both)

    @pytest.mark.parametrize("evidence, line", [
        ("31 characters outside the boxes on page 3 stopped showing",
         "Text on page 3 outside the black boxes would have stopped showing."),
        ("1 character outside the boxes on page 3 started showing",
         "Hidden text on page 3 outside the black boxes would have started showing."),
        ("4 characters outside the boxes on page 3 moved or went missing",
         "Text on page 3 outside the black boxes would have moved or gone."),
    ])
    def test_text_outside_the_boxes(self, evidence, line):
        """Damage too (#150): the headline is the damage one, one line a
        page, and nothing is offered, by the window or the one-line message."""
        failed = [_failed("redaction.text_outside", evidence)]
        d = _detail(*failed)
        assert d == {"headline": pdf_verify.DAMAGE_HEADLINE, "found": [line],
                     "nextStep": "The file was left as it was."}
        assert pdf_ops._refusal_message(failed) == pdf_verify.DAMAGE_HEADLINE

    def test_text_outside_the_boxes_one_line_a_page(self):
        """A page that changed in more than one way is named once, by the
        first of stopped showing, started showing, moved or gone; pages in
        order. With the picture of a page changed too, it is still damage."""
        failed = [_failed("redaction.text_outside",
                          "2 characters outside the boxes on page 5 moved or went missing",
                          "3 characters outside the boxes on page 5 started showing",
                          "9 characters outside the boxes on page 2 moved or went missing",
                          "1 character outside the boxes on page 2 stopped showing"),
                  _failed("redaction.ocr_outside", "792 pixels changed outside the boxes on page 4")]
        d = _detail(*failed)
        assert d["headline"] == pdf_verify.DAMAGE_HEADLINE
        assert d["found"] == [
            "Text on page 2 outside the black boxes would have stopped showing.",
            "Hidden text on page 5 outside the black boxes would have started showing.",
            "The picture of page 4 would have changed outside the places that were blacked out."]
        assert pdf_ops._refusal_message(failed) == pdf_verify.DAMAGE_HEADLINE

    def test_text_outside_the_boxes_with_a_leak_is_a_leak(self):
        failed = [_failed("redaction.text_outside",
                          "2 characters outside the boxes on page 1 moved or went missing"),
                  _failed("redaction.page_text",
                           "term 'x' is still extractable from page text on page 1")]
        assert _detail(*failed)["headline"].startswith("Redaction refused: some of what you")
        assert "still present" in pdf_ops._refusal_message(failed)

    @pytest.mark.parametrize("evidence, line", [
        (["a picture drawn on page 1 before the redaction is still in the file, and nothing "
          "draws it (document-level)"],
         "A picture from page 1 is still in the file, though no page shows it any more. It could "
         "be taken out of the file."),
        (["a picture drawn on page 1 before the redaction is still in the file, and nothing "
          "draws it (document-level)",
          "a picture drawn on page 3 before the redaction is still in the file, and nothing "
          "draws it (document-level)"],
         "Pictures from pages 1 and 3 are still in the file, though no page shows them any more. "
         "They could be taken out of the file."),
        (["content drawn on page 2 before the redaction is still in the file, and nothing draws "
          "it (document-level)"],
         "Content from page 2 is still in the file, though no page shows it any more. It could "
         "be taken out of the file."),
    ])
    def test_what_a_box_redacted_left_behind(self, evidence, line):
        """A leak, and in the file, not on a page (#150): turning pages into
        pictures is not offered."""
        d = _detail(_failed("redaction.left_behind", *evidence))
        assert d == {"headline": "Redaction refused: some of what you asked to remove is still "
                                 "in the new file. Nothing was saved.",
                     "found": [line],
                     "nextStep": "It is not drawn on a page, so turning pages into pictures would "
                                 "not remove it."}

    def test_text_outside_the_boxes_that_could_not_be_compared(self):
        failed = [_failed("redaction.text_outside", "the text on page 2, which no box touched, "
                          "changed and could not be compared, so it could not be checked",
                          unevaluated=True)]
        d = _detail(*failed)
        assert d["headline"] == ("Redaction refused: part of the new file could not be read, "
                                 "so it could not be checked. Nothing was saved.")
        assert d["found"] == ["Part of page 2 could not be read, so it could not be checked."]

    def test_no_promise_anywhere(self):
        """The old words promised a fix ("the scrubber should have removed
        it"). None of these do."""
        for checks in ([_failed("redaction.docinfo", "x (document-level)")],
                       [_failed("redaction.file_id", "x (document-level)")],
                       [_failed("redaction.term_sweep", "x (document-level)", unevaluated=True)]):
            d = _detail(*checks)
            words = " ".join([d["headline"], d["nextStep"], *d["found"]]).lower()
            assert "report" not in words and "should" not in words and "will be" not in words


# ── Through a run, and through the slot ──────────────────────────────────


def _pdf(path, body="a line about Whitfield here", plant=None) -> str:
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[0].obj
    page["/Resources"] = Dictionary(Font=Dictionary(F1=pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))))
    page["/Contents"] = pdf.make_stream(f"BT /F1 14 Tf 72 720 Td ({body}) Tj ET".encode())
    if plant:
        plant(pdf)
    pdf.save(str(path))
    pdf.close()
    return str(path)


@pytest.mark.integration
class TestThroughARun:
    def test_the_payload_carries_the_summary_and_survives_json(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf")
        res = pdf_ops.redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["Whitfield"])
        sent = json.loads(json.dumps(pdf_ops.redact_result_payload(res), ensure_ascii=False))
        assert sent["redaction_count"] == 1 and sent["verification"]["verified"] is True
        assert sent["summary"]["headline"] == "Redacted 1 place on 1 page."
        assert {k: sent[k] for k in dataclasses.asdict(res)} == json.loads(json.dumps(
            dataclasses.asdict(res)))

    def test_a_refusal_carries_its_words(self, tmp_path):
        def plant(pdf):
            pdf.trailer["/Info"] = pdf.make_indirect(Dictionary(Title=String("Whitfield report")))
        src = _pdf(tmp_path / "src.pdf", plant=plant)
        # The scrub is switched off, so the title keeps the word: a refusal
        # about a document-level place.
        import unittest.mock as mock
        with mock.patch.object(pdf_ops, "_scrub_document_surfaces", lambda *a, **k: {}):
            with pytest.raises(pdf_ops.RedactionVerificationError) as ei:
                pdf_ops.redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["Whitfield"])
        d = ei.value.detail
        assert "What you searched for is still in the document's details, such as the title " \
               "or author." in d["found"]
        assert d["nextStep"] == ("It is not drawn on a page, so turning pages into pictures "
                                 "would not remove it.")

    def test_a_line_art_refusal_carries_its_words(self, tmp_path):
        def plant(pdf):
            page = pdf.pages[0].obj
            page["/Contents"] = pdf.make_stream(b"0 0 1 RG 1 w 50 615 m 150 615 l S")
        src = _pdf(tmp_path / "src.pdf", plant=plant)
        box = {"page": 0, "x0": 90, "y0": 142, "x1": 210, "y1": 202}
        with pytest.raises(pdf_ops.RedactionVerificationError) as ei:
            pdf_ops.redact_pdf(src, str(tmp_path / "out.pdf"), rects=[box])
        assert ei.value.detail["found"] == [
            "A drawing under a box on page 1 could not be removed without also removing the "
            "part of it that goes on outside the box."]
        assert ei.value.detail["nextStep"].startswith("You can turn page 1 into a picture to "
                                                      "cover it.")
        assert not os.path.exists(tmp_path / "out.pdf")


@pytest.mark.integration
class TestWhatCannotBeRedacted:
    def test_a_form_that_is_only_xfa(self, tmp_path):
        def plant(pdf):
            packet = pdf.make_stream(b"<datasets><name>Whitfield</name></datasets>")
            pdf.Root["/AcroForm"] = Dictionary(Fields=pikepdf.Array(),
                                               XFA=pikepdf.Array([String("datasets"), packet]))
        src = _pdf(tmp_path / "src.pdf", body="nothing to see here", plant=plant)
        with pytest.raises(pdf_ops.RedactionNotPossibleError) as ei:
            pdf_ops.redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["Whitfield"])
        assert str(ei.value) == (
            "This file's form is stored in a format (XFA) that redaction cannot change, and "
            "what you searched for is in that form. Nothing was saved.")
        assert not os.path.exists(tmp_path / "out.pdf")


def _source(*parts) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


class TestTheBridgeAndTheWindow:
    """The bridge cannot be imported here (it pulls in Qt), nor can the
    window be run; their sources are read instead."""

    def test_the_slot_hands_the_window_the_payload(self):
        slot = _source("ui", "bridge.py")
        slot = slot[slot.index("def startRedact"):]
        slot = slot[:slot.index("self._run_in_thread")]
        assert "redact_result_payload(" in slot

    def test_a_refusal_carries_its_words_to_the_window(self):
        worker = _source("ui", "bridge.py")
        branch = worker[worker.index("except RedactionVerificationError"):]
        branch = branch[:branch.index("except ", 10)]
        assert '"refusal": exc.detail' in branch

    def test_what_cannot_be_redacted_is_a_plain_refusal(self):
        """An XFA-only form, PyMuPDF 1.28: their own message, and nothing in
        the log that reads as a crash (no traceback)."""
        worker = _source("ui", "bridge.py")
        start = worker.index("except RedactionNotPossibleError")
        assert start < worker.index("except Exception as exc:\n            log.exception")
        branch = worker[start:worker.index("except ", start + 10)]
        assert "log.exception" not in branch and "traceback" not in branch
        assert "str(exc)" in branch and '"notPossible": True' in branch

    def test_the_window_makes_no_promise(self):
        page = _source("web-react", "src", "pages", "tools", "RedactPage.tsx")
        assert "scrubber should have removed it" not in page
        assert "Please report this file" not in page
        bundle = "".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in (ROOT / "web-react" / "dist").rglob("*.js"))
        assert "scrubber should have removed it" not in bundle
