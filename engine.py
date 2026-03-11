"""
engine.py — PDF compression engine

Correct DPI-aware image recompression:
  - Reads the page content stream to find each image's display size
  - Calculates true rendered DPI (pixels per inch as displayed)
  - Only downscales images that exceed the target DPI
  - Preserves grayscale images as single-channel (saves ~66% vs RGB)
  - Skips tiny images (logos, icons) where recompression adds artifacts for no gain
  - Detects already-compressed images and avoids generation loss
  - Strips unreferenced resources and compresses PDF streams
"""

import io
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

import pikepdf
from PIL import Image


# ═══════════════════════════════════════════════════════════════════
#  Quality presets — discrete, meaningful levels
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    target_dpi: int
    jpeg_quality: int
    skip_below_px: int   # don't recompress images smaller than this
    force_grayscale: bool


PRESETS = {
    "screen": Preset(
        name="Screen",
        description="72 DPI · For on-screen viewing only",
        target_dpi=72, jpeg_quality=35, skip_below_px=64, force_grayscale=False,
    ),
    "ebook": Preset(
        name="E-book",
        description="120 DPI · Good for reading on tablets and laptops",
        target_dpi=120, jpeg_quality=55, skip_below_px=48, force_grayscale=False,
    ),
    "standard": Preset(
        name="Standard",
        description="150 DPI · Lecture notes, reports, datasheets",
        target_dpi=150, jpeg_quality=65, skip_below_px=32, force_grayscale=False,
    ),
    "high": Preset(
        name="High quality",
        description="200 DPI · Good prints, detailed diagrams",
        target_dpi=200, jpeg_quality=80, skip_below_px=24, force_grayscale=False,
    ),
    "prepress": Preset(
        name="Prepress",
        description="300 DPI · Professional printing",
        target_dpi=300, jpeg_quality=90, skip_below_px=16, force_grayscale=False,
    ),
}

PRESET_ORDER = ["screen", "ebook", "standard", "high", "prepress"]


# ═══════════════════════════════════════════════════════════════════
#  Content stream parsing — find image display dimensions
# ═══════════════════════════════════════════════════════════════════

