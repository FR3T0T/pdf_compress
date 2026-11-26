"""Earlier versions a file keeps from incremental saves (#122).

A file redacted or edited by a program that saves incrementally keeps every
earlier version of itself: the update is appended, and the text or picture
taken out is still in the bytes before it. Cut the file at its first %%EOF
and the earlier version opens, showing what was removed.

Analyze now says so (content.earlier_versions), counted with PyMuPDF's
version_count, which counts a linearized file right where counting %%EOF
markers does not. A file MuPDF repaired as it opened it cannot be counted,
and is reported as not checked. Redact and Sanitize check that what they
write keeps one version (redaction.one_version, sanitize.one_version), and
their report records how many earlier versions the source kept.

Every file is built here (tests/_versions.py). Qt-free (see CLAUDE.md).
"""

import pikepdf
import pytest

from pdf_analyze import analyze_document, sanitize_pdf
from pdf_ops import redact_pdf
from pdf_verify import (
    redaction_refusal_detail,
    sanitize_refusal_detail,
    verify_redaction,
    verify_sanitization,
)
from tests import _versions as v

fitz = pytest.importorskip("fitz")

EARLIER = "content.earlier_versions"


def _finding(report: dict, fid: str = EARLIER):
    return next((f for f in report["findings"] if f["id"] == fid), None)


def _check(report: dict, cid: str):
    return next((c for c in report["checks"] if c["id"] == cid), None)


# ── Analyze ──────────────────────────────────────────────────────────────

def test_a_file_saved_incrementally_is_flagged(tmp_path):
    """The case #122 is about: a word taken off the page by an incremental
    save. The page no longer shows it; the first version still does."""
    src = v.rewrite_page(v.base(tmp_path / "src.pdf"))
    cut = tmp_path / "first.pdf"
    cut.write_bytes(v.first_version(src))
    with fitz.open(src) as now, fitz.open(str(cut)) as then:
        assert "ACME-4471" not in now[0].get_text() and "ACME-4471" in then[0].get_text()

    report = analyze_document(src)
    found = _finding(report)
    assert found is not None
    assert (found["category"], found["severity"], found["count"]) == ("content", "medium", 1)
    assert found["title"] == "Earlier versions kept in the file"
    assert found["detail"] == (
        "The file keeps 1 earlier version of itself from incremental saves. Text or pictures "
        "removed in a later version may still be in the file. Sanitize writes a copy that keeps "
        "only the current version.")
    assert report["overallRisk"] in ("medium", "high")


def test_two_incremental_saves_are_two_earlier_versions(tmp_path):
    src = v.rewrite_page(v.rewrite_page(v.base(tmp_path / "src.pdf")), b"closed")
    found = _finding(analyze_document(src))
    assert found["count"] == 2
    assert found["detail"].startswith("The file keeps 2 earlier versions of itself from "
                                      "incremental saves.")


def test_a_plain_file_is_not_flagged(tmp_path):
    """Guard (passes on main): one version, nothing to say; still clean."""
    report = analyze_document(v.base(tmp_path / "src.pdf"))
    assert _finding(report) is None
    assert [f["id"] for f in report["findings"]] == ["clean"]


def test_a_linearized_file_is_not_flagged(tmp_path):
    """Guard (passes on main): a linearized file has two %%EOF markers and
    is one version."""
    src = v.base(tmp_path / "src.pdf", linearize=True)
    assert open(src, "rb").read().count(b"%%EOF") == 2
    report = analyze_document(src)
    assert _finding(report) is None and "content.earlier_versions" not in report["unevaluated"]


def test_a_linearized_file_saved_incrementally_is_flagged(tmp_path):
    """Three %%EOF markers, two versions: one earlier version."""
    src = v.rewrite_page(v.base(tmp_path / "src.pdf", linearize=True))
    assert open(src, "rb").read().count(b"%%EOF") == 3
    assert _finding(analyze_document(src))["count"] == 1


def test_an_encrypted_file_saved_incrementally_is_flagged(tmp_path):
    src = v.change_layout(v.base(tmp_path / "src.pdf", encrypt=True), v.PASSWORD)
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(src).close()
    report = analyze_document(src, password=v.PASSWORD)
    assert _finding(report, "encryption.present") is not None
    assert _finding(report)["count"] == 1


