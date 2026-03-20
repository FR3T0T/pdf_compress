"""Tests for engine.py — compression engine, utilities, and security helpers."""

import os
import shutil
import threading

import pikepdf
import pytest

from engine import (
    PRESET_ORDER,
    PRESETS,
    CancelledError,
    CompressionStats,
    EncryptedPDFError,
    FileTooLargeError,
    InvalidPDFError,
    Result,
    _sanitize_path_for_subprocess,
    analyze_pdf,
    compress_pdf,
    create_backup,
    fmt_size,
    validate_pdf_magic,
)


# ═══════════════════════════════════════════════════════════════════
#  Pure unit tests (no file I/O)
# ═══════════════════════════════════════════════════════════════════


class TestFmtSize:
    def test_bytes(self):
        assert fmt_size(0) == "0 B"
        assert fmt_size(512) == "512 B"
        assert fmt_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert fmt_size(1024) == "1.0 KB"
        assert fmt_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert fmt_size(1048576) == "1.00 MB"
        assert fmt_size(5 * 1048576) == "5.00 MB"


class TestPresets:
    def test_preset_order_matches_keys(self):
        assert set(PRESET_ORDER) == set(PRESETS.keys())

    def test_all_presets_have_valid_dpi(self):
        for key, preset in PRESETS.items():
            assert preset.target_dpi > 0, f"{key}: target_dpi must be > 0"
            assert preset.jpeg_quality > 0, f"{key}: jpeg_quality must be > 0"
            assert preset.jpeg_quality <= 100, f"{key}: jpeg_quality must be <= 100"

    def test_get_dpi_for_color_image(self):
        preset = PRESETS["standard"]
        assert preset.get_dpi_for_image(False, False) == 150

    def test_get_dpi_for_grayscale_image(self):
        # standard preset has gray_dpi=0, so should fall back to target_dpi
        preset = PRESETS["standard"]
        assert preset.get_dpi_for_image(True, False) == preset.target_dpi

    def test_get_dpi_for_mono_image(self):
        # screen preset has mono_dpi=150 (different from target_dpi=72)
        preset = PRESETS["screen"]
        assert preset.get_dpi_for_image(False, True) == 150

    def test_screen_strips_metadata(self):
        assert PRESETS["screen"].strip_metadata is True

    def test_standard_keeps_metadata(self):
        assert PRESETS["standard"].strip_metadata is False


class TestSanitizePath:
    def test_rejects_null_bytes(self):
        with pytest.raises(ValueError, match="null byte"):
            _sanitize_path_for_subprocess("/tmp/evil\x00.pdf")

    def test_rejects_control_chars(self):
        with pytest.raises(ValueError, match="control characters"):
            _sanitize_path_for_subprocess("/tmp/evil\x07.pdf")

    def test_rejects_dash_prefix(self):
        with pytest.raises(ValueError, match="starts with"):
            _sanitize_path_for_subprocess("-evil.pdf")

    def test_accepts_normal_path(self, tmp_path):
        path = str(tmp_path / "normal.pdf")
        result = _sanitize_path_for_subprocess(path)
        assert os.path.isabs(result)

    def test_returns_absolute_path(self):
        result = _sanitize_path_for_subprocess("relative/path.pdf")
        assert os.path.isabs(result)


class TestResultProperties:
    def _make_result(self, original, compressed, skipped=False):
        return Result(
            input_path="in.pdf",
            output_path="out.pdf",
            original_size=original,
            compressed_size=compressed,
            stats=CompressionStats(),
            skipped=skipped,
        )

    def test_saved_bytes(self):
        r = self._make_result(1000, 600)
        assert r.saved_bytes == 400

    def test_saved_pct(self):
        r = self._make_result(1000, 600)
        assert abs(r.saved_pct - 40.0) < 0.01

    def test_saved_bytes_when_skipped(self):
        r = self._make_result(1000, 1000, skipped=True)
        assert r.saved_bytes == 0

    def test_saved_pct_when_skipped(self):
        r = self._make_result(1000, 1000, skipped=True)
        assert r.saved_pct == 0.0

    def test_saved_pct_zero_original(self):
        r = self._make_result(0, 0)
        assert r.saved_pct == 0.0


