"""Tests for pdf_analyze.py — the privacy/security auditor and sanitiser.

Covers ANL-02 (sanitiser must follow ``/Next`` action chains), ANL-01
(in-place sanitise on Windows), ANL-03 (invisible render-mode-3 text
detection), ANL-04 (embedded-file detection + removal), the Phase-1 image
metadata analyzer (analyze_image / analyze_file), and the embedded-image
EXIF scanner (GPS/camera metadata inside JPEGs stored in a PDF).
pdf_analyze imports no Qt, so these run Qt-free (per CLAUDE.md).
"""

import os

import pikepdf
import pytest
from PIL import Image

from pdf_analyze import (
    DEFAULT_SANITIZE,
    AnalysisResult,
    _dms_to_decimal,
    _scan_image_thumbnail,
    analyze_document,
    analyze_file,
    analyze_image,
    sanitize_pdf,
    strip_file,
    strip_image_metadata,
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


# ═══════════════════════════════════════════════════════════════════
#  Embedded-image EXIF (photos dropped into a PDF keep their metadata)
# ═══════════════════════════════════════════════════════════════════
#
#  These need PyMuPDF to embed a JPEG as a /DCTDecode stream (the form that
#  preserves the original EXIF bytes). The empirical round-trip — GPS
#  written by PIL, embedded, then recovered by the scanner — is the whole
#  point of the feature, so the tests exercise it end-to-end.


def _gps_jpeg(path, *, with_gps: bool = True, camera: bool = True) -> str:
    exif = Image.Exif()
    if camera:
        exif[0x010F] = "Canon"                       # Make
        exif[0x0110] = "EOS 5D"                       # Model
        exif[0x0132] = "2021:07:04 09:30:00"          # DateTime
    if with_gps:
        # 40°44'54.38"N, 73°59'8.5"W -> ~40.748439, ~-73.985694
        exif[0x8825] = {1: "N", 2: (40.0, 44.0, 54.38),
                        3: "W", 4: (73.0, 59.0, 8.5)}
    kwargs = {"quality": 90}
    if len(exif):
        kwargs["exif"] = exif
    Image.new("RGB", (64, 48), (10, 120, 200)).save(str(path), **kwargs)
    return str(path)


def _embed_jpeg_pdf(jpeg_path: str, out_path, *, pages: int = 1) -> str:
    """Embed a JPEG into a PDF via PyMuPDF (stores it as a /DCTDecode stream,
    preserving the original bytes + EXIF)."""
    doc = fitz.open()
    with open(jpeg_path, "rb") as fh:
        data = fh.read()
    for _ in range(pages):
        page = doc.new_page(width=300, height=300)
        page.insert_image(fitz.Rect(20, 20, 200, 160), stream=data)
    doc.save(str(out_path))
    doc.close()
    return str(out_path)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestEmbeddedImageExif:
    def test_gps_jpeg_embedded_yields_high_location(self, tmp_path):
        jpg = _gps_jpeg(tmp_path / "geo.jpg")
        pdf = _embed_jpeg_pdf(jpg, tmp_path / "geo.pdf")

        report = analyze_document(pdf)
        assert report["overallRisk"] == "high"
        loc = _finding(report, "location.embedded_image")
        assert loc is not None and loc["severity"] == "high"
        # EXIF survived embedding: the decoded coordinates are surfaced.
        text = " ".join(loc["items"])
        assert "40.7484" in text
        assert "-73.9856" in text or "-73.9857" in text
        assert "page 1" in text

    def test_camera_metadata_surfaced_for_embedded_image(self, tmp_path):
        jpg = _gps_jpeg(tmp_path / "cam.jpg", with_gps=False)
        pdf = _embed_jpeg_pdf(jpg, tmp_path / "cam.pdf")

        report = analyze_document(pdf)
        assert "location.embedded_image" not in _finding_ids(report)
        cam = _finding(report, "metadata.embedded_image")
        assert cam is not None and cam["severity"] == "medium"
        assert any("Canon" in it and "EOS 5D" in it for it in cam["items"])

    def test_clean_embedded_jpeg_has_no_finding(self, tmp_path):
        jpg = _gps_jpeg(tmp_path / "clean.jpg", with_gps=False, camera=False)
        pdf = _embed_jpeg_pdf(jpg, tmp_path / "clean.pdf")

        ids = _finding_ids(analyze_document(pdf))
        assert "location.embedded_image" not in ids
        assert "metadata.embedded_image" not in ids

    def test_pdf_without_images_has_no_finding_and_no_error(self, tmp_path):
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "just some text, no images")
        src = str(tmp_path / "text.pdf")
        doc.save(src)
        doc.close()

        ids = _finding_ids(analyze_document(src))
        assert "location.embedded_image" not in ids
        assert "metadata.embedded_image" not in ids

    def test_same_image_on_two_pages_reported_once(self, tmp_path):
        # The image reused on both pages is one shared XObject — dedup must
        # collapse it to a single finding entry, not two.
        jpg = _gps_jpeg(tmp_path / "dup.jpg")
        pdf = _embed_jpeg_pdf(jpg, tmp_path / "dup.pdf", pages=2)

        loc = _finding(analyze_document(pdf), "location.embedded_image")
        assert loc is not None
        assert loc["count"] == 1
        assert len(loc["items"]) == 1


