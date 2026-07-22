"""Tests for pdf_ops.py — merge, split, page operations, and more."""

import io
import os

import pikepdf
import pytest
from PIL import Image

from pdf_ops import (
    MergeResult,
    PageOpResult,
    RedactionVerificationError,
    RedactResult,
    SplitResult,
    _parse_ranges,
    _sanitize_title,
    add_watermark,
    apply_page_operations,
    contained_output_path,
    extract_text,
    flatten_pdf,
    get_toc,
    images_to_pdf,
    is_within_directory,
    merge_pdfs,
    protect_pdf,
    read_metadata,
    redact_pdf,
    repair_pdf,
    split_pdf,
    unlock_pdf,
    write_metadata,
)
from pdf_verify import verify_redaction

try:
    import fitz  # noqa: F401  (PyMuPDF — required by redact_pdf below)
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

# ═══════════════════════════════════════════════════════════════════
#  Pure unit tests
# ═══════════════════════════════════════════════════════════════════


class TestParseRanges:
    def test_single_page(self):
        assert _parse_ranges("3", 10) == [(3, 3)]

    def test_range(self):
        assert _parse_ranges("1-3", 10) == [(1, 3)]

    def test_multiple(self):
        assert _parse_ranges("1-3, 5, 8-10", 10) == [(1, 3), (5, 5), (8, 10)]

    def test_whitespace(self):
        assert _parse_ranges("  2 - 4  ,  7  ", 10) == [(2, 4), (7, 7)]

    def test_invalid_zero_start(self):
        with pytest.raises(ValueError, match="Invalid range"):
            _parse_ranges("0-3", 10)

    def test_invalid_exceeds_max(self):
        with pytest.raises(ValueError, match="Invalid range"):
            _parse_ranges("1-999", 10)

    def test_invalid_reversed(self):
        with pytest.raises(ValueError, match="Invalid range"):
            _parse_ranges("5-3", 10)

    def test_empty_string(self):
        assert _parse_ranges("", 10) == []


# ═══════════════════════════════════════════════════════════════════
#  Merge tests
# ═══════════════════════════════════════════════════════════════════


class TestMergePdfs:
    @pytest.mark.integration
    def test_merge_two(self, sample_pdf, multi_page_pdf, tmp_path):
        out = str(tmp_path / "merged.pdf")
        result = merge_pdfs([sample_pdf, multi_page_pdf], out)
        assert isinstance(result, MergeResult)
        assert result.total_pages == 4  # 1 + 3
        assert os.path.isfile(out)

        # Verify page count in output
        with pikepdf.open(out) as pdf:
            assert len(pdf.pages) == 4

    def test_merge_requires_two(self, sample_pdf, tmp_path):
        out = str(tmp_path / "merged.pdf")
        with pytest.raises(ValueError, match="at least 2"):
            merge_pdfs([sample_pdf], out)


# ═══════════════════════════════════════════════════════════════════
#  Split tests
# ═══════════════════════════════════════════════════════════════════


class TestSplitPdf:
    @pytest.mark.integration
    def test_split_all_pages(self, multi_page_pdf, tmp_path):
        out_dir = str(tmp_path / "split_out")
        result = split_pdf(multi_page_pdf, out_dir, mode="all")
        assert isinstance(result, SplitResult)
        assert len(result.output_paths) == 3
        for p in result.output_paths:
            assert os.path.isfile(p)

    @pytest.mark.integration
    def test_split_ranges(self, multi_page_pdf, tmp_path):
        out_dir = str(tmp_path / "split_ranges")
        result = split_pdf(multi_page_pdf, out_dir,
                           mode="ranges", ranges="1-2, 3")
        assert len(result.output_paths) == 2
        assert result.pages_per_output == [2, 1]

    @pytest.mark.integration
    def test_split_every_n(self, multi_page_pdf, tmp_path):
        out_dir = str(tmp_path / "split_every")
        result = split_pdf(multi_page_pdf, out_dir,
                           mode="every_n", every_n=2)
        assert len(result.output_paths) == 2  # pages [1-2] and [3]
        assert result.pages_per_output == [2, 1]

    def test_split_invalid_mode(self, multi_page_pdf, tmp_path):
        with pytest.raises(ValueError, match="Unknown split mode"):
            split_pdf(multi_page_pdf, str(tmp_path), mode="bogus")


# ═══════════════════════════════════════════════════════════════════
#  Page operations tests
# ═══════════════════════════════════════════════════════════════════


class TestPageOperations:
    @pytest.mark.integration
    def test_rotate_page(self, multi_page_pdf, tmp_path):
        out = str(tmp_path / "rotated.pdf")
        result = apply_page_operations(
            multi_page_pdf, out, rotations={0: 90})
        assert isinstance(result, PageOpResult)
        assert any("Rotated" in op for op in result.operations)

        with pikepdf.open(out) as pdf:
            assert int(pdf.pages[0].get("/Rotate", 0)) == 90

    @pytest.mark.integration
    def test_delete_page(self, multi_page_pdf, tmp_path):
        out = str(tmp_path / "deleted.pdf")
        result = apply_page_operations(
            multi_page_pdf, out, delete_pages=[1])
        assert any("Deleted" in op for op in result.operations)

        with pikepdf.open(out) as pdf:
            assert len(pdf.pages) == 2  # was 3, deleted 1


