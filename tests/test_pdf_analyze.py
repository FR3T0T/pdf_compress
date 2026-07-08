"""Tests for pdf_analyze.py — the privacy/security auditor and sanitiser.

Covers ANL-02 (sanitiser must follow ``/Next`` action chains), ANL-01
(in-place sanitise on Windows), ANL-03 (invisible render-mode-3 text
detection), and ANL-04 (embedded-file detection + removal). pdf_analyze
imports no Qt, so these run Qt-free (per CLAUDE.md).
"""

import os

import pikepdf
import pytest

from pdf_analyze import analyze_document, sanitize_pdf

try:
    import fitz  # noqa: F401  (PyMuPDF — optional; drives the ANL-03 scan)
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

# ── Builders ─────────────────────────────────────────────────────────────


def _uri_action() -> pikepdf.Dictionary:
    return pikepdf.Dictionary(S=pikepdf.Name("/URI"),
                              URI=pikepdf.String("https://example.com/"))


def _js_action() -> pikepdf.Dictionary:
    return pikepdf.Dictionary(S=pikepdf.Name("/JavaScript"),
                              JS=pikepdf.String("app.alert('pwned');"))


def _launch_action() -> pikepdf.Dictionary:
    return pikepdf.Dictionary(S=pikepdf.Name("/Launch"),
                              F=pikepdf.String("calc.exe"))


def _make_next_chain_pdf(path, *, head: pikepdf.Dictionary,
                         buried: pikepdf.Dictionary) -> str:
    """One-page PDF with a single Link annotation whose ``/A`` is *head*
    and whose ``/A``'s ``/Next`` is *buried* — the ANL-02 evasion shape."""
    head["/Next"] = buried
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, 612, 792],
    ))
    pdf.pages.append(page)
    annot = pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Link"),
        Rect=[0, 0, 100, 100],
        A=head,
    )
    pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annot)])
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _finding_ids(report: dict) -> set:
    return {f["id"] for f in report["findings"]}


# ── Tests ────────────────────────────────────────────────────────────────


class TestSanitizeNextChain:
    def test_analyzer_flags_buried_js(self, tmp_path):
        # Baseline: the detector already walks /Next and flags the buried JS
        # alongside the benign link — this is what the sanitiser must match.
        src = _make_next_chain_pdf(tmp_path / "evasion.pdf",
                                   head=_uri_action(), buried=_js_action())
        ids = _finding_ids(analyze_document(src))
        assert "scripts.js" in ids
        assert "links.uri" in ids

    def test_sanitize_removes_buried_js_keeps_link(self, tmp_path):
        # The core ANL-02 fix: JS chained after a kept /URI head is excised
        # (and counted), while the URI link itself survives.
        src = _make_next_chain_pdf(tmp_path / "evasion.pdf",
                                   head=_uri_action(), buried=_js_action())
        out = str(tmp_path / "clean.pdf")
        result = sanitize_pdf(src, out,
                              {"javascript": True, "external_links": False})

        assert result["removed"].get("annot_javascript", 0) >= 1
        assert result["total_removed"] >= 1

        ids = _finding_ids(analyze_document(out))
        assert "scripts.js" not in ids     # JS gone…
        assert "links.uri" in ids          # …link preserved

    def test_js_gate_respected(self, tmp_path):
        # With javascript off, the buried JS is deliberately left alone.
        src = _make_next_chain_pdf(tmp_path / "evasion.pdf",
                                   head=_uri_action(), buried=_js_action())
        out = str(tmp_path / "keep.pdf")
        result = sanitize_pdf(src, out,
                              {"javascript": False, "external_links": False})

        assert result["removed"].get("annot_javascript", 0) == 0
        assert "scripts.js" in _finding_ids(analyze_document(out))

    def test_buried_launch_gated_on_launch_actions(self, tmp_path):
        # The chain walk isn't JS-only: a /Launch behind a /URI head is
        # excised when launch_actions is on, link still preserved.
        src = _make_next_chain_pdf(tmp_path / "launch.pdf",
                                   head=_uri_action(), buried=_launch_action())
        out = str(tmp_path / "clean.pdf")
        result = sanitize_pdf(src, out, {"launch_actions": True,
                                         "javascript": False,
                                         "external_links": False})

        assert result["removed"].get("launch_action", 0) >= 1
        ids = _finding_ids(analyze_document(out))
        assert "actions.launch" not in ids
        assert "links.uri" in ids

    def test_plain_uri_untouched(self, tmp_path):
        # A /URI with no dangerous /Next must not be disturbed.
        path = str(tmp_path / "plain.pdf")
        pdf = pikepdf.Pdf.new()
        page = pikepdf.Page(pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792]))
        pdf.pages.append(page)
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Link"),
            Rect=[0, 0, 100, 100], A=_uri_action())
        pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annot)])
        pdf.save(path)
        pdf.close()

        out = str(tmp_path / "clean.pdf")
        result = sanitize_pdf(path, out,
                              {"javascript": True, "external_links": False})

        assert "annot_javascript" not in result["removed"]
        assert "links.uri" in _finding_ids(analyze_document(out))