# ═══════════════════════════════════════════════════════════════════
#  Strip image metadata (the "remove" half — Phase 3)
# ═══════════════════════════════════════════════════════════════════
#
#  detect → remove completion: strip_image_metadata writes a clean copy of
#  the same format, and re-scanning the OUTPUT must find nothing. Uses the
#  Phase-1 `_gps_jpeg` builder (module-level, defined above).


class TestStripImageMetadata:
    def test_strip_gps_jpeg_output_rescans_clean(self, tmp_path):
        src = _gps_jpeg(tmp_path / "geo.jpg")            # GPS + camera EXIF
        assert _finding_ids(analyze_image(src))          # sanity: source is dirty
        out = str(tmp_path / "geo_clean.jpg")

        result = strip_image_metadata(src, out)
        assert result["removed"].get("gps") == 1
        assert result["total_removed"] >= 1
        assert result["output"] == out

        # The whole point: the output carries no metadata any more.
        rescan = analyze_image(out)
        assert rescan["findings"] == []
        assert rescan["overallRisk"] == "info"

    def test_output_is_valid_same_size_and_mode(self, tmp_path):
        src = _gps_jpeg(tmp_path / "geo.jpg")
        out = str(tmp_path / "clean.jpg")
        strip_image_metadata(src, out)
        with Image.open(src) as before, Image.open(out) as after:
            assert after.format == "JPEG"
            assert after.size == before.size
            assert after.mode == before.mode

    def test_strip_clean_jpeg_removes_nothing(self, tmp_path):
        src = _gps_jpeg(tmp_path / "clean.jpg", with_gps=False, camera=False)
        out = str(tmp_path / "clean_out.jpg")
        result = strip_image_metadata(src, out)
        assert result["total_removed"] == 0
        assert result["removed"] == {}
        with Image.open(out) as im:
            assert im.size == (64, 48)      # still a valid image

    def test_strip_png_removes_gps(self, tmp_path):
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (1.0, 2.0, 3.0), 3: "E", 4: (4.0, 5.0, 6.0)}
        src = str(tmp_path / "geo.png")
        Image.new("RGB", (30, 20), (5, 5, 5)).save(src, exif=exif)
        assert "location.gps" in _finding_ids(analyze_image(src))
        out = str(tmp_path / "geo_clean.png")

        result = strip_image_metadata(src, out)
        assert result["removed"].get("gps") == 1
        assert analyze_image(out)["findings"] == []
        with Image.open(out) as im:
            assert im.format == "PNG"
            assert im.size == (30, 20)

    def test_rejects_non_image(self, tmp_path):
        bad = str(tmp_path / "note.txt")
        with open(bad, "wb") as fh:
            fh.write(b"not an image at all")
        with pytest.raises(ValueError):
            strip_image_metadata(bad, str(tmp_path / "out.jpg"))


class TestStripFileDispatch:
    def test_image_routes_to_strip(self, tmp_path):
        src = _gps_jpeg(tmp_path / "g.jpg")
        out = str(tmp_path / "g_clean.jpg")
        result = strip_file(src, out)
        assert result["removed"].get("gps") == 1
        assert analyze_image(out)["findings"] == []

    def test_pdf_routes_to_sanitize_with_options(self, tmp_path):
        # A JS /OpenAction is removed by sanitize_pdf; strip_file must route a
        # PDF there AND pass the options through (same removed report).
        pdf = pikepdf.Pdf.new()
        _blank_page(pdf)
        pdf.Root.OpenAction = _js_action()
        src = str(tmp_path / "js.pdf")
        pdf.save(src)
        pdf.close()

        via_strip = strip_file(src, str(tmp_path / "a.pdf"), {"javascript": True})
        via_direct = sanitize_pdf(src, str(tmp_path / "b.pdf"), {"javascript": True})
        assert via_strip["removed"] == via_direct["removed"]
        assert via_strip["removed"].get("open_action") == 1

    def test_unsupported_type_raises(self, tmp_path):
        bad = str(tmp_path / "note.txt")
        with open(bad, "wb") as fh:
            fh.write(b"plain text, unsupported")
        with pytest.raises(ValueError):
            strip_file(bad, str(tmp_path / "out"))

# ═══════════════════════════════════════════════════════════════════
#  Metadata is found and removed by presence, never by parsing (#101)
# ═══════════════════════════════════════════════════════════════════

_XMP_HEAD = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
             b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
             b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">')
_XMP_OK = (_XMP_HEAD
           + b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
             b'<dc:creator>Alex Example</dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>')
# Undeclared dc: prefix and a root element that is never closed.
_XMP_MALFORMED = (_XMP_HEAD + b'<rdf:Description rdf:about=""><dc:creator>Alex Example'
                              b'</dc:creator></rdf:Description></rdf:RDF>\n')

_ALL_OFF = {k: False for k in DEFAULT_SANITIZE}
_METADATA_ONLY = {**_ALL_OFF, "metadata": True}


def _xmp_stream(pdf, data: bytes, *, typed: bool = True):
    st = pdf.make_stream(data)
    if typed:
        st["/Type"] = pikepdf.Name("/Metadata")
        st["/Subtype"] = pikepdf.Name("/XML")
    return st


