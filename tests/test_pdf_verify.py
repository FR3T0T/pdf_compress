"""Tests for pdf_verify.py — the post-operation verification loop (#110).

verify_redaction must independently prove a term is unextractable from EVERY
surface of the saved output (page text, raw content streams, annotations,
form-field values, link URIs, docinfo, XMP, bookmarks, embedded-file names)
with page-attributed evidence — decision D1: every failure names its surface
and page (or "document-level") so the #99 fail-closed UX can decide
flatten-eligibility per surface and target the flatten per page.
verify_sanitization asserts exactly what the enabled sanitize options
promise — expectations-based, never assert-zero.

pdf_verify imports no Qt, so these run Qt-free (per CLAUDE.md). Modelled on
the TST-01 redaction tests in test_pdf_ops.py: every "gone" claim is checked
through an independent extraction path.
"""

import hashlib
import os

import pikepdf
import pytest
from PIL import Image

from pdf_analyze import sanitize_pdf
from pdf_ops import redact_pdf
from pdf_verify import (
    WalkStatus,
    render_path,
    verify_redaction,
    verify_sanitization,
    walk_objects,
)

try:
    import fitz  # noqa: F401  (PyMuPDF — hard dep of pdf_verify/pdf_ops)
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

# Distinctive target string — no PDF-operator characters, no hyphens, so a
# hit in any surface is unambiguously the planted term.
TERM = "SECRETXQZ741"


# ── Builders ─────────────────────────────────────────────────────────────


def _text_pdf(path, text) -> str:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return str(path)


def _clean_pdf(path) -> str:
    return _text_pdf(path, "nothing sensitive on this page")


def _blank_page(pdf: pikepdf.Pdf) -> None:
    pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792])))


def _check(report, cid):
    for c in report.checks:
        if c.id == cid:
            return c
    raise AssertionError(
        f"check {cid!r} missing from report: {[c.id for c in report.checks]}")


def _failed_ids(report) -> set:
    return {c.id for c in report.checks if not c.passed}


def _residual_ids(report) -> set:
    return {f["id"] for f in report.residual_findings}


# ── verify_redaction: guards ─────────────────────────────────────────────


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
class TestVerifyRedactionGuards:
    def test_no_terms_or_rects_raises(self, tmp_path):
        with pytest.raises(ValueError):
            verify_redaction(str(tmp_path / "out.pdf"))

    def test_blank_terms_alone_raise(self, tmp_path):
        with pytest.raises(ValueError):
            verify_redaction(str(tmp_path / "out.pdf"), ["", "   "])

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            verify_redaction(str(tmp_path / "nope.pdf"), [TERM])

    def test_locked_output_fails_closed_not_raises(self, tmp_path):
        # R4: an output we cannot decrypt is a FAILED verification — not an
        # exception, and never a pass.
        p = str(tmp_path / "locked.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.save(p, encryption=fitz.PDF_ENCRYPT_AES_256,
                 user_pw="pw123", owner_pw="pw123")
        doc.close()

        report = verify_redaction(p, [TERM])
        assert report.verified is False
        assert not _check(report, "redaction.readable").passed
        # With the right password the gate opens and the clean doc verifies.
        ok = verify_redaction(p, [TERM], password="pw123")
        assert ok.verified is True

    def test_corrupt_output_fails_closed(self, tmp_path):
        p = tmp_path / "garbage.pdf"
        p.write_bytes(b"%PDF-not really\x00\xff")
        report = verify_redaction(str(p), [TERM])
        assert report.verified is False
        assert not _check(report, "redaction.readable").passed


