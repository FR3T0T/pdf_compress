"""The proof of removal: the panel's groups, and the report saved on request (#100).

Redact and Sanitize already return a verification report (pdf_verify's
VerificationReport) that the window gets. Now each answer also carries
``proof``: the run in plain words, in three groups (removed and confirmed
gone; found but not targeted, each with a next step; not checked), by page
where there are pages, with a line on earlier versions (#122) and one on the
network (#120 d). removal_report.save writes it, when asked, beside the new
file as "<name>.removal-report.json" and ".txt", never over another file.

What the saved report must never hold: the words searched for, the text
found, a password, or a folder. Tested with a word in the page text, the
title and an attachment's name, a password, and a folder whose name is
easy to spot.

Qt-free (see CLAUDE.md): the bridge only stores the last answer and calls
removal_report.save_payload.
"""

import json
import os

import pikepdf
import pytest
from pikepdf import Dictionary, Name

import network_guard
import removal_report
from pdf_analyze import sanitize_payload
from pdf_ops import redact_pdf, redact_result_payload
from tests import _versions as v

fitz = pytest.importorskip("fitz")

TERM = "ACME-4471"
PASSWORD = "Pw-Kestrel-55"
FOLDER = "Folder-Zebra-Quokka-731"


def _attach(pdf, name: str, data: bytes) -> None:
    pdf.attachments[name] = pikepdf.AttachedFileSpec(pdf, data, filename=name)


def _source(folder, *, encrypt: bool = False, incremental: bool = False) -> str:
    """Two pages: the term on page 1 and in the title, a scan with no text
    on page 2, and two attachments, one named with the term."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "statement.pdf")
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1,
                                        BaseFont=Name.Helvetica))
    scan = pdf.make_stream(bytes(range(64)) * 4, Type=Name.XObject, Subtype=Name.Image, Width=16,
                           Height=16, ColorSpace=Name.DeviceGray, BitsPerComponent=8)
    for words in (f"Account {TERM} is closed".encode(), None):
        pdf.pages.append(pikepdf.Page(Dictionary(
            Type=Name.Page, MediaBox=[0, 0, 300, 200],
            Resources=Dictionary(Font=Dictionary(F1=font), XObject=Dictionary(Im0=scan)))))
        content = (b"BT /F1 12 Tf 20 100 Td (" + words + b") Tj ET" if words else
                   b"q 300 0 0 200 0 0 cm /Im0 Do Q")
        pdf.pages[-1].obj["/Contents"] = pdf.make_stream(content)
    pdf.docinfo["/Title"] = f"Statement {TERM}"
    _attach(pdf, f"{TERM}-ledger.txt", b"ledger")
    _attach(pdf, "notes.txt", b"plain notes")
    kwargs = {"encryption": pikepdf.Encryption(user=PASSWORD, owner=PASSWORD + "-o", R=6)} \
        if encrypt else {}
    pdf.save(path, static_id=True, **kwargs)
    pdf.close()
    if incremental:
        v.change_layout(path)
    return path


def _saved(paths) -> str:
    return "\n".join(open(p, encoding="utf-8").read() for p in paths)


def _assert_private(text: str, folder: str) -> None:
    """No searched-for word, no password, and no part of the folder's path."""
    lowered = text.lower()
    for secret in (TERM, PASSWORD, FOLDER):
        assert secret.lower() not in lowered, secret
    for part in os.path.abspath(folder).replace("\\", "/").split("/"):
        if len(part) > 3:
            assert part.lower() not in lowered, part


# ── Redact ───────────────────────────────────────────────────────────────

