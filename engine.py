"""
engine.py — PDF compression engine

DPI-aware image recompression with:
  - Single-pass content stream parser (CTM tracking for true display DPI)
  - Deduplication by PDF object ID (no double-recompression of shared XObjects)
  - Decompression bomb protection (pixel count limits)
  - Soft mask / transparency preservation (composite against white for JPEG)
  - Grayscale preservation (single-channel JPEG saves ~66% vs RGB)
  - Smart image format detection (photo vs diagram vs B&W)
  - Per-image-type DPI (color, grayscale, monochrome)
  - Smart encoding: JPEG for photos, Flate for diagrams, 1-bit for B&W
  - PDF structure optimization (remove JS, thumbnails, app-specific data)
  - Optional Ghostscript backend for font subsetting
  - Smart skip logic (tiny images, already-compressed below target quality)
  - Encrypted PDF detection with clear error messages
  - Optional metadata stripping and linearization
  - Safe temp-file writes with permission preservation
  - Cancellation support via threading.Event
  - Configurable file size limits
  - PDF magic byte validation
  - Ghostscript path sanitization
  - PDF/A compliance detection and preservation
  - Automatic backup before overwriting originals
  - File logging for diagnostics
  - Duplicate font merging and content stream optimization
"""

import ctypes
import hashlib
import io
import logging
import logging.handlers
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pikepdf
from PIL import Image

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

log = logging.getLogger(__name__)

# ── File logging setup ──────────────────────────────────────────
_LOG_DIR: Optional[str] = None


def setup_file_logging(log_dir: Optional[str] = None) -> str:
    """
    Configure file-based logging for diagnostics.
    Returns the log file path.
    """
    global _LOG_DIR
    if log_dir is None:
        if platform.system() == "Windows":
            appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
            log_dir = os.path.join(appdata, "PDFCompress", "logs")
        elif platform.system() == "Darwin":
            log_dir = os.path.expanduser("~/Library/Logs/PDFCompress")
        else:
            log_dir = os.path.expanduser("~/.local/share/PDFCompress/logs")

    os.makedirs(log_dir, exist_ok=True)
    _LOG_DIR = log_dir

    log_file = os.path.join(log_dir, "pdfcompress.log")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    if root_logger.level > logging.DEBUG:
        root_logger.setLevel(logging.DEBUG)

    return log_file


# ── Decompression bomb protection ────────────────────────────────
# PIL default is ~178M pixels. We enforce our own, tighter limit
# to prevent OOM on malicious files.  200M pixels ≈ ~600 MB for RGB.
MAX_IMAGE_PIXELS = 200_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# Maximum content stream size we'll attempt to tokenize (bytes).
# Prevents ReDoS / excessive memory on pathological streams.
MAX_CONTENT_STREAM_BYTES = 16 * 1024 * 1024  # 16 MB

# Maximum input file size (2 GB).  Prevents OOM on absurdly large files.
MAX_INPUT_FILE_SIZE = 2 * 1024 * 1024 * 1024

# PDF magic bytes — all valid PDFs start with this
_PDF_MAGIC = b"%PDF-"

# ── Ghostscript cache ───────────────────────────────────────────
_gs_cache: dict[str, Optional[str]] = {}
_gs_cache_lock = threading.Lock()


# ── Security helpers ────────────────────────────────────────────

def validate_pdf_magic(path: str) -> bool:
    """Verify that a file starts with the PDF magic bytes (%PDF-)."""
    try:
        with open(path, "rb") as f:
            header = f.read(5)
        return header == _PDF_MAGIC
    except OSError:
        return False


class InvalidPDFError(Exception):
    """Raised when a file does not appear to be a valid PDF."""
    pass


def _sanitize_path_for_subprocess(path: str) -> str:
    """
    Ensure a path is safe for use as a subprocess argument.
    Converts to absolute path and validates it doesn't start with '-'.
    Rejects null bytes and other control characters.
    """
    if "\x00" in path:
        raise ValueError("Path contains null byte")
    if any(ord(c) < 32 and c not in ('\n', '\r', '\t') for c in path):
        raise ValueError("Path contains control characters")
    path = os.path.abspath(path)
    basename = os.path.basename(path)
    if basename.startswith("-"):
        raise ValueError(f"Unsafe filename (starts with '-'): {basename}")
    return path


def _secure_delete_string(s: str) -> None:
    """
    Best-effort zeroing of a Python string's internal buffer.
    Python strings are immutable so this uses ctypes to overwrite the
    underlying C buffer. Not guaranteed on all implementations but
    reduces the window for password exposure in memory.
    """
    if not s:
        return
    try:
        str_type = type(s)
        # CPython: string data follows the PyUnicodeObject header
        # This is a best-effort approach
        buf = ctypes.cast(id(s), ctypes.POINTER(ctypes.c_char))
        # Find the string data (after the object header)
        # We overwrite a generous range that should cover the string data
        header_size = 48 + 4  # approximate PyUnicodeObject header on 64-bit
        for i in range(header_size, header_size + len(s) * 4 + 8):
            try:
                buf[i] = b'\x00'
            except (ValueError, IndexError):
                break
    except Exception:
        pass  # non-CPython or restricted environment


