"""
engine.py — PDF compression engine

DPI-aware image recompression with:
  - Single-pass content stream parser (CTM tracking for true display DPI)
  - Deduplication by PDF object ID (no double-recompression of shared XObjects)
  - Decompression bomb protection (pixel count limits)
  - Soft mask / transparency preservation (composite against white for JPEG)
  - Grayscale preservation (single-channel JPEG saves ~66% vs RGB)
  - Smart skip logic (tiny images, already-compressed below target quality)
  - Encrypted PDF detection with clear error messages
  - Optional metadata stripping and linearization
  - Safe temp-file writes with permission preservation
"""

import io
import math
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

import pikepdf
from PIL import Image

# ── Decompression bomb protection ────────────────────────────────
# PIL default is ~178M pixels. We enforce our own, tighter limit
# to prevent OOM on malicious files.  200M pixels ≈ ~600 MB for RGB.
MAX_IMAGE_PIXELS = 200_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# Maximum content stream size we'll attempt to tokenize (bytes).
# Prevents ReDoS / excessive memory on pathological streams.
MAX_CONTENT_STREAM_BYTES = 16 * 1024 * 1024  # 16 MB


# ═══════════════════════════════════════════════════════════════════
#  Quality presets — discrete, meaningful levels
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    target_dpi: int
    jpeg_quality: int
    skip_below_px: int     # don't recompress images smaller than this
    force_grayscale: bool
    strip_metadata: bool   # remove XMP / document info


PRESETS = {
    "screen": Preset(
        name="Screen",
        description="72 DPI · For on-screen viewing only",
        target_dpi=72, jpeg_quality=35, skip_below_px=64,
        force_grayscale=False, strip_metadata=True,
    ),
    "ebook": Preset(
        name="E-book",
        description="120 DPI · Good for reading on tablets and laptops",
        target_dpi=120, jpeg_quality=55, skip_below_px=48,
        force_grayscale=False, strip_metadata=True,
    ),
    "standard": Preset(
        name="Standard",
        description="150 DPI · Lecture notes, reports, datasheets",
        target_dpi=150, jpeg_quality=65, skip_below_px=32,
        force_grayscale=False, strip_metadata=False,
    ),
    "high": Preset(
        name="High quality",
        description="200 DPI · Good prints, detailed diagrams",
        target_dpi=200, jpeg_quality=80, skip_below_px=24,
        force_grayscale=False, strip_metadata=False,
    ),
    "prepress": Preset(
        name="Prepress",
        description="300 DPI · Professional printing",
        target_dpi=300, jpeg_quality=90, skip_below_px=16,
        force_grayscale=False, strip_metadata=False,
    ),
}

PRESET_ORDER = ["screen", "ebook", "standard", "high", "prepress"]


# ═══════════════════════════════════════════════════════════════════
#  Content stream parsing — single-pass CTM tracker
# ═══════════════════════════════════════════════════════════════════

# Simple tokenizer regex.  Matches:
#   - numbers:    -3.14, +2, .5, 100
#   - operators:  cm, Do, q, Q, etc.  (also BT, ET, re, m, l ...)
#   - names:      /ImageName
_TOKEN_RE = re.compile(
    rb"[-+]?(?:\d+\.?\d*|\.\d+)"   # number
    rb"|[A-Za-z*']+"               # operator
    rb"|/[!-~]+"                    # name
)