def test_a_file_mupdf_repairs_is_not_checked(tmp_path):
    """Its cross-references have to be rebuilt to read it, and then the
    versions cannot be counted (MuPDF counts one). That is said, as for any
    other check that could not finish: not checked, never clean."""
    src = v.break_last_xref(v.rewrite_page(v.base(tmp_path / "src.pdf")))
    with fitz.open(src) as doc:
        assert doc.is_repaired and doc.version_count == 1
    report = analyze_document(src)
    assert _finding(report) is None
    assert report["unevaluated"][EARLIER] == [
        "the file is damaged and was repaired to be read, so its saved versions could not be "
        "counted"]
    incomplete = _finding(report, "audit.incomplete")
    assert "Earlier versions kept in the file: the file is damaged and was repaired to be read, " \
           "so its saved versions could not be counted" in incomplete["items"]
    assert _finding(report, "clean") is None


def test_a_repaired_file_whose_bytes_hold_one_version_is_checked(tmp_path):
    """Most files MuPDF repairs hold one version, and their bytes say so:
    one %%EOF, one startxref, no object defined twice. Such a file is
    checked, and has nothing to report."""
    src = v.break_last_xref(v.base(tmp_path / "src.pdf"))
    with fitz.open(src) as doc:
        assert doc.is_repaired
    report = analyze_document(src)
    assert _finding(report) is None and EARLIER not in report["unevaluated"]
    assert _finding(report, "clean") is not None
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["closed"])
    assert res.verification["earlierVersions"] == {"source": 0, "output": 0}


def test_a_repaired_file_with_an_object_defined_twice_is_still_not_checked(tmp_path):
    """One %%EOF and one startxref, but the page is defined twice: an update
    is in the bytes, so it is not called one version."""
    src = v.break_last_xref(v.drop_first_end(v.rewrite_page(v.base(tmp_path / "src.pdf"))))
    data = open(src, "rb").read()
    assert data.count(b"%%EOF") == 1 and data.count(b"startxref") == 1
    with fitz.open(src) as doc:
        assert doc.is_repaired
    report = analyze_document(src)
    assert _finding(report) is None and EARLIER in report["unevaluated"]


# ── Redact and Sanitize: the copy keeps one version ─────────────────────

def test_redact_writes_one_version_and_records_the_source_s(tmp_path):
    src = v.rewrite_page(v.base(tmp_path / "src.pdf"))
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=["closed"])
    check = _check(res.verification, "redaction.one_version")
    assert check is not None and check["passed"] is True
    assert res.verification["earlierVersions"] == {"source": 1, "output": 0}
    with fitz.open(out) as doc:
        assert doc.version_count == 1


def test_sanitize_writes_one_version_and_records_the_source_s(tmp_path):
    src = v.rewrite_page(v.rewrite_page(v.base(tmp_path / "src.pdf", title="Old title")))
    out = str(tmp_path / "out.pdf")
    res = sanitize_pdf(src, out)
    check = _check(res["verification"], "sanitize.one_version")
    assert check is not None and check["passed"] is True
    assert res["verification"]["earlierVersions"] == {"source": 2, "output": 0}


def test_a_source_mupdf_repaired_is_recorded_as_not_counted(tmp_path):
    src = v.break_last_xref(v.rewrite_page(v.base(tmp_path / "src.pdf")))
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["closed"])
    assert res.verification["earlierVersions"] == {
        "source": None, "output": 0,
        "sourceWhy": "the file is damaged and was repaired to be read, so its saved versions "
                     "could not be counted"}
    assert _check(res.verification, "redaction.one_version")["passed"] is True


def test_the_redaction_check_refuses_a_file_with_earlier_versions(tmp_path):
    """The check is not a formality: handed a file that keeps an earlier
    version, it fails, document-level, and the refusal says so."""
    out = v.rewrite_page(v.base(tmp_path / "out.pdf"), b"closed")
    report = verify_redaction(out, terms=["nowhere-in-it"])
    check = next(c for c in report.checks if c.id == "redaction.one_version")
    assert check.passed is False and check.is_document_level
    assert check.evidence == ["the output keeps 1 earlier version of itself from incremental "
                              "saves, which can hold what was removed (document-level)"]
    assert report.verified is False and report.flatten_target_pages() is None
    assert ("The new file would still keep an earlier version of itself, which can hold what "
            "was removed.") in redaction_refusal_detail(report)["found"]


def test_the_sanitize_check_refuses_a_file_with_earlier_versions(tmp_path):
    out = v.rewrite_page(v.base(tmp_path / "out.pdf"), b"closed")
    report = verify_sanitization(out)
    check = next(c for c in report.checks if c.id == "sanitize.one_version")
    assert check.passed is False
    assert report.verified is False
    assert sanitize_refusal_detail(report)["stillPresent"] == [
        "Earlier versions of the file, from incremental saves"]