def _parse_image_transforms(page):
    """
    Parse a page's content stream to find the display size of each image.

    Returns dict: {image_name: (display_width_pts, display_height_pts)}

    The content stream uses a graphics state with transformation matrices.
    When an image is drawn with `Do`, the current transformation matrix (CTM)
    determines its display size. The image is drawn into a 1×1 unit square,
    so the CTM directly gives the display dimensions in PDF points.

    We track q/Q (save/restore) and cm (concat matrix) to maintain the CTM.
    """
    transforms = {}

    try:
        content = page.get("/Contents")
        if content is None:
            return transforms

        # Handle array of content streams
        if isinstance(content, pikepdf.Array):
            raw = b""
            for stream in content:
                raw += pikepdf.Stream(page.owner, stream).read_bytes()
        else:
            raw = content.read_bytes()

        text = raw.decode("latin-1", errors="replace")
    except Exception:
        return transforms

    # Simple state machine: track CTM through q/Q/cm operators
    # CTM is [a, b, c, d, e, f] representing the 3×3 affine matrix:
    #   [a  b  0]
    #   [c  d  0]
    #   [e  f  1]
    ctm_stack = []
    ctm = [1, 0, 0, 1, 0, 0]  # identity

    # Tokenize — we need numbers and operators
    tokens = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)|[A-Za-z*]+|/\w+", text)

    num_stack = []
    for tok in tokens:
        # Try as number
        try:
            num_stack.append(float(tok))
            continue
        except ValueError:
            pass

        if tok == "q":
            ctm_stack.append(ctm[:])
            num_stack.clear()

        elif tok == "Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
            num_stack.clear()

        elif tok == "cm" and len(num_stack) >= 6:
            # Concatenate matrix: new_ctm = [params] × ctm
            a2, b2, c2, d2, e2, f2 = num_stack[-6:]
            a1, b1, c1, d1, e1, f1 = ctm
            ctm = [
                a2*a1 + b2*c1,
                a2*b1 + b2*d1,
                c2*a1 + d2*c1,
                c2*b1 + d2*d1,
                e2*a1 + f2*c1 + e1,
                e2*b1 + f2*d1 + f1,
            ]
            num_stack.clear()

        elif tok == "Do" and len(num_stack) == 0:
            # The image name should be the last token that started with /
            pass  # handled below
        elif tok == "Do":
            num_stack.clear()

        elif tok.startswith("/") and len(tok) > 1:
            # Could be an image name before Do
            # Peek: if this is an image reference, the next operator should be Do
            # For now, store it and check on next iteration
            pass

        else:
            num_stack.clear()

    # More robust approach: regex for the pattern `/ImageName Do`
    # with the current CTM at that point
    # Re-parse focusing specifically on image invocations

    ctm_stack2 = []
    ctm2 = [1, 0, 0, 1, 0, 0]
    num_buf = []

    for tok in tokens:
        try:
            num_buf.append(float(tok))
            continue
        except ValueError:
            pass

        if tok == "q":
            ctm_stack2.append(ctm2[:])
            num_buf.clear()
        elif tok == "Q":
            if ctm_stack2:
                ctm2 = ctm_stack2.pop()
            num_buf.clear()
        elif tok == "cm" and len(num_buf) >= 6:
            a2, b2, c2, d2, e2, f2 = num_buf[-6:]
            a1, b1, c1, d1, e1, f1 = ctm2
            ctm2 = [
                a2*a1 + b2*c1, a2*b1 + b2*d1,
                c2*a1 + d2*c1, c2*b1 + d2*d1,
                e2*a1 + f2*c1 + e1, e2*b1 + f2*d1 + f1,
            ]
            num_buf.clear()
        elif tok == "Do":
            # Find the preceding /Name token
            # Look backwards in the raw token list
            pass
        elif tok.startswith("/"):
            # Store as potential image name
            num_buf.clear()
            num_buf = []
            # Check if followed by Do — we handle this by finding /Name Do patterns
        else:
            num_buf.clear()

    # Third approach (most reliable): find all `/Name Do` patterns with positions,
    # then replay the CTM to each position

    # Find all image invocations with their positions in the token stream
    invocations = []
    for i in range(1, len(tokens)):
        if tokens[i] == "Do" and tokens[i-1].startswith("/"):
            name = tokens[i-1][1:]  # strip the leading /
            invocations.append((i, name))

    if not invocations:
        return transforms

    # Replay CTM up to each invocation position
    ctm_r = [1, 0, 0, 1, 0, 0]
    ctm_stack_r = []
    nb = []
    inv_idx = 0

    for i, tok in enumerate(tokens):
        # Check if we've reached an invocation point
        if inv_idx < len(invocations) and i == invocations[inv_idx][0]:
            name = invocations[inv_idx][1]
            # Display width and height from the CTM
            # Image maps unit square → CTM, so:
            #   width  = sqrt(a² + b²) in points
            #   height = sqrt(c² + d²) in points
            a, b, c, d = ctm_r[0], ctm_r[1], ctm_r[2], ctm_r[3]
            w_pts = (a*a + b*b) ** 0.5
            h_pts = (c*c + d*d) ** 0.5
            if w_pts > 0.1 and h_pts > 0.1:
                transforms[name] = (w_pts, h_pts)
            inv_idx += 1
            nb.clear()
            continue

        try:
            nb.append(float(tok))
            continue
        except ValueError:
            pass

        if tok == "q":
            ctm_stack_r.append(ctm_r[:])
            nb.clear()
        elif tok == "Q":
            if ctm_stack_r:
                ctm_r = ctm_stack_r.pop()
            nb.clear()
        elif tok == "cm" and len(nb) >= 6:
            a2, b2, c2, d2, e2, f2 = nb[-6:]
            a1, b1, c1, d1, e1, f1 = ctm_r
            ctm_r = [
                a2*a1 + b2*c1, a2*b1 + b2*d1,
                c2*a1 + d2*c1, c2*b1 + d2*d1,
                e2*a1 + f2*c1 + e1, e2*b1 + f2*d1 + f1,
            ]
            nb.clear()
        else:
            nb.clear()

    return transforms