# ── verify_redaction: one test per surface ───────────────────────────────


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestVerifyRedactionSurfaces:
    def test_term_in_page_text_fails_with_page_evidence(self, tmp_path):
        src = _text_pdf(tmp_path / "t.pdf", f"report about {TERM} here")
        report = verify_redaction(src, [TERM])
        assert report.verified is False
        c = _check(report, "redaction.page_text")
        assert c.passed is False
        assert any(TERM in e and "page 1" in e for e in c.evidence)

    def test_invisible_text_still_fails(self, tmp_path):
        # Render-mode-3 text (the classic fake-redaction leak) is invisible
        # but fully extractable — verification must flag it like any text.
        p = str(tmp_path / "inv.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), TERM, render_mode=3)
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.page_text")
        assert c.passed is False
        assert any("page 1" in e for e in c.evidence)

    def test_term_in_second_content_stream_raw_bytes(self, tmp_path):
        # A term buried in a later content stream with no font setup renders
        # nowhere and may be invisible to the text APIs — the raw-bytes
        # backstop must still find it (paint-over regression guard).
        p = str(tmp_path / "raw.pdf")
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        pdf.pages[0]["/Contents"] = pikepdf.Array([
            pdf.make_stream(b"q Q"),
            pdf.make_stream(b"BT (" + TERM.encode() + b") Tj ET"),
        ])
        pdf.save(p)
        pdf.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.raw_content")
        assert c.passed is False
        assert any("page 1" in e for e in c.evidence)

    def test_term_in_freetext_annotation(self, tmp_path):
        p = str(tmp_path / "annot.pdf")
        doc = fitz.open()
        page = doc.new_page()
        page.add_freetext_annot(fitz.Rect(72, 72, 300, 120), f"note: {TERM}")
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.annotations")
        assert c.passed is False
        assert any("page 1" in e for e in c.evidence)

    def test_term_in_form_field_value(self, tmp_path):
        p = str(tmp_path / "form.pdf")
        doc = fitz.open()
        page = doc.new_page()
        w = fitz.Widget()
        w.field_name = "subject"
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.rect = fitz.Rect(72, 72, 400, 120)
        w.field_value = f"about {TERM}"
        page.add_widget(w)
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.form_fields")
        assert c.passed is False
        assert any("page 1" in e for e in c.evidence)

    def test_orphan_field_value_without_widget(self, tmp_path):
        # The exact leak the pikepdf backstop exists for: a /V that no page
        # widget exposes (e.g. redaction deleted the widget but a parent
        # field kept the value). page.widgets() sees nothing; the whole-file
        # walk must still find it, attributed document-level.
        p = str(tmp_path / "orphan.pdf")
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        ghost = pdf.make_indirect(pikepdf.Dictionary(
            FT=pikepdf.Name("/Tx"), T=pikepdf.String("ghost"),
            V=pikepdf.String(f"value {TERM}")))
        pdf.Root["/AcroForm"] = pikepdf.Dictionary(
            Fields=pikepdf.Array([ghost]))
        pdf.save(p)
        pdf.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.form_fields")
        assert c.passed is False
        assert any("document-level" in e for e in c.evidence)

    def test_term_in_link_uri(self, tmp_path):
        p = str(tmp_path / "link.pdf")
        doc = fitz.open()
        page = doc.new_page()
        page.insert_link({"kind": fitz.LINK_URI,
                          "from": fitz.Rect(72, 72, 200, 90),
                          "uri": f"https://example.com/?u={TERM}"})
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.links")
        assert c.passed is False
        assert any("page 1" in e for e in c.evidence)

    def test_term_in_docinfo(self, tmp_path):
        p = str(tmp_path / "info.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.set_metadata({"title": f"{TERM} quarterly report"})
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.docinfo")
        assert c.passed is False
        assert any("document-level" in e for e in c.evidence)
        # Page surfaces are clean — the failure is isolated to docinfo.
        assert _check(report, "redaction.page_text").passed is True

    def test_term_in_xmp(self, tmp_path):
        p = str(tmp_path / "xmp.pdf")
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        with pdf.open_metadata(set_pikepdf_as_editor=False) as m:
            m["dc:description"] = f"contains {TERM}"
        pdf.save(p)
        pdf.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.xmp")
        assert c.passed is False
        assert any("document-level" in e for e in c.evidence)

    def test_term_in_bookmark_title(self, tmp_path):
        p = str(tmp_path / "toc.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.set_toc([[1, f"Chapter on {TERM}", 1]])
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.bookmarks")
        assert c.passed is False
        assert any("document-level" in e for e in c.evidence)

    def test_term_in_embedded_file_name(self, tmp_path):
        p = str(tmp_path / "emb.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.embfile_add(f"{TERM}.txt", b"payload")
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.embedded_files")
        assert c.passed is False
        assert any("document-level" in e for e in c.evidence)


# ── verify_redaction: clean output, case mode, rect mode ─────────────────


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestVerifyRedactionOutcomes:
    def test_genuinely_redacted_output_verifies(self, tmp_path):
        src = _text_pdf(tmp_path / "s.pdf", f"the {TERM} is here")
        out = str(tmp_path / "s_out.pdf")
        redact_pdf(src, out, search_terms=[TERM])

        report = verify_redaction(out, [TERM])
        assert report.verified is True
        assert _failed_ids(report) == set()
        # The proof artifact enumerates every surface it checked.
        assert {c.id for c in report.checks} >= {
            "redaction.readable", "redaction.page_text",
            "redaction.raw_content", "redaction.annotations",
            "redaction.form_fields", "redaction.links",
            "redaction.docinfo", "redaction.xmp",
            "redaction.bookmarks", "redaction.embedded_files"}

    def test_case_sensitive_respected(self, tmp_path):
        src = _text_pdf(tmp_path / "cs.pdf", TERM.lower())
        # Exact-case search for the uppercase term: no exact-case hit.
        assert verify_redaction(src, [TERM], case_sensitive=True).verified is True
        # Case-insensitive (the default) still catches it.
        report = verify_redaction(src, [TERM])
        assert not _check(report, "redaction.page_text").passed

    def test_rect_with_text_underneath_fails(self, tmp_path):
        # A drawn box with NO redaction applied — clipped extraction finds
        # the text and the rect check fails with page evidence.
        src = _text_pdf(tmp_path / "r.pdf", TERM)
        report = verify_redaction(
            src, rects=[{"page": 0, "x0": 60, "y0": 55, "x1": 300, "y1": 95}])
        c = _check(report, "redaction.rect_regions")
        assert c.passed is False
        assert any("page 1" in e for e in c.evidence)

    def test_rect_after_real_redaction_passes(self, tmp_path):
        src = _text_pdf(tmp_path / "r2.pdf", TERM)
        out = str(tmp_path / "r2_out.pdf")
        rect = {"page": 0, "x0": 60, "y0": 55, "x1": 300, "y1": 95}
        redact_pdf(src, out, rects=[rect])

        report = verify_redaction(out, rects=[rect])
        assert report.verified is True
        # The rect check itself must be present and passing — verified alone
        # would also be True off the readable gate if the check went missing.
        assert _check(report, "redaction.rect_regions").passed is True

    def test_malformed_rect_is_a_failure_not_a_skip(self, tmp_path):
        src = _clean_pdf(tmp_path / "m.pdf")
        report = verify_redaction(src, rects=[{"page": "x"}])
        assert report.verified is False
        assert not _check(report, "redaction.rect_regions").passed

    def test_out_of_range_rect_page_is_a_failure(self, tmp_path):
        src = _clean_pdf(tmp_path / "o.pdf")
        report = verify_redaction(
            src, rects=[{"page": 9, "x0": 0, "y0": 0, "x1": 10, "y1": 10}])
        assert not _check(report, "redaction.rect_regions").passed

    def test_to_dict_shape_is_camel_case(self, tmp_path):
        src = _clean_pdf(tmp_path / "d.pdf")
        d = verify_redaction(src, [TERM], input_path="in.pdf").to_dict()
        assert set(d) == {"inputPath", "outputPath", "tool", "timestamp",
                          "checks", "residualFindings", "verified",
                          "flattenTargetPages", "sourceSha256", "outputSha256",
                          "unevaluated", "diagnostics"}
        assert d["tool"] == "redaction"
        assert d["inputPath"] == "in.pdf"
        assert d["verified"] is True
        assert d["flattenTargetPages"] is None       # nothing failed
        assert all(set(c) == {"id", "description", "passed", "evidence",
                              "pages", "isDocumentLevel", "unevaluated",
                              "objectPaths"}
                   for c in d["checks"])


# ── Fail-closed regressions (from the #110 adversarial review) ──────────


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestVerifyRedactionFailClosed:
    def test_invalid_rect_geometry_is_a_failure(self, tmp_path):
        # An inverted rect (swapped corners — e.g. a drag that ended up-left
        # of its start) is exactly the rect redact_pdf silently skips, and
        # clipped extraction over it returns nothing: without the validity
        # guard it produced a verified=True proof for an unredacted region.
        src = _text_pdf(tmp_path / "inv.pdf", TERM)
        report = verify_redaction(
            src, rects=[{"page": 0, "x0": 300, "y0": 95, "x1": 60, "y1": 55}])
        c = _check(report, "redaction.rect_regions")
        assert c.passed is False
        assert any("invalid or empty" in e and "page 1" in e for e in c.evidence)

        # Zero-area rect: same treatment.
        report2 = verify_redaction(
            src, rects=[{"page": 0, "x0": 60, "y0": 55, "x1": 60, "y1": 95}])
        assert not _check(report2, "redaction.rect_regions").passed

    def test_case_sensitive_cross_line_term_detected(self, tmp_path):
        # A term wrapped across a line break is returned by search_for as one
        # rect per line, so the per-rect exact-case confirm can never accept
        # it — the whitespace-normalized text view must catch it instead.
        p = str(tmp_path / "wrap.pdf")
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "SecretXQZ")
        page.insert_text((72, 86), "Codename")
        doc.save(p)
        doc.close()

        report = verify_redaction(p, ["SecretXQZ Codename"],
                                  case_sensitive=True)
        c = _check(report, "redaction.page_text")
        assert c.passed is False
        assert any("page 1" in e for e in c.evidence)

    def test_case_sensitive_detects_exact_case(self, tmp_path):
        # The accept path of case-sensitive matching, in both a page surface
        # and a _contains-only document surface — without this, a mutant
        # that made case_sensitive mode find nothing passed every test.
        src = _text_pdf(tmp_path / "cs1.pdf", f"about {TERM} indeed")
        rep = verify_redaction(src, [TERM], case_sensitive=True)
        assert not _check(rep, "redaction.page_text").passed

        p = str(tmp_path / "cs2.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.set_metadata({"author": f"{TERM} team"})
        doc.save(p)
        doc.close()
        rep2 = verify_redaction(p, [TERM], case_sensitive=True)
        assert not _check(rep2, "redaction.docinfo").passed
        # Wrong-case in the same surface: exact-case search stays quiet.
        rep3 = verify_redaction(p, [TERM.lower()], case_sensitive=True)
        assert _check(rep3, "redaction.docinfo").passed is True

    def test_term_in_custom_docinfo_key(self, tmp_path):
        # fitz doc.metadata only exposes the fixed keys — a custom /Info key
        # (real producers write /Company, /SourceModified, …) is only visible
        # to the pikepdf cross-check.
        p = str(tmp_path / "custom.pdf")
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        pdf.trailer["/Info"] = pdf.make_indirect(pikepdf.Dictionary(
            Company=pikepdf.String(f"unit {TERM} internal")))
        pdf.save(p)
        pdf.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.docinfo")
        assert c.passed is False
        assert any("'company'" in e and "document-level" in e for e in c.evidence)

    def test_unsupported_annotation_subtype_detected(self, tmp_path):
        # page.annots() silently skips subtypes MuPDF can't render an
        # appearance for — the pikepdf annotation sweep must still search
        # their /Contents.
        p = str(tmp_path / "bogus.pdf")
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/BogusKind"),
            Rect=[0, 0, 50, 50], Contents=pikepdf.String(f"hidden {TERM}"))
        pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annot)])
        pdf.save(p)
        pdf.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.annotations")
        assert c.passed is False
        assert any("page 1" in e for e in c.evidence)

    def test_non_stream_contents_fails_closed_without_crashing(self, tmp_path):
        # xref_stream() returns None (not an exception) when a /Contents xref
        # is not a stream; that None used to escape as a TypeError from
        # b"\n".join. It must surface as failed evidence, never a crash.
        p = str(tmp_path / "nonstream.pdf")
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        pdf.pages[0]["/Contents"] = pdf.make_indirect(
            pikepdf.Dictionary(Foo=pikepdf.Name("/Bar")))
        pdf.save(p)
        pdf.close()

        report = verify_redaction(p, [TERM])   # must not raise
        assert report.verified is False
        assert any("could not be read" in e
                   for c in report.checks for e in c.evidence)

    def test_multi_page_leak_attributed_to_correct_page(self, tmp_path):
        # D1 depends on real page attribution — with only single-page
        # fixtures, an implementation stamping every hit "page 1" passed.
        p = str(tmp_path / "multi.pdf")
        doc = fitz.open()
        doc.new_page()                                   # page 1: clean
        doc.new_page().insert_text((72, 72), TERM)       # page 2: leak
        doc.new_page()                                   # page 3: clean
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.page_text")
        assert c.passed is False
        assert any("page 2" in e for e in c.evidence)
        assert not any("page 1" in e or "page 3" in e for e in c.evidence)

    def test_multi_term_aggregation(self, tmp_path):
        # Verification must aggregate over ALL terms — a regression checking
        # only terms[0] would hand ['NAME', 'SSN'] a false pass when only
        # the second term leaked.
        src = _text_pdf(tmp_path / "mt.pdf", f"payload {TERM} here")
        report = verify_redaction(src, ["ABSENTQZ99", TERM])
        c = _check(report, "redaction.page_text")
        assert c.passed is False
        assert any(TERM in e for e in c.evidence)
        assert not any("ABSENTQZ99" in e for e in c.evidence)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestFlattenTargeting:
    """D1: page-content residual → flatten offered on the right pages;
    any document-level residual → flatten not offerable."""

    def test_page_content_residual_targets_its_pages(self, tmp_path):
        p = str(tmp_path / "multi.pdf")
        doc = fitz.open()
        doc.new_page()                                   # page 1: clean
        doc.new_page().insert_text((72, 72), TERM)       # page 2: leak
        doc.new_page()                                   # page 3: clean
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        assert report.flatten_target_pages() == [2]
        c = _check(report, "redaction.page_text")
        assert c.pages == [2] and c.is_document_level is False

    def test_document_level_residual_blocks_flatten(self, tmp_path):
        p = str(tmp_path / "toc.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), TERM)       # page-content leak…
        doc.set_toc([[1, f"Chapter {TERM}", 1]])         # …plus a doc-level one
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        # A document-level residual is present → no flatten offer at all.
        assert report.flatten_target_pages() is None
        assert _check(report, "redaction.bookmarks").is_document_level is True

    def test_bookmark_targeting_page_is_not_flattenable(self, tmp_path):
        # Guard the parsing subtlety: bookmark evidence reads
        # "targeting page N (document-level)" — the page number there must
        # NOT make the check look page-attributed/flattenable.
        p = str(tmp_path / "b.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.set_toc([[1, f"Chapter {TERM}", 1]])
        doc.save(p)
        doc.close()

        report = verify_redaction(p, [TERM])
        c = _check(report, "redaction.bookmarks")
        assert c.pages == []
        assert report.flatten_target_pages() is None


# ── File identifier (/ID) check ──────────────────────────────────────────


def _ids(path) -> list:
    with pikepdf.open(path) as pdf:
        tid = pdf.trailer.get("/ID")
        return [] if tid is None else [bytes(x).hex() for x in tid]


def _sha(path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactionFileId:
    """An output that still carries one of its source's trailer /ID elements
    is linkable to that source. The caller hands the verifier the source's
    identifiers and hash as plain values; the verifier never opens the input."""

    def _linked_copy(self, tmp_path):
        src = _clean_pdf(tmp_path / "src.pdf")
        out = str(tmp_path / "out.pdf")
        with pikepdf.open(src) as pdf:
            pdf.save(out)                     # a plain qpdf save keeps /ID[0]
        assert set(_ids(out)) & set(_ids(src))
        return src, out

    def test_shared_id_fails_document_level_and_blocks_flatten(self, tmp_path):
        src, out = self._linked_copy(tmp_path)
        report = verify_redaction(out, [TERM], source_ids=_ids(src),
                                  source_sha256=_sha(src))
        c = _check(report, "redaction.file_id")
        assert c.passed is False and c.is_document_level is True
        assert _ids(src)[0] in " ".join(c.evidence)
        assert report.verified is False
        assert report.flatten_target_pages() is None

    def test_content_derived_id_passes(self, tmp_path):
        src = _clean_pdf(tmp_path / "src.pdf")
        out = str(tmp_path / "out.pdf")
        with pikepdf.open(src) as pdf:
            del pdf.trailer["/ID"]
            pdf.save(out, deterministic_id=True)
        report = verify_redaction(out, [TERM], source_ids=_ids(src),
                                  source_sha256=_sha(src))
        assert _check(report, "redaction.file_id").passed is True
        assert report.verified is True

    def test_byte_identical_output_is_exempt(self, tmp_path):
        # Same bytes is the same file, not a second file linkable to the
        # first — the only case where sharing the identifier is not a leak.
        src = _clean_pdf(tmp_path / "src.pdf")
        report = verify_redaction(src, [TERM], source_ids=_ids(src),
                                  source_sha256=_sha(src))
        assert _check(report, "redaction.file_id").passed is True

    def test_identifiers_compare_case_insensitively(self, tmp_path):
        src, out = self._linked_copy(tmp_path)
        report = verify_redaction(out, [TERM],
                                  source_ids=[s.upper() for s in _ids(src)])
        assert _check(report, "redaction.file_id").passed is False

    def test_no_check_without_source_identifiers(self, tmp_path):
        src, out = self._linked_copy(tmp_path)
        report = verify_redaction(out, [TERM])
        assert "redaction.file_id" not in {c.id for c in report.checks}

    def test_verifier_never_needs_the_input_file(self, tmp_path):
        src, out = self._linked_copy(tmp_path)
        ids, sha = _ids(src), _sha(src)
        os.unlink(src)                        # the input is gone; values remain
        report = verify_redaction(out, [TERM], input_path=src,
                                  source_ids=ids, source_sha256=sha)
        assert _check(report, "redaction.file_id").passed is False

    def test_report_carries_both_hashes(self, tmp_path):
        src, out = self._linked_copy(tmp_path)
        d = verify_redaction(out, [TERM], source_ids=_ids(src),
                             source_sha256=_sha(src)).to_dict()
        assert d["sourceSha256"] == _sha(src)
        assert d["outputSha256"] == _sha(out)
        # Without a caller-supplied source hash the field is null, not guessed.
        assert verify_redaction(out, [TERM]).to_dict()["sourceSha256"] is None


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
class TestReportRetarget:
    """Verify-then-commit runs the checks on a temp candidate; the report the
    caller keeps must name the real destination, evidence text included."""

    def test_retarget_renames_output_and_evidence(self, tmp_path):
        tmp = tmp_path / "tmpab12cd.pdf"
        tmp.write_bytes(b"%PDF-not really\x00\xff")
        report = verify_redaction(str(tmp), [TERM])
        c = _check(report, "redaction.readable")
        c.evidence.append(f"cannot open {tmp} (document-level)")
        real = str(tmp_path / "report_out.pdf")
        report.retarget(real)
        assert report.output_path == real
        assert report.to_dict()["outputPath"] == real
        assert "tmpab12cd" not in repr(report.to_dict())
        assert any("report_out.pdf" in e for e in c.evidence)
        assert c.is_document_level is True     # markers survive the rename


# ── verify_sanitization ──────────────────────────────────────────────────


def _dirty_pdf(path) -> str:
    """One-page PDF with an /OpenAction JavaScript and a file attachment —
    both covered by the default sanitize options."""
    pdf = pikepdf.Pdf.new()
    _blank_page(pdf)
    pdf.Root["/OpenAction"] = pikepdf.Dictionary(
        S=pikepdf.Name("/JavaScript"), JS=pikepdf.String("app.alert(1);"))
    pdf.attachments["payload.bin"] = pikepdf.AttachedFileSpec(
        pdf, b"MZpayload", filename="payload.bin")
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _gps_jpeg_pdf(tmp_path) -> str:
    """PDF embedding a JPEG whose EXIF carries GPS coordinates (the R1
    residual case — same builder shape as test_pdf_analyze's)."""
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (40.0, 44.0, 54.38),
                    3: "W", 4: (73.0, 59.0, 8.5)}
    jpg = str(tmp_path / "geo.jpg")
    Image.new("RGB", (64, 48), (10, 120, 200)).save(jpg, quality=90, exif=exif)

    p = str(tmp_path / "geo.pdf")
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    with open(jpg, "rb") as fh:
        page.insert_image(fitz.Rect(20, 20, 200, 160), stream=fh.read())
    doc.save(p)
    doc.close()
    return p


@pytest.mark.integration
class TestVerifySanitization:
    def test_sanitized_output_verifies(self, tmp_path):
        src = _dirty_pdf(tmp_path / "dirty.pdf")
        out = str(tmp_path / "clean.pdf")
        sanitize_pdf(src, out)

        report = verify_sanitization(out)
        assert report.verified is True
        assert _check(report, "sanitize.scripts_js").passed is True
        assert _check(report, "sanitize.files_embedded").passed is True

    def test_unsanitized_output_fails_promised_checks(self, tmp_path):
        src = _dirty_pdf(tmp_path / "dirty.pdf")
        report = verify_sanitization(src)   # never sanitized
        assert report.verified is False
        assert not _check(report, "sanitize.scripts_js").passed
        assert not _check(report, "sanitize.files_embedded").passed

    def test_disabled_option_moves_finding_to_residuals(self, tmp_path):
        # Expectations-based: with javascript/auto_actions deliberately off,
        # the surviving JS is NOT a failure — it is honest residue.
        src = _dirty_pdf(tmp_path / "dirty.pdf")
        out = str(tmp_path / "kept.pdf")
        opts = {"javascript": False, "auto_actions": False}
        sanitize_pdf(src, out, opts)

        report = verify_sanitization(out, opts)
        assert report.verified is True
        assert "sanitize.scripts_js" not in {c.id for c in report.checks}
        assert "scripts.js" in _residual_ids(report)
        # The still-enabled promises must actually have run and passed —
        # attachment removal stays on by default in these opts.
        assert _check(report, "sanitize.files_embedded").passed is True
        # The disabled auto_actions gate leaves /OpenAction too: also residual.
        assert "actions.autorun" in _residual_ids(report)

    @pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
    def test_embedded_gps_image_is_residual_not_failure(self, tmp_path):
        # Pins the R1 asymmetry: default sanitize cannot strip EXIF inside
        # embedded images, so the high-severity finding must surface as a
        # residual — while the promised categories still verify.
        src = _gps_jpeg_pdf(tmp_path)
        out = str(tmp_path / "san.pdf")
        sanitize_pdf(src, out)

        report = verify_sanitization(out)
        assert report.verified is True
        assert "location.embedded_image" in _residual_ids(report)

    def test_locked_output_fails_closed(self, tmp_path):
        # R4: a locked file re-scans as one info finding with nothing
        # inspected — that must read as a FAILED verification, never a pass.
        plain = str(tmp_path / "p.pdf")
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        pdf.save(plain)
        pdf.close()
        locked = str(tmp_path / "locked.pdf")
        with pikepdf.open(plain) as pdf:
            pdf.save(locked, encryption=pikepdf.Encryption(
                owner="o", user="u"))

        report = verify_sanitization(locked)
        assert report.verified is False
        assert not _check(report, "sanitization.readable").passed

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            verify_sanitization(str(tmp_path / "nope.pdf"))

def _planted(path, build) -> str:
    """One blank page; *build(pdf)* plants content. Saved without
    fix_metadata_version, which would re-serialize a planted XMP packet."""
    pdf = pikepdf.Pdf.new()
    _blank_page(pdf)
    build(pdf)
    pdf.save(str(path), fix_metadata_version=False,
             stream_decode_level=pikepdf.StreamDecodeLevel.none)
    pdf.close()
    return str(path)


def _typed_xmp(pdf, data=b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>Alex Example</x:xmpmeta>"):
    st = pdf.make_stream(data)
    st["/Type"] = pikepdf.Name("/Metadata")
    st["/Subtype"] = pikepdf.Name("/XML")
    return st


class TestVerifySanitizationMetadata:
    """With `metadata` promised, any metadata stream — on any object,
    decodable or not — and any identifying /Info entry fails verification."""

    OPTS = {"metadata": True}

    def test_page_level_xmp_fails_the_promise(self, tmp_path):
        p = _planted(tmp_path / "p.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/Metadata", _typed_xmp(pdf)))
        report = verify_sanitization(p, self.OPTS)
        check = _check(report, "sanitize.meta_xmp")
        assert check.passed is False
        assert any("page 1" in e.lower() for e in check.evidence)

    def test_undecodable_xmp_fails_the_promise(self, tmp_path):
        def build(pdf):
            st = _typed_xmp(pdf)
            st.write(b"not deflate data", filter=pikepdf.Name("/FlateDecode"))
            pdf.Root["/Metadata"] = st

        report = verify_sanitization(_planted(tmp_path / "x.pdf", build), self.OPTS)
        assert _check(report, "sanitize.meta_xmp").passed is False

    def test_custom_info_key_fails_the_promise(self, tmp_path):
        p = _planted(tmp_path / "i.pdf", lambda pdf: pdf.trailer.__setitem__(
            "/Info", pdf.make_indirect(pikepdf.Dictionary(
                Company=pikepdf.String("Example Holdings")))))
        report = verify_sanitization(p, self.OPTS)
        assert _check(report, "sanitize.meta_docinfo").passed is False

    def test_sanitized_output_passes(self, tmp_path):
        def build(pdf):
            pdf.Root["/Metadata"] = _typed_xmp(pdf)
            pdf.pages[0].obj["/Metadata"] = _typed_xmp(pdf)
            pdf.trailer["/Info"] = pdf.make_indirect(pikepdf.Dictionary(
                Company=pikepdf.String("Example Holdings")))

        src = _planted(tmp_path / "s.pdf", build)
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, self.OPTS)
        assert res["removed"].get("xmp_metadata") == 2
        assert res["removed"].get("doc_info") == 1
        report = verify_sanitization(out, self.OPTS)
        assert _check(report, "sanitize.meta_xmp").passed is True
        assert _check(report, "sanitize.meta_docinfo").passed is True
        assert report.verified is True

# ── Whole-file sweep (D-H) ───────────────────────────────────────────────

_ALL_ON = {"javascript": True, "launch_actions": True, "auto_actions": True,
           "submit_actions": True, "embedded_files": True,
           "external_links": True, "metadata": True}
_SWEEP_IDS = {f"sanitize.sweep_{o}" for o in _ALL_ON}


def _js():
    return pikepdf.Dictionary(S=pikepdf.Name("/JavaScript"),
                              JS=pikepdf.String("app.alert(1);"))


def _form_xobject(pdf):
    st = pdf.make_stream(b"0 0 10 10 re f")
    st["/Type"] = pikepdf.Name("/XObject")
    st["/Subtype"] = pikepdf.Name("/Form")
    st["/BBox"] = [0, 0, 10, 10]
    return st


class TestSweepNameCollisions:
    """A key name proves nothing. Resource dictionaries map arbitrary names
    to fonts, images and graphics states; a producer naming them A, B, ...
    reaches /AA, /EF and /JS, and an image may be called /Metadata."""

    def test_resources_named_like_the_things_it_looks_for(self, tmp_path):
        def build(pdf):
            font = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"), Subtype=pikepdf.Name("/Type1"),
                BaseFont=pikepdf.Name("/Helvetica"),
                FontDescriptor=pikepdf.Dictionary(Type=pikepdf.Name("/FontDescriptor"))))
            gstate = pikepdf.Dictionary(
                Type=pikepdf.Name("/ExtGState"),
                D=pikepdf.Array([pikepdf.Array([3]), 0]),         # dash pattern, not "mouse down"
                SMask=pikepdf.Dictionary(S=pikepdf.Name("/Luminosity"),
                                         G=_form_xobject(pdf)))
            names = ("JS", "AA", "EF", "Metadata", "OpenAction", "S", "URI")
            pdf.pages[0].obj["/Resources"] = pikepdf.Dictionary(
                Font=pikepdf.Dictionary({f"/{n}": font for n in names}),
                XObject=pikepdf.Dictionary({f"/{n}": _form_xobject(pdf) for n in names}),
                ExtGState=pikepdf.Dictionary({f"/{n}": gstate for n in names}),
                ColorSpace=pikepdf.Dictionary(S=pikepdf.Name("/DeviceGray"),
                                              JS=pikepdf.Name("/DeviceRGB")))
            pdf.pages[0].obj["/Contents"] = pdf.make_stream(
                b"/JS gs /Metadata Do BT /AA 12 Tf (x) Tj ET")

        report = verify_sanitization(_planted(tmp_path / "c.pdf", build), _ALL_ON)
        assert {c.id for c in report.checks} >= _SWEEP_IDS
        assert report.verified is True, [(c.id, c.evidence) for c in report.failed_checks()]