# ═══════════════════════════════════════════════════════════════════
#  File I/O tests (require fixtures)
# ═══════════════════════════════════════════════════════════════════


class TestValidatePDFMagic:
    def test_valid_pdf(self, sample_pdf):
        assert validate_pdf_magic(sample_pdf) is True

    def test_invalid_file(self, invalid_file):
        assert validate_pdf_magic(invalid_file) is False

    def test_nonexistent_file(self):
        assert validate_pdf_magic("/nonexistent/file.pdf") is False


class TestAnalyzePdf:
    @pytest.mark.integration
    def test_basic_analysis(self, sample_pdf):
        analysis = analyze_pdf(sample_pdf)
        assert analysis.page_count == 1
        assert analysis.file_size > 0
        assert analysis.is_valid_pdf is True
        assert analysis.is_encrypted is False

    @pytest.mark.integration
    def test_image_count(self, sample_pdf):
        analysis = analyze_pdf(sample_pdf)
        assert analysis.image_count >= 1

    @pytest.mark.integration
    def test_encrypted_pdf(self, encrypted_pdf):
        analysis = analyze_pdf(encrypted_pdf)
        assert analysis.is_encrypted is True

    @pytest.mark.integration
    def test_invalid_pdf(self, invalid_file):
        analysis = analyze_pdf(invalid_file)
        assert analysis.is_valid_pdf is False


class TestCompressPdf:
    @pytest.mark.integration
    def test_compress_standard(self, sample_pdf, tmp_path):
        out = str(tmp_path / "compressed.pdf")
        result = compress_pdf(sample_pdf, out, preset_key="standard")
        assert os.path.isfile(out) or result.skipped

    @pytest.mark.integration
    @pytest.mark.parametrize("preset_key", PRESET_ORDER)
    def test_compress_all_presets(self, sample_pdf, tmp_path, preset_key):
        out = str(tmp_path / f"compressed_{preset_key}.pdf")
        result = compress_pdf(sample_pdf, out, preset_key=preset_key)
        assert isinstance(result, Result)
        assert result.original_size > 0

    @pytest.mark.integration
    def test_compress_linearize(self, sample_pdf, tmp_path):
        out = str(tmp_path / "linearized.pdf")
        result = compress_pdf(sample_pdf, out, linearize=True)
        assert isinstance(result, Result)

    @pytest.mark.integration
    def test_compress_invalid_pdf(self, invalid_file, tmp_path):
        out = str(tmp_path / "out.pdf")
        with pytest.raises(InvalidPDFError):
            compress_pdf(invalid_file, out)

    @pytest.mark.integration
    def test_compress_encrypted_no_password(self, encrypted_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        with pytest.raises(EncryptedPDFError):
            compress_pdf(encrypted_pdf, out)

    def test_compress_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            compress_pdf("/nonexistent/file.pdf", str(tmp_path / "out.pdf"))

    @pytest.mark.integration
    def test_compress_cancellation(self, sample_pdf, tmp_path):
        cancel = threading.Event()
        cancel.set()  # pre-set before compression starts
        out = str(tmp_path / "out.pdf")
        with pytest.raises(CancelledError):
            compress_pdf(sample_pdf, out, cancel=cancel)

    @pytest.mark.integration
    def test_compress_backup_on_overwrite(self, sample_pdf, tmp_path):
        # Copy sample to a working file and compress in-place
        work = str(tmp_path / "work.pdf")
        shutil.copy2(sample_pdf, work)
        result = compress_pdf(work, work, backup_on_overwrite=True)
        if not result.skipped:
            assert result.backup_path is not None
            assert os.path.isfile(result.backup_path)


class TestCreateBackup:
    def test_creates_backup(self, sample_pdf):
        backup = create_backup(sample_pdf)
        assert backup is not None
        assert os.path.isfile(backup)
        assert backup.endswith(".backup")

    def test_nonexistent_file(self):
        result = create_backup("/nonexistent/file.pdf")
        assert result is None