def _parse_image_transforms(page) -> dict[str, tuple[float, float]]:
    """
    Single-pass content stream parser.

    Returns {image_name: (display_width_pts, display_height_pts)}.

    Tracks the CTM through q (save) / Q (restore) / cm (concat) operators.
    When `/Name Do` is encountered, the CTM gives the display size because
    images are painted into a 1×1 unit square.
    """
    transforms: dict[str, tuple[float, float]] = {}

    try:
        content = page.get("/Contents")
        if content is None:
            return transforms

        if isinstance(content, pikepdf.Array):
            raw = b""
            for stream in content:
                raw += pikepdf.Stream(page.owner, stream).read_bytes()
        else:
            raw = content.read_bytes()
    except Exception:
        return transforms

    # Guard against pathologically large streams
    if len(raw) > MAX_CONTENT_STREAM_BYTES:
        return transforms

    # ── Tokenize once ────────────────────────────────────────────
    tokens = _TOKEN_RE.findall(raw)

    # ── First pass: locate all  /Name Do  invocations ────────────
    invocations: list[tuple[int, str]] = []      # (token_index_of_Do, name)
    for i in range(1, len(tokens)):
        if tokens[i] == b"Do" and tokens[i - 1].startswith(b"/"):
            name = tokens[i - 1][1:].decode("latin-1", errors="replace")
            invocations.append((i, name))

    if not invocations:
        return transforms

    # ── Single replay: track CTM up to each invocation ───────────
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    ctm_stack: list[list[float]] = []
    num_buf: list[float] = []
    inv_idx = 0

    for i, tok in enumerate(tokens):
        # Check if we've reached an invocation point
        if inv_idx < len(invocations) and i == invocations[inv_idx][0]:
            name = invocations[inv_idx][1]
            a, b, c, d = ctm[0], ctm[1], ctm[2], ctm[3]
            w_pts = math.hypot(a, b)
            h_pts = math.hypot(c, d)
            if w_pts > 0.1 and h_pts > 0.1:
                transforms[name] = (w_pts, h_pts)
            inv_idx += 1
            num_buf.clear()
            if inv_idx >= len(invocations):
                break                     # no more images — stop early
            continue

        # Try to parse as number
        try:
            num_buf.append(float(tok))
            continue
        except ValueError:
            pass

        if tok == b"q":
            ctm_stack.append(ctm[:])
            num_buf.clear()
        elif tok == b"Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
            num_buf.clear()
        elif tok == b"cm" and len(num_buf) >= 6:
            a2, b2, c2, d2, e2, f2 = num_buf[-6:]
            a1, b1, c1, d1, e1, f1 = ctm
            ctm = [
                a2 * a1 + b2 * c1,    a2 * b1 + b2 * d1,
                c2 * a1 + d2 * c1,    c2 * b1 + d2 * d1,
                e2 * a1 + f2 * c1 + e1, e2 * b1 + f2 * d1 + f1,
            ]
            num_buf.clear()
        else:
            num_buf.clear()

    return transforms


# ═══════════════════════════════════════════════════════════════════
#  Image analysis
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ImageInfo:
    key: str
    obj_id: int           # pikepdf object generation-independent ID
    pixel_w: int
    pixel_h: int
    display_w_pts: float  # 0 if unknown
    display_h_pts: float
    effective_dpi_x: float
    effective_dpi_y: float
    raw_size: int
    is_grayscale: bool
    is_jpeg: bool
    has_soft_mask: bool
    estimated_quality: int   # 0-100
    page_index: int

    @property
    def effective_dpi(self) -> float:
        return max(self.effective_dpi_x, self.effective_dpi_y)

    @property
    def pixel_count(self) -> int:
        return self.pixel_w * self.pixel_h

    @property
    def is_tiny(self) -> bool:
        return max(self.pixel_w, self.pixel_h) < 64


def _estimate_jpeg_quality(raw_size: int, pixel_w: int, pixel_h: int,
                           channels: int) -> int:
    """
    Estimate JPEG quality from the compression ratio.

    This is inherently imprecise — ratio depends on image content, not just
    the quality setting.  We intentionally estimate HIGH so that
    _should_skip's  `estimated_quality <= (target - 15)`  test is LESS
    likely to trigger, meaning we recompress more aggressively.
    That avoids the worse outcome of skipping an image that would have
    benefited from recompression.
    """
    uncompressed = pixel_w * pixel_h * channels
    if raw_size <= 0 or uncompressed <= 0:
        return 85
    ratio = raw_size / uncompressed
    if ratio > 0.40:  return 97
    if ratio > 0.25:  return 92
    if ratio > 0.12:  return 85
    if ratio > 0.06:  return 72
    if ratio > 0.03:  return 55
    if ratio > 0.015: return 40
    return 25


def _get_obj_id(xobj) -> int:
    """
    Return a stable identifier for a PDF indirect object.
    Used to deduplicate shared XObjects across pages.
    """
    try:
        return xobj.objgen[0]           # (object_number, generation)
    except Exception:
        return id(xobj)                 # fallback — unique per Python object