# ═══════════════════════════════════════════════════════════════════
#  Image analysis
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ImageInfo:
    key: str
    pixel_w: int
    pixel_h: int
    display_w_pts: float  # 0 if unknown
    display_h_pts: float
    effective_dpi_x: float  # 0 if unknown
    effective_dpi_y: float
    raw_size: int
    is_grayscale: bool
    is_jpeg: bool
    estimated_quality: int  # 0-100, estimated from compression ratio

    @property
    def effective_dpi(self) -> float:
        return max(self.effective_dpi_x, self.effective_dpi_y)

    @property
    def pixel_count(self) -> int:
        return self.pixel_w * self.pixel_h

    @property
    def is_tiny(self) -> bool:
        return max(self.pixel_w, self.pixel_h) < 64


def _estimate_jpeg_quality(raw_size, pixel_w, pixel_h, channels):
    """
    Estimate the JPEG quality of an already-compressed image.
    Based on the compression ratio: uncompressed_size / compressed_size.

    This is inherently imprecise — compression ratio depends on image content,
    not just quality setting. A smooth gradient at q95 can have the same ratio
    as a noisy photo at q60. We intentionally estimate HIGH (conservative)
    so we err on the side of recompressing rather than skipping.
    """
    uncompressed = pixel_w * pixel_h * channels
    if raw_size <= 0 or uncompressed <= 0:
        return 85  # assume high quality if we can't tell
    ratio = raw_size / uncompressed
    # Conservative mapping — always assume quality is higher than it might be
    if ratio > 0.4:   return 97
    if ratio > 0.25:  return 92
    if ratio > 0.12:  return 85
    if ratio > 0.06:  return 72
    if ratio > 0.03:  return 55
    if ratio > 0.015: return 40
    return 25


def analyze_images(pdf):
    """
    Analyze all images in the PDF. Returns list of ImageInfo per page.
    Also returns total image byte count and page count.
    """
    all_images = []
    total_img_bytes = 0

    for page_idx, page in enumerate(pdf.pages):
        if "/Resources" not in page:
            continue
        res = page["/Resources"]
        if "/XObject" not in res:
            continue

        # Get display sizes from content stream
        transforms = _parse_image_transforms(page)

        xobjects = res["/XObject"]
        for key_name in xobjects.keys():
            xobj = xobjects[key_name]
            if xobj.get("/Subtype") != "/Image":
                continue

            try:
                raw = bytes(xobj.read_raw_bytes())
                raw_size = len(raw)
                total_img_bytes += raw_size

                pw = int(xobj.get("/Width", 0))
                ph = int(xobj.get("/Height", 0))

                # Determine color space
                cs = xobj.get("/ColorSpace")
                cs_str = str(cs) if cs else ""
                is_gray = "Gray" in cs_str or "CalGray" in cs_str

                # Check if already JPEG
                filt = xobj.get("/Filter")
                is_jpeg = str(filt) == "/DCTDecode" if filt else False

                # Try to detect grayscale from image data if color space is ambiguous
                if not is_gray and is_jpeg:
                    try:
                        img = Image.open(io.BytesIO(raw))
                        if img.mode == "L":
                            is_gray = True
                    except Exception:
                        pass

                channels = 1 if is_gray else 3
                est_q = _estimate_jpeg_quality(raw_size, pw, ph, channels) if is_jpeg else 100

                # Display dimensions
                clean_key = str(key_name).lstrip("/")
                dw, dh = transforms.get(clean_key, (0.0, 0.0))

                # Calculate effective DPI
                eff_dpi_x = (pw / (dw / 72.0)) if dw > 0.1 else 0.0
                eff_dpi_y = (ph / (dh / 72.0)) if dh > 0.1 else 0.0

                info = ImageInfo(
                    key=str(key_name),
                    pixel_w=pw, pixel_h=ph,
                    display_w_pts=dw, display_h_pts=dh,
                    effective_dpi_x=eff_dpi_x, effective_dpi_y=eff_dpi_y,
                    raw_size=raw_size,
                    is_grayscale=is_gray, is_jpeg=is_jpeg,
                    estimated_quality=est_q,
                )
                all_images.append(info)

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


