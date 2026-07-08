"""Tests for pdf_analyze.py — the privacy/security auditor and sanitiser.

Covers ANL-02 (sanitiser must follow ``/Next`` action chains), ANL-01
(in-place sanitise on Windows), ANL-03 (invisible render-mode-3 text
detection), ANL-04 (embedded-file detection + removal), and the Phase-1
image metadata analyzer (analyze_image / analyze_file). pdf_analyze
imports no Qt, so these run Qt-free (per CLAUDE.md).
"""

import os

import pikepdf
import pytest
from PIL import Image

from pdf_analyze import (
    AnalysisResult,
    _dms_to_decimal,
    _scan_image_thumbnail,
    analyze_document,
    analyze_file,
    analyze_image,
    sanitize_pdf,
)

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


# ═══════════════════════════════════════════════════════════════════
#  Image metadata analyzer (analyze_image / analyze_file)
# ═══════════════════════════════════════════════════════════════════

# EXIF tag ids used to build fixtures (see Pillow ExifTags.TAGS).
_TAG_MAKE, _TAG_MODEL, _TAG_DATETIME = 0x010F, 0x0110, 0x0132
_TAG_GPSINFO = 0x8825


def _jpeg(path, exif=None, *, color=(10, 120, 200)) -> str:
    """Write a tiny JPEG, optionally carrying a PIL ``Image.Exif`` block."""
    img = Image.new("RGB", (48, 32), color)
    if exif is None:
        img.save(str(path))
    else:
        img.save(str(path), exif=exif)
    return str(path)


def _finding(report: dict, fid: str):
    return next((f for f in report["findings"] if f["id"] == fid), None)


class TestAnalyzeImage:
    def test_gps_jpeg_yields_high_location(self, tmp_path):
        exif = Image.Exif()
        exif[_TAG_MAKE] = "Canon"
        exif[_TAG_MODEL] = "EOS"
        # 40°44'54.38"N, 73°59'8.5"W  ->  ~40.748439, ~-73.985694
        exif[_TAG_GPSINFO] = {1: "N", 2: (40.0, 44.0, 54.38),
                              3: "W", 4: (73.0, 59.0, 8.5)}
        src = _jpeg(tmp_path / "gps.jpg", exif)

        report = analyze_image(src)
        assert report["overallRisk"] == "high"
        loc = _finding(report, "location.gps")
        assert loc is not None and loc["severity"] == "high"
        # Decoded coordinates are surfaced (to ~4 decimal places).
        joined = " ".join(loc["items"]) + " " + loc["detail"]
        assert "40.7484" in joined
        assert "-73.9856" in joined or "-73.9857" in joined

    def test_camera_jpeg_no_gps(self, tmp_path):
        exif = Image.Exif()
        exif[_TAG_MAKE] = "Nikon"
        exif[_TAG_MODEL] = "D3500"
        exif[_TAG_DATETIME] = "2020:01:01 00:00:00"
        src = _jpeg(tmp_path / "cam.jpg", exif)

        report = analyze_image(src)
        ids = _finding_ids(report)
        assert "location.gps" not in ids
        cam = _finding(report, "metadata.camera")
        assert cam is not None and cam["severity"] == "medium"
        assert any("Nikon" in it and "D3500" in it for it in cam["items"])
        assert report["overallRisk"] == "medium"

    def test_clean_jpeg_no_findings(self, tmp_path):
        report = analyze_image(_jpeg(tmp_path / "clean.jpg"))
        assert report["findings"] == []
        assert report["overallRisk"] == "info"

    def test_png_runs_clean(self, tmp_path):
        src = str(tmp_path / "x.png")
        Image.new("RGB", (20, 20), (0, 0, 0)).save(src)
        report = analyze_image(src)
        assert report["findings"] == []
        assert report["overallRisk"] == "info"

    def test_rejects_non_image(self, tmp_path):
        bad = str(tmp_path / "notimg.jpg")
        with open(bad, "wb") as fh:
            fh.write(b"this is plainly not an image")
        with pytest.raises(ValueError):
            analyze_image(bad)

    def test_output_shape_matches_document(self, tmp_path):
        # The image report exposes the same top-level keys as a PDF report,
        # so the frontend can consume either shape unchanged.
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        pdf_src = str(tmp_path / "d.pdf")
        pdf.save(pdf_src)
        pdf.close()

        img_report = analyze_image(_jpeg(tmp_path / "s.jpg"))
        assert set(img_report) == set(analyze_document(pdf_src))


class TestAnalyzeFileDispatch:
    def test_pdf_routes_to_analyze_document(self, tmp_path):
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        src = str(tmp_path / "d.pdf")
        pdf.save(src)
        pdf.close()
        assert analyze_file(src) == analyze_document(src)

    def test_jpeg_routes_to_analyze_image(self, tmp_path):
        exif = Image.Exif()
        exif[_TAG_GPSINFO] = {1: "N", 2: (1.0, 0.0, 0.0),
                              3: "E", 4: (1.0, 0.0, 0.0)}
        src = _jpeg(tmp_path / "g.jpg", exif)
        assert analyze_file(src) == analyze_image(src)
        assert "location.gps" in _finding_ids(analyze_file(src))

    def test_unsupported_type_raises(self, tmp_path):
        bad = str(tmp_path / "note.txt")
        with open(bad, "wb") as fh:
            fh.write(b"plain text, not a supported file type")
        with pytest.raises(ValueError):
            analyze_file(bad)


class TestImageHelpers:
    # The DMS→decimal decode is version-independent, so pin it directly.
    def test_dms_to_decimal_north_east_positive(self):
        assert _dms_to_decimal((40.0, 44.0, 54.38), "N") == pytest.approx(40.748439, abs=1e-5)
        assert _dms_to_decimal((73.0, 59.0, 8.5), "E") == pytest.approx(73.985694, abs=1e-5)

    def test_dms_to_decimal_south_west_negative(self):
        assert _dms_to_decimal((40.0, 44.0, 54.38), "S") == pytest.approx(-40.748439, abs=1e-5)
        assert _dms_to_decimal((73.0, 59.0, 8.5), "W") == pytest.approx(-73.985694, abs=1e-5)

    def test_thumbnail_scanner_flags_ifd1_with_thumbnail(self):
        res = AnalysisResult()
        _scan_image_thumbnail({513: 100, 514: 2048}, res)
        assert [f.id for f in res.findings] == ["content.thumbnail"]

    def test_thumbnail_scanner_ignores_empty_ifd1(self):
        res = AnalysisResult()
        _scan_image_thumbnail({}, res)
        assert res.findings == []
