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

import pikepdf
import pytest
from PIL import Image

from pdf_analyze import sanitize_pdf
from pdf_ops import redact_pdf
from pdf_verify import verify_redaction, verify_sanitization

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
                          "flattenTargetPages"}
        assert d["tool"] == "redaction"
        assert d["inputPath"] == "in.pdf"
        assert d["verified"] is True
        assert d["flattenTargetPages"] is None       # nothing failed
        assert all(set(c) == {"id", "description", "passed", "evidence",
                              "pages", "isDocumentLevel"}
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