def create_backup(filepath: str) -> Optional[str]:
    """
    Create a backup of a file before overwriting.
    Returns the backup path, or None if backup failed.
    Backup is stored as <name>.pdf.backup in the same directory.
    """
    if not os.path.isfile(filepath):
        return None

    backup_path = filepath + ".backup"
    # If a backup already exists, rotate it
    if os.path.exists(backup_path):
        try:
            old_backup = backup_path + ".old"
            if os.path.exists(old_backup):
                os.remove(old_backup)
            os.rename(backup_path, old_backup)
        except OSError as e:
            log.warning("Could not rotate old backup: %s", e)

    try:
        shutil.copy2(filepath, backup_path)
        log.info("Backup created: %s", backup_path)
        return backup_path
    except OSError as e:
        log.warning("Failed to create backup of %s: %s", filepath, e)
        return None


# ── PDF/A detection ─────────────────────────────────────────────

def detect_pdfa_conformance(pdf: pikepdf.Pdf) -> Optional[str]:
    """
    Detect if a PDF claims PDF/A conformance.
    Returns the conformance level string (e.g., "PDF/A-1b", "PDF/A-2u")
    or None if not PDF/A.
    """
    try:
        with pdf.open_metadata() as meta:
            # PDF/A conformance is declared in XMP metadata
            # under pdfaid:part and pdfaid:conformance
            raw_xml = str(meta)
            if "pdfaid" not in raw_xml.lower() and "PDF/A" not in raw_xml:
                return None

            part = None
            conformance = None

            # Look for pdfaid:part and pdfaid:conformance
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(raw_xml)
                ns = {"pdfaid": "http://www.aiim.org/pdfa/ns/id/"}
                for elem in root.iter():
                    tag = elem.tag.lower() if elem.tag else ""
                    if "part" in tag and elem.text:
                        part = elem.text.strip()
                    if "conformance" in tag and elem.text:
                        conformance = elem.text.strip()
            except ET.ParseError:
                # Fallback: regex search
                import re as _re
                part_m = _re.search(r'pdfaid:part["\s>]+(\d+)', raw_xml, _re.IGNORECASE)
                conf_m = _re.search(r'pdfaid:conformance["\s>]+([a-zA-Z]+)', raw_xml, _re.IGNORECASE)
                if part_m:
                    part = part_m.group(1)
                if conf_m:
                    conformance = conf_m.group(1)

            if part:
                level = f"PDF/A-{part}"
                if conformance:
                    level += conformance.lower()
                return level
    except Exception as e:
        log.debug("PDF/A detection failed: %s", e)

    return None

# ── Cancellation ─────────────────────────────────────────────────

class CancelledError(Exception):
    """Raised when compression is cancelled by the user."""
    pass


def _check_cancel(cancel: Optional[threading.Event]):
    """Raise CancelledError if the cancel event is set."""
    if cancel is not None and cancel.is_set():
        raise CancelledError("Compression cancelled")


# ═══════════════════════════════════════════════════════════════════
#  Quality presets — discrete, meaningful levels
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    target_dpi: int           # color image DPI
    gray_dpi: int             # grayscale image DPI (0 = same as target_dpi)
    mono_dpi: int             # monochrome/B&W DPI (0 = same as target_dpi)
    jpeg_quality: int
    skip_below_px: int        # don't recompress images smaller than this
    force_grayscale: bool
    strip_metadata: bool      # remove XMP / document info

    def get_dpi_for_image(self, is_grayscale: bool, is_monochrome: bool) -> int:
        """Return the appropriate target DPI for a given image type."""
        if is_monochrome:
            return self.mono_dpi if self.mono_dpi > 0 else self.target_dpi
        if is_grayscale:
            return self.gray_dpi if self.gray_dpi > 0 else self.target_dpi
        return self.target_dpi


PRESETS = {
    "screen": Preset(
        name="Screen",
        description="72 DPI · For on-screen viewing only",
        target_dpi=72, gray_dpi=0, mono_dpi=150,
        jpeg_quality=35, skip_below_px=64,
        force_grayscale=False, strip_metadata=True,
    ),
    "ebook": Preset(
        name="E-book",
        description="120 DPI · Good for reading on tablets and laptops",
        target_dpi=120, gray_dpi=0, mono_dpi=200,
        jpeg_quality=55, skip_below_px=48,
        force_grayscale=False, strip_metadata=True,
    ),
    "standard": Preset(
        name="Standard",
        description="150 DPI · Lecture notes, reports, datasheets",
        target_dpi=150, gray_dpi=0, mono_dpi=0,
        jpeg_quality=65, skip_below_px=32,
        force_grayscale=False, strip_metadata=False,
    ),
    "high": Preset(
        name="High quality",
        description="200 DPI · Good prints, detailed diagrams",
        target_dpi=200, gray_dpi=0, mono_dpi=0,
        jpeg_quality=80, skip_below_px=24,
        force_grayscale=False, strip_metadata=False,
    ),
    "prepress": Preset(
        name="Prepress",
        description="300 DPI · Professional printing",
        target_dpi=300, gray_dpi=0, mono_dpi=0,
        jpeg_quality=90, skip_below_px=16,
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
    is_monochrome: bool       # 1-bit / bilevel
    filter_name: str          # original filter: "DCTDecode", "FlateDecode", etc.
    bits_per_component: int

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


def _get_filter_name(xobj) -> str:
    """Extract the primary filter name from a PDF image XObject."""
    filt = xobj.get("/Filter")
    if filt is None:
        return ""
    filt_str = str(filt)
    # Handle arrays like [/FlateDecode /DCTDecode]
    if isinstance(filt, pikepdf.Array):
        if len(filt) > 0:
            return str(filt[-1]).strip("/")
        return ""
    return filt_str.strip("/")


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

                # ── BitsPerComponent ─────────────────────────────
                bpc = int(xobj.get("/BitsPerComponent", 8))

                # ── Color space ──────────────────────────────────
                cs = xobj.get("/ColorSpace")
                cs_str = str(cs) if cs else ""
                is_gray = "Gray" in cs_str or "CalGray" in cs_str

                # ── Filter → is JPEG? ────────────────────────────
                filt = xobj.get("/Filter")
                filt_str = str(filt) if filt else ""
                is_jpeg = "DCTDecode" in filt_str
                filter_name = _get_filter_name(xobj)

                # ── Monochrome detection ─────────────────────────
                is_mono = (bpc == 1)

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
                    is_monochrome=is_mono,
                    filter_name=filter_name,
                    bits_per_component=bpc,
                ))

            except Exception as exc:
                log.debug("Skipping image %s on page %d: %s",
                          key_name, page_idx, exc)
                continue

    return all_images, total_img_bytes