def _undecodable_xmp_stream(pdf):
    """A metadata stream labelled FlateDecode whose bytes are not deflate
    data: nothing can decode it, but it is still sitting in the file."""
    st = _xmp_stream(pdf, b"x")
    st.write(b"not deflate data: Alex Example", filter=pikepdf.Name("/FlateDecode"))
    return st


def _meta_pdf(path, build, **save_kw) -> str:
    """One blank page; *build(pdf)* plants the metadata. Saved with
    fix_metadata_version off: left on, pikepdf re-serializes any catalog XMP
    packet on the way out and the fixture is no longer what was planted."""
    pdf = pikepdf.Pdf.new()
    _blank_page(pdf)
    build(pdf)
    pdf.save(str(path), fix_metadata_version=False, **save_kw)
    pdf.close()
    return str(path)


def _all_bytes(path) -> bytes:
    """Every string and every stream in the file, concatenated. Walks into
    direct (inline) dictionaries and arrays too — pdf.objects yields indirect
    objects only, so a check over it alone misses anything stored inline."""
    out = []

    def visit(obj, depth=0):
        if depth > 60 or not isinstance(obj, pikepdf.Object):
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


def _finding(report: dict, fid: str) -> dict:
    for f in report["findings"]:
        if f["id"] == fid:
            return f
    raise AssertionError(f"no {fid!r} finding in {sorted(_finding_ids(report))}")


class TestMetadataPresence:
    """An XMP stream is a finding because it is there — on the catalog, a
    page, or anything else, parseable or not — and removing it deletes the
    stream. /Info is a finding when it has any identifying entry."""

    def test_malformed_catalog_xmp_is_deleted_not_rewritten(self, tmp_path):
        src = _meta_pdf(tmp_path / "m.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Metadata", _xmp_stream(pdf, _XMP_MALFORMED)))
        assert "meta.xmp" in _finding_ids(analyze_document(src))
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, _METADATA_ONLY)
        assert b"Alex Example" not in _all_bytes(out), \
            "the packet was cleared and re-saved; the recovered tree kept the name"
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.Root
        assert res["removed"] == {"xmp_metadata": 1}
        assert "meta.xmp" not in _finding_ids(analyze_document(out))

    def test_page_level_xmp_is_reported_where_it_is_and_removed(self, tmp_path):
        src = _meta_pdf(tmp_path / "p.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/Metadata", _xmp_stream(pdf, _XMP_OK)))
        finding = _finding(analyze_document(src), "meta.xmp")
        assert finding["count"] == 1
        assert any("page 1" in item.lower() for item in finding["items"])
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, _METADATA_ONLY)
        assert b"Alex Example" not in _all_bytes(out)
        assert res["removed"] == {"xmp_metadata": 1}
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.pages[0].obj
            assert "/Metadata" not in pdf.Root            # and no stub invented
        assert "meta.xmp" not in _finding_ids(analyze_document(out))

    def test_stream_shared_by_catalog_and_page_counts_once(self, tmp_path):
        def build(pdf):
            st = _xmp_stream(pdf, _XMP_OK)
            pdf.Root["/Metadata"] = st
            pdf.pages[0].obj["/Metadata"] = st

        src = _meta_pdf(tmp_path / "s.pdf", build)
        finding = _finding(analyze_document(src), "meta.xmp")
        assert finding["count"] == 1 and len(finding["items"]) == 2
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _METADATA_ONLY)["removed"] == {"xmp_metadata": 1}
        assert b"Alex Example" not in _all_bytes(out)

    def test_stream_held_under_another_key_or_in_an_array_is_removed(self, tmp_path):
        # Found by what the stream says it is, not by the key it sits under.
        def build(pdf):
            pdf.Root["/Custom"] = pikepdf.Dictionary(Packets=pikepdf.Array(
                [_xmp_stream(pdf, _XMP_OK)]))

        src = _meta_pdf(tmp_path / "a.pdf", build)
        assert "meta.xmp" in _finding_ids(analyze_document(src))
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _METADATA_ONLY)["removed"] == {"xmp_metadata": 1}
        assert b"Alex Example" not in _all_bytes(out)

    def test_untyped_stream_in_the_catalog_slot_is_metadata(self, tmp_path):
        # Root /Metadata is the document's metadata by position: readers take
        # whatever stream is there, typed or not.
        src = _meta_pdf(tmp_path / "u.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Metadata", _xmp_stream(pdf, _XMP_OK, typed=False)))
        ids = _finding_ids(analyze_document(src))
        assert "meta.xmp" in ids
        assert "meta.docinfo" not in ids, \
            "analysis reported /Info entries the file does not have"
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _METADATA_ONLY)["removed"] == {"xmp_metadata": 1}
        assert b"Alex Example" not in _all_bytes(out)

    def _undecodable(self, tmp_path) -> str:
        return _meta_pdf(tmp_path / "x.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Metadata", _undecodable_xmp_stream(pdf)),
            stream_decode_level=pikepdf.StreamDecodeLevel.none)

    def test_undecodable_xmp_is_still_a_finding(self, tmp_path):
        assert "meta.xmp" in _finding_ids(analyze_document(self._undecodable(tmp_path)))

    @pytest.mark.parametrize("options", [_ALL_OFF, None, _METADATA_ONLY],
                             ids=["all-off", "defaults", "metadata-only"])
    def test_undecodable_xmp_never_crashes_sanitize(self, tmp_path, options):
        # Used to raise "PdfError: While trying to update XMP metadata" under
        # any options: saving with fix_metadata_version parses the packet.
        src = self._undecodable(tmp_path)
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, options)
        with pikepdf.open(out) as pdf:
            if options is _METADATA_ONLY:
                assert "/Metadata" not in pdf.Root
                assert res["removed"] == {"xmp_metadata": 1}
            else:
                assert pdf.Root.Metadata.read_raw_bytes() == b"not deflate data: Alex Example"
                assert "xmp_metadata" not in res["removed"]

    def test_custom_info_keys_are_reported(self, tmp_path):
        src = _meta_pdf(tmp_path / "i.pdf", lambda pdf: pdf.trailer.__setitem__(
            "/Info", pdf.make_indirect(pikepdf.Dictionary(
                Company=pikepdf.String("Example Holdings"),
                Trapped=pikepdf.Name("/False")))))
        finding = _finding(analyze_document(src), "meta.docinfo")
        assert finding["items"] == ["Company: Example Holdings"]    # /Trapped is not identifying
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _METADATA_ONLY)["removed"] == {"doc_info": 1}
        with pikepdf.open(out) as pdf:
            assert "/Info" not in pdf.trailer
        assert b"Example Holdings" not in _all_bytes(out)

    def test_trapped_alone_is_not_a_finding(self, tmp_path):
        src = _meta_pdf(tmp_path / "t.pdf", lambda pdf: pdf.trailer.__setitem__(
            "/Info", pdf.make_indirect(pikepdf.Dictionary(Trapped=pikepdf.Name("/False")))))
        assert _finding_ids(analyze_document(src)) == {"clean"}

    def test_blank_pdf_reports_nothing_removed_and_gains_no_xmp(self, tmp_path):
        src = _meta_pdf(tmp_path / "b.pdf", lambda pdf: None)
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, _METADATA_ONLY)
        assert res["removed"] == {} and res["total_removed"] == 0
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.Root
            assert "/Info" not in pdf.trailer
        assert b"xmpmeta" not in _all_bytes(out)

    def test_resource_named_metadata_is_not_metadata(self, tmp_path):
        # A form XObject a producer happened to name /Metadata. It is page
        # content; deleting "the /Metadata key" would break the page.
        def build(pdf):
            form = pdf.make_stream(b"0 0 10 10 re f")
            form["/Type"] = pikepdf.Name("/XObject")
            form["/Subtype"] = pikepdf.Name("/Form")
            form["/BBox"] = [0, 0, 10, 10]
            page = pdf.pages[0].obj
            page["/Resources"] = pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Metadata=form))
            page["/Contents"] = pdf.make_stream(b"/Metadata Do")

        src = _meta_pdf(tmp_path / "r.pdf", build)
        assert _finding_ids(analyze_document(src)) == {"clean"}
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _METADATA_ONLY)["removed"] == {}
        with pikepdf.open(out) as pdf:
            assert pdf.pages[0].obj.Resources.XObject.Metadata.read_bytes() == b"0 0 10 10 re f"