@pytest.fixture
def redacted(tmp_path):
    folder = str(tmp_path / FOLDER)
    src = _source(folder, encrypt=True)
    out = os.path.join(folder, "statement_redacted.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], password=PASSWORD)
    return folder, out, redact_result_payload(res)


def test_the_saved_report_holds_no_word_password_or_folder(redacted):
    folder, out, payload = redacted
    answer = removal_report.save_payload(payload)
    assert answer == {"success": True, "files": ["statement_redacted.removal-report.json",
                                                 "statement_redacted.removal-report.txt"]}
    paths = [os.path.join(folder, name) for name in answer["files"]]
    _assert_private(_saved(paths), folder)
    doc = json.load(open(paths[0], encoding="utf-8"))
    assert doc["tool"] == "Redact" and doc["verified"] is True
    assert doc["source"]["file"] == "statement.pdf" and doc["output"]["file"] == \
        "statement_redacted.pdf"
    assert len(doc["source"]["sha256"]) == 64 and len(doc["output"]["sha256"]) == 64
    assert doc["source"]["sha256"] != doc["output"]["sha256"]
    assert all(set(c) == {"id", "description", "passed", "pages"} for c in doc["checks"])
    assert {c["id"] for c in doc["checks"]} >= {"redaction.page_text", "redaction.docinfo",
                                                "redaction.term_sweep", "redaction.one_version"}
    assert set(doc) == {"report", "formatVersion", "tool", "time", "source", "output", "verified",
                        "removed", "foundNotTargeted", "notChecked", "earlierVersions",
                        "network", "checks"}


def test_what_the_redaction_report_says(redacted):
    _folder, _out, payload = redacted
    proof = payload["proof"]
    assert proof["tool"] == "Redact"
    assert {"what": "1 place blacked out", "pages": [1], "surface": "page_content",
            "where": "Page 1"} in proof["removed"]
    assert any(item["surface"] == "docinfo" and item["pages"] == [] and item["where"] == ""
               for item in proof["removed"])
    assert any(item["surface"] == "embedded_files" for item in proof["removed"])
    [attached] = [i for i in proof["notChecked"] if i["what"].startswith("1 attached file")]
    assert attached == {
        "what": "1 attached file was searched as it is stored, and not opened.",
        "pages": [], "where": "",
        "next": "Open it to check it, or run Sanitize with “Embedded files / attachments” ticked "
                "to remove it."}
    [blank] = [i for i in proof["notChecked"] if i["pages"] == [2]]
    assert blank["what"].startswith("No text to search") and blank["where"] == "Page 2"
    assert proof["notTargeted"] == []
    assert proof["earlierVersions"] == "The source kept no earlier versions; this copy keeps none."
    assert proof["network"] == "Network: not watched (the network guard was not running)."
    words = {c["id"]: c["description"] for c in proof["checks"]}
    assert words["redaction.page_text"] == ("None of the searched-for words can be read in the "
                                            "text of the pages")
    assert words["redaction.one_version"] == "The new file keeps no earlier versions of itself"
    assert all(c["passed"] for c in proof["checks"])


def test_the_report_says_what_the_source_kept_and_the_network_line(tmp_path):
    network_guard.install()
    try:
        folder = str(tmp_path / FOLDER)
        src = _source(folder, incremental=True)
        res = redact_pdf(src, os.path.join(folder, "out.pdf"), search_terms=[TERM])
    finally:
        network_guard.uninstall()
    proof = redact_result_payload(res)["proof"]
    assert proof["earlierVersions"] == "The source kept 1 earlier version; this copy keeps none."
    assert proof["network"] == "Network: none used during this run."
    assert res.verification["network"]["watched"] is True


def test_a_repaired_source_is_listed_as_not_checked(tmp_path):
    src = v.break_last_xref(v.rewrite_page(v.base(tmp_path / "src.pdf")))
    res = redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["closed"])
    proof = redact_result_payload(res)["proof"]
    assert proof["earlierVersions"] == ("Whether the source kept earlier versions could not be "
                                        "checked; this copy keeps none.")
    assert {"what": "Whether the source kept earlier versions of itself: the file is damaged and "
                    "was repaired to be read, so its saved versions could not be counted.",
            "pages": [], "where": "", "next": None} in proof["notChecked"]


def test_pages_with_the_same_count_are_one_line():
    """A run over many pages lists each count once, with its pages."""
    from pdf_ops import RedactResult, redaction_proof
    result = RedactResult(input_path="a.pdf", output_path="b.pdf", redaction_count=9,
                          pages_affected=5, verification={"checks": []},
                          removed_by_page={1: 2, 2: 2, 3: 2, 9: 2, 5: 1})
    assert redaction_proof(result)["removed"] == [
        {"what": "2 places blacked out on each", "pages": [1, 2, 3, 9],
         "surface": "page_content", "where": "Pages 1-3 and 9"},
        {"what": "1 place blacked out", "pages": [5], "surface": "page_content",
         "where": "Page 5"}]