# ═══════════════════════════════════════════════════════════════════
#  Smart image format detection
# ═══════════════════════════════════════════════════════════════════

def _is_photographic(img: Image.Image, raw: bytes, info: ImageInfo) -> bool:
    """
    Determine if an image is a photograph vs a diagram/screenshot/line-art.

    Returns True for photographs, False for diagrams/screenshots/line-art.

    Heuristics used:
    - If BitsPerComponent is 1: monochrome/B&W, NOT a photo
    - If the image is already JPEG-encoded: likely a photo
    - If unique colors < 256 and image is not tiny: NOT a photo
    - Sample a portion and check color variance — low variance with
      sharp edges = diagram
    """
    # Monochrome is never a photo
    if info.bits_per_component == 1 or info.is_monochrome:
        return False

    # JPEG-encoded images are almost always photos
    if info.is_jpeg:
        return True

    # Check unique color count on a sample
    try:
        # Work on a manageable sample size for performance
        sample = img
        if img.width > 256 or img.height > 256:
            sample = img.resize(
                (min(256, img.width), min(256, img.height)),
                Image.NEAREST,
            )

        # Convert to RGB for consistent color counting
        if sample.mode not in ("RGB", "L"):
            sample = sample.convert("RGB")

        colors = sample.getcolors(maxcolors=512)
        if colors is not None:
            # getcolors returns None if more than maxcolors unique colors
            num_colors = len(colors)
            # Few unique colors on a non-tiny image = diagram/screenshot
            if num_colors < 256 and info.pixel_count > 4096:
                return False

        # If getcolors returned None, there are many colors — likely a photo.
        # But also check variance for the edge case of smooth gradients
        # vs. actual photographic content.

        if sample.mode == "L":
            if not HAS_NUMPY:
                return True

            arr = np.asarray(sample, dtype=float)

            # Compute local variance using a simple block approach
            if arr.size > 64:
                block_size = min(8, arr.shape[0], arr.shape[1])
                h_trim = (arr.shape[0] // block_size) * block_size
                w_trim = (arr.shape[1] // block_size) * block_size
                if h_trim > 0 and w_trim > 0:
                    trimmed = arr[:h_trim, :w_trim]
                    blocks = trimmed.reshape(
                        h_trim // block_size, block_size,
                        w_trim // block_size, block_size,
                    )
                    block_vars = blocks.var(axis=(1, 3))
                    mean_var = block_vars.mean()
                    if mean_var < 50:
                        return False
        else:
            if not HAS_NUMPY:
                return True

            arr = np.asarray(sample, dtype=float)

            if arr.size > 64:
                overall_var = arr.var()
                if overall_var < 100:
                    return False

    except Exception:
        pass

    # Default: assume photographic
    return True


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
    images_kept_lossless: int = 0      # kept as PNG/Flate
    images_converted_bw: int = 0       # converted to 1-bit B&W


def _should_skip(info: ImageInfo, preset: Preset) -> Optional[str]:
    """Return a reason to skip, or None if the image should be processed."""
    if info.is_tiny or max(info.pixel_w, info.pixel_h) < preset.skip_below_px:
        return "tiny"

    # Get the appropriate DPI for this image type
    target_dpi = preset.get_dpi_for_image(
        info.is_grayscale, info.is_monochrome,
    )

    needs_downscale = (
        info.effective_dpi > 0
        and info.effective_dpi > target_dpi * 1.1
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


def _encode_as_bw(img: Image.Image) -> tuple[bytes, int, int]:
    """
    Convert an image to 1-bit black and white and encode as raw packed bits.
    Returns (encoded_bytes, width, height).
    """
    # Convert to grayscale first if needed
    if img.mode != "L":
        img = img.convert("L")

    # Threshold to 1-bit (Pillow's "1" mode uses 0/255 internally)
    bw = img.point(lambda x: 0 if x < 128 else 255, "1")

    w, h = bw.size
    # Pack into bytes: each byte holds 8 pixels, MSB first
    # PIL's tobytes() for mode "1" returns packed bits
    raw_bits = bw.tobytes()

    return raw_bits, w, h


def _encode_as_flate(img: Image.Image) -> tuple[bytes, str, int, int]:
    """
    Encode an image as raw pixel data (to be Flate-compressed by pikepdf).
    Returns (raw_bytes, colorspace_name, width, height).
    """
    if img.mode == "L":
        cs_name = "/DeviceGray"
    elif img.mode == "RGB":
        cs_name = "/DeviceRGB"
    else:
        img = img.convert("RGB")
        cs_name = "/DeviceRGB"

    raw = img.tobytes()
    return raw, cs_name, img.width, img.height


def compress_images_smart(
    pdf: pikepdf.Pdf,
    preset: Preset,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> CompressionStats:
    """
    Recompress images with DPI awareness, smart format detection, and skipping.

    Key design choices:
      - Tracks processed XObjects by object ID to avoid double-recompression
        of images shared across pages.
      - Composites soft-masked images against white before JPEG encoding
        so transparency is preserved visually.
      - Enforces a pixel-count ceiling to prevent decompression bombs.
      - Chooses encoding format based on image content:
        * B&W (1-bit) images: Flate with BitsPerComponent 1
        * Diagrams/screenshots: Flate (lossless), only downscale DPI
        * Photographs: JPEG as before
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

            _check_cancel(cancel)

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

                # ── Determine target DPI for this image type ─────
                target_dpi = preset.get_dpi_for_image(
                    info.is_grayscale, info.is_monochrome,
                )

                # ── Downscale if DPI exceeds target ──────────────
                scale = 1.0
                if info.effective_dpi > 0:
                    if info.effective_dpi > target_dpi * 1.1:
                        scale = target_dpi / info.effective_dpi
                else:
                    # Fallback heuristic when DPI is unknown
                    assumed_inches = max(info.pixel_w, info.pixel_h) / 150.0
                    if assumed_inches > 1.0:
                        approx_dpi = max(info.pixel_w, info.pixel_h) / assumed_inches
                        if approx_dpi > target_dpi * 1.2:
                            scale = target_dpi / approx_dpi

                if scale < 0.95:
                    new_w = max(1, int(info.pixel_w * scale))
                    new_h = max(1, int(info.pixel_h * scale))
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    stats.images_downscaled += 1

                # ── Determine encoding strategy ──────────────────
                is_photo = _is_photographic(img, raw, info)

                if info.is_monochrome or info.bits_per_component == 1:
                    # ── B&W (1-bit) encoding ─────────────────────
                    bw_data, bw_w, bw_h = _encode_as_bw(img)

                    # Only replace if we save space
                    if len(bw_data) < info.raw_size:
                        xobj.write(bw_data, filter=pikepdf.Name("/FlateDecode"))
                        xobj["/Type"]             = pikepdf.Name("/XObject")
                        xobj["/Subtype"]          = pikepdf.Name("/Image")
                        xobj["/ColorSpace"]       = pikepdf.Name("/DeviceGray")
                        xobj["/BitsPerComponent"] = 1
                        xobj["/Width"]            = bw_w
                        xobj["/Height"]           = bw_h

                        if smask_obj is not None and "/SMask" in xobj:
                            del xobj["/SMask"]

                        stats.images_recompressed += 1
                        stats.images_converted_bw += 1

                elif not is_photo:
                    # ── Diagram/screenshot: lossless Flate ───────
                    # Color mode
                    if info.is_grayscale or (preset.force_grayscale and img.mode != "L"):
                        if img.mode != "L":
                            img = img.convert("L")
                    else:
                        if img.mode not in ("RGB", "L"):
                            img = img.convert("RGB")

                    raw_pixels, cs_name, enc_w, enc_h = _encode_as_flate(img)

                    # Only replace if we save space
                    if len(raw_pixels) < info.raw_size:
                        xobj.write(raw_pixels, filter=pikepdf.Name("/FlateDecode"))
                        xobj["/Type"]             = pikepdf.Name("/XObject")
                        xobj["/Subtype"]          = pikepdf.Name("/Image")
                        xobj["/ColorSpace"]       = pikepdf.Name(cs_name)
                        xobj["/BitsPerComponent"] = 8
                        xobj["/Width"]            = enc_w
                        xobj["/Height"]           = enc_h

                        if smask_obj is not None and "/SMask" in xobj:
                            del xobj["/SMask"]

                        stats.images_recompressed += 1
                        stats.images_kept_lossless += 1
                    elif len(raw_pixels) >= info.raw_size:
                        # Flate didn't save space — try JPEG as fallback
                        # for diagrams that happen to have many colors
                        if img.mode == "L":
                            cs_name_jpg = "/DeviceGray"
                        else:
                            if img.mode != "RGB":
                                img = img.convert("RGB")
                            cs_name_jpg = "/DeviceRGB"

                        buf = io.BytesIO()
                        img.save(buf, format="JPEG",
                                 quality=preset.jpeg_quality, optimize=True)
                        new_data = buf.getvalue()

                        if len(new_data) < info.raw_size:
                            xobj.write(new_data,
                                       filter=pikepdf.Name("/DCTDecode"))
                            xobj["/Type"]             = pikepdf.Name("/XObject")
                            xobj["/Subtype"]          = pikepdf.Name("/Image")
                            xobj["/ColorSpace"]       = pikepdf.Name(cs_name_jpg)
                            xobj["/BitsPerComponent"] = 8
                            xobj["/Width"]            = img.width
                            xobj["/Height"]           = img.height

                            if smask_obj is not None and "/SMask" in xobj:
                                del xobj["/SMask"]

                            stats.images_recompressed += 1

                else:
                    # ── Photo: JPEG encoding ─────────────────────
                    if info.is_grayscale or (preset.force_grayscale
                                             and img.mode != "L"):
                        if img.mode != "L":
                            img = img.convert("L")
                        cs_name = "/DeviceGray"
                    else:
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        cs_name = "/DeviceRGB"

                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=preset.jpeg_quality,
                             optimize=True)
                    new_data = buf.getvalue()

                    # Only replace if we actually saved space
                    if len(new_data) < info.raw_size:
                        xobj.write(new_data,
                                   filter=pikepdf.Name("/DCTDecode"))
                        xobj["/Type"]             = pikepdf.Name("/XObject")
                        xobj["/Subtype"]          = pikepdf.Name("/Image")
                        xobj["/ColorSpace"]       = pikepdf.Name(cs_name)
                        xobj["/BitsPerComponent"] = 8
                        xobj["/Width"]            = img.width
                        xobj["/Height"]           = img.height

                        if smask_obj is not None and "/SMask" in xobj:
                            del xobj["/SMask"]

                        stats.images_recompressed += 1

            except CancelledError:
                raise
            except (Image.DecompressionBombError, Image.DecompressionBombWarning):
                stats.images_skipped_bomb += 1
                if on_progress:
                    on_progress(current, stats.images_total, "Skip (bomb)")
                continue
            except Exception as exc:
                log.debug("Error compressing image %s: %s", key_name, exc)
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
#  PDF structure optimization
# ═══════════════════════════════════════════════════════════════════

def optimize_structure(pdf: pikepdf.Pdf, strip_meta: bool = False):
    """
    Optimize PDF structure by removing unnecessary objects.

    Removes:
      - Embedded JavaScript (/JS, /JavaScript in catalog and pages)
      - /OpenAction if it references JavaScript
      - Page thumbnails
      - /PieceInfo (application-specific data from InDesign, Illustrator, etc.)
      - /AcroForm if empty (no actual form fields)
      - /MarkInfo, /StructTreeRoot if strip_meta is True (accessibility tree)
    """
    root = pdf.Root

    # ── Remove JavaScript from catalog ───────────────────────────
    # /Names -> /JavaScript
    names = root.get("/Names")
    if names is not None:
        if "/JavaScript" in names:
            try:
                del names["/JavaScript"]
            except Exception:
                pass

    # /OpenAction — remove if it references JavaScript
    open_action = root.get("/OpenAction")
    if open_action is not None:
        try:
            oa_str = str(open_action)
            # Remove if it's a JavaScript action or references JS
            if "/JavaScript" in oa_str or "/JS" in oa_str:
                del root["/OpenAction"]
        except Exception:
            pass

    # ── Remove /PieceInfo from catalog ───────────────────────────
    if "/PieceInfo" in root:
        try:
            del root["/PieceInfo"]
        except Exception:
            pass

    # ── Remove /AcroForm if empty ────────────────────────────────
    acroform = root.get("/AcroForm")
    if acroform is not None:
        try:
            fields = acroform.get("/Fields")
            if fields is None or (isinstance(fields, pikepdf.Array)
                                  and len(fields) == 0):
                del root["/AcroForm"]
        except Exception:
            pass

    # ── Per-page cleanup ─────────────────────────────────────────
    for page in pdf.pages:
        # Remove page thumbnails
        if "/Thumb" in page:
            try:
                del page["/Thumb"]
            except Exception:
                pass

        # Remove /PieceInfo from pages
        if "/PieceInfo" in page:
            try:
                del page["/PieceInfo"]
            except Exception:
                pass

        # Remove JavaScript actions from page annotations
        annots = page.get("/Annots")
        if annots is not None:
            try:
                for annot in annots:
                    aa = annot.get("/AA")
                    if aa is not None:
                        # Remove JavaScript-type additional actions
                        for js_key in ["/JS", "/JavaScript"]:
                            if js_key in aa:
                                try:
                                    del aa[js_key]
                                except Exception:
                                    pass
                    # Also check direct /A action
                    action = annot.get("/A")
                    if action is not None:
                        action_type = action.get("/S")
                        if action_type is not None and str(action_type) == "/JavaScript":
                            try:
                                del annot["/A"]
                            except Exception:
                                pass
            except Exception:
                pass

    # ── Accessibility tree removal (can be huge) ─────────────────
    if strip_meta:
        if "/MarkInfo" in root:
            try:
                del root["/MarkInfo"]
            except Exception:
                pass
        if "/StructTreeRoot" in root:
            try:
                del root["/StructTreeRoot"]
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════
#  Ghostscript backend (optional)
# ═══════════════════════════════════════════════════════════════════

def find_ghostscript(*, force_refresh: bool = False) -> Optional[str]:
    """
    Check for Ghostscript on PATH. Result is cached after first call.
    Returns the executable name/path if found, None otherwise.
    Checks: gs, gswin64c, gswin32c
    """
    cache_key = "gs"
    with _gs_cache_lock:
        if not force_refresh and cache_key in _gs_cache:
            return _gs_cache[cache_key]

    result_path = None
    for candidate in ("gs", "gswin64c", "gswin32c"):
        path = shutil.which(candidate)
        if path is not None:
            try:
                result = subprocess.run(
                    [path, "--version"],
                    capture_output=True, timeout=10,
                )
                if result.returncode == 0:
                    result_path = path
                    break
            except (subprocess.TimeoutExpired, OSError):
                continue

    with _gs_cache_lock:
        _gs_cache[cache_key] = result_path
    return result_path


def _preset_to_gs_setting(preset_key: str) -> str:
    """Map a preset key to a Ghostscript -dPDFSETTINGS value."""
    mapping = {
        "screen": "/screen",
        "ebook": "/ebook",
        "standard": "/printer",
        "high": "/printer",
        "prepress": "/prepress",
    }
    return mapping.get(preset_key, "/default")


def compress_with_ghostscript(
    input_path: str,
    output_path: str,
    preset_key: str,
    cancel: Optional[threading.Event] = None,
) -> Optional[int]:
    """
    Run Ghostscript to further optimize a PDF (font subsetting, etc.).

    Returns the output file size on success, or None if Ghostscript is
    not available or the process fails.
    """
    gs_path = find_ghostscript()
    if gs_path is None:
        return None

    # Sanitize paths to prevent argument injection
    safe_input = _sanitize_path_for_subprocess(input_path)
    safe_output = _sanitize_path_for_subprocess(output_path)

    pdf_settings = _preset_to_gs_setting(preset_key)

    cmd = [
        gs_path,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.5",
        f"-dPDFSETTINGS={pdf_settings}",
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        "-dSAFER",  # Ghostscript sandbox — restricts file access
        # Don't re-encode images — pikepdf already handled that
        "-dAutoFilterColorImages=false",
        "-dAutoFilterGrayImages=false",
        "-dColorImageFilter=/FlateEncode",
        "-dGrayImageFilter=/FlateEncode",
        "-dDownsampleColorImages=false",
        "-dDownsampleGrayImages=false",
        "-dDownsampleMonoImages=false",
        # Font subsetting
        "-dSubsetFonts=true",
        "-dEmbedAllFonts=true",
        f"-sOutputFile={safe_output}",
        "--",  # end of options — prevents input path from being parsed as flag
        safe_input,
    ]

    # Maximum wall-clock time for the Ghostscript process (5 minutes).
    gs_timeout = 300

    try:
        _check_cancel(cancel)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Poll for completion, checking cancellation and enforcing timeout
        elapsed = 0.0
        while True:
            try:
                retcode = proc.wait(timeout=2.0)
                break
            except subprocess.TimeoutExpired:
                elapsed += 2.0
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise CancelledError("Compression cancelled during Ghostscript pass")
                if elapsed >= gs_timeout:
                    proc.kill()
                    proc.wait()
                    log.warning("Ghostscript timed out after %d seconds", int(elapsed))
                    return None

        if retcode != 0:
            stderr_out = ""
            try:
                stderr_out = proc.stderr.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            log.warning("Ghostscript failed (code %d): %s", retcode, stderr_out)
            return None

        if os.path.isfile(output_path):
            return os.path.getsize(output_path)
        return None

    except CancelledError:
        raise
    except Exception as exc:
        log.debug("Ghostscript error: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════
#  PDF analysis (for size estimation in UI)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PDFAnalysis:
    file_size: int
    page_count: int
    image_count: int
    image_bytes: int
    font_count: int           # number of unique font objects
    font_bytes: int           # estimated total font bytes
    non_image_bytes: int
    images: list              # list of ImageInfo
    is_encrypted: bool
    gs_available: bool = False
    pdfa_conformance: Optional[str] = None  # e.g. "PDF/A-1b"
    is_valid_pdf: bool = True               # has correct magic bytes

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

            # Get appropriate DPI for this image type
            target_dpi = preset.get_dpi_for_image(
                img.is_grayscale, img.is_monochrome,
            )

            scale = 1.0
            if img.effective_dpi > target_dpi * 1.1:
                scale = target_dpi / img.effective_dpi

            new_pixels = img.pixel_w * img.pixel_h * (scale ** 2)
            channels = 1 if img.is_grayscale else 3

            # Estimate based on encoding type
            if img.is_monochrome or img.bits_per_component == 1:
                # B&W images compress very well with Flate
                ratio = 0.02  # 1 bit/pixel + Flate compression
                est_size = int(new_pixels * ratio)
            elif not img.is_jpeg and img.pixel_count > 4096:
                # Likely diagram/screenshot — lossless Flate
                # Flate on raw pixels typically achieves 2:1 to 10:1
                ratio = channels * 0.3
                est_size = int(new_pixels * ratio)
            else:
                # Photographic JPEG estimation
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


def _count_fonts(pdf: pikepdf.Pdf) -> tuple[int, int]:
    """
    Count unique font objects in the PDF and estimate their total size.
    Returns (font_count, estimated_font_bytes).
    """
    seen_font_ids: set[int] = set()
    total_font_bytes = 0

    for page in pdf.pages:
        res = page.get("/Resources")
        if res is None:
            continue
        fonts = res.get("/Font")
        if fonts is None:
            continue

        try:
            for font_key in fonts.keys():
                font_obj = fonts[font_key]
                try:
                    font_id = font_obj.objgen[0]
                except Exception:
                    font_id = id(font_obj)

                if font_id in seen_font_ids:
                    continue
                seen_font_ids.add(font_id)

                # Estimate font size from embedded font streams
                # Check /FontDescriptor -> /FontFile, /FontFile2, /FontFile3
                descriptor = font_obj.get("/FontDescriptor")
                if descriptor is not None:
                    for ff_key in ("/FontFile", "/FontFile2", "/FontFile3"):
                        ff = descriptor.get(ff_key)
                        if ff is not None:
                            try:
                                font_data = bytes(ff.read_raw_bytes())
                                total_font_bytes += len(font_data)
                            except Exception:
                                # Estimate a small default if we can't read
                                total_font_bytes += 8192
                            break

                # Also check for CIDFont descendants
                descendants = font_obj.get("/DescendantFonts")
                if descendants is not None:
                    try:
                        for desc_font in descendants:
                            desc_descriptor = desc_font.get("/FontDescriptor")
                            if desc_descriptor is not None:
                                for ff_key in ("/FontFile", "/FontFile2",
                                               "/FontFile3"):
                                    ff = desc_descriptor.get(ff_key)
                                    if ff is not None:
                                        try:
                                            font_data = bytes(
                                                ff.read_raw_bytes())
                                            total_font_bytes += len(font_data)
                                        except Exception:
                                            total_font_bytes += 8192
                                        break
                    except Exception:
                        pass

        except Exception:
            continue

    return len(seen_font_ids), total_font_bytes


def analyze_pdf(path: str, password: Optional[str] = None) -> PDFAnalysis:
    """Quick analysis of a PDF file for the UI."""
    file_size = os.path.getsize(path)
    gs_available = find_ghostscript() is not None

    # Check PDF magic bytes first
    is_valid = validate_pdf_magic(path)
    if not is_valid:
        return PDFAnalysis(
            file_size=file_size, page_count=0,
            image_count=0, image_bytes=0,
            font_count=0, font_bytes=0,
            non_image_bytes=file_size, images=[],
            is_encrypted=False,
            gs_available=gs_available,
            is_valid_pdf=False,
        )

    try:
        with pikepdf.open(path, password=password or "") as pdf:
            page_count = len(pdf.pages)
            images, img_bytes = analyze_images(pdf)
            font_count, font_bytes = _count_fonts(pdf)
            pdfa_level = detect_pdfa_conformance(pdf)
            return PDFAnalysis(
                file_size=file_size,
                page_count=page_count,
                image_count=len(images),
                image_bytes=img_bytes,
                font_count=font_count,
                font_bytes=font_bytes,
                non_image_bytes=file_size - img_bytes,
                images=images,
                is_encrypted=False,
                gs_available=gs_available,
                pdfa_conformance=pdfa_level,
            )
    except pikepdf.PasswordError:
        return PDFAnalysis(
            file_size=file_size, page_count=0,
            image_count=0, image_bytes=0,
            font_count=0, font_bytes=0,
            non_image_bytes=file_size, images=[],
            is_encrypted=True,
            gs_available=gs_available,
        )
    except Exception as exc:
        log.warning("Failed to analyze %s: %s", path, exc)
        return PDFAnalysis(
            file_size=file_size, page_count=0,
            image_count=0, image_bytes=0,
            font_count=0, font_bytes=0,
            non_image_bytes=file_size, images=[],
            is_encrypted=False,
            gs_available=gs_available,
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
    backup_path: Optional[str] = None
    pdfa_conformance: Optional[str] = None
    pdfa_warning: bool = False

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


class FileTooLargeError(Exception):
    """Raised when input file exceeds MAX_INPUT_FILE_SIZE."""
    pass


def _merge_duplicate_fonts(pdf: pikepdf.Pdf) -> int:
    """
    Merge duplicate embedded fonts that have identical font data.
    Returns the number of fonts merged.
    """
    merged = 0
    font_data_map: dict[bytes, pikepdf.Object] = {}  # hash -> first font descriptor

    for page in pdf.pages:
        res = page.get("/Resources")
        if res is None:
            continue
        fonts = res.get("/Font")
        if fonts is None:
            continue

        try:
            for font_key in list(fonts.keys()):
                font_obj = fonts[font_key]
                descriptor = font_obj.get("/FontDescriptor")
                if descriptor is None:
                    continue

                for ff_key in ("/FontFile", "/FontFile2", "/FontFile3"):
                    ff = descriptor.get(ff_key)
                    if ff is None:
                        continue
                    try:
                        font_bytes = bytes(ff.read_raw_bytes())
                        font_hash = hashlib.sha256(font_bytes).digest()

                        if font_hash in font_data_map:
                            # Point to the existing font stream instead
                            descriptor[ff_key] = font_data_map[font_hash]
                            merged += 1
                        else:
                            font_data_map[font_hash] = ff
                    except Exception:
                        pass
                    break
        except Exception:
            continue

    if merged > 0:
        log.info("Merged %d duplicate font streams", merged)
    return merged


def _optimize_content_streams(pdf: pikepdf.Pdf) -> None:
    """
    Optimize page content streams by removing redundant operators.
    Removes duplicate consecutive state saves/restores and empty groups.
    """
    for page in pdf.pages:
        try:
            contents = page.get("/Contents")
            if contents is None:
                continue

            # Read and parse the content stream
            if isinstance(contents, pikepdf.Array):
                raw_parts = []
                for part in contents:
                    try:
                        raw_parts.append(bytes(part.read_raw_bytes()))
                    except Exception:
                        raw_parts.append(b"")
                raw = b"\n".join(raw_parts)
            else:
                raw = bytes(contents.read_raw_bytes())

            if len(raw) > MAX_CONTENT_STREAM_BYTES:
                continue

            # Remove redundant q/Q pairs (empty save/restore)
            # Pattern: q followed immediately by Q with only whitespace between
            original_len = len(raw)
            cleaned = re.sub(rb'\bq\s+Q\b', b'', raw)

            # Only rewrite if we actually saved something meaningful
            if len(cleaned) < original_len - 16:
                page["/Contents"] = pdf.make_stream(cleaned)

        except Exception as e:
            log.debug("Content stream optimization failed on page: %s", e)
            continue


def compress_pdf(
    input_path: str,
    output_path: Optional[str] = None,
    preset_key: str = "standard",
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    linearize: bool = False,
    cancel: Optional[threading.Event] = None,
    password: Optional[str] = None,
    use_ghostscript: bool = False,
    backup_on_overwrite: bool = True,
) -> Result:
    """
    Compress a PDF file.

    Args:
        input_path:         source file
        output_path:        destination (default: <name>_compressed.pdf)
        preset_key:         key from PRESETS dict
        on_progress:        callback(current_image, total_images, status)
        linearize:          if True, produce a web-optimized (linearized) PDF
        cancel:             threading.Event — set to cancel compression
        password:           password for encrypted PDFs (None = no password)
        use_ghostscript:    if True and gs is available, run a Ghostscript
                            pass after pikepdf for font subsetting
        backup_on_overwrite: if True and output overwrites input, create a
                            .backup copy first

    Returns:
        Result with full statistics.

    Raises:
        EncryptedPDFError: if the PDF is password-protected and no
                           valid password was provided.
        InvalidPDFError:   if the file does not have valid PDF magic bytes.
        FileNotFoundError: if input_path doesn't exist.
        FileTooLargeError: if input_path exceeds MAX_INPUT_FILE_SIZE.
        CancelledError:    if cancel event is set during compression.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"File not found: {input_path}")

    # Validate PDF magic bytes before doing anything
    if not validate_pdf_magic(input_path):
        raise InvalidPDFError(
            f"Not a valid PDF file (missing %PDF- header): "
            f"{os.path.basename(input_path)}"
        )

    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_compressed{ext}"

    preset = PRESETS[preset_key]
    original_size = os.path.getsize(input_path)

    if original_size > MAX_INPUT_FILE_SIZE:
        raise FileTooLargeError(
            f"File is {fmt_size(original_size)} — exceeds the "
            f"{fmt_size(MAX_INPUT_FILE_SIZE)} limit"
        )

    _check_cancel(cancel)

    # Create backup if overwriting the original file
    backup_path = None
    is_overwriting = os.path.normpath(os.path.abspath(input_path)) == \
                     os.path.normpath(os.path.abspath(output_path))
    if is_overwriting and backup_on_overwrite:
        backup_path = create_backup(input_path)

    out_dir = os.path.dirname(output_path) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", dir=out_dir)

    try:
        os.close(fd)

        try:
            pdf = pikepdf.open(input_path, password=password or "")
        except pikepdf.PasswordError:
            raise EncryptedPDFError(
                "This PDF is password-protected. "
                "Please provide the correct password."
            )

        _check_cancel(cancel)

        with pdf:
            # Detect PDF/A conformance before any modifications
            pdfa_level = detect_pdfa_conformance(pdf)
            pdfa_warning = False
            if pdfa_level:
                log.info("Detected %s conformance in %s", pdfa_level, input_path)
                if preset.strip_metadata:
                    pdfa_warning = True
                    log.warning(
                        "%s: stripping metadata will break %s conformance",
                        os.path.basename(input_path), pdfa_level,
                    )

            stats = compress_images_smart(pdf, preset, on_progress, cancel)

            _check_cancel(cancel)

            # ── Font deduplication ────────────────────────────────
            _merge_duplicate_fonts(pdf)

            # ── Content stream optimization ───────────────────────
            _optimize_content_streams(pdf)

            # ── Structure optimization ───────────────────────────
            optimize_structure(pdf, strip_meta=preset.strip_metadata)

            if preset.strip_metadata:
                strip_metadata(pdf)

            pdf.remove_unreferenced_resources()
            pdf.save(
                tmp_path,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                linearize=linearize,
            )

        pikepdf_size = os.path.getsize(tmp_path)

        # ── Optional Ghostscript pass ────────────────────────────
        gs_size = None
        gs_tmp_path = None
        if use_ghostscript:
            _check_cancel(cancel)
            fd2, gs_tmp_path = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
            try:
                os.close(fd2)
                gs_size = compress_with_ghostscript(
                    tmp_path, gs_tmp_path, preset_key, cancel,
                )
            except CancelledError:
                if gs_tmp_path and os.path.exists(gs_tmp_path):
                    os.remove(gs_tmp_path)
                raise
            except Exception as e:
                log.warning("Ghostscript pass failed: %s", e)
                if gs_tmp_path and os.path.exists(gs_tmp_path):
                    os.remove(gs_tmp_path)
                gs_tmp_path = None

        # ── Pick the best result ─────────────────────────────────
        if gs_size is not None and gs_size < pikepdf_size:
            compressed_size = gs_size
            os.remove(tmp_path)
            tmp_path = gs_tmp_path
        else:
            compressed_size = pikepdf_size
            if gs_tmp_path and os.path.exists(gs_tmp_path):
                os.remove(gs_tmp_path)

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
                backup_path=backup_path,
                pdfa_conformance=pdfa_level,
                pdfa_warning=pdfa_warning,
            )

        # ── Preserve original file permissions ───────────────────
        _copy_file_permissions(input_path, tmp_path)

        os.replace(tmp_path, output_path)

        # Securely clear password from memory
        if password:
            _secure_delete_string(password)

        return Result(
            input_path=input_path,
            output_path=output_path,
            original_size=original_size,
            compressed_size=compressed_size,
            stats=stats,
            skipped=False,
            backup_path=backup_path,
            pdfa_conformance=pdfa_level,
            pdfa_warning=pdfa_warning,
        )

    except CancelledError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def fmt_size(n: int) -> str:
    if n < 1024:    return f"{n} B"
    if n < 1048576: return f"{n / 1024:.1f} KB"
    return f"{n / 1048576:.2f} MB"