# ── ANL-01: in-place sanitise (os.replace over a still-open handle) ────────


def _one_page_pdf(path) -> str:
    """One-page PDF carrying an /OpenAction JavaScript, so sanitise has
    something to strip and the before/after is observable."""
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792])))
    pdf.Root["/OpenAction"] = pikepdf.Dictionary(
        S=pikepdf.Name("/JavaScript"), JS=pikepdf.String("app.alert(1);"))
    pdf.save(str(path))
    pdf.close()
    return str(path)


class TestSanitizeInPlace:
    def test_in_place_replaces_file(self, tmp_path):
        # output_path == input_path must succeed and actually rewrite the file
        # (on Windows this used to raise PermissionError [WinError 5]).
        p = _one_page_pdf(tmp_path / "doc.pdf")
        assert "scripts.js" in _finding_ids(analyze_document(p))
        result = sanitize_pdf(p, p, {"javascript": True, "auto_actions": True})
        assert result["output"] == p
        assert os.path.isfile(p)
        assert "scripts.js" not in _finding_ids(analyze_document(p))

    def test_distinct_output_still_works(self, tmp_path):
        p = _one_page_pdf(tmp_path / "doc.pdf")
        out = str(tmp_path / "clean.pdf")
        sanitize_pdf(p, out, {"javascript": True, "auto_actions": True})
        assert os.path.isfile(out)
        assert "scripts.js" in _finding_ids(analyze_document(p))       # original intact
        assert "scripts.js" not in _finding_ids(analyze_document(out))

    def test_save_failure_cleans_tmp_and_keeps_original(self, tmp_path, monkeypatch):
        p = _one_page_pdf(tmp_path / "doc.pdf")
        with open(p, "rb") as fh:
            original = fh.read()

        def _boom(self, *a, **k):
            raise RuntimeError("simulated save failure")

        monkeypatch.setattr(pikepdf.Pdf, "save", _boom)
        with pytest.raises(RuntimeError):
            sanitize_pdf(p, p, {})

        with open(p, "rb") as fh:
            assert fh.read() == original                     # original untouched
        assert [f for f in os.listdir(tmp_path) if f != "doc.pdf"] == []  # tmp cleaned


# ── ANL-03: invisible (render mode 3) text detection ───────────────────────


def _content_pdf(path, contents, *, resources=None) -> str:
    """One-page PDF whose /Contents is *contents* (bytes, or a list of bytes
    for multiple content streams). *resources*, if given, is a callable
    (pdf) -> resource dict."""
    pdf = pikepdf.Pdf.new()
    pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[0]
    if isinstance(contents, (list, tuple)):
        page["/Contents"] = pikepdf.Array([pdf.make_stream(c) for c in contents])
    else:
        page["/Contents"] = pdf.make_stream(contents)
    if resources is not None:
        page["/Resources"] = resources(pdf)
    pdf.save(str(path))
    pdf.close()
    return str(path)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
class TestInvisibleText:
    def test_render_mode_3_in_second_stream_detected(self, tmp_path):
        # The `3 Tr` lives in the 2nd content stream — the old [0]-only check
        # missed this (common after incremental edits).
        p = _content_pdf(tmp_path / "m.pdf",
                         [b"BT /F1 12 Tf (hi) Tj ET", b"BT 3 Tr (secret) Tj ET"])
        assert "content.invisible_text" in _finding_ids(analyze_document(p))

    def test_newline_prefixed_detected(self, tmp_path):
        p = _content_pdf(tmp_path / "nl.pdf", b"BT\n3 Tr\n(x) Tj ET")
        assert "content.invisible_text" in _finding_ids(analyze_document(p))

    def test_13_tr_not_flagged(self, tmp_path):
        # Token boundary: "13 Tr" (render mode 13) must not read as "3 Tr".
        p = _content_pdf(tmp_path / "13.pdf", b"BT 13 Tr (x) Tj ET")
        assert "content.invisible_text" not in _finding_ids(analyze_document(p))

    def test_clean_not_flagged(self, tmp_path):
        p = _content_pdf(tmp_path / "clean.pdf", b"BT /F1 12 Tf (visible) Tj ET")
        assert "content.invisible_text" not in _finding_ids(analyze_document(p))

    def test_form_xobject_detected(self, tmp_path):
        def _res(pdf):
            xobj = pdf.make_stream(b"BT 3 Tr (hidden) Tj ET")
            xobj["/Type"] = pikepdf.Name("/XObject")
            xobj["/Subtype"] = pikepdf.Name("/Form")
            xobj["/BBox"] = [0, 0, 100, 100]
            return pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=xobj))

        p = _content_pdf(tmp_path / "xo.pdf", b"q /Fm0 Do Q", resources=_res)
        assert "content.invisible_text" in _finding_ids(analyze_document(p))