def analyze_images(pdf: pikepdf.Pdf) -> tuple[list["ImageInfo"], int]:
    """
    Analyze all images in the PDF.
    Returns (list_of_ImageInfo, total_image_byte_count).
    Deduplicates by object ID so shared images are counted once.
    """
    all_images: list[ImageInfo] = []
    total_img_bytes = 0
    seen_obj_ids: set[int] = set()

    for page_idx, page in enumerate(pdf.pages):
        res = page.get("/Resources")
        if res is None:
            continue
        xobjects = res.get("/XObject")
        if xobjects is None:
            continue

        transforms = _parse_image_transforms(page)

        for key_name in xobjects.keys():
            xobj = xobjects[key_name]
            if xobj.get("/Subtype") != "/Image":
                continue

            obj_id = _get_obj_id(xobj)
            if obj_id in seen_obj_ids:
                continue
            seen_obj_ids.add(obj_id)

            try:
                raw = bytes(xobj.read_raw_bytes())
                raw_size = len(raw)
                total_img_bytes += raw_size

                pw = int(xobj.get("/Width", 0))
                ph = int(xobj.get("/Height", 0))

                # ── Pixel count bomb guard ───────────────────────
                if pw * ph > MAX_IMAGE_PIXELS:
                    continue

                # ── Color space ──────────────────────────────────
                cs = xobj.get("/ColorSpace")
                cs_str = str(cs) if cs else ""
                is_gray = "Gray" in cs_str or "CalGray" in cs_str

                # ── Filter → is JPEG? ────────────────────────────
                filt = xobj.get("/Filter")
                is_jpeg = str(filt) == "/DCTDecode" if filt else False

                # Detect grayscale from actual data if ambiguous
                if not is_gray and is_jpeg:
                    try:
                        img = Image.open(io.BytesIO(raw))
                        if img.mode == "L":
                            is_gray = True
                    except Exception:
                        pass

                channels = 1 if is_gray else 3
                est_q = (_estimate_jpeg_quality(raw_size, pw, ph, channels)
                         if is_jpeg else 100)

                # ── Soft mask ────────────────────────────────────
                has_smask = xobj.get("/SMask") is not None

                # ── Display dimensions ───────────────────────────
                clean_key = str(key_name).lstrip("/")
                dw, dh = transforms.get(clean_key, (0.0, 0.0))

                eff_dpi_x = (pw / (dw / 72.0)) if dw > 0.1 else 0.0
                eff_dpi_y = (ph / (dh / 72.0)) if dh > 0.1 else 0.0

                all_images.append(ImageInfo(
                    key=str(key_name),
                    obj_id=obj_id,
                    pixel_w=pw, pixel_h=ph,
                    display_w_pts=dw, display_h_pts=dh,
                    effective_dpi_x=eff_dpi_x, effective_dpi_y=eff_dpi_y,
                    raw_size=raw_size,
                    is_grayscale=is_gray, is_jpeg=is_jpeg,
                    has_soft_mask=has_smask,
                    estimated_quality=est_q,
                    page_index=page_idx,
                ))

            except Exception:
                continue

    return all_images, total_img_bytes


# ═══════════════════════════════════════════════════════════════════
#  Smart image compression
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CompressionStats:
    images_total: int = 0
    images_recompressed: int = 0
    images_downscaled: int = 0
    images_skipped_tiny: int = 0
    images_skipped_quality: int = 0
    images_skipped_bomb: int = 0
    images_with_mask_composited: int = 0


def _should_skip(info: ImageInfo, preset: Preset) -> Optional[str]:
    """Return a reason to skip, or None if the image should be processed."""
    if info.is_tiny or max(info.pixel_w, info.pixel_h) < preset.skip_below_px:
        return "tiny"

    needs_downscale = (
        info.effective_dpi > 0
        and info.effective_dpi > preset.target_dpi * 1.1
    )

    # Only skip on quality if we DON'T also need to downscale.
    if (not needs_downscale
            and info.is_jpeg
            and info.estimated_quality <= (preset.jpeg_quality - 15)):
        return "quality"

    return None


