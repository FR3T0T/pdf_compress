"""Tests for pdf_ops.py — merge, split, page operations, and more."""

import hashlib
import io
import os
import shutil

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

    def test_no_op_apply_redactions_raises_and_writes_no_output(
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
        # Flatten is also a no-op → the leak survives the retry too. (It
        # reports the areas it blacked out on each picture: none.)
        monkeypatch.setattr("pdf_ops._flatten_pages_to_image",
                            lambda s, d, p, **k: __import__("shutil").copyfile(s, d) and {})
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


# ═══════════════════════════════════════════════════════════════════
#  Redact hardening — verify-then-commit, file identifier, XMP removal
# ═══════════════════════════════════════════════════════════════════


def _file_bytes(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _trailer_ids(path, password=None) -> list:
    """Hex of each string in the trailer /ID ([] when there is none). A /ID
    that is a bare string instead of an array counts as one element."""
    kw = {"password": password} if password else {}
    with pikepdf.open(path, **kw) as pdf:
        tid = pdf.trailer.get("/ID")
        if tid is None:
            return []
        return [bytes(x).hex() for x in (tid if isinstance(tid, pikepdf.Array) else [tid])
                if isinstance(x, pikepdf.String)]


def _handmade_pdf(trailer_id=None, extra_objects=(),
                  text="account SECRET_TERM 12345") -> bytes:
    """A complete one-page PDF assembled byte by byte, drawing *text* at
    (72, 72) from the top-left in Helvetica.

    For the trailer /ID shapes no library will write: *trailer_id* is the
    PDF syntax placed after ``/ID`` (None leaves the entry out), and
    *extra_objects* become objects 6, 7, … for it to reference. No library
    produces any of it and nothing in it is random, so the same arguments
    always give the same bytes. (Patching a fitz-saved file instead is
    flaky: MuPDF writes a fresh identifier as hex or as a literal string
    depending on the random bytes it drew.)
    """
    content = b"BT /F1 12 Tf 72 720 Td (" + text.encode("ascii") + b") Tj ET"
    bodies = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(content), content),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        *extra_objects,
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for num, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (num, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(bodies) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<</Size %d/Root 1 0 R" % (len(bodies) + 1)
    if trailer_id is not None:
        out += b"/ID " + trailer_id
    out += b">>\nstartxref\n%d\n%%%%EOF\n" % xref_at
    return bytes(out)


def _with_trailer_id(path, trailer_id, extra_objects=()) -> str:
    """Write _handmade_pdf(trailer_id, extra_objects) to *path*."""
    with open(path, "wb") as f:
        f.write(_handmade_pdf(trailer_id, extra_objects))
    return str(path)


# label -> (syntax after /ID, extra objects, the elements it must read as).
# None for the elements means "malformed, compared by serialization": only
# agreement between the two readers is asserted, not a spelling.
_ID_SHAPES = {
    "two-strings": (b"[<0123456789ABCDEF><FEDCBA9876543210>]", (),
                    ["0123456789abcdef", "fedcba9876543210"]),
    "absent": (None, (), []),
    "bare-hex": (b"<0123456789ABCDEF>", (), ["0123456789abcdef"]),
    "bare-literal": (rb"(id\(1\)\\x\012\377~)", (), [b"id(1)\\x\n\xff~".hex()]),
    "bare-utf16-bom": (b"<FEFF00410042>", (), ["feff00410042"]),  # fitz says "AB"
    "one-element": (b"[<AB12>]", (), ["ab12"]),
    "empty-array": (b"[]", (), []),
    "non-string-elements": (b"[/Foo 12 true]", (), None),
    "bare-name": (b"/Foo", (), None),
    "indirect-array": (b"6 0 R", (b"[<AB12><CD34>]",), ["ab12", "cd34"]),
    "indirect-bare-string": (b"6 0 R", (b"<AB12>",), ["ab12"]),
    "indirect-elements": (b"[6 0 R 7 0 R]", (b"<AB12>", b"<CD34>"), ["ab12", "cd34"]),
}


def _content_id_rewrite(path, dst) -> bytes:
    """The write a redaction must end with, done independently of pdf_ops:
    drop the trailer /ID and let qpdf derive a new one from the output's own
    content. If redact_pdf wrote its identifier this way, and wrote it last,
    doing it once more changes nothing."""
    with pikepdf.open(path) as pdf:
        if "/ID" in pdf.trailer:
            del pdf.trailer["/ID"]
        pdf.save(dst, deterministic_id=True)
    return _file_bytes(dst)


def _leak_page_content(monkeypatch):
    """Turn the page-content redaction into a no-op so verification refuses."""
    monkeypatch.setattr(fitz.Page, "apply_redactions", lambda self, **k: None)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactVerifyThenCommit:
    """The candidate is verified in a temp beside the destination and only a
    verified candidate is moved into place. A refused run never creates,
    replaces, or deletes output_path."""

    def test_in_place_refusal_leaves_original_byte_identical(
            self, tmp_path, monkeypatch):
        src = _text_pdf(tmp_path / "only_copy.pdf", "leak SECRET_TERM leak")
        before = _file_bytes(src)
        _leak_page_content(monkeypatch)
        with pytest.raises(RedactionVerificationError):
            redact_pdf(src, src, search_terms=["SECRET_TERM"])
        assert os.path.exists(src), "the refusal deleted the user's only copy"
        assert _file_bytes(src) == before
        assert os.listdir(tmp_path) == ["only_copy.pdf"]      # no stray temp

    def test_refusal_keeps_a_pre_existing_destination(self, tmp_path, monkeypatch):
        src = _text_pdf(tmp_path / "s.pdf", "leak SECRET_TERM leak")
        out = tmp_path / "earlier_export.pdf"
        out.write_bytes(b"an earlier export the user still wants")
        _leak_page_content(monkeypatch)
        with pytest.raises(RedactionVerificationError):
            redact_pdf(src, str(out), search_terms=["SECRET_TERM"])
        assert out.read_bytes() == b"an earlier export the user still wants"
        assert sorted(os.listdir(tmp_path)) == ["earlier_export.pdf", "s.pdf"]

    def test_refusal_with_distinct_output_leaves_no_file(self, tmp_path, monkeypatch):
        src = _text_pdf(tmp_path / "s.pdf", "leak SECRET_TERM leak")
        out = str(tmp_path / "s_out.pdf")
        _leak_page_content(monkeypatch)
        with pytest.raises(RedactionVerificationError):
            redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert os.listdir(tmp_path) == ["s.pdf"]              # no output, no temp

    def test_destination_is_not_written_until_verified(self, tmp_path, monkeypatch):
        src = _text_pdf(tmp_path / "s.pdf", "the SECRET_TERM is here")
        out = str(tmp_path / "s_out.pdf")
        seen = {}

        def spy(path, *a, **k):
            seen["path"], seen["dest_existed"] = path, os.path.exists(out)
            return verify_redaction(path, *a, **k)

        monkeypatch.setattr("pdf_ops.verify_redaction", spy)
        res = redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert seen["dest_existed"] is False, \
            "the output was committed before it was verified"
        # Same directory as the destination, so the commit is one atomic rename.
        assert os.path.dirname(seen["path"]) == str(tmp_path)
        assert seen["path"] != out
        assert sorted(os.listdir(tmp_path)) == ["s.pdf", "s_out.pdf"]
        # The proof names the file the user actually has, not the temp.
        assert res.output_path == out
        assert res.verification["outputPath"] == out
        assert res.verification["verified"] is True

    def test_refusal_report_names_destination_never_the_temp(
            self, tmp_path, monkeypatch):
        from pdf_verify import VerificationCheck, VerificationReport
        src = _text_pdf(tmp_path / "s.pdf", "the SECRET_TERM is here")
        out = str(tmp_path / "s_out.pdf")
        seen = {}

        def unreadable(path, *a, **k):
            # What verify_redaction reports for an output it cannot open: the
            # library's error text routinely quotes the path it was given.
            seen["path"] = path
            rep = VerificationReport(input_path=src, output_path=path,
                                     tool="redaction", timestamp="t")
            rep.checks.append(VerificationCheck(
                id="redaction.readable", description="opens", passed=False,
                evidence=[f"output could not be opened: cannot open {path} "
                          f"(document-level)"]))
            return rep

        monkeypatch.setattr("pdf_ops.verify_redaction", unreadable)
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=["SECRET_TERM"])
        rep = ei.value.report
        assert rep["outputPath"] == out
        assert os.path.basename(seen["path"]) not in repr(rep)
        assert os.listdir(tmp_path) == ["s.pdf"]

    def test_in_place_success_replaces_the_original(self, tmp_path):
        # The commit is a rename onto the input: every handle on both files
        # must be closed by then, or Windows refuses it.
        src = _text_pdf(tmp_path / "doc.pdf", "the SECRET_TERM is here")
        res = redact_pdf(src, src, search_terms=["SECRET_TERM"])
        assert res.verification["verified"] is True
        assert "SECRET_TERM" not in _get_text(src)
        assert os.listdir(tmp_path) == ["doc.pdf"]


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactFileId:
    """No redaction output may carry the source's trailer /ID — on any path —
    and the identifier it does carry is derived from the output's content and
    written last."""

    RECT = {"page": 0, "x0": 60, "y0": 55, "x1": 420, "y1": 85}

    def _two_pages(self, tmp_path) -> str:
        p = str(tmp_path / "src.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "account SECRET_TERM 12345")
        doc.new_page().insert_text((72, 72), "ordinary text on page two")
        doc.save(p)
        doc.close()
        return p

    def _assert_unlinked(self, src, out, tmp_path, password=None):
        src_ids, out_ids = _trailer_ids(src, password), _trailer_ids(out)
        assert src_ids, "fixture must carry a trailer /ID"
        assert out_ids and not set(out_ids) & set(src_ids), \
            f"output is linkable to its source through /ID: {out_ids} vs {src_ids}"
        produced = _file_bytes(out)
        assert _content_id_rewrite(out, str(tmp_path / "again.pdf")) == produced, \
            "the identifier is not content-derived, or was not the last write"

    def test_terms_path(self, tmp_path):
        src, out = self._two_pages(tmp_path), str(tmp_path / "out.pdf")
        redact_pdf(src, out, search_terms=["SECRET_TERM"])
        self._assert_unlinked(src, out, tmp_path)

    def test_rects_only_path(self, tmp_path):
        src, out = self._two_pages(tmp_path), str(tmp_path / "out.pdf")
        redact_pdf(src, out, rects=[self.RECT])
        self._assert_unlinked(src, out, tmp_path)

    def test_flatten_path(self, tmp_path):
        src, out = self._two_pages(tmp_path), str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["SECRET_TERM"], flatten_pages=[1])
        assert res.flattened_pages == [1]
        self._assert_unlinked(src, out, tmp_path)

    def test_encrypted_source(self, tmp_path):
        plain, enc = self._two_pages(tmp_path), str(tmp_path / "enc.pdf")
        with pikepdf.open(plain) as pdf:
            pdf.save(enc, encryption=pikepdf.Encryption(owner="o", user="u"))
        out = str(tmp_path / "out.pdf")
        redact_pdf(enc, out, search_terms=["SECRET_TERM"], password="u")
        self._assert_unlinked(enc, out, tmp_path, password="u")

    def test_report_carries_the_check_and_both_hashes(self, tmp_path):
        src, out = self._two_pages(tmp_path), str(tmp_path / "out.pdf")
        src_sha = hashlib.sha256(_file_bytes(src)).hexdigest()
        res = redact_pdf(src, out, search_terms=["SECRET_TERM"])
        checks = {c["id"]: c for c in res.verification["checks"]}
        assert checks["redaction.file_id"]["passed"] is True
        assert res.verification["sourceSha256"] == src_sha
        assert res.verification["outputSha256"] == \
            hashlib.sha256(_file_bytes(out)).hexdigest()

    def test_kept_source_id_is_a_document_level_refusal(self, tmp_path, monkeypatch):
        # A final write that keeps the source identifier (plain qpdf save
        # preserves /ID[0]) must be refused, and flatten must not be offered:
        # rasterizing pages cannot change a trailer entry.
        src, out = self._two_pages(tmp_path), str(tmp_path / "out.pdf")
        monkeypatch.setattr("pdf_ops.save_with_content_id",
                            lambda pdf, path, **kw: pdf.save(path))
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=["SECRET_TERM"])
        failed = [c["id"] for c in ei.value.report["checks"] if not c["passed"]]
        assert failed == ["redaction.file_id"]
        assert "identifier" in str(ei.value)
        assert ei.value.document_level is True
        assert ei.value.flatten_pages is None
        assert os.listdir(tmp_path) == ["src.pdf"]

    def test_source_without_an_id(self, tmp_path):
        src, out = str(tmp_path / "noid.pdf"), str(tmp_path / "out.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "account SECRET_TERM 12345")
        doc.save(src, no_new_id=True)
        doc.close()
        assert _trailer_ids(src) == []
        res = redact_pdf(src, out, search_terms=["SECRET_TERM"])
        checks = {c["id"]: c for c in res.verification["checks"]}
        assert checks["redaction.file_id"]["passed"] is True
        assert _content_id_rewrite(out, str(tmp_path / "again.pdf")) == _file_bytes(out)

    def test_unreadable_source_identity_refuses_before_writing(
            self, tmp_path, monkeypatch):
        # No /ID shape gets here (see TestSourceFileIds). This is the file
        # itself failing to give up its identity — e.g. it cannot be hashed.
        src, out = self._two_pages(tmp_path), str(tmp_path / "out.pdf")
        before = _file_bytes(src)

        def _boom(path):
            raise OSError("simulated read failure")

        monkeypatch.setattr("pdf_ops.file_sha256", _boom)
        with pytest.raises(ValueError, match="identity"):
            redact_pdf(src, out, search_terms=["SECRET_TERM"])
        assert os.listdir(tmp_path) == ["src.pdf"]
        assert _file_bytes(src) == before

    @pytest.mark.parametrize("id_syntax", [
        b"<0123456789ABCDEF0123456789ABCDEF>",      # hex
        rb"(id\(1\)\\x\012\377~)",                   # literal, with escapes
        b"<FEFF00410042>",                           # reads as the text "AB"
    ], ids=["hex", "literal", "utf16-bom"])
    @pytest.mark.parametrize("mode", ["terms", "rects"])
    def test_bare_string_id_redacts_and_is_not_carried(self, tmp_path, id_syntax, mode):
        # /ID as a bare string, not an array: MuPDF carries it through a save
        # as is, so it is an identifier like any other.
        src = _with_trailer_id(tmp_path / "bare.pdf", id_syntax)
        with pikepdf.open(src) as pdf:
            assert isinstance(pdf.trailer["/ID"], pikepdf.String)
        (bare,) = _trailer_ids(src)
        out = str(tmp_path / "out.pdf")
        kw = ({"search_terms": ["SECRET_TERM"]} if mode == "terms"
              else {"rects": [self.RECT]})
        res = redact_pdf(src, out, **kw)
        assert bare not in _trailer_ids(out), "the bare-string /ID was carried over"
        with pikepdf.open(out) as pdf:
            assert isinstance(pdf.trailer["/ID"], pikepdf.Array)
        checks = {c["id"]: c for c in res.verification["checks"]}
        assert checks["redaction.file_id"]["passed"] is True
        assert _content_id_rewrite(out, str(tmp_path / "again.pdf")) == _file_bytes(out)

    def test_carried_bare_string_id_is_refused(self, tmp_path, monkeypatch):
        # Make the final write a plain copy of what fitz saved — which still
        # has the bare string — to show it was captured as an element the
        # check can match, not read as "no identifier".
        src = _with_trailer_id(tmp_path / "bare.pdf", b"<0123456789ABCDEF>")
        out = str(tmp_path / "out.pdf")
        monkeypatch.setattr("pdf_ops.save_with_content_id",
                            lambda pdf, path, **kw: shutil.copyfile(pdf.filename, path))
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, rects=[self.RECT])
        failed = {c["id"]: c for c in ei.value.report["checks"] if not c["passed"]}
        assert list(failed) == ["redaction.file_id"]
        assert "0123456789abcdef" in " ".join(failed["redaction.file_id"]["evidence"])
        assert ei.value.flatten_pages is None
        assert os.listdir(tmp_path) == ["bare.pdf"]

    @pytest.mark.parametrize("shape", list(_ID_SHAPES))
    def test_no_id_shape_is_refused(self, tmp_path, shape):
        # Boxes only: the path where fitz's save is the last one before the
        # identifier is rewritten, so whatever sat in /ID is still there.
        trailer_id, extra, _ = _ID_SHAPES[shape]
        src = _with_trailer_id(tmp_path / "src.pdf", trailer_id, extra)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[self.RECT])
        assert res.verification["verified"] is True
        checks = {c["id"]: c for c in res.verification["checks"]}
        assert checks["redaction.file_id"]["passed"] is True
        out_ids = _trailer_ids(out)
        assert len(out_ids) == 2 and not set(out_ids) & set(_trailer_ids(src))