# ═══════════════════════════════════════════════════════════════════
#  Metadata tests
# ═══════════════════════════════════════════════════════════════════


class TestMetadata:
    @pytest.mark.integration
    def test_read_write_roundtrip(self, sample_pdf, tmp_path):
        fields = read_metadata(sample_pdf)
        assert isinstance(fields, dict)

        # Set a title and write back
        fields["title"] = "Test Title"
        fields["author"] = "Test Author"
        out = str(tmp_path / "with_meta.pdf")
        write_metadata(sample_pdf, out, fields)

        # Read back and verify
        result = read_metadata(out)
        assert result["title"] == "Test Title"
        assert result["author"] == "Test Author"


# ═══════════════════════════════════════════════════════════════════
#  Extract / Flatten / Repair tests
# ═══════════════════════════════════════════════════════════════════


class TestExtractText:
    @pytest.mark.integration
    def test_extract_text(self, sample_pdf, tmp_path):
        out = str(tmp_path / "text.txt")
        result = extract_text(sample_pdf, out)
        assert os.path.isfile(out)
        assert result.page_count >= 1


def _make_form_and_annot_pdf(path: str) -> str:
    """A PDF with one page holding a /Widget annotation (a form field with
    a /V value) and a non-Widget /Text annotation, plus an /AcroForm at
    the document root — enough to distinguish "remove form fields" from
    "remove other annotations" (OPS-02)."""
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 200, 200],
    ))
    pdf.pages.append(page)

    widget = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Widget"),
        FT=pikepdf.Name("/Tx"), T="field1", V="hello world",
        Rect=[10, 10, 100, 30],
    ))
    text_annot = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Text"),
        Contents="a sticky note", Rect=[10, 50, 100, 70],
    ))
    pdf.pages[0]["/Annots"] = pikepdf.Array([widget, text_annot])
    pdf.Root["/AcroForm"] = pdf.make_indirect(pikepdf.Dictionary(Fields=[widget]))

    pdf.save(path)
    pdf.close()
    return path


class TestFlatten:
    @pytest.mark.integration
    def test_flatten(self, sample_pdf, tmp_path):
        out = str(tmp_path / "flat.pdf")
        flatten_pdf(sample_pdf, out)
        assert os.path.isfile(out)
        # Verify the output opens as a valid PDF
        with pikepdf.open(out) as pdf:
            assert len(pdf.pages) >= 1

    def _subtypes(self, pdf):
        annots = pdf.pages[0].get("/Annots")
        if not annots:
            return []
        return sorted(str(a.get("/Subtype")) for a in annots)

    def test_annotations_and_forms_removes_everything(self, tmp_path):
        src = _make_form_and_annot_pdf(str(tmp_path / "src.pdf"))
        out = str(tmp_path / "out.pdf")
        flatten_pdf(src, out, annotations=True, forms=True)
        with pikepdf.open(out) as pdf:
            assert self._subtypes(pdf) == []
            assert "/AcroForm" not in pdf.Root

    def test_annotations_only_keeps_widgets(self, tmp_path):
        src = _make_form_and_annot_pdf(str(tmp_path / "src.pdf"))
        out = str(tmp_path / "out.pdf")
        flatten_pdf(src, out, annotations=True, forms=False)
        with pikepdf.open(out) as pdf:
            assert self._subtypes(pdf) == ["/Widget"]
            assert "/AcroForm" in pdf.Root

    def test_forms_only_removes_widgets_keeps_other_annotations(self, tmp_path):
        # OPS-02: forms=True must strip /Widget annotations even when
        # annotations=False -- previously the whole /Annots block was
        # skipped in this combination and the form field survived intact.
        src = _make_form_and_annot_pdf(str(tmp_path / "src.pdf"))
        out = str(tmp_path / "out.pdf")
        flatten_pdf(src, out, annotations=False, forms=True)
        with pikepdf.open(out) as pdf:
            assert self._subtypes(pdf) == ["/Text"]
            assert "/AcroForm" not in pdf.Root

    def test_neither_flag_leaves_annotations_untouched(self, tmp_path):
        src = _make_form_and_annot_pdf(str(tmp_path / "src.pdf"))
        out = str(tmp_path / "out.pdf")
        flatten_pdf(src, out, annotations=False, forms=False)
        with pikepdf.open(out) as pdf:
            assert self._subtypes(pdf) == ["/Text", "/Widget"]
            assert "/AcroForm" in pdf.Root


class TestRepair:
    @pytest.mark.integration
    def test_repair(self, sample_pdf, tmp_path):
        out = str(tmp_path / "repaired.pdf")
        repair_pdf(sample_pdf, out)
        assert os.path.isfile(out)
        with pikepdf.open(out) as pdf:
            assert len(pdf.pages) >= 1


# ═══════════════════════════════════════════════════════════════════
#  TOC / Chapter-aware split tests
# ═══════════════════════════════════════════════════════════════════