# ── ANL-04: embedded-file detection + removal ──────────────────────────────


def _embedded_stream(pdf, data: bytes = b"SECRET-PAYLOAD"):
    ef = pdf.make_stream(data)
    ef["/Type"] = pikepdf.Name("/EmbeddedFile")
    return ef


def _no_ef_stream_survives(path) -> bool:
    """True if the saved PDF holds no embedded-file stream — i.e. the actual
    payload was GC'd, not just the name-tree entry."""
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            try:
                if isinstance(obj, pikepdf.Dictionary) and "/EF" in obj:
                    return False
                t = obj.get("/Type") if hasattr(obj, "get") else None
                if t is not None and str(t) == "/EmbeddedFile":
                    return False
            except Exception:
                continue
    return True


def _blank_page(pdf):
    pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792])))


class TestEmbeddedFiles:
    def test_file_attachment_detected_and_removed(self, tmp_path):
        # An embedded file that rides ONLY on a /FileAttachment annotation
        # (no name-tree entry) — the sanitiser used to leave it entirely.
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        filespec = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Filespec"), F=pikepdf.String("secret.txt"),
            EF=pikepdf.Dictionary(F=_embedded_stream(pdf))))
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/FileAttachment"),
            Rect=[0, 0, 20, 20], FS=filespec)
        pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annot)])
        src = str(tmp_path / "attach.pdf")
        pdf.save(src)
        pdf.close()

        assert "files.embedded" in _finding_ids(analyze_document(src))

        out = str(tmp_path / "clean.pdf")
        result = sanitize_pdf(src, out, {"embedded_files": True})
        assert result["removed"].get("file_attachment_annot", 0) >= 1
        assert _no_ef_stream_survives(out)          # payload actually gone
        assert "files.embedded" not in _finding_ids(analyze_document(out))

    def test_filespec_without_type_detected_and_af_stripped(self, tmp_path):
        # /Type is optional on a filespec; here it's omitted and the file is
        # referenced via a root /AF array.
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        spec = pdf.make_indirect(pikepdf.Dictionary(   # no /Type
            F=pikepdf.String("noType.bin"),
            EF=pikepdf.Dictionary(F=_embedded_stream(pdf))))
        pdf.Root["/AF"] = pikepdf.Array([spec])
        src = str(tmp_path / "notype.pdf")
        pdf.save(src)
        pdf.close()

        assert "files.embedded" in _finding_ids(analyze_document(src))

        out = str(tmp_path / "clean.pdf")
        result = sanitize_pdf(src, out, {"embedded_files": True})
        assert result["removed"].get("associated_file", 0) >= 1
        assert _no_ef_stream_survives(out)
        assert "files.embedded" not in _finding_ids(analyze_document(out))

    def test_kids_name_tree_walked(self, tmp_path):
        # /EmbeddedFiles as a /Kids-structured tree (intermediate nodes hold
        # /Kids, not /Names) — the old walk only read a root-level /Names.
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        spec = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Filespec"), F=pikepdf.String("kid.txt"),
            EF=pikepdf.Dictionary(F=_embedded_stream(pdf))))
        leaf = pdf.make_indirect(pikepdf.Dictionary(
            Names=pikepdf.Array([pikepdf.String("kid.txt"), spec])))
        intermediate = pdf.make_indirect(pikepdf.Dictionary(
            Kids=pikepdf.Array([leaf])))
        pdf.Root["/Names"] = pikepdf.Dictionary(EmbeddedFiles=intermediate)
        src = str(tmp_path / "kids.pdf")
        pdf.save(src)
        pdf.close()

        report = analyze_document(src)
        emb = next((f for f in report["findings"] if f["id"] == "files.embedded"), None)
        assert emb is not None
        assert any("kid.txt" in it for it in emb["items"])   # name from the /Kids leaf

    def test_clean_pdf_reports_and_removes_nothing(self, tmp_path):
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        src = str(tmp_path / "clean.pdf")
        pdf.save(src)
        pdf.close()

        assert "files.embedded" not in _finding_ids(analyze_document(src))
        out = str(tmp_path / "out.pdf")
        result = sanitize_pdf(src, out, {"embedded_files": True})
        assert result["removed"].get("file_attachment_annot", 0) == 0
        assert result["removed"].get("associated_file", 0) == 0