class TestHandmadeFixture:
    """The /ID-shape fixture must be the same bytes every time. Its
    predecessor patched a fitz-saved file and failed about one build in
    fifty, whenever MuPDF happened to write the random identifier it was
    looking for as a literal string instead of hex."""

    @pytest.mark.parametrize("shape", list(_ID_SHAPES))
    def test_same_arguments_same_bytes_every_time(self, shape):
        trailer_id, extra, _ = _ID_SHAPES[shape]
        builds = {_handmade_pdf(trailer_id, extra) for _ in range(300)}
        assert len(builds) == 1
        (raw,) = builds
        # ...and the entry really is what was asked for, exactly once.
        want = b"/Root 1 0 R" + (b"" if trailer_id is None else b"/ID " + trailer_id) + b">>"
        assert raw.count(want) == 1 and raw.count(b"/ID") == (trailer_id is not None)

    @pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
    @pytest.mark.parametrize("shape", ["two-strings", "absent", "indirect-array",
                                       "indirect-elements"])
    def test_well_formed_shapes_need_no_repair(self, tmp_path, shape):
        # The offsets are computed, not recovered: neither library has to
        # repair the file, so tests on it exercise the ordinary read path.
        trailer_id, extra, _ = _ID_SHAPES[shape]
        p = _with_trailer_id(tmp_path / "f.pdf", trailer_id, extra)
        with pikepdf.open(p) as pdf:
            list(pdf.objects)
            assert pdf.get_warnings() == []
        doc = fitz.open(p)
        try:
            assert doc.is_repaired is False
            assert doc[0].search_for("SECRET_TERM")       # the text is findable
        finally:
            doc.close()


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
class TestSourceFileIds:
    """The source's /ID is read through the open fitz handle and must come
    out as the same values pikepdf reads from the same file — that is what
    the verifier compares the output against. No shape of /ID is refused."""

    def _from_handle(self, path):
        # The file path handed over does not exist, so the read-the-file
        # fallback cannot be what produced the answer.
        from pdf_ops import _source_file_ids
        doc = fitz.open(path)
        try: return _source_file_ids(doc, str(path) + ".absent")
        finally: doc.close()

    def _from_pikepdf(self, path):
        from pdf_analyze import trailer_ids
        with pikepdf.open(path) as pdf:
            return trailer_ids(pdf)

    @pytest.mark.parametrize(
        "shape", [s for s in _ID_SHAPES if s != "indirect-elements"])
    def test_every_shape_is_captured_as_pikepdf_reads_it(self, tmp_path, shape):
        trailer_id, extra, expected = _ID_SHAPES[shape]
        p = _with_trailer_id(tmp_path / "odd.pdf", trailer_id, extra)
        got = self._from_handle(p)
        assert got == self._from_pikepdf(p)
        if expected is not None:
            assert got == expected
        else:
            # Malformed elements are kept (by their serialization), not
            # dropped: a copy carrying the same ones must still match.
            assert got and all(got)

    def test_indirect_elements_are_read_from_the_file(self, tmp_path):
        # The handle prints indirect elements unresolved ("6 0 R"), which
        # cannot be decoded out of context; the file is read instead.
        from pdf_ops import _source_file_ids
        trailer_id, extra, expected = _ID_SHAPES["indirect-elements"]
        p = _with_trailer_id(tmp_path / "odd.pdf", trailer_id, extra)
        doc = fitz.open(p)
        try:
            assert _source_file_ids(doc, p) == expected == self._from_pikepdf(p)
            with pytest.raises(FileNotFoundError):       # the one shape that needs it
                _source_file_ids(doc, p + ".absent")
        finally:
            doc.close()

    def test_file_read_uses_the_callers_password(self, tmp_path):
        from pdf_ops import _source_file_ids
        plain = _text_pdf(tmp_path / "plain.pdf", "x")
        enc = str(tmp_path / "enc.pdf")
        with pikepdf.open(plain) as pdf:
            pdf.save(enc, encryption=pikepdf.Encryption(owner="o", user="u"))

        class _Doc:
            def xref_get_key(self, xref, key):
                return ("array", "[5 0 R 6 0 R]")

        assert _source_file_ids(_Doc(), enc, "u") == _trailer_ids(enc, "u") != []

    def test_literal_string_ids_decode_like_pikepdf(self, tmp_path):
        base = _text_pdf(tmp_path / "base.pdf", "x")
        p = str(tmp_path / "literal.pdf")
        with pikepdf.open(base) as pdf:
            # Printable bytes make MuPDF print a literal string with escapes
            # rather than hex: parens, a backslash, a newline, a high byte.
            pdf.trailer["/ID"] = pikepdf.Array([
                pikepdf.String(b"abc(d)e\\f\n\x00\xff~"), pikepdf.String(b"x")])
            pdf.save(p)
        expected = _trailer_ids(p)
        assert expected[0] == b"abc(d)e\\f\n\x00\xff~".hex()
        assert self._from_handle(p) == expected

    def test_indirect_id_array_is_followed(self):
        from pdf_ops import _source_file_ids

        class _Doc:
            def xref_get_key(self, xref, key):
                assert (xref, key) == (-1, "ID")
                return ("xref", "7 0 R")

            def xref_object(self, xref, compressed=False):
                assert xref == 7
                return "[<AB12><cd34>]"

        assert _source_file_ids(_Doc(), "unused.pdf") == ["ab12", "cd34"]

    def test_missing_id_is_an_empty_list(self):
        from pdf_ops import _source_file_ids

        class _Doc:
            def xref_get_key(self, xref, key):
                return ("null", "null")

        assert _source_file_ids(_Doc(), "unused.pdf") == []