class TestGetToc:
    @pytest.mark.integration
    def test_get_toc_with_bookmarks(self, pdf_with_toc):
        toc = get_toc(pdf_with_toc)
        assert len(toc) == 3
        assert toc[0]["title"] == "Chapter 1"
        assert toc[0]["page"] == 1
        assert toc[0]["end_page"] == 3
        assert toc[1]["title"] == "Chapter 2"
        assert toc[1]["page"] == 4
        assert toc[1]["end_page"] == 7
        assert toc[2]["title"] == "Chapter 3"
        assert toc[2]["page"] == 8
        assert toc[2]["end_page"] == 10

    @pytest.mark.integration
    def test_get_toc_no_bookmarks(self, sample_pdf):
        toc = get_toc(sample_pdf)
        assert toc == []


class TestSplitTemplateVariables:
    @pytest.mark.integration
    def test_filename_and_n_template(self, multi_page_pdf, tmp_path):
        """Ensure {filename} and {n} template variables work (used by web UI)."""
        out_dir = str(tmp_path / "tmpl_out")
        result = split_pdf(
            multi_page_pdf, out_dir,
            mode="all",
            name_template="{filename}_page_{n}",
        )
        assert len(result.output_paths) == 3
        for p in result.output_paths:
            assert os.path.isfile(p)
            assert "multi_page_" in os.path.basename(p)

    @pytest.mark.integration
    def test_name_and_start_template(self, multi_page_pdf, tmp_path):
        """Ensure {name} and {start} template variables work (backend default)."""
        out_dir = str(tmp_path / "tmpl_out2")
        result = split_pdf(
            multi_page_pdf, out_dir,
            mode="all",
            name_template="{name}_page_{start}",
        )
        assert len(result.output_paths) == 3
        for p in result.output_paths:
            assert os.path.isfile(p)


class TestSplitChapters:
    @pytest.mark.integration
    def test_split_chapters_mode(self, pdf_with_toc, tmp_path):
        out_dir = str(tmp_path / "chapters_out")
        chapters = [
            {"title": "Chapter 1", "start_page": 1, "end_page": 3},
            {"title": "Chapter 3", "start_page": 8, "end_page": 10},
        ]
        result = split_pdf(
            pdf_with_toc, out_dir,
            mode="chapters", chapters=chapters,
            name_template="{name}_{title}",
        )
        assert isinstance(result, SplitResult)
        assert len(result.output_paths) == 2
        assert result.pages_per_output == [3, 3]

        for p in result.output_paths:
            assert os.path.isfile(p)

        # Verify page counts in output files
        with pikepdf.open(result.output_paths[0]) as pdf:
            assert len(pdf.pages) == 3
        with pikepdf.open(result.output_paths[1]) as pdf:
            assert len(pdf.pages) == 3

    @pytest.mark.integration
    def test_split_chapters_requires_chapters(self, pdf_with_toc, tmp_path):
        with pytest.raises(ValueError, match="Chapters list required"):
            split_pdf(pdf_with_toc, str(tmp_path), mode="chapters")

    @pytest.mark.integration
    def test_repeated_titles_disambiguate_instead_of_overwriting(self, pdf_with_toc, tmp_path):
        # OPS-04: chapters that sanitize to the same name (a common repeated
        # TOC title like "Introduction") must not silently overwrite each
        # other -- every group's output must survive as a distinct file.
        out_dir = str(tmp_path / "chapters_out")
        chapters = [
            {"title": "Introduction", "start_page": 1, "end_page": 3},
            {"title": "Introduction", "start_page": 4, "end_page": 7},
            {"title": "Introduction", "start_page": 8, "end_page": 10},
        ]
        result = split_pdf(
            pdf_with_toc, out_dir,
            mode="chapters", chapters=chapters,
            name_template="{name}_{title}",
        )
        assert len(result.output_paths) == 3
        assert len(set(result.output_paths)) == 3  # all distinct
        assert result.pages_per_output == [3, 4, 3]

        for p, expected_pages in zip(result.output_paths, result.pages_per_output, strict=True):
            assert os.path.isfile(p)
            with pikepdf.open(p) as pdf:
                assert len(pdf.pages) == expected_pages


class TestSanitizeTitle:
    def test_basic(self):
        assert _sanitize_title("Chapter 1") == "Chapter 1"

    def test_special_chars(self):
        result = _sanitize_title('Ch 1: "Intro" <test>')
        assert '"' not in result
        assert '<' not in result
        assert '>' not in result
        assert ':' not in result

    def test_long_title(self):
        long = "A" * 200
        assert len(_sanitize_title(long)) <= 80

    def test_empty(self):
        assert _sanitize_title("") == "untitled"


# ═══════════════════════════════════════════════════════════════════
#  Path-containment guard (TST-02)
# ═══════════════════════════════════════════════════════════════════


