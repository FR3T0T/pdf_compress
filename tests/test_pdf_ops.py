"""Tests for pdf_ops.py — merge, split, page operations, and more."""

import os

import pikepdf
import pytest

from pdf_ops import (
    MergeResult,
    PageOpResult,
    SplitResult,
    _parse_ranges,
    _sanitize_title,
    apply_page_operations,
    extract_text,
    flatten_pdf,
    get_toc,
    merge_pdfs,
    read_metadata,
    repair_pdf,
    split_pdf,
    write_metadata,
)


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
