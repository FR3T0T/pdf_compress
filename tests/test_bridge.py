"""Tests for what the bridge answers, without the bridge.

The logic behind two bridge slots lives in Qt-free modules so it can run on
a headless CI runner without the PySide6 GUI stack: the compress output-path
helper in ``compress_paths`` (see its module docstring for why that import
path matters), and the answer to a sanitize request -- success, refusal or
failure -- in ``pdf_analyze.sanitize_payload``. Nothing here imports
``ui.bridge``.

The sanitize names are reached through the modules rather than imported at
the top, so this file still collects against a tree that does not have them.
"""

import json
import logging
import os

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

import pdf_analyze
import pdf_verify
from compress_paths import compress_output_path


def test_single_file_honors_explicit_output(tmp_path):
    chosen = str(tmp_path / "chosen.pdf")
    got = compress_output_path(
        str(tmp_path / "a.pdf"), 1, chosen, "", "{name}_compressed", "standard"
    )
    assert got == chosen


def test_batch_does_not_reuse_single_explicit_output(tmp_path):
    # Regression: a single explicit outputPath must NOT be applied to every
    # file in a batch (that made all files overwrite one path). With no output
    # dir and default naming, each file falls back to the beside-source default
    # (None -> compress_pdf writes <name>_compressed.pdf next to the source).
    chosen = str(tmp_path / "chosen.pdf")
    got = compress_output_path(
        str(tmp_path / "a.pdf"), 3, chosen, "", "{name}_compressed", "standard"
    )
    assert got != chosen
    assert got is None


def test_default_returns_none_for_beside_source(tmp_path):
    got = compress_output_path(
        str(tmp_path / "a.pdf"), 1, None, "", "{name}_compressed", "standard"
    )
    assert got is None


def test_output_dir_builds_distinct_per_file_path(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    got = compress_output_path(
        str(tmp_path / "report.pdf"), 2, None, str(out_dir), "{name}_compressed", "ebook"
    )
    assert got is not None
    assert os.path.basename(got) == "report_compressed.pdf"
    assert os.path.realpath(os.path.dirname(got)) == os.path.realpath(str(out_dir))


def test_naming_template_variables_applied(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    got = compress_output_path(
        str(tmp_path / "report.pdf"), 1, None, str(out_dir), "{name}-{preset}", "ebook"
    )
    assert os.path.basename(got) == "report-ebook.pdf"


def test_bad_naming_template_falls_back(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    got = compress_output_path(
        str(tmp_path / "report.pdf"), 1, None, str(out_dir), "{unknown}", "standard"
    )
    assert os.path.basename(got) == "report_compressed.pdf"


# ── Sanitize: what the bridge answers ────────────────────────────────────


def _pdf(path, build=None) -> str:
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    if build is not None:
        build(pdf)
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _js() -> Dictionary:
    return Dictionary(S=Name.JavaScript, JS=String("app.alert('marker-js')"))


def _script_on_open(pdf) -> None:
    pdf.Root["/OpenAction"] = _js()


def _stashed_script(pdf) -> None:
    """A script under a key that is no action site: Sanitize has no way to
    remove it, so a sanitize with `javascript` on is refused."""
    pdf.pages[0].obj["/Annots"] = Array([pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 50, 50], Stash=_js()))])


def _link_and_script(pdf) -> None:
    _script_on_open(pdf)
    pdf.pages[0].obj["/Annots"] = Array([pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 50, 50],
        A=Dictionary(S=Name.URI, URI=String("https://marker.example/"))))])


def _boom(*_a, **_k):
    raise RuntimeError("simulated scanner failure")


NOTHING_LEFT = {"stillPresent": [], "notChecked": [], "identifier": False, "unreadable": False}


