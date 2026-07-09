"""Tests for engine.py — compression engine, utilities, and security helpers."""

import io
import os
import random
import shutil
import threading
import zlib

import pikepdf
import pytest
from PIL import Image

from engine import (
    PRESET_ORDER,
    PRESETS,
    CancelledError,
    CompressionStats,
    EncryptedPDFError,
    InvalidPDFError,
    Result,
    _sanitize_path_for_subprocess,
    analyze_pdf,
    compress_images_smart,
    compress_pdf,
    create_backup,
    fmt_size,
    validate_pdf_magic,
)

# ── Helpers for non-JPEG image fixtures (ENG-01 / ENG-05) ────────────


def _make_flate_diagram_pdf(path: str, width: int = 200, height: int = 200,
                             color: tuple[int, int, int] = (30, 120, 30)) -> str:
    """A PDF with a Flate-encoded (non-JPEG), few-color 'diagram' image —
    the class of image ENG-01 silently skipped (decode via Image.open()
    on still-filter-encoded bytes fails for anything but JPEG)."""
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792],
    ))
    pdf.pages.append(page)

    pixel_data = bytes(color) * (width * height)
    # Stored with level=0 (store, no compression) so the "original" is
    # deliberately poorly compressed — real recompression should shrink it.
    xobj = pdf.make_stream(zlib.compress(pixel_data, level=0))
    xobj["/Type"] = pikepdf.Name("/XObject")
    xobj["/Subtype"] = pikepdf.Name("/Image")
    xobj["/Width"] = width
    xobj["/Height"] = height
    xobj["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    xobj["/BitsPerComponent"] = 8
    xobj["/Filter"] = pikepdf.Name("/FlateDecode")

    pdf.pages[0]["/Resources"] = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Img0=xobj))

    pdf.save(path)
    pdf.close()
    return path


def _make_bw_pdf(path: str, width: int = 200, height: int = 200) -> str:
    """A PDF with a 1-bit (bilevel) Flate-encoded image — the other class
    of image ENG-01 silently skipped."""
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792],
    ))
    pdf.pages.append(page)

    img = Image.new("1", (width, height), color=1)  # all white
    for x in range(0, width // 5):
        for y in range(0, height // 5):
            img.putpixel((x, y), 0)                 # black corner square
    raw_bits = img.tobytes()

    xobj = pdf.make_stream(zlib.compress(raw_bits, level=0))
    xobj["/Type"] = pikepdf.Name("/XObject")
    xobj["/Subtype"] = pikepdf.Name("/Image")
    xobj["/Width"] = width
    xobj["/Height"] = height
    xobj["/ColorSpace"] = pikepdf.Name("/DeviceGray")
    xobj["/BitsPerComponent"] = 1
    xobj["/Filter"] = pikepdf.Name("/FlateDecode")

    pdf.pages[0]["/Resources"] = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Img0=xobj))

    pdf.save(path)
    pdf.close()
    return path


def _make_jpeg_with_smask_pdf(path: str, *, decodable_smask: bool,
                               width: int = 200, height: int = 200) -> str:
    """A PDF with a JPEG base image and a FlateDecode 8bpc soft mask — the
    common DCTDecode-base + FlateDecode-mask case from ENG-02. Base image
    content is random noise so it isn't caught by the (unrelated) JPEG
    quality-skip heuristic. When decodable_smask is False, the mask XObject
    has degenerate (0×0) dimensions so `_load_smask_image` deterministically
    fails to decode it, regardless of environment/codec support — simulating
    an unsupported/undecodable mask without relying on a specific codec gap."""
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792],
    ))
    pdf.pages.append(page)

    random.seed(42)
    noise = bytes(random.randrange(256) for _ in range(width * height * 3))
    img = Image.frombytes("RGB", (width, height), noise)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)

    xobj = pdf.make_stream(buf.getvalue())
    xobj["/Type"] = pikepdf.Name("/XObject")
    xobj["/Subtype"] = pikepdf.Name("/Image")
    xobj["/Width"] = width
    xobj["/Height"] = height
    xobj["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    xobj["/BitsPerComponent"] = 8
    xobj["/Filter"] = pikepdf.Name("/DCTDecode")

    mask_pixels = bytes([180]) * (width * height)
    smask = pdf.make_stream(zlib.compress(mask_pixels, level=9))
    smask["/Type"] = pikepdf.Name("/XObject")
    smask["/Subtype"] = pikepdf.Name("/Image")
    smask["/Width"] = width if decodable_smask else 0
    smask["/Height"] = height if decodable_smask else 0
    smask["/ColorSpace"] = pikepdf.Name("/DeviceGray")
    smask["/BitsPerComponent"] = 8
    smask["/Filter"] = pikepdf.Name("/FlateDecode")

    xobj["/SMask"] = smask
    pdf.pages[0]["/Resources"] = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Img0=xobj))

    pdf.save(path)
    pdf.close()
    return path

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
        # The requested output path must always exist afterwards — even a
        # "no size gain" result now materializes the file (see skip branch).
        assert isinstance(result, Result)
        assert os.path.isfile(out)

    @pytest.mark.integration
    def test_compress_skip_still_writes_distinct_output(self, text_only_pdf, tmp_path):
        """A distinct output path must be created even when compression can't
        beat the original (skip branch), instead of silently producing nothing.
        A text-only PDF has no images to recompress, so it reliably skips."""
        out = str(tmp_path / "out.pdf")
        result = compress_pdf(text_only_pdf, out, preset_key="prepress")
        assert os.path.isfile(out), "output path must exist even when skipped"
        assert validate_pdf_magic(out)
        if result.skipped:
            # Skip branch writes a verbatim copy of the original.
            assert os.path.getsize(out) == os.path.getsize(text_only_pdf)

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
        # Copy sample to a working file and compress in-place. The backup is
        # created UNCONDITIONALLY (before the skip decision), so these must
        # hold whether or not compression ends up skipping — the old
        # `if not result.skipped:` guard let the test verify nothing when a
        # skip occurred (TST-04).
        work = str(tmp_path / "work.pdf")
        shutil.copy2(sample_pdf, work)
        result = compress_pdf(work, work, backup_on_overwrite=True)
        assert result.backup_path is not None
        assert os.path.isfile(result.backup_path)
        # The backup is a real copy of the pre-compress original.
        assert os.path.getsize(result.backup_path) == os.path.getsize(sample_pdf)