def _should_skip(info: ImageInfo, preset: Preset) -> Optional[str]:
    """Return a reason to skip, or None if the image should be processed."""
    if info.is_tiny or max(info.pixel_w, info.pixel_h) < preset.skip_below_px:
        return "tiny"

    # Check if the image exceeds the target DPI (needs downscaling)
    needs_downscale = False
    if info.effective_dpi > 0 and info.effective_dpi > preset.target_dpi * 1.1:
        needs_downscale = True

    # Only skip on quality if we DON'T also need to downscale.
    # Recompressing q60 at q65 wastes bits, but downscaling q60 at q65
    # to hit a lower DPI is still worthwhile.
    if not needs_downscale and info.is_jpeg and info.estimated_quality <= (preset.jpeg_quality - 15):
        return "quality"

    return None


def compress_images_smart(
    pdf: pikepdf.Pdf,
    preset: Preset,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> CompressionStats:
    """
    Recompress images with DPI awareness and smart skipping.

    on_progress(current, total, status_text) called per image.
    """
    # First pass: analyze all images
    all_images, _ = analyze_images(pdf)
    stats = CompressionStats(images_total=len(all_images))

    if not all_images:
        return stats

    # Build a lookup from key → ImageInfo
    info_map = {img.key: img for img in all_images}

    current = 0
    for page in pdf.pages:
        if "/Resources" not in page:
            continue
        res = page["/Resources"]
        if "/XObject" not in res:
            continue

        xobjects = res["/XObject"]
        for key_name in list(xobjects.keys()):
            xobj = xobjects[key_name]
            if xobj.get("/Subtype") != "/Image":
                continue

            current += 1
            key_str = str(key_name)
            info = info_map.get(key_str)

            if info is None:
                if on_progress:
                    on_progress(current, stats.images_total, "Skipping unknown")
                continue

            skip_reason = _should_skip(info, preset)
            if skip_reason == "tiny":
                stats.images_skipped_tiny += 1
                if on_progress:
                    on_progress(current, stats.images_total, f"Skip (tiny)")
                continue
            if skip_reason == "quality":
                stats.images_skipped_quality += 1
                if on_progress:
                    on_progress(current, stats.images_total, f"Skip (already q{info.estimated_quality})")
                continue

            if on_progress:
                on_progress(current, stats.images_total, "Compressing…")

            try:
                raw = bytes(xobj.read_raw_bytes())
                img = Image.open(io.BytesIO(raw))

                # Determine if downscaling is needed based on true DPI
                scale = 1.0
                if info.effective_dpi > 0:
                    # We know the true rendered DPI
                    if info.effective_dpi > preset.target_dpi * 1.1:
                        scale = preset.target_dpi / info.effective_dpi
                else:
                    # Fallback: heuristic based on image size
                    # Assume roughly A4 page, so ~8 inches wide
                    assumed_display_inches = max(info.pixel_w, info.pixel_h) / 150.0
                    if assumed_display_inches > 1.0:
                        current_approx_dpi = max(info.pixel_w, info.pixel_h) / assumed_display_inches
                        if current_approx_dpi > preset.target_dpi * 1.2:
                            scale = preset.target_dpi / current_approx_dpi

                if scale < 0.95:
                    new_w = max(1, int(info.pixel_w * scale))
                    new_h = max(1, int(info.pixel_h * scale))
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    stats.images_downscaled += 1

                # Determine output mode
                if info.is_grayscale or (preset.force_grayscale and img.mode != "L"):
                    if img.mode != "L":
                        img = img.convert("L")
                    cs_name = "/DeviceGray"
                else:
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    cs_name = "/DeviceRGB"

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=preset.jpeg_quality, optimize=True)
                buf.seek(0)
                new_data = buf.read()

                # Only replace if we actually saved space
                if len(new_data) < info.raw_size:
                    xobjects[key_name] = pikepdf.Stream(pdf, new_data)
                    xobjects[key_name]["/Type"]             = pikepdf.Name("/XObject")
                    xobjects[key_name]["/Subtype"]          = pikepdf.Name("/Image")
                    xobjects[key_name]["/ColorSpace"]       = pikepdf.Name(cs_name)
                    xobjects[key_name]["/BitsPerComponent"] = 8
                    xobjects[key_name]["/Width"]            = img.width
                    xobjects[key_name]["/Height"]           = img.height
                    xobjects[key_name]["/Filter"]           = pikepdf.Name("/DCTDecode")
                    stats.images_recompressed += 1

            except Exception:
                continue

    return stats