@pytest.mark.integration
class TestSanitizePayload:
    def test_success_carries_the_verification_report(self, tmp_path):
        src, out = _pdf(tmp_path / "src.pdf", _script_on_open), str(tmp_path / "out.pdf")
        payload = pdf_analyze.sanitize_payload(src, out, {})
        assert payload["success"] is True
        assert payload["removed"] == {"open_action": 1} and payload["total_removed"] == 1
        assert payload["output"] == out and payload["output_size"] == os.path.getsize(out)
        assert payload["verification"]["verified"] is True
        assert payload["verification"]["residualFindings"] == []
        assert "error" not in payload and "refusal" not in payload
        json.dumps(payload)                                # it has to cross the bridge

    def test_what_the_options_left_alone_rides_along_as_residuals(self, tmp_path):
        src, out = _pdf(tmp_path / "src.pdf", _link_and_script), str(tmp_path / "out.pdf")
        payload = pdf_analyze.sanitize_payload(src, out, {})    # links are off by default
        assert payload["success"] is True and payload["verification"]["verified"] is True
        assert [f["id"] for f in payload["verification"]["residualFindings"]] == ["links.uri"]

    @pytest.mark.parametrize("options", [None, {}, ["javascript"], "javascript"],
                             ids=["none", "empty", "a list", "a string"])
    def test_options_that_are_not_a_dictionary_mean_the_defaults(self, tmp_path, options):
        src, out = _pdf(tmp_path / "src.pdf", _script_on_open), str(tmp_path / "out.pdf")
        payload = pdf_analyze.sanitize_payload(src, out, options)
        assert payload["success"] is True and payload["removed"] == {"open_action": 1}

    def test_an_image_succeeds_and_has_no_report(self, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        jpg, out = str(tmp_path / "p.jpg"), str(tmp_path / "o.jpg")
        Image.new("RGB", (8, 8), (1, 2, 3)).save(jpg)
        payload = pdf_analyze.sanitize_payload(jpg, out, {})
        assert payload["success"] is True and os.path.isfile(out)
        assert "verification" not in payload
        json.dumps(payload)

    def test_a_refusal_carries_the_report_and_says_what_is_left(self, tmp_path):
        src, out = _pdf(tmp_path / "src.pdf", _stashed_script), str(tmp_path / "out.pdf")
        payload = pdf_analyze.sanitize_payload(src, out, {})
        assert payload["success"] is False
        assert payload["error"] == ("Sanitize could not be verified: still present in the "
                                    "output: JavaScript.")
        assert payload["verification"]["verified"] is False
        assert payload["verification"]["outputPath"] == out
        assert payload["refusal"] == {**NOTHING_LEFT, "stillPresent": [
            "JavaScript: a JavaScript action on page 1, annotation 1, under /Stash"]}
        assert not os.path.exists(out) and os.listdir(tmp_path) == ["src.pdf"]
        json.dumps(payload)

    def test_a_refusal_over_the_identifier_alone_says_only_that(self, tmp_path, monkeypatch):
        src, out = _pdf(tmp_path / "src.pdf", _script_on_open), str(tmp_path / "out.pdf")
        monkeypatch.setattr(pdf_analyze, "save_with_content_id",
                            lambda pdf, path, **kw: pdf.save(path, **kw))   # qpdf keeps /ID[0]
        payload = pdf_analyze.sanitize_payload(src, out, {})
        assert payload["success"] is False and "identifier" in payload["error"]
        assert payload["refusal"] == {**NOTHING_LEFT, "identifier": True}

    def test_a_refusal_over_what_could_not_be_checked_names_it(self, tmp_path, monkeypatch):
        src, out = _pdf(tmp_path / "src.pdf", _script_on_open), str(tmp_path / "out.pdf")
        monkeypatch.setattr(pdf_analyze, "_scan_links", _boom)
        payload = pdf_analyze.sanitize_payload(
            src, out, {k: True for k in pdf_analyze.DEFAULT_SANITIZE})
        assert payload["success"] is False
        assert payload["refusal"] == {**NOTHING_LEFT, "notChecked": ["External links"]}

    def test_a_copy_that_could_not_be_saved_is_a_plain_error(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", _script_on_open)
        payload = pdf_analyze.sanitize_payload(src, str(tmp_path / "no-such-folder" / "o.pdf"), {})
        assert payload["success"] is False and payload["errorType"] == "FileNotFoundError"
        assert "nothing was saved" in payload["error"]
        assert str(tmp_path) not in payload["error"] and ".pdf" not in payload["error"]
        assert "verification" not in payload and "refusal" not in payload

    def test_a_missing_input_is_a_plain_error(self, tmp_path):
        payload = pdf_analyze.sanitize_payload(
            str(tmp_path / "gone.pdf"), str(tmp_path / "out.pdf"), {})
        assert payload["success"] is False and payload["errorType"] == "FileNotFoundError"
        assert payload["error"] and "gone.pdf" not in payload["error"]

    def test_an_unsupported_file_keeps_its_message(self, tmp_path):
        other = tmp_path / "notes.txt"
        other.write_text("not a document")
        payload = pdf_analyze.sanitize_payload(str(other), str(tmp_path / "out.pdf"), {})
        assert payload == {"success": False, "errorType": "ValueError",
                           "error": "Unsupported file type (expected PDF, JPEG, or PNG)"}

    def test_nothing_it_logs_names_a_file(self, tmp_path, monkeypatch, caplog):
        refused = _pdf(tmp_path / "quarterly-figures.pdf", _stashed_script)
        fine = _pdf(tmp_path / "annual-figures.pdf", _script_on_open)
        with caplog.at_level(logging.DEBUG):
            assert not pdf_analyze.sanitize_payload(refused, str(tmp_path / "a.pdf"), {})["success"]
            assert not pdf_analyze.sanitize_payload(
                fine, str(tmp_path / "missing" / "b.pdf"), {})["success"]
            assert not pdf_analyze.sanitize_payload(
                str(tmp_path / "gone.pdf"), str(tmp_path / "c.pdf"), {})["success"]

            def crash(*a, **k):
                raise RuntimeError(f"simulated crash while reading {fine}")

            monkeypatch.setattr(pdf_analyze, "sanitize_pdf", crash)
            crashed = pdf_analyze.sanitize_payload(fine, str(tmp_path / "d.pdf"), {})
            assert crashed["success"] is False and crashed["errorType"] == "RuntimeError"
        assert "RuntimeError" in caplog.text               # the type is what is kept
        for leak in ("quarterly-figures", "annual-figures", "gone.pdf", str(tmp_path)):
            assert leak not in caplog.text


def _report(*checks):
    return pdf_verify.VerificationReport(
        input_path="", output_path="", tool="sanitization", timestamp="", checks=list(checks))


def _failed(cid: str, evidence: list, *, unevaluated: bool = False, paths=()):
    return pdf_verify.VerificationCheck(
        id=cid, description="", passed=False, evidence=list(evidence),
        unevaluated=unevaluated, object_paths=list(paths))


class TestRefusalDetail:
    """A refusal, as the three things a person needs to be told."""

    def test_a_category_two_checks_failed_for_is_named_once_in_the_sweeps_words(self, tmp_path,
                                                                               monkeypatch):
        src = _pdf(tmp_path / "src.pdf", _script_on_open)
        monkeypatch.setattr(pdf_analyze, "_scrub_owner_actions", lambda *a, **k: False)
        with pytest.raises(pdf_analyze.SanitizationVerificationError) as ei:
            pdf_analyze.sanitize_pdf(src, str(tmp_path / "out.pdf"))
        assert ei.value.detail == {**NOTHING_LEFT, "stillPresent": [
            "JavaScript: a JavaScript action in the document's open action",
            "Auto-run actions: an action that runs on opening in the document's open action"]}

    def test_where_only_the_audit_found_it_the_audits_own_lines_are_used(self):
        detail = pdf_verify.sanitize_refusal_detail(_report(_failed(
            "sanitize.actions_autorun",
            ["'Auto-run actions' still detected in the output", "Page 1 /AA /Xx — fires by itself"])))
        assert detail == {**NOTHING_LEFT, "stillPresent": [
            "Auto-run actions: Page 1 /AA /Xx — fires by itself"]}

    def test_more_than_three_places_are_not_all_listed(self):
        places = [f"a script on page {n}" for n in range(1, 6)]
        detail = pdf_verify.sanitize_refusal_detail(_report(_failed(
            "sanitize.sweep_javascript", places, paths=[f"p{n}" for n in range(5)])))
        assert detail["stillPresent"] == [
            "JavaScript: a script on page 1; a script on page 2; a script on page 3; and others"]

    def test_a_sweep_that_also_stopped_short_lists_only_what_it_found(self):
        detail = pdf_verify.sanitize_refusal_detail(_report(_failed(
            "sanitize.sweep_embedded_files",
            ["an embedded file on page 2",
             "the sweep did not cover the whole file (could not inspect x); what it did not "
             "inspect cannot be reported absent (document-level)"], paths=["p0"])))
        assert detail["stillPresent"] == ["Embedded files: an embedded file on page 2"]

    def test_what_was_found_is_not_also_listed_as_unchecked(self):
        detail = pdf_verify.sanitize_refusal_detail(_report(
            _failed("sanitize.sweep_external_links", ["a link on page 1"], paths=["p0"]),
            _failed("sanitize.links_uri", ["'links.uri' could not be fully checked: x "
                                           "(document-level)"], unevaluated=True),
            _failed("sanitize.sweep_metadata", ["the sweep did not cover the whole file"],
                    unevaluated=True)))
        assert detail == {**NOTHING_LEFT, "stillPresent": ["External links: a link on page 1"],
                          "notChecked": ["Document metadata"]}

    def test_an_output_that_could_not_be_read_back(self):
        detail = pdf_verify.sanitize_refusal_detail(_report(_failed(
            "sanitization.readable", ["output could not be re-scanned (PdfError)"],
            unevaluated=True)))
        assert detail == {**NOTHING_LEFT, "unreadable": True}

    def test_a_verified_report_has_nothing_to_say(self, tmp_path):
        src, out = _pdf(tmp_path / "src.pdf", _script_on_open), str(tmp_path / "out.pdf")
        pdf_analyze.sanitize_pdf(src, out)
        report = pdf_verify.verify_sanitization(out)
        assert report.verified and pdf_verify.sanitize_refusal_detail(report) == NOTHING_LEFT