# ═══════════════════════════════════════════════════════════════════
#  Actions: every place one can hang is both found and removed (#101)
# ═══════════════════════════════════════════════════════════════════

_JS_ONLY = {**_ALL_OFF, "javascript": True}
_LINKS_ONLY = {**_ALL_OFF, "external_links": True}


def _named_action() -> pikepdf.Dictionary:
    """A harmless action: go to the next page."""
    return pikepdf.Dictionary(S=pikepdf.Name("/Named"), N=pikepdf.Name("/NextPage"))


def _action_pdf(path, build) -> str:
    pdf = pikepdf.Pdf.new()
    _blank_page(pdf)
    build(pdf)
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _link(pdf, **entries):
    return pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Link"),
        Rect=[0, 0, 50, 50], **entries))


class TestActionSites:
    """Each case plants one action somewhere the analyzer and the sanitizer
    did not both look. `_all_bytes` is the independent check: it reads every
    string in the file, including inline ones."""

    def _sanitize(self, tmp_path, src, options):
        out = str(tmp_path / "out.pdf")
        return sanitize_pdf(src, out, options), out

    def test_bookmark_javascript(self, tmp_path):
        def build(pdf):
            item = pdf.make_indirect(pikepdf.Dictionary(
                Title=pikepdf.String("Chapter 1"), A=_js_action()))
            outlines = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/Outlines"), First=item, Last=item, Count=1))
            item["/Parent"] = outlines
            pdf.Root["/Outlines"] = outlines

        src = _action_pdf(tmp_path / "b.pdf", build)
        assert "scripts.js" in _finding_ids(analyze_document(src))
        res, out = self._sanitize(tmp_path, src, None)
        assert res["total_removed"] == 1
        assert b"app.alert" not in _all_bytes(out)
        with pikepdf.open(out) as pdf:            # the bookmark itself stays
            assert str(pdf.Root.Outlines.First.Title) == "Chapter 1"

    def test_script_on_a_parent_field_with_a_widget_kid(self, tmp_path):
        # The calculate script sits on the field; only its kid is a page
        # annotation, so a walk over page annotations never reaches it.
        def build(pdf):
            parent = pdf.make_indirect(pikepdf.Dictionary(
                FT=pikepdf.Name("/Tx"), T=pikepdf.String("total"),
                AA=pikepdf.Dictionary(C=_js_action())))
            kid = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Widget"),
                Rect=[10, 10, 100, 30], Parent=parent))
            parent["/Kids"] = pikepdf.Array([kid])
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([kid])
            pdf.Root["/AcroForm"] = pikepdf.Dictionary(Fields=pikepdf.Array([parent]))

        src = _action_pdf(tmp_path / "f.pdf", build)
        ids = _finding_ids(analyze_document(src))
        assert "scripts.js" in ids and "actions.autorun" in ids
        res, out = self._sanitize(tmp_path, src, None)
        assert res["total_removed"] == 1
        assert b"app.alert" not in _all_bytes(out)
        with pikepdf.open(out) as pdf:            # the field and its widget stay
            assert len(pdf.Root.AcroForm.Fields) == 1
            assert len(pdf.pages[0].obj.Annots) == 1

    def test_script_on_a_dropped_widget_does_not_stay_in_the_field_tree(self, tmp_path):
        # A button whose /A runs JavaScript. Taking it off the page is not
        # removal: the same dictionary is still listed under /AcroForm.
        def build(pdf):
            w = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Widget"),
                FT=pikepdf.Name("/Btn"), T=pikepdf.String("go"),
                Rect=[10, 10, 100, 30], A=_js_action()))
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([w])
            pdf.Root["/AcroForm"] = pikepdf.Dictionary(Fields=pikepdf.Array([w]))

        src = _action_pdf(tmp_path / "w.pdf", build)
        res, out = self._sanitize(tmp_path, src, None)
        assert res["total_removed"] == 1
        assert b"app.alert" not in _all_bytes(out)

    def test_page_aa_javascript_with_auto_actions_off(self, tmp_path):
        src = _action_pdf(tmp_path / "p.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/AA", pikepdf.Dictionary(O=_js_action(), C=_named_action())))
        res, out = self._sanitize(tmp_path, src, _JS_ONLY)
        assert res["total_removed"] == 1
        assert b"app.alert" not in _all_bytes(out)
        ids = _finding_ids(analyze_document(out))
        assert "scripts.js" not in ids
        assert "actions.autorun" in ids           # the page-close action was not asked for

    def test_openaction_script_in_next_keeps_the_benign_head(self, tmp_path):
        def build(pdf):
            head = _named_action()
            head["/Next"] = pdf.make_indirect(_js_action())
            pdf.Root["/OpenAction"] = head

        src = _action_pdf(tmp_path / "o.pdf", build)
        res, out = self._sanitize(tmp_path, src, _JS_ONLY)
        assert res["total_removed"] == 1
        assert b"app.alert" not in _all_bytes(out)
        with pikepdf.open(out) as pdf:
            assert str(pdf.Root.OpenAction.S) == "/Named", \
                "auto_actions was off: only the script should have gone"
            assert "/Next" not in pdf.Root.OpenAction

    def test_auto_actions_removes_every_additional_action(self, tmp_path):
        # Analyze calls any /AA entry an auto-run action, whatever it does.
        # A pointer-enter /Hide is not JavaScript, so it used to be left.
        src = _action_pdf(tmp_path / "h.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/Annots", pikepdf.Array([_link(pdf, AA=pikepdf.Dictionary(
                E=pikepdf.Dictionary(S=pikepdf.Name("/Hide"), T=pikepdf.String("x"))))])))
        assert "actions.autorun" in _finding_ids(analyze_document(src))
        res, out = self._sanitize(tmp_path, src, None)
        assert res["total_removed"] == 1
        assert "actions.autorun" not in _finding_ids(analyze_document(out))

    def test_external_links_removes_a_uri_from_a_next_chain(self, tmp_path):
        def build(pdf):
            head = _named_action()
            head["/Next"] = _uri_action()
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([_link(pdf, A=head)])

        src = _action_pdf(tmp_path / "n.pdf", build)
        res, out = self._sanitize(tmp_path, src, _LINKS_ONLY)
        assert res["total_removed"] == 1
        assert b"example.com" not in _all_bytes(out)
        with pikepdf.open(out) as pdf:            # the link keeps its harmless head
            assert str(pdf.pages[0].obj.Annots[0].A.S) == "/Named"

    def test_link_previous_action(self, tmp_path):
        # /PA is where a link keeps the URI action it had before it was
        # retargeted (web capture). The link itself now goes to a page.
        def build(pdf):
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([_link(
                pdf, Dest=pikepdf.Array([pdf.pages[0].obj, pikepdf.Name("/Fit")]),
                PA=_uri_action())])

        src = _action_pdf(tmp_path / "pa.pdf", build)
        assert "links.uri" in _finding_ids(analyze_document(src))
        res, out = self._sanitize(tmp_path, src, _LINKS_ONLY)
        assert res["removed"] == {"external_link": 1}
        assert b"example.com" not in _all_bytes(out)
        with pikepdf.open(out) as pdf:             # the link stays: its own target is fine
            assert "/Dest" in pdf.pages[0].obj.Annots[0]
            assert "/PA" not in pdf.pages[0].obj.Annots[0]

    def test_3d_instantiation_script(self, tmp_path):
        # A script run when 3D artwork loads. It is a bare stream on the 3D
        # stream's dictionary, not an action, so it sits at no action site.
        def build(pdf):
            artwork = pdf.make_stream(b"U3D\x00")
            artwork["/Type"] = pikepdf.Name("/3D")
            artwork["/Subtype"] = pikepdf.Name("/U3D")
            artwork["/OnInstantiate"] = pdf.make_stream(b"app.alert('marker-3d');")
            annot = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/3D"),
                Rect=[0, 0, 50, 50]))
            annot["/3DD"] = artwork
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([annot])

        src = _action_pdf(tmp_path / "3d.pdf", build)
        res, out = self._sanitize(tmp_path, src, _JS_ONLY)
        assert res["removed"] == {"instantiate_script": 1}
        assert b"marker-3d" not in _all_bytes(out)
        with pikepdf.open(out) as pdf:             # the artwork itself stays
            assert pdf.pages[0].obj.Annots[0]["/3DD"].read_bytes() == b"U3D\x00"

        kept = str(tmp_path / "kept.pdf")
        assert sanitize_pdf(src, kept, _ALL_OFF)["removed"] == {}
        assert b"marker-3d" in _all_bytes(kept)

    def test_external_links_removes_a_uri_openaction(self, tmp_path):
        src = _action_pdf(tmp_path / "u.pdf", lambda pdf: pdf.Root.__setitem__(
            "/OpenAction", _uri_action()))
        res, out = self._sanitize(tmp_path, src, _LINKS_ONLY)
        assert res["total_removed"] == 1
        assert b"example.com" not in _all_bytes(out)