class TestSweepIsIndependent:
    """It finds what it is looking for where the analyzer has no site."""

    def test_script_at_a_site_nobody_walks_fails_with_its_path(self, tmp_path):
        # An action stored under a key that is no action site at all. Neither
        # the analyzer nor the sanitizer has any reason to look there.
        def build(pdf):
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([pdf.make_indirect(
                pikepdf.Dictionary(Type=pikepdf.Name("/Annot"),
                                   Subtype=pikepdf.Name("/Link"),
                                   Rect=[0, 0, 50, 50], Stash=_js()))])

        src = _planted(tmp_path / "stash.pdf", build)
        report = verify_sanitization(src)
        assert _check(report, "sanitize.scripts_js").passed is True     # no site scanner sees it
        sweep = _check(report, "sanitize.sweep_javascript")
        assert sweep.passed is False and sweep.unevaluated is False
        assert sweep.evidence == ["a JavaScript action on page 1, annotation 1, under /Stash"]
        assert sweep.object_paths == ["trailer/Root/Pages/Kids[0]/Annots[0]/Stash"]
        assert report.verified is False

    def test_3d_instantiation_script_is_seen_then_gone(self, tmp_path):
        # A script on a 3D stream is not an action, so only a predicate about
        # the stream itself sees it. Sanitize removes it under `javascript`.
        def build(pdf):
            artwork = pdf.make_stream(b"U3D\x00")
            artwork["/Type"] = pikepdf.Name("/3D")
            artwork["/Subtype"] = pikepdf.Name("/U3D")
            artwork["/OnInstantiate"] = pdf.make_stream(b"app.alert(1);")
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([pdf.make_indirect(
                pikepdf.Dictionary(Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/3D"),
                                   Rect=[0, 0, 50, 50]))])
            pdf.pages[0].obj["/Annots"][0]["/3DD"] = artwork

        src = _planted(tmp_path / "3d.pdf", build)
        before = _check(verify_sanitization(src), "sanitize.sweep_javascript")
        assert before.passed is False
        assert before.evidence == [
            "a script that runs when 3D artwork loads on page 1, annotation 1"]
        assert before.object_paths == ["trailer/Root/Pages/Kids[0]/Annots[0]/3DD"]

        out = str(tmp_path / "out.pdf")
        sanitize_pdf(src, out)
        after = verify_sanitization(out)
        assert after.verified is True, [(c.id, c.evidence) for c in after.failed_checks()]

    def test_inline_script_text_without_an_action_type(self, tmp_path):
        p = _planted(tmp_path / "s.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Custom", pikepdf.Dictionary(JS=pikepdf.String("app.alert(1);"))))
        sweep = _check(verify_sanitization(p, {"javascript": True}),
                       "sanitize.sweep_javascript")
        assert sweep.passed is False
        assert sweep.evidence == ["a script in the document catalog, under /Custom"]
        assert sweep.object_paths == ["trailer/Root/Custom"]

    @pytest.mark.parametrize("make", [
        lambda pdf: pikepdf.Array([pdf.pages[0].obj, pikepdf.Name("/Fit")]),
        lambda pdf: pikepdf.Name("/Chapter1"),
        lambda pdf: pikepdf.String("Chapter1"),
    ], ids=["explicit-array", "named-as-name", "named-as-string"])
    def test_destination_openaction_is_not_an_action(self, tmp_path, make):
        p = _planted(tmp_path / "d.pdf", lambda pdf: pdf.Root.__setitem__(
            "/OpenAction", make(pdf)))
        report = verify_sanitization(p, _ALL_ON)
        assert report.verified is True, [(c.id, c.evidence) for c in report.failed_checks()]

    def test_disabled_options_are_not_swept(self, tmp_path):
        p = _planted(tmp_path / "o.pdf", lambda pdf: pdf.Root.__setitem__("/OpenAction", _js()))
        report = verify_sanitization(p, {k: False for k in _ALL_ON})
        assert not {c.id for c in report.checks} & _SWEEP_IDS