class TestContainedOutputPath:
    """contained_output_path() is the sole guard against a user-editable
    naming template writing outside the chosen folder (arbitrary-file-write
    via an absolute name or ../ traversal). The negative path was untested."""

    def test_normal_name_stays_inside(self, tmp_path):
        result = contained_output_path(str(tmp_path), "report.pdf")
        base = os.path.realpath(str(tmp_path))
        assert os.path.commonpath([base, result]) == base

    def test_subfolder_name_ok(self, tmp_path):
        # A non-escaping subfolder must be allowed — only ESCAPES should raise,
        # so this confirms the guard isn't over-eager.
        result = contained_output_path(str(tmp_path), "sub/report.pdf")
        base = os.path.realpath(str(tmp_path))
        assert os.path.commonpath([base, result]) == base
        assert result != base

    def test_traversal_raises(self, tmp_path):
        # "../../../etc/passwd" walks out of the folder on both POSIX and Windows.
        with pytest.raises(ValueError):
            contained_output_path(str(tmp_path), "../../../etc/passwd")

    def test_absolute_path_raises(self, tmp_path):
        # os.path.join() silently discards the folder when out_name is absolute.
        # abspath(os.sep + ...) is absolute on the running OS on both platforms.
        outside = os.path.abspath(os.sep + "redact_outside_target")
        with pytest.raises(ValueError):
            contained_output_path(str(tmp_path), outside)


class TestIsWithinDirectory:
    """is_within_directory() vets an already-built path against a base dir
    (BRG-02: scoping bridge deleteFile/copyFile to the workspace temp dir).
    Boolean and commonpath-based — never raises, and not fooled by a sibling
    that merely shares a name prefix."""

    def test_path_inside_is_true(self, tmp_path):
        base = tmp_path / "ws"
        base.mkdir()
        assert is_within_directory(str(base / "working.pdf"), str(base)) is True

    def test_nested_subdir_inside_is_true(self, tmp_path):
        base = tmp_path / "ws"
        base.mkdir()
        nested = base / "sub" / "deep" / "working.pdf"
        assert is_within_directory(str(nested), str(base)) is True

    def test_base_itself_is_true(self, tmp_path):
        base = tmp_path / "ws"
        base.mkdir()
        assert is_within_directory(str(base), str(base)) is True

    def test_traversal_escape_is_false(self, tmp_path):
        base = tmp_path / "ws"
        base.mkdir()
        escape = base / ".." / ".." / "etc" / "passwd"
        assert is_within_directory(str(escape), str(base)) is False

    def test_absolute_outside_is_false(self, tmp_path):
        base = tmp_path / "ws"
        base.mkdir()
        outside = os.path.abspath(os.sep + "workspace_outside_target")
        assert is_within_directory(outside, str(base)) is False

    def test_sibling_prefix_is_false(self, tmp_path):
        # The startswith trap: /foo/ws_evil is NOT inside /foo/ws.
        base = tmp_path / "ws"
        base.mkdir()
        sibling = tmp_path / "ws_evil" / "x"
        assert is_within_directory(str(sibling), str(base)) is False


# ═══════════════════════════════════════════════════════════════════
#  Redaction (real data destruction) (TST-01)
# ═══════════════════════════════════════════════════════════════════