class TestOpenActionDestination:
    """/OpenAction may be a destination (where the document opens) rather
    than an action. That is a view setting, not active content: it is not a
    finding and Sanitize leaves it where it is."""

    @pytest.mark.parametrize("make", [
        lambda pdf: pikepdf.Array([pdf.pages[0].obj, pikepdf.Name("/Fit")]),
        lambda pdf: pikepdf.Name("/Chapter1"),
        lambda pdf: pikepdf.String("Chapter1"),
    ], ids=["explicit-array", "named-as-name", "named-as-string"])
    def test_destination_is_not_a_finding_and_is_kept(self, tmp_path, make):
        src = _action_pdf(tmp_path / "d.pdf", lambda pdf: pdf.Root.__setitem__(
            "/OpenAction", make(pdf)))
        assert _finding_ids(analyze_document(src)) == {"clean"}
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out)                       # defaults: auto_actions on
        assert res["removed"] == {}
        with pikepdf.open(out) as pdf:
            assert "/OpenAction" in pdf.Root


# ═══════════════════════════════════════════════════════════════════
#  Embedded files: a payload is what makes a filespec one (#101)
# ═══════════════════════════════════════════════════════════════════

_FILES_ONLY = {**_ALL_OFF, "embedded_files": True}


def _payload_stream(pdf, data=b"PAYLOAD-123"):
    st = pdf.make_stream(data)
    st["/Type"] = pikepdf.Name("/EmbeddedFile")
    return st