class TestSweepDepth:
    """The walk is iterative. A /Next chain is a linked list a file can make
    as long as it likes; Python's recursion limit is about a thousand."""

    def test_script_at_the_end_of_a_very_long_chain(self, tmp_path):
        links = 20_000

        def build(pdf):
            node = pdf.make_indirect(_js())
            for _ in range(links):
                node = pdf.make_indirect(pikepdf.Dictionary(
                    S=pikepdf.Name("/Named"), N=pikepdf.Name("/NextPage"), Next=node))
            pdf.Root["/OpenAction"] = node

        p = _planted(tmp_path / "deep.pdf", build)
        report = verify_sanitization(p, {"javascript": True, "auto_actions": False})
        sweep = _check(report, "sanitize.sweep_javascript")
        assert sweep.passed is False
        assert sweep.evidence == [
            f"a JavaScript action in the document's open action, {links} steps "
            f"down a chain of actions"]
        assert sweep.object_paths == [f"trailer/Root/OpenAction/Next(x{links})"]

    def test_script_under_deep_inline_nesting(self, tmp_path):
        def build(pdf):
            node = _js()
            for _ in range(150):
                node = pikepdf.Dictionary(Inner=pikepdf.Array([node]))
            pdf.Root["/Custom"] = node

        p = _planted(tmp_path / "nest.pdf", build)
        sweep = _check(verify_sanitization(p, {"javascript": True}),
                       "sanitize.sweep_javascript")
        assert sweep.passed is False

    def test_cycles_end(self, tmp_path):
        def build(pdf):
            a = pdf.make_indirect(pikepdf.Dictionary(Name=pikepdf.String("a")))
            b = pdf.make_indirect(pikepdf.Dictionary(Name=pikepdf.String("b"), Peer=a))
            a["/Peer"] = b
            pdf.Root["/Custom"] = a

        status = WalkStatus()
        with pikepdf.open(_planted(tmp_path / "cyc.pdf", build)) as pdf:
            seen = [render_path(path) for path, _obj in walk_objects(pdf, status)]
        assert status.complete
        assert seen.count("trailer/Root/Custom") == 1
        assert "trailer/Root/Custom/Peer" in seen

    def test_strings_are_yielded_on_request(self, tmp_path):
        # What a term search over the whole file would read.
        p = _planted(tmp_path / "t.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Custom", pikepdf.Dictionary(Note=pikepdf.String("needle-123"))))
        with pikepdf.open(p) as pdf:
            found = {render_path(path): bytes(obj) for path, obj
                     in walk_objects(pdf, WalkStatus(), strings=True)
                     if isinstance(obj, pikepdf.String)}
        assert found["trailer/Root/Custom/Note"] == b"needle-123"


