"""Tests for pdf_ops.py — merge, split, page operations, and more."""

import os

import pikepdf
import pytest

from pdf_ops import (
    MergeResult,
    PageOpResult,
    RedactResult,
    SplitResult,
    _parse_ranges,
    _sanitize_title,
    apply_page_operations,
    contained_output_path,
    extract_text,
    flatten_pdf,
    get_toc,
    merge_pdfs,
    read_metadata,
    redact_pdf,
    repair_pdf,
    split_pdf,
    write_metadata,
)

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


class TestFlatten:
    @pytest.mark.integration
    def test_flatten(self, sample_pdf, tmp_path):
        out = str(tmp_path / "flat.pdf")
        flatten_pdf(sample_pdf, out)
        assert os.path.isfile(out)
        # Verify the output opens as a valid PDF
        with pikepdf.open(out) as pdf:
            assert len(pdf.pages) >= 1


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