def _filespec(pdf, *, payload=True, **extra) -> pikepdf.Dictionary:
    d = pikepdf.Dictionary(Type=pikepdf.Name("/Filespec"),
                           F=pikepdf.String("report-a.bin"),
                           UF=pikepdf.String("report-a.bin"),
                           Desc=pikepdf.String("quarterly numbers"), **extra)
    if payload:
        d["/EF"] = pikepdf.Dictionary(F=_payload_stream(pdf))
    return d


def _screen_annot(pdf, fs):
    """An annotation that is not a /FileAttachment but still holds a file
    specification — one of the places an embedded file can sit that the
    name tree, /AF and /FileAttachment handling never reach."""
    return pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Screen"),
        Rect=[0, 0, 50, 50], FS=fs))


class TestEmbeddedFileRule:
    def test_payload_at_an_unusual_holder_is_stripped_with_its_names(self, tmp_path):
        src = _action_pdf(tmp_path / "s.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/Annots", pikepdf.Array([_screen_annot(pdf, pdf.make_indirect(_filespec(pdf)))])))
        assert "files.embedded" in _finding_ids(analyze_document(src))
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, _FILES_ONLY)
        left = _all_bytes(out)
        assert b"PAYLOAD-123" not in left
        assert b"report-a.bin" not in left, "the attachment's file name stayed behind"
        assert b"quarterly numbers" not in left
        assert res["removed"] == {"embedded_file": 1}
        ids = _finding_ids(analyze_document(out))
        assert "files.embedded" not in ids and "files.external" not in ids

    def test_inline_filespec_is_found_and_stripped(self, tmp_path):
        # The filespec is written inline in the annotation, so it is not an
        # object of its own and pdf.objects never yields it.
        src = _action_pdf(tmp_path / "i.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/Annots", pikepdf.Array([_screen_annot(pdf, _filespec(pdf))])))
        finding = _finding(analyze_document(src), "files.embedded")
        assert finding["items"] == ["report-a.bin"]
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, _FILES_ONLY)
        assert res["removed"] == {"embedded_file": 1}
        assert b"PAYLOAD-123" not in _all_bytes(out)
        assert b"report-a.bin" not in _all_bytes(out)

    @pytest.mark.parametrize("inline", [False, True], ids=["indirect", "inline"])
    def test_external_filespec_is_not_an_embedded_file(self, tmp_path, inline):
        # A link to another document. Nothing is embedded, so there is
        # nothing for "remove embedded files" to remove — but the path it
        # carries is worth showing.
        def build(pdf):
            fs = _filespec(pdf, payload=False)
            action = pikepdf.Dictionary(
                S=pikepdf.Name("/GoToR"), D=pikepdf.Array([0, pikepdf.Name("/Fit")]),
                F=fs if inline else pdf.make_indirect(fs))
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([_link(pdf, A=action)])

        src = _action_pdf(tmp_path / "x.pdf", build)
        report = analyze_document(src)
        assert "files.embedded" not in _finding_ids(report)
        assert _finding(report, "files.external")["items"] == ["report-a.bin"]
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _FILES_ONLY)["removed"] == {}
        assert "files.external" in _finding_ids(analyze_document(out))

    def test_resource_named_ef_is_not_an_embedded_file(self, tmp_path):
        def build(pdf):
            font = pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"), Subtype=pikepdf.Name("/Type1"),
                BaseFont=pikepdf.Name("/Helvetica")))
            pdf.pages[0].obj["/Resources"] = pikepdf.Dictionary(
                Font=pdf.make_indirect(pikepdf.Dictionary(EF=font)))

        src = _action_pdf(tmp_path / "r.pdf", build)
        assert _finding_ids(analyze_document(src)) == {"clean"}
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _FILES_ONLY)["removed"] == {}
        with pikepdf.open(out) as pdf:
            assert str(pdf.pages[0].obj.Resources.Font.EF.BaseFont) == "/Helvetica"

    @staticmethod
    def _in_name_tree(pdf, *specs) -> None:
        """List *specs* in the catalog's /Names /EmbeddedFiles tree.

        Built by hand, not through ``pdf.attachments``: from pikepdf 10.16
        that API also registers the file in the catalog's /AF array (as the
        same object), so what a fixture made with it contains depends on the
        library version installed.
        """
        entries = pikepdf.Array()
        for n, spec in enumerate(specs):
            entries.append(pikepdf.String(f"file-{n}"))
            entries.append(spec)
        pdf.Root["/Names"] = pikepdf.Dictionary(
            EmbeddedFiles=pikepdf.Dictionary(Names=entries))

    def _sanitized(self, tmp_path, build):
        src = _action_pdf(tmp_path / "n.pdf", build)
        out = str(tmp_path / "out.pdf")
        return sanitize_pdf(src, out, _FILES_ONLY), out

    def test_name_tree_attachment_is_counted_once(self, tmp_path):
        res, out = self._sanitized(tmp_path, lambda pdf: self._in_name_tree(
            pdf, pdf.make_indirect(_filespec(pdf))))
        assert res["removed"] == {"embedded_files": 1}
        assert b"PAYLOAD-123" not in _all_bytes(out)

    def test_one_file_in_the_name_tree_and_in_af_is_one_file(self, tmp_path):
        # What pdf.attachments writes from pikepdf 10.16 on: one filespec,
        # referenced from the name tree and from the catalog's /AF.
        def build(pdf):
            spec = pdf.make_indirect(_filespec(pdf))
            self._in_name_tree(pdf, spec)
            pdf.Root["/AF"] = pikepdf.Array([spec])

        res, out = self._sanitized(tmp_path, build)
        assert res["removed"] == {"embedded_files": 1}
        assert b"PAYLOAD-123" not in _all_bytes(out)
        with pikepdf.open(out) as pdf:
            assert "/AF" not in pdf.Root and "/Names" not in pdf.Root.get("/Names", {})

    def test_one_file_also_shown_by_an_attachment_annotation_is_one_file(self, tmp_path):
        def build(pdf):
            spec = pdf.make_indirect(_filespec(pdf))
            self._in_name_tree(pdf, spec)
            pdf.pages[0].obj["/Annots"] = pikepdf.Array([pdf.make_indirect(
                pikepdf.Dictionary(Type=pikepdf.Name("/Annot"),
                                   Subtype=pikepdf.Name("/FileAttachment"),
                                   Rect=[0, 0, 20, 20], FS=spec, AF=pikepdf.Array([spec])))])

        res, out = self._sanitized(tmp_path, build)
        assert res["removed"] == {"embedded_files": 1}
        with pikepdf.open(out) as pdf:
            assert len(pdf.pages[0].obj.get("/Annots", [])) == 0     # the annotation still goes

    def test_each_file_is_counted_where_it_is_first_reached(self, tmp_path):
        def build(pdf):
            self._in_name_tree(pdf, pdf.make_indirect(_filespec(pdf)),
                               pdf.make_indirect(_filespec(pdf)))
            pdf.Root["/AF"] = pikepdf.Array([pdf.make_indirect(_filespec(pdf))])

        res, out = self._sanitized(tmp_path, build)
        assert res["removed"] == {"embedded_files": 2, "associated_file": 1}
        assert b"PAYLOAD-123" not in _all_bytes(out)