class TestSweepNeverPassesOnAPartialWalk:
    """If the sweep did not see the whole file, what it did not see cannot
    be reported absent. Every enabled option's check fails."""

    def _clean(self, tmp_path) -> str:
        return _planted(tmp_path / "clean.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Custom", pikepdf.Dictionary(Marker=pikepdf.Name("/Unreadable"))))

    def test_clean_file_passes_when_fully_walked(self, tmp_path):
        assert verify_sanitization(self._clean(tmp_path), _ALL_ON).verified is True

    def test_unreadable_object_fails_every_sweep_check(self, tmp_path, monkeypatch):
        real = pikepdf.Object.keys

        def keys(self):
            found = real(self)
            if "/Marker" in found:
                raise RuntimeError("simulated damaged object")
            return found

        monkeypatch.setattr(pikepdf.Object, "keys", keys)
        report = verify_sanitization(self._clean(tmp_path), _ALL_ON)
        sweeps = [c for c in report.checks if c.id in _SWEEP_IDS]
        assert len(sweeps) == len(_SWEEP_IDS)
        assert all(c.passed is False for c in sweeps)
        assert all("did not cover the whole file" in c.evidence[0] for c in sweeps)
        assert all("trailer/Root/Custom" in c.evidence[0] for c in sweeps)
        assert report.verified is False

    def test_object_limit_is_recorded_not_swallowed(self, tmp_path):
        status = WalkStatus()
        with pikepdf.open(self._clean(tmp_path)) as pdf:
            list(walk_objects(pdf, status, max_nodes=2))
        assert status.complete is False
        assert "stopped after 2 objects" in status.problems[0]

    def test_output_that_cannot_be_opened_fails_every_sweep_check(self, tmp_path):
        from pdf_verify import _sweep_checks
        checks = _sweep_checks(str(tmp_path / "missing.pdf"), _ALL_ON)
        assert {c.id for c in checks} == _SWEEP_IDS
        assert all(c.passed is False and c.evidence for c in checks)