# ═══════════════════════════════════════════════════════════════════
#  PDF analysis (for size estimation)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PDFAnalysis:
    file_size: int
    page_count: int
    image_count: int
    image_bytes: int
    non_image_bytes: int
    images: list  # list of ImageInfo

    def estimate_output(self, preset: Preset) -> int:
        """Estimate the compressed output size for a given preset."""
        if self.image_count == 0:
            # No images — only stream compression, ~5-15% savings
            return int(self.file_size * 0.90)

        est_img_total = 0
        for img in self.images:
            skip = _should_skip(img, preset)
            if skip:
                est_img_total += img.raw_size  # unchanged
                continue

            # Estimate new image size
            # Downscale factor
            scale = 1.0
            if img.effective_dpi > preset.target_dpi * 1.1:
                scale = preset.target_dpi / img.effective_dpi

            new_pixels = img.pixel_w * img.pixel_h * (scale ** 2)
            channels = 1 if img.is_grayscale else 3

            # JPEG compression ratio estimate
            if preset.jpeg_quality >= 90:   ratio = 0.35
            elif preset.jpeg_quality >= 75: ratio = 0.18
            elif preset.jpeg_quality >= 60: ratio = 0.10
            elif preset.jpeg_quality >= 40: ratio = 0.06
            else:                           ratio = 0.04

            est_size = int(new_pixels * channels * ratio)
            # Don't estimate larger than original
            est_img_total += min(est_size, img.raw_size)

        # Non-image content gets ~5% smaller from stream compression
        est_non_img = int(self.non_image_bytes * 0.95)

        return max(1024, est_img_total + est_non_img)


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
            )
    except Exception:
        return PDFAnalysis(
            file_size=file_size, page_count=0,
            image_count=0, image_bytes=0,
            non_image_bytes=file_size, images=[],
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
    skipped: bool  # True if no reduction achieved

    @property
    def saved_bytes(self) -> int:
        return self.original_size - self.compressed_size if not self.skipped else 0

    @property
    def saved_pct(self) -> float:
        if self.skipped or self.original_size == 0:
            return 0.0
        return (1 - self.compressed_size / self.original_size) * 100


def compress_pdf(
    input_path: str,
    output_path: Optional[str] = None,
    preset_key: str = "standard",
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> Result:
    """
    Compress a PDF file.

    Args:
        input_path:   source file
        output_path:  destination (default: <name>_compressed.pdf)
        preset_key:   key from PRESETS dict
        on_progress:  callback(current_image, total_images, status)

    Returns:
        Result with full statistics.
    """
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_compressed{ext}"

    preset = PRESETS[preset_key]
    original_size = os.path.getsize(input_path)

    # Write to temp file for safety
    out_dir = os.path.dirname(output_path) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=out_dir)

    try:
        os.close(fd)

        with pikepdf.open(input_path) as pdf:
            stats = compress_images_smart(pdf, preset, on_progress)
            pdf.remove_unreferenced_resources()
            pdf.save(
                tmp_path,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
            )

        compressed_size = os.path.getsize(tmp_path)

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