class TestAnnotationWalkParity:
    """sanitize_pdf reaches annotations through its own page loop, because it
    rewrites each page's /Annots as it goes; every other owner comes from
    _action_owners. The two walks must reach the same annotations."""

    def _fixture(self, tmp_path) -> str:
        def build(pdf):
            _blank_page(pdf)                                   # a second page
            shared = _link(pdf, A=_js_action())
            other = _link(pdf, AA=pikepdf.Dictionary(E=_js_action()))
            # An indirect /Annots array with a null entry in it...
            pdf.pages[0].obj["/Annots"] = pdf.make_indirect(
                pikepdf.Array([shared, None, other]))
            # ...and one annotation that a second page lists as well.
            pdf.pages[1].obj["/Annots"] = pikepdf.Array([shared])

        return _action_pdf(tmp_path / "w.pdf", build)

    def test_loop_and_generator_visit_the_same_annotations(self, tmp_path, monkeypatch):
        import pdf_analyze
        src = self._fixture(tmp_path)
        with pikepdf.open(src) as pdf:
            assert pdf.pages[0].obj["/Annots"].is_indirect
            assert len(pdf.pages[0].obj["/Annots"]) == 3
            from_generator = [tuple(owner.objgen)
                              for kind, _label, owner in pdf_analyze._action_owners(pdf)
                              if kind == "annot"]

        from_loop = []
        real = pdf_analyze._scrub_owner_actions

        def spy(owner_kind, label, owner, opts, bump):
            if owner_kind == "annot":
                from_loop.append(tuple(owner.objgen))
            return real(owner_kind, label, owner, opts, bump)

        monkeypatch.setattr(pdf_analyze, "_scrub_owner_actions", spy)
        sanitize_pdf(src, str(tmp_path / "out.pdf"), _ALL_OFF)

        assert len(from_generator) == 2 == len(set(from_generator))   # each one once
        assert set(from_loop) == set(from_generator)

    def test_shared_annotation_is_dropped_everywhere_and_counted_once(self, tmp_path):
        src = self._fixture(tmp_path)
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out)
        assert res["removed"] == {"annot_javascript": 2}      # one per script, not per listing
        assert b"app.alert" not in _all_bytes(out)
        with pikepdf.open(out) as pdf:
            first = [a for a in pdf.pages[0].obj["/Annots"] if a is not None]
            assert len(first) == 1 and "/AA" not in first[0]  # the other link stays, scriptless
            assert len(pdf.pages[1].obj.get("/Annots", [])) == 0