def _composite_with_mask(img: Image.Image, mask_img: Image.Image) -> Image.Image:
    """
    Composite an image against a white background using its soft mask.
    This preserves the visual appearance when converting to JPEG (no alpha).
    """
    if mask_img.size != img.size:
        mask_img = mask_img.resize(img.size, Image.LANCZOS)

    if mask_img.mode != "L":
        mask_img = mask_img.convert("L")

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        img = img.convert("RGB")

    white = Image.new("RGB", img.size, (255, 255, 255))
    white.paste(img, mask=mask_img)
    return white


def _load_smask_image(smask_obj) -> Optional[Image.Image]:
    """Try to decode a soft-mask XObject into a PIL Image."""
    try:
        raw = bytes(smask_obj.read_raw_bytes())
        w = int(smask_obj.get("/Width", 0))
        h = int(smask_obj.get("/Height", 0))
        bpc = int(smask_obj.get("/BitsPerComponent", 8))

        if w <= 0 or h <= 0:
            return None

        # Soft masks are single-channel (grayscale)
        if bpc == 8 and len(raw) >= w * h:
            return Image.frombytes("L", (w, h), raw[:w * h])

        # Fallback: try PIL
        return Image.open(io.BytesIO(raw)).convert("L")
    except Exception:
        return None


def compress_images_smart(
    pdf: pikepdf.Pdf,
    preset: Preset,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> CompressionStats:
    """
    Recompress images with DPI awareness and smart skipping.

    Key design choices:
      - Tracks processed XObjects by object ID to avoid double-recompression
        of images shared across pages.
      - Composites soft-masked images against white before JPEG encoding
        so transparency is preserved visually.
      - Enforces a pixel-count ceiling to prevent decompression bombs.
    """
    all_images, _ = analyze_images(pdf)
    stats = CompressionStats(images_total=len(all_images))

    if not all_images:
        return stats

    info_map = {img.obj_id: img for img in all_images}

    # Track which object IDs we've already recompressed
    processed_ids: set[int] = set()

    current = 0
    for page in pdf.pages:
        res = page.get("/Resources")
        if res is None:
            continue
        xobjects = res.get("/XObject")
        if xobjects is None:
            continue

        for key_name in list(xobjects.keys()):
            xobj = xobjects[key_name]
            if xobj.get("/Subtype") != "/Image":
                continue

            obj_id = _get_obj_id(xobj)

            # ── Deduplication: skip if already processed ─────────
            if obj_id in processed_ids:
                continue
            processed_ids.add(obj_id)

            current += 1
            info = info_map.get(obj_id)

            if info is None:
                if on_progress:
                    on_progress(current, stats.images_total, "Skipping unknown")
                continue

            # ── Pixel bomb guard ─────────────────────────────────
            if info.pixel_count > MAX_IMAGE_PIXELS:
                stats.images_skipped_bomb += 1
                if on_progress:
                    on_progress(current, stats.images_total, "Skip (too large)")
                continue

            skip_reason = _should_skip(info, preset)
            if skip_reason == "tiny":
                stats.images_skipped_tiny += 1
                if on_progress:
                    on_progress(current, stats.images_total, "Skip (tiny)")
                continue
            if skip_reason == "quality":
                stats.images_skipped_quality += 1
                if on_progress:
                    on_progress(current, stats.images_total,
                                f"Skip (already q{info.estimated_quality})")
                continue

            if on_progress:
                on_progress(current, stats.images_total, "Compressing…")

            try:
                raw = bytes(xobj.read_raw_bytes())
                img = Image.open(io.BytesIO(raw))

                # ── Handle soft mask (transparency) ──────────────
                smask_obj = xobj.get("/SMask")
                if smask_obj is not None:
                    mask_img = _load_smask_image(smask_obj)
                    if mask_img is not None:
                        img = _composite_with_mask(img, mask_img)
                        stats.images_with_mask_composited += 1

                # ── Downscale if DPI exceeds target ──────────────
                scale = 1.0
                if info.effective_dpi > 0:
                    if info.effective_dpi > preset.target_dpi * 1.1:
                        scale = preset.target_dpi / info.effective_dpi
                else:
                    # Fallback heuristic when DPI is unknown
                    assumed_inches = max(info.pixel_w, info.pixel_h) / 150.0
                    if assumed_inches > 1.0:
                        approx_dpi = max(info.pixel_w, info.pixel_h) / assumed_inches
                        if approx_dpi > preset.target_dpi * 1.2:
                            scale = preset.target_dpi / approx_dpi

                if scale < 0.95:
                    new_w = max(1, int(info.pixel_w * scale))
                    new_h = max(1, int(info.pixel_h * scale))
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    stats.images_downscaled += 1

                # ── Color mode ───────────────────────────────────
                if info.is_grayscale or (preset.force_grayscale and img.mode != "L"):
                    if img.mode != "L":
                        img = img.convert("L")
                    cs_name = "/DeviceGray"
                else:
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    cs_name = "/DeviceRGB"

                # ── JPEG encode ──────────────────────────────────
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=preset.jpeg_quality,
                         optimize=True)
                new_data = buf.getvalue()

                # Only replace if we actually saved space
                if len(new_data) < info.raw_size:
                    # Write into the EXISTING stream object so all pages
                    # that share this XObject see the update.  Creating a
                    # new Stream would only rebind this page's dictionary
                    # entry, leaving the old data referenced by other pages.
                    xobj.write(new_data, filter=pikepdf.Name("/DCTDecode"))
                    xobj["/Type"]             = pikepdf.Name("/XObject")
                    xobj["/Subtype"]          = pikepdf.Name("/Image")
                    xobj["/ColorSpace"]       = pikepdf.Name(cs_name)
                    xobj["/BitsPerComponent"] = 8
                    xobj["/Width"]            = img.width
                    xobj["/Height"]           = img.height

                    # Remove the soft mask reference — we composited it
                    if smask_obj is not None and "/SMask" in xobj:
                        del xobj["/SMask"]

                    stats.images_recompressed += 1

            except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                stats.images_skipped_bomb += 1
                if on_progress:
                    on_progress(current, stats.images_total, "Skip (bomb)")
                continue
            except Exception:
                continue

    return stats