class TestCreateBackup:
    def test_creates_backup(self, sample_pdf):
        backup = create_backup(sample_pdf)
        assert backup is not None
        assert os.path.isfile(backup)
        assert backup.endswith(".backup")

    def test_nonexistent_file(self):
        result = create_backup("/nonexistent/file.pdf")
        assert result is None


class TestCompressImagesSmartNonJpeg:
    """ENG-01/ENG-05: non-JPEG images must actually decode and recompress
    (not be silently skipped via a swallowed UnidentifiedImageError), and
    the accepted candidate must be compared/written as genuinely
    Flate-compressed bytes — not raw bytes mislabeled with a FlateDecode
    filter, which pdf.save() does not fix up and produces an unreadable
    image."""

    def test_flate_diagram_image_is_recompressed(self, tmp_path):
        path = str(tmp_path / "diagram.pdf")
        _make_flate_diagram_pdf(path)

        pdf = pikepdf.open(path)
        stats = compress_images_smart(pdf, PRESETS["standard"])
        assert stats.images_recompressed == 1
        assert stats.images_kept_lossless == 1

        out = str(tmp_path / "out.pdf")
        pdf.save(out)
        pdf.close()

        pdf2 = pikepdf.open(out)
        xobj2 = pdf2.pages[0]["/Resources"]["/XObject"]["/Img0"]
        decoded = pikepdf.PdfImage(xobj2).as_pil_image().convert("RGB")
        assert decoded.size == (200, 200)
        assert decoded.getpixel((100, 100)) == (30, 120, 30)
        pdf2.close()

    def test_bw_image_is_recompressed(self, tmp_path):
        path = str(tmp_path / "bw.pdf")
        _make_bw_pdf(path)

        pdf = pikepdf.open(path)
        stats = compress_images_smart(pdf, PRESETS["standard"])
        assert stats.images_recompressed == 1
        assert stats.images_converted_bw == 1

        out = str(tmp_path / "out.pdf")
        pdf.save(out)
        pdf.close()

        pdf2 = pikepdf.open(out)
        xobj2 = pdf2.pages[0]["/Resources"]["/XObject"]["/Img0"]
        decoded = pikepdf.PdfImage(xobj2).as_pil_image().convert("L")
        assert decoded.size == (200, 200)
        assert decoded.getpixel((5, 5)) == 0        # black corner preserved
        assert decoded.getpixel((150, 150)) == 255  # white elsewhere


class TestCompressImagesSmartSoftMask:
    """ENG-02: /SMask must only be deleted when compositing against it
    actually succeeded — never unconditionally just because the original
    had one."""

    def test_smask_composited_and_removed_on_success(self, tmp_path):
        path = str(tmp_path / "smask_ok.pdf")
        _make_jpeg_with_smask_pdf(path, decodable_smask=True)

        pdf = pikepdf.open(path)
        stats = compress_images_smart(pdf, PRESETS["standard"])
        assert stats.images_with_mask_composited == 1
        assert stats.images_recompressed == 1

        out = str(tmp_path / "out.pdf")
        pdf.save(out)
        pdf.close()

        pdf2 = pikepdf.open(out)
        xobj2 = pdf2.pages[0]["/Resources"]["/XObject"]["/Img0"]
        assert "/SMask" not in xobj2

    def test_smask_preserved_when_undecodable(self, tmp_path):
        path = str(tmp_path / "smask_bad.pdf")
        _make_jpeg_with_smask_pdf(path, decodable_smask=False)

        pdf = pikepdf.open(path)
        stats = compress_images_smart(pdf, PRESETS["standard"])
        # Compositing was skipped (mask undecodable) — the base image may
        # still get re-encoded, but its /SMask reference must survive so
        # the (untouched) original mask keeps applying at render time,
        # instead of baking in a false-opaque rectangle.
        assert stats.images_with_mask_composited == 0
        assert stats.images_recompressed == 1

        out = str(tmp_path / "out.pdf")
        pdf.save(out)
        pdf.close()

        pdf2 = pikepdf.open(out)
        xobj2 = pdf2.pages[0]["/Resources"]["/XObject"]["/Img0"]
        assert "/SMask" in xobj2