# Undeclared dc: prefix and a root element that is never closed: pikepdf's
# recovering parser accepts it, but clearing the parsed view and re-saving
# writes the recovered tree — name included — straight back.
_MALFORMED_XMP = (
    b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about=""><dc:creator>ACME99Z Holdings</dc:creator>'
    b'</rdf:Description></rdf:RDF>\n')


def _with_catalog_xmp(path, body: str, packet: bytes) -> str:
    """One page drawing *body*, with *packet* as the catalog /Metadata stream."""
    base = str(path) + ".base"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), body)
    doc.save(base)
    doc.close()
    with pikepdf.open(base) as pdf:
        st = pdf.make_stream(packet)
        st["/Type"] = pikepdf.Name("/Metadata")
        st["/Subtype"] = pikepdf.Name("/XML")
        pdf.Root["/Metadata"] = st
        # fix_metadata_version (on by default) would re-serialize the packet
        # on the way out and the fixture would no longer be malformed.
        pdf.save(str(path), fix_metadata_version=False)
    os.unlink(base)
    with pikepdf.open(str(path)) as pdf:
        assert pdf.Root.Metadata.read_bytes() == packet
    return str(path)


def _stream_bytes(path) -> bytes:
    """Every stream's decoded bytes, concatenated."""
    out = []
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream):
                try: out.append(obj.read_bytes())
                except Exception: out.append(obj.read_raw_bytes())
    return b"\n".join(out)


@pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
@pytest.mark.integration
class TestRedactXmpRemoval:
    """The scrub deletes a matching XMP stream outright. It never clears a
    parsed view and re-saves it, and it leaves metadata that does not carry
    a term exactly as it found it."""

    TERM = "ACME99Z"

    def test_term_in_malformed_xmp_is_deleted_and_redaction_succeeds(self, tmp_path):
        src = _with_catalog_xmp(tmp_path / "bad.pdf", f"body {self.TERM}",
                                _MALFORMED_XMP)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[self.TERM])
        assert res.verification["verified"] is True
        assert res.surface_counts.get("xmp") == 1
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.Root
        assert b"ACME99Z" not in _stream_bytes(out)
        assert verify_redaction(out, [self.TERM]).verified is True

    def test_term_only_in_unparseable_xmp_counts_as_a_hit(self, tmp_path):
        # Not XML at all: the parsed view is an empty packet, so a scrub that
        # only reads the parsed view reports "no matching content" for a term
        # that is sitting in the file.
        src = _with_catalog_xmp(tmp_path / "junk.pdf", "nothing sensitive here",
                                b"creator=ACME99Z Holdings <<< not xml")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[self.TERM])
        assert res.redaction_count == 0
        assert res.surface_counts == {"xmp": 1}
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.Root
        assert b"ACME99Z" not in _stream_bytes(out)

    def test_term_in_utf16_xmp_is_deleted(self, tmp_path):
        # XMP may be UTF-16; read as UTF-8 the term is interleaved with NULs.
        packet = ("<x:xmpmeta xmlns:x='adobe:ns:meta/'><broken>"
                  "ACME99Z Holdings").encode("utf-16")
        src = _with_catalog_xmp(tmp_path / "u16.pdf", "nothing sensitive here",
                                packet)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[self.TERM])
        assert res.surface_counts == {"xmp": 1}
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.Root

    def test_scrub_writes_no_xmp_stub(self, tmp_path):
        src = _text_pdf(tmp_path / "plain.pdf", f"body {self.TERM}")
        with pikepdf.open(src) as pdf:
            assert "/Metadata" not in pdf.Root                # fixture has none
        out = str(tmp_path / "out.pdf")
        redact_pdf(src, out, search_terms=[self.TERM])
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.Root, \
                "the scrub invented an XMP packet the source never had"

    def test_metadata_without_a_term_is_left_as_found(self, tmp_path):
        # Docinfo and XMP are stripped when a term appears in them (D3) —
        # not rewritten from each other as a side effect of looking.
        src = str(tmp_path / "meta.pdf")
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), f"body {self.TERM}")
        doc.set_metadata({"title": "Quarterly report", "author": "Bob Example"})
        doc.save(src)
        doc.close()
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[self.TERM])
        assert "docinfo" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            info = {str(k): str(v) for k, v in pdf.docinfo.items()}
        assert info.get("/Title") == "Quarterly report"
        assert info.get("/Author") == "Bob Example"


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