# ═══════════════════════════════════════════════════════════════════
#  Metadata stripping
# ═══════════════════════════════════════════════════════════════════

def strip_metadata(pdf: pikepdf.Pdf):
    """
    Remove XMP metadata, document info dict, and page thumbnails.
    These can be surprisingly large (especially from InDesign exports).
    """
    # XMP metadata stream
    with pdf.open_metadata() as meta:
        # pikepdf's context manager — exiting without changes strips it
        # We need to explicitly delete all keys
        for key in list(meta.keys()):
            try:
                del meta[key]
            except Exception:
                pass

    # /Info dictionary (Title, Author, Creator, etc.)
    if "/Info" in pdf.trailer:
        try:
            del pdf.trailer["/Info"]
        except Exception:
            pass

    # Page thumbnails
    for page in pdf.pages:
        if "/Thumb" in page:
            try:
                del page["/Thumb"]
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════
#  PDF analysis (for size estimation in UI)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PDFAnalysis:
    file_size: int
    page_count: int
    image_count: int
    image_bytes: int
    non_image_bytes: int
    images: list          # list of ImageInfo
    is_encrypted: bool

    def estimate_output(self, preset: Preset) -> int:
        """Estimate the compressed output size for a given preset."""
        if self.image_count == 0:
            return int(self.file_size * 0.90)

        est_img_total = 0
        for img in self.images:
            skip = _should_skip(img, preset)
            if skip:
                est_img_total += img.raw_size
                continue

            scale = 1.0
            if img.effective_dpi > preset.target_dpi * 1.1:
                scale = preset.target_dpi / img.effective_dpi

            new_pixels = img.pixel_w * img.pixel_h * (scale ** 2)
            channels = 1 if img.is_grayscale else 3

            if   preset.jpeg_quality >= 90: ratio = 0.35
            elif preset.jpeg_quality >= 75: ratio = 0.18
            elif preset.jpeg_quality >= 60: ratio = 0.10
            elif preset.jpeg_quality >= 40: ratio = 0.06
            else:                           ratio = 0.04

            est_size = int(new_pixels * channels * ratio)
            est_img_total += min(est_size, img.raw_size)

        est_non_img = int(self.non_image_bytes * 0.95)
        return max(1024, est_img_total + est_non_img)