def _text_pdf(path, text: str) -> str:
    """A one-page PDF with *text* drawn into the content stream."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return str(path)


def _form_pdf(path, field_value: str, *, rect=(72, 72, 400, 120)) -> str:
    """A one-page PDF with a single AcroForm text widget carrying
    *field_value* (in /V and its appearance stream)."""
    doc = fitz.open()
    page = doc.new_page()
    w = fitz.Widget()
    w.field_name = "sensitive"
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.rect = fitz.Rect(*rect)
    w.field_value = field_value
    page.add_widget(w)
    doc.save(str(path))
    doc.close()
    return str(path)


def _image_only_pdf(path, *, size=300, color=(255, 0, 0)) -> str:
    """A one-page PDF whose whole content is a single full-page solid-color
    image — a scanned-document stand-in (no text, one image)."""
    doc = fitz.open()
    page = doc.new_page(width=size, height=size)
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(path))
    doc.close()
    return str(path)


def _get_text(path) -> str:
    doc = fitz.open(path)
    text = "".join(page.get_text() for page in doc)
    doc.close()
    return text


def _raw_contents(path) -> bytes:
    """Concatenated, decompressed page content-stream bytes — where the text
    operators physically live. If a term is gone from get_text() but present
    here, it was painted over, not redacted."""
    doc = fitz.open(path)
    raw = b"".join(page.read_contents() for page in doc)
    doc.close()
    return raw


def _acroform_values(path) -> list:
    """Every /V value string reachable in the saved PDF."""
    vals = []
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            try:
                if isinstance(obj, pikepdf.Dictionary) and "/V" in obj:
                    vals.append(str(obj["/V"]))
            except Exception:
                continue
    return vals


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
class TestRedactPdf:
    def test_no_terms_or_rects_raises(self, tmp_path):
        # Guard fires before opening the file, so the input need not exist.
        with pytest.raises(ValueError):
            redact_pdf(str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"))

    @pytest.mark.integration
    def test_redacted_text_gone_from_get_text(self, tmp_path):
        src = _text_pdf(tmp_path / "t.pdf",
                        "SECRET_TERM appears and also secret_term lowercase")
        out = str(tmp_path / "t_out.pdf")
        redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert "SECRET_TERM" not in _get_text(out)

    @pytest.mark.integration
    def test_redacted_text_gone_from_raw_bytes(self, tmp_path):
        # Physically stripped from the content stream, not painted over — this
        # is the assertion a painting-over regression would fail.
        src = _text_pdf(tmp_path / "t.pdf",
                        "SECRET_TERM appears and also secret_term lowercase")
        out = str(tmp_path / "t_out.pdf")
        redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert b"SECRET_TERM" not in _raw_contents(out)

    @pytest.mark.integration
    def test_redaction_count_reported(self, tmp_path):
        src = _text_pdf(tmp_path / "c.pdf", "MARK here MARK there MARK everywhere")
        out = str(tmp_path / "c_out.pdf")
        result = redact_pdf(src, out, search_terms=["MARK"])
        assert isinstance(result, RedactResult)
        assert result.redaction_count == 3      # three matches on the page
        assert result.pages_affected == 1

    @pytest.mark.integration
    def test_case_sensitive_filter(self, tmp_path):
        text = "SECRET_TERM upper and secret_term lower"

        # case_sensitive=True: only the exact-case match is removed.
        src = _text_pdf(tmp_path / "cs.pdf", text)
        cs_out = str(tmp_path / "cs_out.pdf")
        redact_pdf(src, cs_out, search_terms=["SECRET_TERM"], case_sensitive=True)
        cs = _get_text(cs_out)
        assert "SECRET_TERM" not in cs
        assert "secret_term" in cs               # lowercase survives

        # case_sensitive=False: both are removed.
        src2 = _text_pdf(tmp_path / "ci.pdf", text)
        ci_out = str(tmp_path / "ci_out.pdf")
        redact_pdf(src2, ci_out, search_terms=["SECRET_TERM"], case_sensitive=False)
        ci = _get_text(ci_out)
        assert "SECRET_TERM" not in ci
        assert "secret_term" not in ci

    @pytest.mark.integration
    def test_form_field_value_removed(self, tmp_path):
        # AcroForm widget values live in /V + a separate appearance stream;
        # apply_redactions() alone leaves them fully extractable (the leak this
        # guards). A redaction rect over the widget must strip the value.
        src = _form_pdf(tmp_path / "f.pdf", "SECRET_FIELD_VALUE")
        assert "SECRET_FIELD_VALUE" in _get_text(src)           # present before
        out = str(tmp_path / "f_out.pdf")
        redact_pdf(src, out,
                   rects=[{"page": 0, "x0": 60, "y0": 60, "x1": 420, "y1": 130}])
        assert "SECRET_FIELD_VALUE" not in _get_text(out)       # not extractable
        assert "SECRET_FIELD_VALUE" not in "".join(_acroform_values(out))  # /V gone

    @pytest.mark.integration
    def test_image_only_page_only_boxed_region_redacted(self, tmp_path):
        # RED-01: on a scanned page (one full-page image), a small redaction
        # rect must black out ONLY the boxed region — not delete the whole
        # image and blank the page (the PDF_REDACT_IMAGE_REMOVE bug).
        src = _image_only_pdf(tmp_path / "scan.pdf", size=300, color=(255, 0, 0))
        out = str(tmp_path / "scan_out.pdf")

        result = redact_pdf(src, out, rects=[{"page": 0, "x0": 50, "y0": 50,
                                              "x1": 100, "y1": 100}])
        assert result.redaction_count == 1

        with fitz.open(out) as doc:
            page = doc[0]
            assert len(page.get_images(full=True)) >= 1   # image NOT removed
            pix = page.get_pixmap()                        # 300×300 @ 72 DPI (1pt=1px)
            inside = pix.pixel(75, 75)                     # under the redaction rect
            outside = pix.pixel(250, 250)                  # well outside it

        # Boxed region blacked out…
        assert max(inside) < 60, f"inside rect not redacted: {inside}"
        # …and the rest of the scan is intact (still red), not blanked.
        assert outside[0] > 200 and outside[1] < 60 and outside[2] < 60, \
            f"outside rect not preserved: {outside}"

    @pytest.mark.integration
    def test_result_carries_verification_report(self, tmp_path):
        src = _text_pdf(tmp_path / "v.pdf", "the SECRET_TERM is here")
        out = str(tmp_path / "v_out.pdf")
        res = redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert res.verification is not None
        assert res.verification["verified"] is True
        assert res.verification["tool"] == "redaction"
        assert res.surface_counts.get("page_content", 0) >= 1


# ═══════════════════════════════════════════════════════════════════
#  #99 — widened scrub, fail-closed verification, flatten fallback
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactWidenedScrub:
    """Each document-level surface apply_redactions never touches must be
    scrubbed whole-value and re-verify clean (#99). Independent-channel
    assertion: re-run verify_redaction on the output."""

    TERM = "ACME99Z"

    def _run_and_verify(self, src, out):
        res = redact_pdf(src, out, search_terms=[self.TERM])
        assert res.verification["verified"] is True
        assert verify_redaction(out, [self.TERM]).verified is True
        return res

    def test_docinfo_standard_key(self, tmp_path):
        p = str(tmp_path / "info.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), f"body {self.TERM}")
        doc.set_metadata({"title": f"{self.TERM} lease", "author": self.TERM})
        doc.save(p)
        doc.close()
        res = self._run_and_verify(p, str(tmp_path / "o.pdf"))
        assert res.surface_counts.get("docinfo", 0) >= 1

    def test_docinfo_custom_key(self, tmp_path):
        # A custom /Info key fitz.metadata can't see — must still be scrubbed.
        base = str(tmp_path / "base.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), f"body {self.TERM}")
        doc.save(base)
        doc.close()
        p = str(tmp_path / "custom.pdf")
        with pikepdf.open(base) as pdf:
            pdf.trailer["/Info"] = pdf.make_indirect(pikepdf.Dictionary(
                Company=pikepdf.String(f"unit {self.TERM}")))
            pdf.save(p)
        self._run_and_verify(p, str(tmp_path / "o.pdf"))

    def test_xmp(self, tmp_path):
        base = str(tmp_path / "base.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), f"body {self.TERM}")
        doc.save(base)
        doc.close()
        p = str(tmp_path / "xmp.pdf")
        with pikepdf.open(base) as pdf:
            with pdf.open_metadata(set_pikepdf_as_editor=False) as m:
                m["dc:description"] = f"about {self.TERM}"
            pdf.save(p)
        res = self._run_and_verify(p, str(tmp_path / "o.pdf"))
        assert res.surface_counts.get("xmp", 0) >= 1

    def test_bookmark_title_whole_value_removed(self, tmp_path):
        p = str(tmp_path / "toc.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), f"body {self.TERM}")
        doc.set_toc([[1, f"{self.TERM}'s Q3 Report", 1]])
        doc.save(p)
        doc.close()
        out = str(tmp_path / "o.pdf")
        self._run_and_verify(p, out)
        # D2: the whole title is gone, not spliced to "'s Q3 Report".
        with fitz.open(out) as d:
            for entry in d.get_toc(simple=True):
                assert "Q3 Report" not in entry[1]

    def test_annotation_contents(self, tmp_path):
        p = str(tmp_path / "annot.pdf")
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 200), f"body {self.TERM}")
        page.add_freetext_annot(fitz.Rect(72, 72, 300, 120), f"note {self.TERM}")
        doc.save(p)
        doc.close()
        self._run_and_verify(p, str(tmp_path / "o.pdf"))

    def test_link_uri(self, tmp_path):
        p = str(tmp_path / "link.pdf")
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 200), f"body {self.TERM}")
        page.insert_link({"kind": fitz.LINK_URI,
                          "from": fitz.Rect(72, 72, 200, 90),
                          "uri": f"https://x.com/?u={self.TERM}"})
        doc.save(p)
        doc.close()
        res = self._run_and_verify(p, str(tmp_path / "o.pdf"))
        assert res.surface_counts.get("links", 0) >= 1

    def test_embedded_file_name(self, tmp_path):
        p = str(tmp_path / "emb.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), f"body {self.TERM}")
        doc.embfile_add(f"{self.TERM}.txt", b"payload")
        doc.save(p)
        doc.close()
        self._run_and_verify(p, str(tmp_path / "o.pdf"))


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactHitCount:
    """§3: a term present only in a document-level surface still counts as a
    hit — the run succeeds and reports the surface, instead of raising the
    zero-match error."""

    def test_title_only_succeeds_and_reports_docinfo(self, tmp_path):
        p = str(tmp_path / "t.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "nothing sensitive on this page")
        doc.set_metadata({"title": "ACME99Z confidential"})
        doc.save(p)
        doc.close()
        out = str(tmp_path / "o.pdf")
        res = redact_pdf(p, out, search_terms=["ACME99Z"])
        assert res.redaction_count == 0            # no page hit
        assert res.surface_counts.get("docinfo", 0) >= 1
        assert res.verification["verified"] is True

    def test_genuinely_absent_term_still_raises_zero_match(self, tmp_path):
        p = _text_pdf(tmp_path / "clean.pdf", "a totally clean document")
        out = str(tmp_path / "o.pdf")
        with pytest.raises(ValueError, match="No matching content"):
            redact_pdf(p, out, search_terms=["ABSENT_TERM_XYZ"])
        assert not os.path.exists(out)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactFailClosed:
    """The core #99 guarantee: a redaction that cannot be proven clean must
    raise and leave no output on disk."""

    def test_no_op_apply_redactions_raises_and_deletes_output(
            self, tmp_path, monkeypatch):
        src = _text_pdf(tmp_path / "s.pdf", "leak SECRET_TERM leak")
        out = str(tmp_path / "s_out.pdf")
        monkeypatch.setattr(fitz.Page, "apply_redactions",
                            lambda self, **k: None)
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=["SECRET_TERM"])
        # Page-content residual → flatten offered on the right page (1-based).
        assert ei.value.flatten_pages == [1]
        assert ei.value.document_level is False
        assert ei.value.report is not None
        assert not os.path.exists(out)            # no half-verified file left

    def test_document_level_residual_hard_raises_no_flatten(
            self, tmp_path, monkeypatch):
        # Scrub the doc-level surface into a no-op so a title term survives;
        # page content is clean → the only residual is document-level, which
        # flatten cannot fix, so no offer.
        src = str(tmp_path / "d.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "clean page body")
        doc.set_metadata({"title": "SECRET_TERM report"})
        doc.save(src)
        doc.close()
        monkeypatch.setattr("pdf_ops._scrub_document_surfaces",
                            lambda *a, **k: {"docinfo": 1})   # claims a hit, removes nothing
        out = str(tmp_path / "d_out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert ei.value.document_level is True
        assert ei.value.flatten_pages is None
        assert not os.path.exists(out)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactFlatten:
    """D1 phase two: the flatten fallback rasterizes only the named pages,
    leaves the others' text selectable, and re-verifies clean."""

    def test_flatten_named_page_only(self, tmp_path, monkeypatch):
        src = str(tmp_path / "m.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "SECRET_TERM on page one")
        doc.new_page().insert_text((72, 72), "ordinary text on page two")
        doc.save(src)
        doc.close()
        out = str(tmp_path / "m_out.pdf")

        # Force page-content to survive so the first call offers flatten.
        monkeypatch.setattr(fitz.Page, "apply_redactions",
                            lambda self, **k: None)
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert ei.value.flatten_pages == [1]

        # Retry with flatten: page 1 rasterized, page 2 text intact, verifies.
        res = redact_pdf(src, out, search_terms=["SECRET_TERM"],
                         flatten_pages=ei.value.flatten_pages)
        assert res.flattened_pages == [1]
        assert res.verification["verified"] is True
        with fitz.open(out) as d:
            assert d[0].get_text().strip() == ""            # page 1 flattened
            assert "ordinary text" in d[1].get_text()       # page 2 preserved

    def test_flatten_still_failing_hard_raises_no_second_offer(
            self, tmp_path, monkeypatch):
        src = _text_pdf(tmp_path / "f.pdf", "SECRET_TERM leaks")
        out = str(tmp_path / "f_out.pdf")
        monkeypatch.setattr(fitz.Page, "apply_redactions",
                            lambda self, **k: None)
        # Flatten is also a no-op → the leak survives the retry too.
        monkeypatch.setattr("pdf_ops._flatten_pages_to_image",
                            lambda s, d, p, **k: __import__("shutil").copyfile(s, d))
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=["SECRET_TERM"], flatten_pages=[1])
        assert ei.value.flatten_pages is None       # no third fallback offered
        assert not os.path.exists(out)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactErrorTail:
    """Error-tail parity with sanitize (previously untested in redact)."""

    def test_password_protected_input(self, tmp_path):
        plain = _text_pdf(tmp_path / "p.pdf", "SECRET here")
        enc = str(tmp_path / "enc.pdf")
        with pikepdf.open(plain) as pdf:
            pdf.save(enc, encryption=pikepdf.Encryption(owner="o", user="u"))
        out = str(tmp_path / "o.pdf")
        # Wrong/no password → clear ValueError, no output.
        with pytest.raises(ValueError):
            redact_pdf(enc, out, search_terms=["SECRET"])
        assert not os.path.exists(out)
        # Correct password → succeeds and verifies.
        res = redact_pdf(enc, out, search_terms=["SECRET"], password="u")
        assert res.verification["verified"] is True

    def test_cancellation(self, tmp_path):
        import threading
        src = _text_pdf(tmp_path / "c.pdf", "SECRET_TERM here")
        out = str(tmp_path / "c_out.pdf")
        evt = threading.Event()
        evt.set()
        with pytest.raises(ValueError, match="Cancelled"):
            redact_pdf(src, out, search_terms=["SECRET_TERM"], cancel=evt)
        assert not os.path.exists(out)

    def test_out_of_range_pages_ignored(self, tmp_path):
        src = _text_pdf(tmp_path / "r.pdf", "SECRET_TERM here")
        out = str(tmp_path / "r_out.pdf")
        # Page index 9 doesn't exist → no page hit; term isn't anywhere else
        # either → zero-match raise, no output.
        with pytest.raises(ValueError, match="No matching content"):
            redact_pdf(src, out, search_terms=["SECRET_TERM"], pages=[9])
        assert not os.path.exists(out)

    def test_empty_terms_raise_before_open(self, tmp_path):
        with pytest.raises(ValueError):
            redact_pdf(str(tmp_path / "x.pdf"), str(tmp_path / "y.pdf"),
                       search_terms=["", "   "])

    def test_save_failure_leaves_no_output(self, tmp_path, monkeypatch):
        src = _text_pdf(tmp_path / "s.pdf", "SECRET_TERM here")
        out = str(tmp_path / "s_out.pdf")

        def _boom(self, *a, **k):
            raise RuntimeError("simulated save failure")

        monkeypatch.setattr(fitz.Document, "save", _boom)
        with pytest.raises(RuntimeError):
            redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert not os.path.exists(out)
        # No stray temp .pdf left beside the output either.
        strays = [f for f in os.listdir(tmp_path) if f.endswith(".pdf")
                  and f not in ("s.pdf",)]
        assert strays == []


class TestProtectPdf:
    """OPS-01: without a distinct owner password, owner == user, so anyone
    holding the user password also holds owner rights and can strip every
    permission restriction. protect_pdf must generate a random owner
    password when the caller doesn't supply one."""

    def test_round_trip_with_password(self, sample_pdf, tmp_path):
        out = str(tmp_path / "protected.pdf")
        protect_pdf(sample_pdf, out, user_password="userpass123")

        with pytest.raises(pikepdf.PasswordError):
            pikepdf.open(out)  # no password -> refused

        with pikepdf.open(out, password="userpass123") as pdf:
            assert len(pdf.pages) >= 1

    def test_no_owner_password_still_denies_owner_rights(self, sample_pdf, tmp_path):
        out = str(tmp_path / "protected.pdf")
        protect_pdf(
            sample_pdf, out, user_password="userpass123", owner_password="",
            permissions={"print": False, "copy": False, "edit": False, "annotate": False},
        )

        with pikepdf.open(out, password="userpass123") as pdf:
            assert pdf.user_password_matched is True
            # This is the bug: without a distinct owner password, the user
            # password matches as owner too, granting full bypass rights.
            assert pdf.owner_password_matched is False

    def test_distinct_owner_password_is_respected(self, sample_pdf, tmp_path):
        out = str(tmp_path / "protected.pdf")
        protect_pdf(
            sample_pdf, out, user_password="userpass123",
            owner_password="ownerpass456",
        )

        with pikepdf.open(out, password="ownerpass456") as pdf:
            assert pdf.owner_password_matched is True

        with pikepdf.open(out, password="userpass123") as pdf:
            assert pdf.owner_password_matched is False


class TestUnlockPdf:
    def test_unlock_removes_password(self, sample_pdf, tmp_path):
        protected = str(tmp_path / "protected.pdf")
        unlocked = str(tmp_path / "unlocked.pdf")
        protect_pdf(sample_pdf, protected, user_password="userpass123")

        unlock_pdf(protected, unlocked, password="userpass123")

        with pikepdf.open(unlocked) as pdf:  # no password needed
            assert len(pdf.pages) >= 1


class TestAddWatermark:
    @pytest.mark.integration
    def test_basic_watermark(self, sample_pdf, tmp_path):
        out = str(tmp_path / "watermarked.pdf")
        add_watermark(sample_pdf, out, text="CONFIDENTIAL")
        assert os.path.isfile(out)
        with pikepdf.open(out) as pdf:
            assert len(pdf.pages) >= 1

    def test_malformed_page_range_does_not_leak_file_handle(self, sample_pdf, tmp_path):
        # OPS-03: a malformed page_range used to raise with `src` (the open
        # pikepdf.Pdf) never closed. On Windows this leaves the input file
        # locked. The exception is caught (not via pytest.raises) and held
        # in `caught`, deliberately keeping its traceback -- and therefore
        # `src`'s frame -- alive, mirroring how a real caller (e.g. the
        # bridge) holds the exception to build an error message before it's
        # released; CPython's refcounting GC would otherwise close the
        # leaked handle "by accident" the moment the traceback is dropped,
        # masking the bug (pytest.raises releases it too promptly to catch
        # this).
        out = str(tmp_path / "watermarked.pdf")
        caught = None
        try:
            add_watermark(sample_pdf, out, page_range="not-a-range")
        except ValueError as e:
            caught = e
        assert caught is not None
        os.remove(sample_pdf)  # must not raise -- proves the handle was released

    def test_malformed_color_does_not_leak_file_handle(self, sample_pdf, tmp_path):
        out = str(tmp_path / "watermarked.pdf")
        caught = None
        try:
            add_watermark(sample_pdf, out, color="#88")  # too short to parse
        except ValueError as e:
            caught = e
        assert caught is not None
        os.remove(sample_pdf)  # must not raise -- proves the handle was released


class TestImagesToPdf:
    """OPS-05: a genuinely lossless source (PNG) must be embedded losslessly
    (Flate), not silently re-encoded to lossy JPEG despite the docstring's
    "preserves image quality" claim. An already-lossy JPEG source is still
    fine to re-encode."""

    @staticmethod
    def _make_noisy_image(w=60, h=40, seed=1):
        import random
        rng = random.Random(seed)
        pixels = bytes(rng.randrange(256) for _ in range(w * h * 3))
        return Image.frombytes("RGB", (w, h), pixels)

    def test_png_source_embedded_losslessly(self, tmp_path):
        img = self._make_noisy_image()
        png_path = str(tmp_path / "src.png")
        img.save(png_path, format="PNG")
        out = str(tmp_path / "out.pdf")

        images_to_pdf([png_path], out, page_size="auto", margin_mm=0)

        with pikepdf.open(out) as pdf:
            xobj = pdf.pages[0]["/Resources"]["/XObject"]["/Img0"]
            assert str(xobj.get("/Filter")) == "/FlateDecode"
            decoded = pikepdf.PdfImage(xobj).as_pil_image()
            assert decoded.tobytes() == img.tobytes()

    def test_jpeg_source_still_jpeg_encoded(self, tmp_path):
        img = self._make_noisy_image()
        jpeg_path = str(tmp_path / "src.jpg")
        img.save(jpeg_path, format="JPEG", quality=95)
        out = str(tmp_path / "out.pdf")

        images_to_pdf([jpeg_path], out, page_size="auto", margin_mm=0)

        with pikepdf.open(out) as pdf:
            xobj = pdf.pages[0]["/Resources"]["/XObject"]["/Img0"]
            assert str(xobj.get("/Filter")) == "/DCTDecode"
