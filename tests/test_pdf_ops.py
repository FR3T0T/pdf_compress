"""Tests for pdf_ops.py — merge, split, page operations, and more."""

import os

import pikepdf
import pytest

from pdf_ops import (
    MergeResult,
    PageOpResult,
    SplitResult,
    _parse_ranges,
    apply_page_operations,
    extract_text,
    flatten_pdf,
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