class EncryptedPDFError(Exception):
    """Raised when a PDF requires a password we don't have."""
    pass


def analyze_pdf(path: str) -> PDFAnalysis:
    """Quick analysis of a PDF file for the UI."""
    file_size = os.path.getsize(path)
    try:
        with pikepdf.open(path) as pdf:
            page_count = len(pdf.pages)
            images, img_bytes = analyze_images(pdf)
            return PDFAnalysis(
                file_size=file_size,
                page_count=page_count,
                image_count=len(images),
                image_bytes=img_bytes,
                non_image_bytes=file_size - img_bytes,
                images=images,
                is_encrypted=False,
            )
    except pikepdf.PasswordError:
        return PDFAnalysis(
            file_size=file_size, page_count=0,
            image_count=0, image_bytes=0,
            non_image_bytes=file_size, images=[],
            is_encrypted=True,
        )
    except Exception:
        return PDFAnalysis(
            file_size=file_size, page_count=0,
            image_count=0, image_bytes=0,
            non_image_bytes=file_size, images=[],
            is_encrypted=False,
        )


# ═══════════════════════════════════════════════════════════════════
#  Main compression function
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Result:
    input_path: str
    output_path: str
    original_size: int
    compressed_size: int
    stats: CompressionStats
    skipped: bool

    @property
    def saved_bytes(self) -> int:
        return self.original_size - self.compressed_size if not self.skipped else 0

    @property
    def saved_pct(self) -> float:
        if self.skipped or self.original_size == 0:
            return 0.0
        return (1 - self.compressed_size / self.original_size) * 100


def _copy_file_permissions(src: str, dst: str):
    """
    Copy file permissions (mode bits) from src to dst.
    Prevents the temp-file's restrictive 0600 from persisting.
    """
    try:
        src_stat = os.stat(src)
        os.chmod(dst, stat.S_IMODE(src_stat.st_mode))
    except OSError:
        pass


def compress_pdf(
    input_path: str,
    output_path: Optional[str] = None,
    preset_key: str = "standard",
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    linearize: bool = False,
) -> Result:
    """
    Compress a PDF file.

    Args:
        input_path:   source file
        output_path:  destination (default: <name>_compressed.pdf)
        preset_key:   key from PRESETS dict
        on_progress:  callback(current_image, total_images, status)
        linearize:    if True, produce a web-optimized (linearized) PDF

    Returns:
        Result with full statistics.

    Raises:
        EncryptedPDFError: if the PDF is password-protected.
        FileNotFoundError: if input_path doesn't exist.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"File not found: {input_path}")

    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_compressed{ext}"

    preset = PRESETS[preset_key]
    original_size = os.path.getsize(input_path)

    out_dir = os.path.dirname(output_path) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=out_dir)

    try:
        os.close(fd)

        try:
            pdf = pikepdf.open(input_path)
        except pikepdf.PasswordError:
            raise EncryptedPDFError(
                "This PDF is password-protected. "
                "Remove the password before compressing."
            )

        with pdf:
            stats = compress_images_smart(pdf, preset, on_progress)

            if preset.strip_metadata:
                strip_metadata(pdf)

            pdf.remove_unreferenced_resources()
            pdf.save(
                tmp_path,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                linearize=linearize,
            )

        compressed_size = os.path.getsize(tmp_path)

        # If compression didn't help, keep original
        if compressed_size >= original_size:
            os.remove(tmp_path)
            return Result(
                input_path=input_path,
                output_path=output_path,
                original_size=original_size,
                compressed_size=original_size,
                stats=stats,
                skipped=True,
            )

        # ── Preserve original file permissions ───────────────────
        _copy_file_permissions(input_path, tmp_path)

        if os.path.exists(output_path):
            os.remove(output_path)
        os.rename(tmp_path, output_path)

        return Result(
            input_path=input_path,
            output_path=output_path,
            original_size=original_size,
            compressed_size=compressed_size,
            stats=stats,
            skipped=False,
        )

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def fmt_size(n: int) -> str:
    if n < 1024:    return f"{n} B"
    if n < 1048576: return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.2f} MB"