def test_a_report_never_overwrites(redacted):
    folder, _out, payload = redacted
    taken = os.path.join(folder, "statement_redacted.removal-report.txt")
    with open(taken, "w", encoding="utf-8") as fh:
        fh.write("mine")
    first = removal_report.save_payload(payload)["files"]
    second = removal_report.save_payload(payload)["files"]
    assert first == ["statement_redacted (2).removal-report.json",
                     "statement_redacted (2).removal-report.txt"]
    assert second == ["statement_redacted (3).removal-report.json",
                      "statement_redacted (3).removal-report.txt"]
    assert open(taken, encoding="utf-8").read() == "mine"


def test_the_text_report_reads_in_order(redacted):
    folder, _out, payload = redacted
    files = removal_report.save_payload(payload)["files"]
    text = open(os.path.join(folder, files[1]), encoding="utf-8").read()
    heads = ["Proof of removal: Redact", "Removed and confirmed gone", "Found but not targeted",
             "Not checked", "Network: ", "Checks"]
    assert [text.index(h) for h in heads] == sorted(text.index(h) for h in heads)
    assert "Page 1: 1 place blacked out" in text
    assert "  passed  redaction.page_text" in text


def test_nothing_to_save_without_a_finished_run():
    assert removal_report.save_payload(None) == {
        "success": False, "error": "There is no finished run to save a report for."}
    assert removal_report.save_payload({"success": False, "verification": {}})["success"] is False


# ── Sanitize ─────────────────────────────────────────────────────────────

def test_the_sanitize_report_lists_what_it_left_with_a_next_step(tmp_path):
    folder = str(tmp_path / FOLDER)
    src = _source(folder, incremental=True)
    out = os.path.join(folder, "statement_clean.pdf")
    payload = sanitize_payload(src, out, {"metadata": False})
    assert payload["success"] is True
    proof = payload["proof"]
    assert proof["tool"] == "Sanitize"
    assert {"what": "2 attached files", "pages": [], "surface": "embedded_files",
            "where": ""} in proof["removed"]
    assert {"what": "Document metadata present", "pages": [], "where": "",
            "next": "Run Sanitize with “Document & XMP metadata” ticked."} in proof["notTargeted"]
    assert proof["earlierVersions"] == "The source kept 1 earlier version; this copy keeps none."
    words = {c["id"]: c["description"] for c in proof["checks"]}
    assert words["sanitize.files_embedded"] == "A new scan finds no attached files"
    assert words["sanitize.one_version"] == "The clean copy keeps no earlier versions of itself"

    answer = removal_report.save_payload(payload)
    assert answer["files"] == ["statement_clean.removal-report.json",
                               "statement_clean.removal-report.txt"]
    text = _saved(os.path.join(folder, f) for f in answer["files"])
    _assert_private(text, folder)                     # the title holds the term; not here
    assert "Next step: Run Sanitize with “Document & XMP metadata” ticked." in text


def test_a_refused_sanitize_has_no_proof(tmp_path, monkeypatch):
    import pdf_verify
    folder = str(tmp_path / FOLDER)
    src = _source(folder)
    monkeypatch.setattr(pdf_verify, "_one_version_check",
                        lambda cid, report, path, source=None: pdf_verify.VerificationCheck(
                            id=cid, description="x", passed=False,
                            evidence=["kept (document-level)"]))
    payload = sanitize_payload(src, os.path.join(folder, "out.pdf"))
    assert payload["success"] is False and "proof" not in payload
    assert removal_report.save_payload(payload)["success"] is False


def test_every_check_has_plain_words():
    """Each check the two tools make has words of its own; the code-like
    description is never what the panel shows."""
    for cid in removal_report.CHECK_WORDS:
        assert removal_report.check_in_words({"id": cid, "description": "code"}) != "code"
    assert removal_report.check_in_words({"id": "sanitize.sweep_metadata", "description": "x"}) \
        == "A look at every object in the file finds no document details"
    assert removal_report.check_in_words({"id": "redaction.new_one", "description": "Plain"}) \
        == "Plain"