class TestSanitizeWriteIsStable:
    """The output is a fixed point: sanitizing it again changes no byte, and
    the same input gives the same bytes whenever it is run."""

    def _src(self, tmp_path) -> str:
        """A hand-assembled PDF whose objects are numbered against the order
        a reader reaches them (font first, catalog last) — as files from
        most producers are, and as files pikepdf writes are not. qpdf packs
        object streams by the numbers objects had on the way in, so only an
        input like this shows whether the first write is the settled one."""
        content = b"BT /F1 12 Tf 72 720 Td (page) Tj ET"
        bodies = [
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
            b"<</Length %d>>\nstream\n%s\nendstream" % (len(content), content),
            b"<</Type/Annot/Subtype/Link/Rect[0 0 50 50]/A<</S/Named/N/NextPage>>>>",
            b"<</Type/Page/Parent 5 0 R/MediaBox[0 0 612 792]/Contents 2 0 R"
            b"/Annots[3 0 R]/Resources<</Font<</F1 1 0 R>>>>>>",
            b"<</Type/Pages/Kids[4 0 R]/Count 1>>",
            b"<</S/JavaScript/JS(app.alert\\(1\\);)>>",
            b"<</Type/Catalog/Pages 5 0 R/OpenAction 6 0 R>>",
        ]
        out, offsets = bytearray(b"%PDF-1.4\n"), []
        for num, body in enumerate(bodies, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (num, body)
        xref_at = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(bodies) + 1)
        out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
        out += b"trailer\n<</Size %d/Root 7 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
            len(bodies) + 1, xref_at)
        src = tmp_path / "numbered.pdf"
        src.write_bytes(bytes(out))
        with pikepdf.open(src) as pdf:                 # well-formed, as written
            list(pdf.objects)
            assert pdf.get_warnings() == []
        return str(src)

    def test_sanitizing_a_sanitized_file_changes_nothing(self, tmp_path):
        first, second = str(tmp_path / "first.pdf"), str(tmp_path / "second.pdf")
        assert sanitize_pdf(self._src(tmp_path), first)["total_removed"] > 0
        assert sanitize_pdf(first, second)["removed"] == {}
        with open(first, "rb") as a, open(second, "rb") as b:
            assert a.read() == b.read()

    def test_same_input_gives_the_same_bytes_a_second_later(self, tmp_path):
        import time
        src = self._src(tmp_path)
        a, b = str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")
        sanitize_pdf(src, a)
        time.sleep(1.1)              # an identifier minted from the clock would differ
        sanitize_pdf(src, b)
        with open(a, "rb") as fa, open(b, "rb") as fb:
            assert fa.read() == fb.read()
