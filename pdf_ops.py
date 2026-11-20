"""PDF operations — merge, split, and page manipulation."""

import logging
import math
import os
import re
import secrets
import tempfile
import threading
import zlib
from dataclasses import dataclass, field
from typing import Callable

import fitz  # PyMuPDF
import pikepdf

from pdf_analyze import (
    file_sha256,
    id_elements,
    name_tree_items,
    remove_embedded_files,
    remove_links,
    remove_metadata_streams,
    remove_scripts,
    save_with_content_id,
    trailer_ids,
    write_name_tree,
)
from pdf_verify import TermFinder, WalkStatus, term_boxes, verify_redaction, walk_objects

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Path safety
# ═══════════════════════════════════════════════════════════════════

def contained_output_path(out_folder: str, out_name: str) -> str:
    """Join out_folder + out_name, guaranteeing the result stays inside
    out_folder.

    os.path.join() silently DISCARDS out_folder if out_name is an
    absolute path (e.g. "/etc/passwd" or "C:\\Windows\\..."), and "../"
    segments can walk back out of out_folder -- both let a user-editable
    naming template/output name escape the folder the user actually
    chose. Used everywhere an output path is built from such a name
    (protect/unlock/watermark naming templates in ui/bridge.py,
    split_pdf's name_template here).

    Raises ValueError if the resolved candidate is not inside out_folder.
    """
    base = os.path.realpath(out_folder)
    candidate = os.path.realpath(os.path.join(base, out_name))
    if os.path.commonpath([base, candidate]) != base:
        raise ValueError(f"Output path escapes the chosen folder: {out_name!r}")
    return candidate


def is_within_directory(path: str, base: str) -> bool:
    """Return True iff *path* resolves to *base* itself or something inside it.

    Boolean sibling of contained_output_path, for callers that need to VET an
    already-built path rather than construct one -- e.g. ui/bridge.py's
    deleteFile/copyFile scoping operations to the workspace temp dir. Lives
    here (a Qt-free module) so its tests don't drag in PySide6 via bridge.py.

    Uses os.path.commonpath on realpath'd paths, NOT ``str.startswith`` --
    startswith would treat ``/foo/ws_evil`` as inside ``/foo/ws``. Returns
    False (never raises) on any escape, and on commonpath's ValueError for
    paths that share no common root (e.g. different drives on Windows).
    """
    try:
        base_r = os.path.realpath(base)
        path_r = os.path.realpath(path)
        return os.path.commonpath([base_r, path_r]) == base_r
    except (ValueError, OSError):
        return False


# ═══════════════════════════════════════════════════════════════════
#  Data classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MergeResult:
    output_path: str
    input_paths: list[str]
    total_pages: int
    output_size: int


@dataclass
class SplitResult:
    input_path: str
    output_paths: list[str]
    pages_per_output: list[int]


@dataclass
class PageOpResult:
    input_path: str
    output_path: str
    operations: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
#  Merge
# ═══════════════════════════════════════════════════════════════════

def merge_pdfs(
    input_paths: list[str],
    output_path: str,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> MergeResult:
    """Merge multiple PDFs into one.

    Args:
        input_paths: List of PDF file paths to merge (in order).
        output_path: Destination path for merged PDF.
        on_progress: Callback(current_file_index, total_files).
        cancel: Threading event to signal cancellation.

    Returns:
        MergeResult with merge details.
    """
    if len(input_paths) < 2:
        raise ValueError("Need at least 2 files to merge")

    total_pages = 0
    dest = pikepdf.Pdf.new()

    for i, path in enumerate(input_paths):
        if cancel and cancel.is_set():
            raise InterruptedError("Merge cancelled")

        if on_progress:
            on_progress(i, len(input_paths))

        try:
            src = pikepdf.open(path)
        except pikepdf.PasswordError:
            raise pikepdf.PasswordError(
                f"Cannot merge password-protected file: {os.path.basename(path)}"
            ) from None

        dest.pages.extend(src.pages)
        total_pages += len(src.pages)
        src.close()

    if cancel and cancel.is_set():
        raise InterruptedError("Merge cancelled")

    # Atomic write via temp file
    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        dest.save(tmp, compress_streams=True)
        dest.close()
        os.replace(tmp, output_path)
    except Exception:
        dest.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    output_size = os.path.getsize(output_path)
    log.info("Merged %d files (%d pages) → %s (%d bytes)",
             len(input_paths), total_pages, output_path, output_size)

    return MergeResult(
        output_path=output_path,
        input_paths=list(input_paths),
        total_pages=total_pages,
        output_size=output_size,
    )


# ═══════════════════════════════════════════════════════════════════
#  TOC / Bookmarks
# ═══════════════════════════════════════════════════════════════════

_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def get_toc(input_path: str) -> list[dict]:
    """Extract the table of contents (bookmarks/outlines) from a PDF.

    Uses PyMuPDF's ``doc.get_toc()`` which returns
    ``[[level, title, page_number], ...]`` (1-indexed pages).

    Each entry is augmented with *end_page* — the last page that belongs
    to that entry (computed from the next entry at the same or higher level,
    or the document's last page).

    Returns:
        List of dicts ``{level, title, page, end_page}`` sorted by page.
        Empty list if the PDF has no bookmarks.
    """
    doc = fitz.open(input_path)
    raw_toc = doc.get_toc()  # [[level, title, page], ...]
    num_pages = doc.page_count
    doc.close()

    if not raw_toc:
        return []

    entries: list[dict] = []
    for i, (level, title, page) in enumerate(raw_toc):
        # Find end page: next entry at same or higher (lower number) level
        end_page = num_pages
        for j in range(i + 1, len(raw_toc)):
            if raw_toc[j][0] <= level:
                end_page = raw_toc[j][2] - 1
                break

        entries.append({
            "level": level,
            "title": title.strip(),
            "page": page,
            "end_page": max(end_page, page),  # ensure at least 1 page
        })

    return entries


def _sanitize_title(title: str) -> str:
    """Sanitize a chapter title for use as a filename component."""
    clean = _UNSAFE_FILENAME_RE.sub('_', title)
    clean = clean.strip('. _')
    return clean[:80] or 'untitled'


# ═══════════════════════════════════════════════════════════════════
#  Split
# ═══════════════════════════════════════════════════════════════════

def _parse_ranges(range_str: str, max_page: int) -> list[tuple[int, int]]:
    """Parse a range string like '1-3, 5, 8-10' into (start, end) tuples (1-indexed, inclusive)."""
    ranges = []
    for part in range_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a.strip()), int(b.strip())
        else:
            start = end = int(part)
        if start < 1 or end > max_page or start > end:
            raise ValueError(f"Invalid range: {part} (pages 1–{max_page})")
        ranges.append((start, end))
    return ranges


def split_pdf(
    input_path: str,
    output_dir: str,
    mode: str = "all",
    ranges: str | None = None,
    every_n: int = 1,
    chapters: list[dict] | None = None,
    name_template: str = "{name}_page_{start}",
    cancel: threading.Event | None = None,
) -> SplitResult:
    """Split a PDF into multiple files.

    Args:
        input_path: Source PDF path.
        output_dir: Directory for output files.
        mode: "all" | "ranges" | "every_n" | "chapters".
        ranges: Range string like "1-3, 5, 8-10" (for mode="ranges").
        every_n: Pages per output file (for mode="every_n").
        chapters: List of {title, start_page, end_page} dicts (for mode="chapters").
        name_template: Output naming template. Supports {name}, {start}, {end}, {title}.
        cancel: Threading event for cancellation.

    Returns:
        SplitResult with output file paths.
    """
    if not output_dir:
        output_dir = os.path.dirname(os.path.abspath(input_path))
    src = pikepdf.open(input_path)
    num_pages = len(src.pages)
    base_name = os.path.splitext(os.path.basename(input_path))[0]

    # Build page groups: list of (start, end) 1-indexed inclusive
    # For chapters mode, also build a parallel titles list
    chapter_titles: list[str] = []

    if mode == "all":
        groups = [(i, i) for i in range(1, num_pages + 1)]
    elif mode == "ranges":
        if not ranges:
            raise ValueError("Range string required for 'ranges' mode")
        groups = _parse_ranges(ranges, num_pages)
    elif mode == "every_n":
        if every_n < 1:
            raise ValueError("every_n must be >= 1")
        groups = []
        for start in range(1, num_pages + 1, every_n):
            end = min(start + every_n - 1, num_pages)
            groups.append((start, end))
    elif mode == "chapters":
        if not chapters:
            raise ValueError("Chapters list required for 'chapters' mode")
        groups = []
        for ch in chapters:
            s = int(ch["start_page"])
            e = int(ch["end_page"])
            groups.append((s, e))
            chapter_titles.append(_sanitize_title(ch.get("title", "")))
    else:
        raise ValueError(f"Unknown split mode: {mode}")

    os.makedirs(output_dir, exist_ok=True)
    output_paths = []
    pages_per_output = []
    seen_counts: dict[str, int] = {}

    for idx, (start, end) in enumerate(groups):
        if cancel and cancel.is_set():
            src.close()
            raise InterruptedError("Split cancelled")

        title = chapter_titles[idx] if idx < len(chapter_titles) else ""
        out_name = name_template.format(
            name=base_name, filename=base_name,  # {name} and {filename} both work
            start=start, end=end, n=start,        # {start}/{end}/{n} all work
            title=title,
        ) + ".pdf"

        # Disambiguate within-run name collisions (e.g. chapters mode with
        # repeated/sanitized-to-the-same titles, or a template with no {n})
        # rather than silently letting a later group overwrite an earlier
        # one's output (OPS-04).
        seen_counts[out_name] = seen_counts.get(out_name, 0) + 1
        if seen_counts[out_name] > 1:
            root, ext = os.path.splitext(out_name)
            out_name = f"{root}_{seen_counts[out_name]}{ext}"

        out_path = contained_output_path(output_dir, out_name)

        dest = pikepdf.Pdf.new()
        for p in range(start - 1, end):  # 0-indexed
            dest.pages.append(src.pages[p])
        dest.save(out_path, compress_streams=True)
        dest.close()

        output_paths.append(out_path)
        pages_per_output.append(end - start + 1)

    src.close()
    log.info("Split %s into %d files", input_path, len(output_paths))

    return SplitResult(
        input_path=input_path,
        output_paths=output_paths,
        pages_per_output=pages_per_output,
    )


# ═══════════════════════════════════════════════════════════════════
#  Page operations
# ═══════════════════════════════════════════════════════════════════

def apply_page_operations(
    input_path: str,
    output_path: str,
    rotations: dict[int, int] | None = None,
    delete_pages: list[int] | None = None,
    new_order: list[int] | None = None,
    cancel: threading.Event | None = None,
) -> PageOpResult:
    """Apply page-level operations: rotate, delete, reorder.

    Args:
        input_path: Source PDF path.
        output_path: Destination PDF path.
        rotations: Dict mapping 0-indexed page → degrees (90, 180, 270).
        delete_pages: List of 0-indexed pages to delete.
        new_order: New page order as list of 0-indexed page numbers.
                   Applied AFTER deletions. If None, original order is kept.
        cancel: Threading event for cancellation.

    Returns:
        PageOpResult describing what was done.
    """
    src = pikepdf.open(input_path)
    ops = []

    # Apply rotations
    if rotations:
        for page_idx, degrees in rotations.items():
            if 0 <= page_idx < len(src.pages):
                current = int(src.pages[page_idx].get("/Rotate", 0))
                src.pages[page_idx]["/Rotate"] = (current + degrees) % 360
        ops.append(f"Rotated {len(rotations)} page(s)")

    if cancel and cancel.is_set():
        src.close()
        raise InterruptedError("Operation cancelled")

    # Build page list (apply deletions)
    delete_set = set(delete_pages) if delete_pages else set()
    remaining = [i for i in range(len(src.pages)) if i not in delete_set]
    if delete_pages:
        ops.append(f"Deleted {len(delete_set)} page(s)")

    # Apply reorder
    if new_order is not None:
        # new_order refers to indices within the remaining pages
        remaining = [remaining[i] for i in new_order if 0 <= i < len(remaining)]
        ops.append("Reordered pages")

    if cancel and cancel.is_set():
        src.close()
        raise InterruptedError("Operation cancelled")

    # Build output
    dest = pikepdf.Pdf.new()
    for idx in remaining:
        dest.pages.append(src.pages[idx])

    # Atomic write
    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        dest.save(tmp, compress_streams=True)
        dest.close()
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        dest.close()
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    ops_str = ", ".join(ops) if ops else "No changes"
    log.info("Page ops on %s → %s: %s", input_path, output_path, ops_str)

    return PageOpResult(
        input_path=input_path,
        output_path=output_path,
        operations=ops,
    )


# ═══════════════════════════════════════════════════════════════════
#  Security
# ═══════════════════════════════════════════════════════════════════

def protect_pdf(input_path, output_path, user_password="", owner_password="",
                permissions=None, encryption="AES-256"):
    """Add password protection and set permissions."""
    if permissions is None:
        permissions = {"print": True, "copy": True, "edit": True, "annotate": True}

    allow = pikepdf.Permissions(
        print_lowres=permissions.get("print", True),
        print_highres=permissions.get("print", True),
        extract=permissions.get("copy", True),
        modify_other=permissions.get("edit", True),
        modify_annotation=permissions.get("annotate", True),
        modify_form=permissions.get("edit", True),
        modify_assembly=permissions.get("edit", True),
        accessibility=True,
    )

    use_aes = encryption.startswith("AES")
    R = 6 if encryption == "AES-256" else 4

    # Permission flags are only enforceable against someone without the
    # owner password. Without a distinct one, owner==user, so anyone who
    # can open the file (holds the user password) also holds owner rights
    # and can strip every restriction (OPS-01). Generate a random one the
    # caller never needs to know -- it only gates permission bits, never
    # the ability to open the file.
    if not owner_password:
        owner_password = secrets.token_urlsafe(24)

    src = pikepdf.open(input_path)
    enc = pikepdf.Encryption(
        owner=owner_password,
        user=user_password,
        allow=allow,
        aes=use_aes,
        R=R,
    )

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        src.save(tmp, encryption=enc)
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("Protected %s → %s", input_path, output_path)


def unlock_pdf(input_path, output_path, password=""):
    """Remove password protection from a PDF."""
    src = pikepdf.open(input_path, password=password)
    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        src.save(tmp)
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("Unlocked %s → %s", input_path, output_path)


# ═══════════════════════════════════════════════════════════════════
#  PDF to Images
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PdfToImagesResult:
    input_path: str
    output_dir: str
    image_paths: list[str]
    page_count: int
    format: str

def pdf_to_images(input_path, output_dir, fmt="png", dpi=150, quality=85,
                  page_range=None, on_progress=None, cancel=None):
    """Export PDF pages as fully-rendered images using PyMuPDF."""
    import fitz  # PyMuPDF

    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(input_path)
    num_pages = len(doc)
    base = os.path.splitext(os.path.basename(input_path))[0]

    if page_range:
        ranges = _parse_ranges(page_range, num_pages)
        pages_to_export = []
        for s, e in ranges:
            pages_to_export.extend(range(s - 1, e))
    else:
        pages_to_export = list(range(num_pages))

    image_paths = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    for i, page_idx in enumerate(pages_to_export):
        if cancel and cancel.is_set():
            doc.close()
            raise InterruptedError("Export cancelled")
        if on_progress:
            on_progress(i, len(pages_to_export))

        page = doc[page_idx]
        pix = page.get_pixmap(matrix=matrix, alpha=False)

        ext = "jpg" if fmt.lower() in ("jpg", "jpeg") else "png"
        out_path = os.path.join(output_dir, f"{base}_page_{page_idx + 1}.{ext}")

        if ext == "jpg":
            pix.save(out_path, output="jpeg", jpg_quality=quality)
        else:
            pix.save(out_path, output="png")
        image_paths.append(out_path)

    doc.close()
    log.info("Exported %d pages from %s as %s", len(image_paths), input_path, fmt)
    return PdfToImagesResult(input_path, output_dir, image_paths, len(image_paths), fmt)


# ═══════════════════════════════════════════════════════════════════
#  Images to PDF
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ImagesToPdfResult:
    output_path: str
    image_count: int
    page_count: int

def images_to_pdf(image_paths, output_path, page_size="auto", margin_mm=10,
                  on_progress=None, cancel=None):
    """Convert images into a PDF document.

    Handles EXIF rotation, transparency (via white background compositing),
    and preserves image quality: a genuinely lossless source (PNG, BMP,
    TIFF, ...) is embedded losslessly (Flate); only an already-lossy JPEG
    source is re-encoded as JPEG (quality 92).
    """
    import io

    from PIL import Image, ImageOps

    PAGE_SIZES = {
        "a4": (595.28, 841.89),
        "letter": (612, 792),
        "auto": None,
    }

    margin_pt = margin_mm * 72 / 25.4
    dest = pikepdf.Pdf.new()

    for i, img_path in enumerate(image_paths):
        if cancel and cancel.is_set():
            raise InterruptedError("Conversion cancelled")
        if on_progress:
            on_progress(i, len(image_paths))

        pil = Image.open(img_path)
        # Capture the source format before any transform -- exif_transpose()
        # (and Image.new() for transparency compositing, below) strip it.
        source_format = (pil.format or "").upper()

        # Apply EXIF orientation (auto-rotates photos from cameras)
        pil = ImageOps.exif_transpose(pil)

        # Handle transparency: composite onto white background
        if pil.mode in ("RGBA", "LA", "PA"):
            background = Image.new("RGB", pil.size, "white")
            background.paste(pil, mask=pil.split()[-1])
            pil = background
        elif pil.mode != "RGB":
            pil = pil.convert("RGB")

        img_w, img_h = pil.size
        size_key = page_size.lower()
        page_dims = PAGE_SIZES.get(size_key)

        if page_dims is None:
            dpi = pil.info.get("dpi", (72, 72))
            # Clamp DPI to sane range to avoid tiny/huge pages
            dpi_x = max(36, min(float(dpi[0]), 1200))
            dpi_y = max(36, min(float(dpi[1]), 1200))
            pw = img_w * 72 / dpi_x + 2 * margin_pt
            ph = img_h * 72 / dpi_y + 2 * margin_pt
        else:
            pw, ph = page_dims

        # Preserve quality: a genuinely lossless source is embedded
        # losslessly (Flate) rather than always re-encoded to JPEG q92
        # (OPS-05). Only an already-lossy JPEG source gets re-encoded --
        # it's already lossy, so a fresh high-quality JPEG pass costs
        # nothing further worth avoiding.
        if source_format == "JPEG":
            buf = io.BytesIO()
            pil.save(buf, "JPEG", quality=92)
            img_data = buf.getvalue()
            img_filter = "/DCTDecode"
        else:
            img_data = zlib.compress(pil.tobytes(), level=9)
            img_filter = "/FlateDecode"

        # Create page with image
        page_pdf = pikepdf.Pdf.new()
        page_pdf.add_blank_page(page_size=(pw, ph))
        raw_img = pikepdf.Stream(page_pdf, img_data)
        raw_img["/Type"] = pikepdf.Name("/XObject")
        raw_img["/Subtype"] = pikepdf.Name("/Image")
        raw_img["/Width"] = img_w
        raw_img["/Height"] = img_h
        raw_img["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
        raw_img["/BitsPerComponent"] = 8
        raw_img["/Filter"] = pikepdf.Name(img_filter)

        # Calculate placement (centered with margins)
        avail_w = pw - 2 * margin_pt
        avail_h = ph - 2 * margin_pt
        scale = min(avail_w / img_w, avail_h / img_h)
        disp_w = img_w * scale
        disp_h = img_h * scale
        x = margin_pt + (avail_w - disp_w) / 2
        y = margin_pt + (avail_h - disp_h) / 2

        page = page_pdf.pages[0]
        page["/Resources"] = pikepdf.Dictionary({
            "/XObject": pikepdf.Dictionary({"/Img0": raw_img})
        })
        content = f"q {disp_w:.2f} 0 0 {disp_h:.2f} {x:.2f} {y:.2f} cm /Img0 Do Q"
        page["/Contents"] = pikepdf.Stream(page_pdf, content.encode())

        dest.pages.extend(page_pdf.pages)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        dest.save(tmp, compress_streams=True)
        dest.close()
        os.replace(tmp, output_path)
    except Exception:
        dest.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    log.info("Created PDF from %d images → %s", len(image_paths), output_path)
    return ImagesToPdfResult(output_path, len(image_paths), len(image_paths))


# ═══════════════════════════════════════════════════════════════════
#  PDF to Word
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PdfToWordResult:
    input_path: str
    output_path: str
    page_count: int

def pdf_to_word(input_path, output_path, on_progress=None, cancel=None):
    """Extract text from PDF and create a Word document using PyMuPDF."""
    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx is required: pip install python-docx") from None

    import fitz  # PyMuPDF

    pdf_doc = fitz.open(input_path)
    doc = Document()
    num_pages = len(pdf_doc)

    for i in range(num_pages):
        if cancel and cancel.is_set():
            pdf_doc.close()
            raise InterruptedError("Conversion cancelled")
        if on_progress:
            on_progress(i, num_pages)

        if i > 0:
            doc.add_page_break()

        page = pdf_doc[i]

        # Extract structured text blocks: (x0, y0, x1, y1, text, block_no, block_type)
        blocks = page.get_text("blocks")

        if not blocks:
            doc.add_paragraph("[No extractable text on this page]")
            continue

        page_has_text = False
        for block in blocks:
            # block_type 0 = text, 1 = image
            if block[6] == 0:
                text = block[4].strip()
                if text:
                    page_has_text = True
                    doc.add_paragraph(text)

        if not page_has_text:
            doc.add_paragraph("[No extractable text on this page]")

    pdf_doc.close()
    doc.save(output_path)
    log.info("PDF to Word: %s → %s (%d pages)", input_path, output_path, num_pages)
    return PdfToWordResult(input_path, output_path, num_pages)


# ═══════════════════════════════════════════════════════════════════
#  Watermark
# ═══════════════════════════════════════════════════════════════════

def _pdf_escape(text):
    """Escape special characters for PDF text strings inside parentheses."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _tiled_watermark_body(text, font_size, cos_a, sin_a, pw, ph):
    """Build the repeated Tm/Tj pairs for a tiled diagonal watermark.

    Spacing scales with the approximate rendered text width and font_size
    (same avg-char-width estimate the single-instance mode already uses)
    so large text doesn't overlap illegibly and small text isn't too
    sparse. Rows are staggered by half a step so the pattern isn't just a
    plain rectangular grid, matching the classic tiled-watermark look and
    making the text harder to crop out along a straight line.

    `pad` extends the grid well past every edge (by a full tile step) so
    a 45-degree-rotated tile still covers the corners fully -- a rotated
    tile's on-page footprint is bigger than its untransformed bounding
    box, so starting/stopping exactly at 0/pw/ph would leave the corners
    thin or bare.
    """
    safe_text = _pdf_escape(text)
    approx_text_width = len(text) * font_size * 0.5
    step_x = approx_text_width + font_size * 2.5
    step_y = font_size * 3
    pad = max(step_x, step_y)

    lines = []
    y = -pad
    row = 0
    while y < ph + pad:
        offset = (row % 2) * (step_x / 2)
        x = -pad + offset
        while x < pw + pad:
            lines.append(f"{cos_a:.4f} {sin_a:.4f} {-sin_a:.4f} {cos_a:.4f} {x:.2f} {y:.2f} Tm")
            lines.append(f"({safe_text}) Tj")
            x += step_x
        y += step_y
        row += 1
    return "\n".join(lines)


def add_watermark(input_path, output_path, text="WATERMARK", opacity=0.3,
                  rotation=45, font_size=48, color="#888888", position="center",
                  mode="single", page_range=None, cancel=None):
    """Add a text watermark to PDF pages.

    mode="single" (default): one instance of the text, centered on the
    page and rotated by `rotation` -- the original behavior, unchanged.

    mode="tiled": the text is repeated in a staggered grid across the
    entire page at the `rotation` angle, so a single crop/redaction of
    one area can't remove the watermark -- every tile carries the same
    fill_opacity/color as the single mode. `position` is ignored in
    tiled mode since there's no one placement to apply it to.

    Text supports special characters (escaped for the PDF content
    stream). Opacity applies to both fill and stroke via the page's
    /ExtGState, same as single mode.
    """
    # Validate the color before opening the PDF -- a malformed value must
    # not leak an open file handle (OPS-03).
    c = color.lstrip("#")
    r, g, b = int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255

    src = pikepdf.open(input_path)
    try:
        num_pages = len(src.pages)

        if page_range:
            ranges = _parse_ranges(page_range, num_pages)
            target_pages = set()
            for s, e in ranges:
                target_pages.update(range(s - 1, e))
        else:
            target_pages = set(range(num_pages))
    except Exception:
        src.close()
        raise

    angle_rad = math.radians(rotation)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    # Approximate text width for Helvetica (avg char width ~0.5 * font_size)
    approx_text_width = len(text) * font_size * 0.5
    safe_text = _pdf_escape(text)

    for i in range(num_pages):
        if cancel and cancel.is_set():
            src.close()
            raise InterruptedError("Watermark cancelled")

        if i not in target_pages:
            continue

        page = src.pages[i]
        mbox = page.get("/MediaBox", pikepdf.Array([0, 0, 612, 792]))
        pw = float(mbox[2]) - float(mbox[0])
        ph = float(mbox[3]) - float(mbox[1])

        if mode == "tiled":
            body = _tiled_watermark_body(text, font_size, cos_a, sin_a, pw, ph)
        else:
            # Center of page, offset by half the text width so it's visually centered
            cx = pw / 2 - (approx_text_width / 2) * cos_a
            cy = ph / 2 - (approx_text_width / 2) * sin_a
            body = (
                f"{cos_a:.4f} {sin_a:.4f} {-sin_a:.4f} {cos_a:.4f} {cx:.2f} {cy:.2f} Tm\n"
                f"({safe_text}) Tj"
            )

        # Build watermark content stream
        wm = (
            f"q\n"
            f"/GS0 gs\n"
            f"{r:.3f} {g:.3f} {b:.3f} rg\n"
            f"BT\n"
            f"/F1 {font_size} Tf\n"
            f"{body}\n"
            f"ET\n"
            f"Q\n"
        )

        wm_stream = pikepdf.Stream(src, wm.encode())

        # Ensure page has a font resource
        if "/Resources" not in page:
            page["/Resources"] = pikepdf.Dictionary()
        resources = page["/Resources"]
        if "/Font" not in resources:
            resources["/Font"] = pikepdf.Dictionary()
        if "/F1" not in resources["/Font"]:
            resources["/Font"]["/F1"] = pikepdf.Dictionary({
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            })

        # Add graphics state for opacity
        if "/ExtGState" not in resources:
            resources["/ExtGState"] = pikepdf.Dictionary()
        resources["/ExtGState"]["/GS0"] = pikepdf.Dictionary({
            "/Type": pikepdf.Name("/ExtGState"),
            "/ca": opacity,
            "/CA": opacity,
        })

        # Append watermark to page contents
        existing = page.get("/Contents")
        if existing is None:
            page["/Contents"] = wm_stream
        elif isinstance(existing, pikepdf.Array):
            existing.append(wm_stream)
        else:
            page["/Contents"] = pikepdf.Array([existing, wm_stream])

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        src.save(tmp, compress_streams=True)
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("Watermarked %s → %s", input_path, output_path)


# ═══════════════════════════════════════════════════════════════════
#  Page Numbers
# ═══════════════════════════════════════════════════════════════════

def add_page_numbers(input_path, output_path, position="bottom-center",
                     font_size=10, fmt="{page}", start_number=1,
                     margin_pt=36, cancel=None):
    """Insert page numbering on all pages.

    Properly centers text for center-aligned positions and escapes
    special PDF characters in the label.
    """
    src = pikepdf.open(input_path)
    num_pages = len(src.pages)

    for i in range(num_pages):
        if cancel and cancel.is_set():
            src.close()
            raise InterruptedError("Page numbering cancelled")

        page = src.pages[i]
        mbox = page.get("/MediaBox", pikepdf.Array([0, 0, 612, 792]))
        pw = float(mbox[2]) - float(mbox[0])
        ph = float(mbox[3]) - float(mbox[1])

        page_num = start_number + i
        label = fmt.replace("{page}", str(page_num)).replace("{total}", str(num_pages))
        safe_label = _pdf_escape(label)

        # Approximate label width for centering (Helvetica avg ~0.5 * font_size per char)
        label_width = len(label) * font_size * 0.5

        # Calculate position — offset centered positions by half the text width
        is_center = "center" in position
        is_right = "right" in position

        if is_center:
            x_base = pw / 2 - label_width / 2
        elif is_right:
            x_base = pw - margin_pt - label_width
        else:
            x_base = margin_pt

        if "top" in position:
            y_base = ph - margin_pt
        else:
            y_base = margin_pt

        content = (
            f"q\n"
            f"0 0 0 rg\n"
            f"BT\n"
            f"/F1 {font_size} Tf\n"
            f"{x_base:.2f} {y_base:.2f} Td\n"
            f"({safe_label}) Tj\n"
            f"ET\n"
            f"Q\n"
        )

        num_stream = pikepdf.Stream(src, content.encode())

        if "/Resources" not in page:
            page["/Resources"] = pikepdf.Dictionary()
        resources = page["/Resources"]
        if "/Font" not in resources:
            resources["/Font"] = pikepdf.Dictionary()
        if "/F1" not in resources["/Font"]:
            resources["/Font"]["/F1"] = pikepdf.Dictionary({
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            })

        existing = page.get("/Contents")
        if existing is None:
            page["/Contents"] = num_stream
        elif isinstance(existing, pikepdf.Array):
            existing.append(num_stream)
        else:
            page["/Contents"] = pikepdf.Array([existing, num_stream])

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        src.save(tmp, compress_streams=True)
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("Added page numbers to %s → %s", input_path, output_path)


# ═══════════════════════════════════════════════════════════════════
#  Metadata
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MetadataFields:
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""

def read_metadata(path):
    """Read PDF metadata fields. Returns a dict."""
    src = pikepdf.open(path)
    info = src.docinfo
    result = {
        "title": str(info.get("/Title", "")),
        "author": str(info.get("/Author", "")),
        "subject": str(info.get("/Subject", "")),
        "keywords": str(info.get("/Keywords", "")),
        "creator": str(info.get("/Creator", "")),
        "producer": str(info.get("/Producer", "")),
    }
    src.close()
    return result


def write_metadata(input_path, output_path, fields):
    """Write PDF metadata fields. fields is a dict with keys: title, author, etc."""
    if isinstance(fields, dict):
        f = fields
    else:
        f = {"title": fields.title, "author": fields.author, "subject": fields.subject,
             "keywords": fields.keywords, "creator": fields.creator, "producer": fields.producer}

    src = pikepdf.open(input_path)
    with src.open_metadata() as meta:
        if f.get("title"):
            meta["dc:title"] = f["title"]
        if f.get("author"):
            meta["dc:creator"] = [f["author"]]
        if f.get("subject"):
            meta["dc:description"] = f["subject"]
        if f.get("keywords"):
            meta["pdf:Keywords"] = f["keywords"]
        if f.get("creator"):
            meta["xmp:CreatorTool"] = f["creator"]
        if f.get("producer"):
            meta["pdf:Producer"] = f["producer"]

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        src.save(tmp)
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("Updated metadata %s → %s", input_path, output_path)


# ═══════════════════════════════════════════════════════════════════
#  Extract Images
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ExtractImagesResult:
    input_path: str
    output_dir: str
    image_paths: list[str]
    image_count: int

def extract_images(input_path, output_dir, fmt="png", min_size=0,
                   on_progress=None, cancel=None):
    """Extract all images from a PDF."""

    from PIL import Image

    os.makedirs(output_dir, exist_ok=True)
    src = pikepdf.open(input_path)
    base = os.path.splitext(os.path.basename(input_path))[0]
    image_paths = []
    img_idx = 0

    for page_num, page in enumerate(src.pages):
        if cancel and cancel.is_set():
            src.close()
            raise InterruptedError("Extraction cancelled")
        if on_progress:
            on_progress(page_num, len(src.pages))

        if "/Resources" not in page:
            continue
        resources = page["/Resources"]
        if "/XObject" not in resources:
            continue

        for _key, xobj_ref in resources["/XObject"].items():
            try:
                xobj = xobj_ref
                if hasattr(xobj, 'resolve'):
                    xobj = xobj.resolve()
                if xobj.get("/Subtype") != "/Image":
                    continue

                w = int(xobj.get("/Width", 0))
                h = int(xobj.get("/Height", 0))
                if w * h < min_size:
                    continue

                # Try to extract image data
                raw = xobj.read_raw_bytes()
                filter_name = str(xobj.get("/Filter", ""))

                if "/DCTDecode" in filter_name:
                    ext = "jpg"
                    out_path = os.path.join(output_dir, f"{base}_img_{img_idx:03d}.{ext}")
                    with open(out_path, "wb") as f:
                        f.write(raw)
                elif "/JPXDecode" in filter_name:
                    ext = "jp2"
                    out_path = os.path.join(output_dir, f"{base}_img_{img_idx:03d}.{ext}")
                    with open(out_path, "wb") as f:
                        f.write(raw)
                else:
                    # Try to decode with Pillow
                    try:
                        data = xobj.read_bytes()
                        cs = str(xobj.get("/ColorSpace", "/DeviceRGB"))

                        if "/DeviceRGB" in cs:
                            mode = "RGB"
                        elif "/DeviceCMYK" in cs:
                            mode = "CMYK"
                        elif "/DeviceGray" in cs:
                            mode = "L"
                        else:
                            mode = "RGB"

                        img = Image.frombytes(mode, (w, h), data)
                        if mode == "CMYK":
                            img = img.convert("RGB")

                        ext = fmt.lower()
                        if ext == "jpeg":
                            ext = "jpg"
                        out_path = os.path.join(output_dir, f"{base}_img_{img_idx:03d}.{ext}")
                        img.save(out_path)
                    except Exception:
                        continue

                image_paths.append(out_path)
                img_idx += 1
            except Exception:
                continue

    src.close()
    log.info("Extracted %d images from %s", len(image_paths), input_path)
    return ExtractImagesResult(input_path, output_dir, image_paths, len(image_paths))


# ═══════════════════════════════════════════════════════════════════
#  Extract Text
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ExtractTextResult:
    input_path: str
    output_path: str
    page_count: int
    char_count: int

def extract_text(input_path, output_path, page_range=None, cancel=None):
    """Extract text content from a PDF to a text file using PyMuPDF."""
    import fitz  # PyMuPDF

    doc = fitz.open(input_path)
    num_pages = len(doc)

    if page_range:
        ranges = _parse_ranges(page_range, num_pages)
        target_pages = []
        for s, e in ranges:
            target_pages.extend(range(s - 1, e))
    else:
        target_pages = list(range(num_pages))

    all_text = []
    for page_idx in target_pages:
        if cancel and cancel.is_set():
            doc.close()
            raise InterruptedError("Extraction cancelled")

        page = doc[page_idx]
        text = page.get_text("text")
        all_text.append(f"--- Page {page_idx + 1} ---\n{text}\n")

    doc.close()

    full_text = "\n".join(all_text)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    log.info("Extracted text from %s → %s", input_path, output_path)
    return ExtractTextResult(input_path, output_path, len(target_pages), len(full_text))


# ═══════════════════════════════════════════════════════════════════
#  Crop Pages
# ═══════════════════════════════════════════════════════════════════

def crop_pages(input_path, output_path, margins, unit="mm"):
    """Crop pages by trimming margins. margins = dict with left/right/top/bottom."""
    # Convert to points
    scale = {"mm": 72 / 25.4, "inches": 72, "points": 1, "pt": 1}.get(unit, 72 / 25.4)

    left = margins.get("left", 0) * scale
    right = margins.get("right", 0) * scale
    top = margins.get("top", 0) * scale
    bottom = margins.get("bottom", 0) * scale

    src = pikepdf.open(input_path)
    for page in src.pages:
        mbox = page.get("/MediaBox", pikepdf.Array([0, 0, 612, 792]))
        x0 = float(mbox[0]) + left
        y0 = float(mbox[1]) + bottom
        x1 = float(mbox[2]) - right
        y1 = float(mbox[3]) - top

        if x1 <= x0 or y1 <= y0:
            src.close()
            raise ValueError("Crop margins are too large — no page area remaining")

        page["/CropBox"] = pikepdf.Array([x0, y0, x1, y1])

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        src.save(tmp, compress_streams=True)
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("Cropped %s → %s", input_path, output_path)


# ═══════════════════════════════════════════════════════════════════
#  Flatten PDF
# ═══════════════════════════════════════════════════════════════════

def flatten_pdf(input_path, output_path, annotations=True, forms=True):
    """Remove annotations and/or form fields from a PDF."""
    src = pikepdf.open(input_path)

    for page in src.pages:
        if (annotations or forms) and "/Annots" in page:
            # Widget annotations are form fields; every other subtype is a
            # regular annotation. Decide each one independently so
            # forms=True always strips widgets and annotations=True always
            # strips the rest, regardless of the other flag (OPS-02) --
            # e.g. forms=True/annotations=False must still remove form
            # fields even though non-widget annotations are kept.
            annots = page["/Annots"]
            kept = []
            for annot in annots:
                a = annot.resolve() if hasattr(annot, 'resolve') else annot
                is_widget = str(a.get("/Subtype", "")) == "/Widget"
                if is_widget and forms:
                    continue
                if not is_widget and annotations:
                    continue
                kept.append(annot)
            if kept:
                page["/Annots"] = pikepdf.Array(kept)
            else:
                del page["/Annots"]

    if forms and "/AcroForm" in src.Root:
        del src.Root["/AcroForm"]

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        src.save(tmp, compress_streams=True)
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("Flattened %s → %s", input_path, output_path)


# ═══════════════════════════════════════════════════════════════════
#  N-up Layout
# ═══════════════════════════════════════════════════════════════════

def nup_layout(input_path, output_path, pages_per_sheet=4,
               page_size="A4", orientation="landscape"):
    """Arrange multiple pages per sheet."""
    PAGE_SIZES = {
        "a4": (595.28, 841.89),
        "letter": (612, 792),
        "a3": (841.89, 1190.55),
    }

    base_w, base_h = PAGE_SIZES.get(page_size.lower(), (595.28, 841.89))
    if orientation == "landscape":
        sheet_w, sheet_h = max(base_w, base_h), min(base_w, base_h)
    else:
        sheet_w, sheet_h = min(base_w, base_h), max(base_w, base_h)

    # Calculate grid
    cols = int(math.ceil(math.sqrt(pages_per_sheet)))
    rows = int(math.ceil(pages_per_sheet / cols))

    cell_w = sheet_w / cols
    cell_h = sheet_h / rows

    src = pikepdf.open(input_path)
    dest = pikepdf.Pdf.new()
    num_pages = len(src.pages)

    for sheet_start in range(0, num_pages, pages_per_sheet):
        dest.add_blank_page(page_size=(sheet_w, sheet_h))
        out_page = dest.pages[-1]

        content_parts = []
        xobjects = {}

        for slot in range(pages_per_sheet):
            page_idx = sheet_start + slot
            if page_idx >= num_pages:
                break

            src_page = src.pages[page_idx]
            # Create form XObject from source page
            xobj_name = f"/P{slot}"

            # Get source page dimensions
            mbox = src_page.get("/MediaBox", pikepdf.Array([0, 0, 612, 792]))
            src_w = float(mbox[2]) - float(mbox[0])
            src_h = float(mbox[3]) - float(mbox[1])

            # Calculate position and scale
            col = slot % cols
            row_idx = slot // cols
            scale = min(cell_w / src_w, cell_h / src_h) * 0.95  # 5% margin

            scaled_w = src_w * scale
            scaled_h = src_h * scale

            x = col * cell_w + (cell_w - scaled_w) / 2
            y = sheet_h - (row_idx + 1) * cell_h + (cell_h - scaled_h) / 2

            # Create form xobject
            form_xobj = dest.copy_foreign(src_page.as_form_xobject())
            xobjects[xobj_name] = form_xobj

            content_parts.append(
                f"q {scale:.6f} 0 0 {scale:.6f} {x:.2f} {y:.2f} cm {xobj_name} Do Q"
            )

        if "/Resources" not in out_page:
            out_page["/Resources"] = pikepdf.Dictionary()
        out_page["/Resources"]["/XObject"] = pikepdf.Dictionary(xobjects)
        out_page["/Contents"] = pikepdf.Stream(dest, "\n".join(content_parts).encode())

    src.close()

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        dest.save(tmp, compress_streams=True)
        dest.close()
        os.replace(tmp, output_path)
    except Exception:
        dest.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("N-up %d pages/sheet %s → %s", pages_per_sheet, input_path, output_path)


# ═══════════════════════════════════════════════════════════════════
#  Repair PDF
# ═══════════════════════════════════════════════════════════════════

def repair_pdf(input_path, output_path):
    """Attempt to repair a corrupted PDF."""
    # pikepdf/QPDF has built-in repair capabilities
    src = pikepdf.open(input_path, attempt_recovery=True)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
    os.close(fd)
    try:
        src.save(tmp, compress_streams=True, linearize=True)
        src.close()
        os.replace(tmp, output_path)
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    log.info("Repaired %s → %s", input_path, output_path)


# ═══════════════════════════════════════════════════════════════════
#  Compare PDFs
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CompareResult:
    path_a: str
    path_b: str
    page_diffs: list[dict]
    metadata_diffs: list[dict]

def compare_pdfs(path_a, path_b):
    """Compare two PDFs page-by-page (text content + metadata) using PyMuPDF."""
    import fitz  # PyMuPDF

    doc_a = fitz.open(path_a)
    doc_b = fitz.open(path_b)

    max_pages = max(len(doc_a), len(doc_b))
    page_diffs = []

    for i in range(max_pages):
        if i >= len(doc_a):
            page_diffs.append({"page": i + 1, "status": "added_in_b", "details": "Page only in document B"})
            continue
        if i >= len(doc_b):
            page_diffs.append({"page": i + 1, "status": "removed_in_b", "details": "Page only in document A"})
            continue

        text_a = doc_a[i].get_text("text")
        text_b = doc_b[i].get_text("text")

        if text_a == text_b:
            page_diffs.append({"page": i + 1, "status": "identical", "details": ""})
        else:
            words_a = set(text_a.split())
            words_b = set(text_b.split())
            added = len(words_b - words_a)
            removed = len(words_a - words_b)
            page_diffs.append({
                "page": i + 1,
                "status": "different",
                "details": f"+{added} words, -{removed} words"
            })

    doc_a.close()
    doc_b.close()

    # Metadata comparison via pikepdf (richer metadata access)
    meta_diffs = []
    src_a = pikepdf.open(path_a)
    src_b = pikepdf.open(path_b)
    for key in ("/Title", "/Author", "/Subject", "/Creator"):
        val_a = str(src_a.docinfo.get(key, ""))
        val_b = str(src_b.docinfo.get(key, ""))
        if val_a != val_b:
            meta_diffs.append({"field": key, "a": val_a, "b": val_b})
    src_a.close()
    src_b.close()

    return CompareResult(path_a, path_b, page_diffs, meta_diffs)


# ═══════════════════════════════════════════════════════════════════
#  Redact
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RedactResult:
    input_path: str
    output_path: str
    redaction_count: int          # page-content redactions (unchanged meaning)
    pages_affected: int
    #: per-surface removal breakdown, e.g. {"page_content": 12, "docinfo": 1,
    #: "bookmarks": 2}. page_content mirrors redaction_count; the rest are the
    #: document-level scrubs apply_redactions never touches (#99, #119), and
    #: structure_refs, the structure tree's references to an unredacted
    #: original, which a run by box alone can have too.
    surface_counts: dict = field(default_factory=dict)
    #: verify_redaction(output).to_dict() — the proof-of-removal artifact.
    verification: dict | None = None
    #: pages rasterized by the flatten fallback on this run, if any (D1).
    flattened_pages: list = field(default_factory=list)


class RedactionVerificationError(Exception):
    """Redaction produced a candidate that verify_redaction could not prove clean.

    Raised instead of returning success (fail-closed, #99). Carries the
    structured detail the UI needs to apply D1: the verification report, the
    pages a flatten-to-image retry would target (``flatten_pages`` — None
    when flatten is not offerable), and whether any residual is
    document-level (``document_level`` — flatten physically cannot fix
    those, so no offer). Nothing was written to the destination: the
    candidate lived in a temp file, now deleted, and whatever was at
    output_path before — the input itself, when redacting in place — is
    untouched.
    """

    def __init__(self, message: str, *, report: dict | None = None,
                 flatten_pages: list | None = None,
                 document_level: bool = False):
        super().__init__(message)
        self.report = report
        self.flatten_pages = flatten_pages
        self.document_level = document_level


# Whole-value redaction marker (D2): a matched title/annotation value is
# replaced wholesale, never spliced — a fragment like "'s Q3 Report" both
# looks broken and leaks context.
_REDACTED_MARKER = "[redacted]"

#: What the marker falls back to when it would itself hold a term: "Ted",
#: "Red" and "act" are all inside "[redacted]", and a scrubbed title that
#: still holds the term is a refused redaction. The last one holds nothing.
_MARKERS = (_REDACTED_MARKER, "[removed]", "[hidden]", "[...]", "")


def _marker_for(finder) -> str:
    """The first marker text that holds none of the terms."""
    for text in _MARKERS:
        if not finder(text.encode()):
            return text
    return ""


def _neutral_name(stems: tuple, taken: set, finder) -> bytes:
    """A name for something that has to keep one — a form field, a named
    destination — that is not in *taken* and holds none of the terms.

    The names count up within one stem: ``field-1``, ``field-2``,
    ``field-3``. A stem is left for the next one only when it holds a term
    itself (the term is "field": ``item-1``, ``item-2``), and the hyphen
    only when it does (``field1``, ``field2``); a number that would make
    the name hold a term is skipped. Adds the name to *taken*."""
    for stem in stems:
        for lead in (f"{stem}-", stem):
            if finder(lead.encode()):
                continue
            for serial in range(1, 10000):
                raw = f"{lead}{serial}".encode()
                if raw not in taken and not finder(raw):
                    taken.add(raw)
                    return raw
    raise ValueError("no neutral name is free of the search terms")


def _make_term_matcher(terms: list[str], case_sensitive: bool):
    """Return match(value) -> bool for whole-value (D2) surface scrubbing.

    *value* is text, or a pikepdf string. The test is the verifier's own
    (pdf_verify.TermFinder), so the scrub removes what the whole-file sweep
    would otherwise find: a string object is read as its raw bytes in every
    encoding as well as in its decoded form, and whitespace in a term
    matches any run of whitespace — a term of two words is found in a note
    that breaks the line between them.
    """
    finder = TermFinder(terms, case_sensitive)

    def match(value) -> bool:
        try:
            if isinstance(value, pikepdf.String) and finder(bytes(value)):
                return True
            return finder(str(value).encode("utf-8", "replace"))
        except Exception:
            return False
    return match


#: Keys that lead out of the structure tree: to a page, to an annotation or
#: XObject, to a content stream. Structure text is scrubbed; those are not
#: the structure tree's to touch.
_STRUCTURE_WAYS_OUT = ("/Pg", "/Obj", "/Stm", "/StmOwn")

_CONTAINERS = (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)


def _redact_strings(root, match, skip: tuple = (), marker: str = _REDACTED_MARKER) -> int:
    """Replace every string under *root* that holds a term with the whole-
    value *marker* (D2), as bookmark titles are; returns how many.

    Walks dictionaries and arrays, direct or indirect, each object once,
    without following the keys in *skip*. Iterative: a structure tree is as
    deep as the document's nesting and as long as its content.
    """
    n, seen, stack = 0, set(), [root]
    while stack:
        cur = stack.pop()
        try:
            if cur.is_indirect:
                ident = tuple(cur.objgen)
                if ident in seen:
                    continue
                seen.add(ident)
            slots = (range(len(cur)) if isinstance(cur, pikepdf.Array)
                     else [k for k in cur.keys() if k not in skip])
            for slot in slots:
                value = cur[slot]
                if isinstance(value, pikepdf.String):
                    if match(value):
                        cur[slot] = pikepdf.String(marker)
                        n += 1
                elif isinstance(value, _CONTAINERS):
                    stack.append(value)
        except Exception:
            continue
    return n


def _holds_term(obj, finder, *, skip: tuple = (), unreadable: bool = True) -> bool:
    """True if a term is anywhere under *obj*: in a string, or in the data
    of a stream. Does not follow the keys in *skip*, or a reference back
    into the document (a page, the page tree, the catalog).

    A stream that cannot be read counts as *unreadable* says. True where
    the thing is expendable and cannot be shown free of the term (private
    data, a form's XFA packets); False where it is the user's own content
    and is removed only for what was found in it (an attachment) — the
    verifier then refuses the run for what it could not read.
    """
    seen, stack = set(), [obj]
    while stack:
        cur = stack.pop()
        try:
            if isinstance(cur, pikepdf.String):
                if finder(bytes(cur)):
                    return True
                continue
            if not isinstance(cur, _CONTAINERS):
                continue
            if cur.is_indirect:
                ident = tuple(cur.objgen)
                if ident in seen:
                    continue
                seen.add(ident)
            if isinstance(cur, pikepdf.Array):
                stack.extend(cur)
                continue
            if str(cur.get("/Type")) in ("/Page", "/Pages", "/Catalog"):
                continue
            if isinstance(cur, pikepdf.Stream):
                try:
                    data = cur.read_bytes()
                except Exception:
                    if unreadable:
                        return True
                    data = b""
                if finder(data):
                    return True
            stack.extend(cur[k] for k in cur.keys() if k not in skip)
        except Exception:
            continue
    return False


def _scrub_piece_info(objects, finder) -> int:
    """Delete every application's entry in a /PieceInfo dictionary — on the
    catalog, a page, a form XObject — that holds a term anywhere under it.
    It is private data of the program that wrote it; nothing can be redacted
    inside it, so it goes whole. Returns the number of entries deleted."""
    holders = []
    for obj in objects:
        try:
            if "/PieceInfo" in obj:
                holders.append(obj)
        except Exception:
            continue
    n = 0
    for holder in holders:
        try:
            info = holder["/PieceInfo"]
            if isinstance(info, pikepdf.Dictionary):
                for app in list(info.keys()):
                    if _holds_term(info[app], finder):
                        del info[app]
                        n += 1
                if not len(info.keys()):
                    del holder["/PieceInfo"]
            elif _holds_term(info, finder):
                del holder["/PieceInfo"]
                n += 1
        except Exception:
            continue
    return n


def _rename_destinations(pdf, objects, finder) -> int:
    """Rename every named destination whose name holds a term, and every
    use of it, so the links that point there still work. Returns how many.

    The names are the keys of the catalog's /Names /Dests tree. A name tree
    is sorted, so a tree with a renamed key is written back as one sorted
    leaf rather than patched in place (pdf_analyze.write_name_tree). The uses
    are the /Dest and /D strings of links, actions and bookmarks, among
    *objects* — every dictionary and stream in the file.
    """
    try:
        names = pdf.Root.get("/Names")
        tree = names.get("/Dests") if isinstance(names, pikepdf.Dictionary) else None
        if not isinstance(tree, pikepdf.Dictionary):
            return 0
        items = list(name_tree_items(tree))
    except Exception:
        return 0
    taken = {bytes(k) for k, _v in items if isinstance(k, pikepdf.String)}
    renames: dict[bytes, bytes] = {}
    for key, _value in items:
        if not isinstance(key, pikepdf.String):
            continue
        old = bytes(key)
        if old in renames or not finder(old):
            continue
        renames[old] = _neutral_name(("redacted", "dest", "d"), taken, finder)
    if not renames:
        return 0

    write_name_tree(tree, [
        (renames.get(bytes(k), bytes(k)) if isinstance(k, pikepdf.String) else k, v)
        for k, v in items])

    for obj in objects:
        for key in ("/Dest", "/D"):
            try:
                value = obj.get(key)
                if isinstance(value, pikepdf.String) and bytes(value) in renames:
                    obj[key] = pikepdf.String(renames[bytes(value)])
            except Exception:
                continue
    return len(renames)


# ── The places real files keep a term (#119, R1b) ────────────────────
#
#  Found by redacting one word out of each of a thousand real PDFs and
#  looking at what the whole-file sweep still found. Each rule below is one
#  of those places. They work on every dictionary in the file, not only on
#  what a page lists: redaction takes an annotation under a box off its
#  page, but a tagged file's structure tree goes on pointing at it, and at
#  everything it says.

_XFA_ONLY = ("This form exists only as XFA data, and that data holds a search "
             "term. An XFA-only form cannot be term-redacted. Nothing was saved.")


class RedactionNotPossibleError(ValueError):
    """The document cannot be term-redacted at all, for a reason the message
    gives in plain words — not a candidate that failed verification, so
    there is no report. A ValueError, like the other reasons a redaction
    does not start ("No matching content found"): callers that show those
    show this. Nothing was written."""


def _all_objects(pdf) -> list:
    """Every dictionary and stream reachable from the trailer, inline ones
    included, each once: what the output will be written from. Collected up
    front, because the rules that use it delete and rewrite as they go."""
    return [obj for _path, obj in walk_objects(pdf, WalkStatus())]


def _name_of(obj, key: str) -> str:
    try:
        value = obj.get(key)
    except Exception:
        return ""
    return str(value) if isinstance(value, pikepdf.Name) else ""


def _is_annotation(obj) -> bool:
    """An annotation, by what it is: it says so, or it has a /Subtype and a
    /Rect. Not by where it is listed — one that is off its page is still
    one."""
    return (isinstance(obj, pikepdf.Dictionary)
            and (_name_of(obj, "/Type") == "/Annot"
                 or (bool(_name_of(obj, "/Subtype")) and "/Rect" in obj)))


def _listed_annotations(pdf) -> set:
    """(num, gen) of every annotation some page lists in its /Annots: the
    ones that are on a page."""
    listed: set = set()
    for page in pdf.pages:
        try:
            annots = page.obj.get("/Annots")
            entries = list(annots) if isinstance(annots, pikepdf.Array) else []
        except Exception:
            continue
        for annot in entries:
            try:
                if annot.is_indirect:
                    listed.add(tuple(annot.objgen))
            except Exception:
                continue
    return listed


def _field_tree(pdf) -> list:
    """Every field dictionary in the AcroForm tree, through /Kids, once."""
    try:
        acro = pdf.Root.get("/AcroForm")
        fields = acro.get("/Fields") if isinstance(acro, pikepdf.Dictionary) else None
        return _with_descendants(list(fields) if isinstance(fields, pikepdf.Array) else [])
    except Exception:
        return []


def _with_descendants(fields: list) -> list:
    """*fields* and every field below them through /Kids, each once."""
    found, seen, stack = [], set(), list(fields)[::-1]
    while stack:
        fld = stack.pop()
        try:
            if not isinstance(fld, pikepdf.Dictionary):
                continue
            if fld.is_indirect:
                ident = tuple(fld.objgen)
                if ident in seen:
                    continue
                seen.add(ident)
            found.append(fld)
            kids = fld.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                stack.extend(list(kids)[::-1])
        except Exception:
            continue
    return found


def _is_field(obj, in_tree: set) -> bool:
    """A form field or a widget: it has a field type, it is a widget, it is
    in the form's field tree, or it is a parent field (a name and kids)."""
    if not isinstance(obj, pikepdf.Dictionary):
        return False
    try:
        if "/FT" in obj or _name_of(obj, "/Subtype") == "/Widget":
            return True
        if obj.is_indirect and tuple(obj.objgen) in in_tree:
            return True
        return "/T" in obj and "/Kids" in obj and _name_of(obj, "/Type") != "/Pages"
    except Exception:
        return False


def _drop_appearances(fld) -> None:
    """Delete the appearance streams of field *fld* and of every widget
    below it. They draw what the field said before it was redacted; a
    viewer makes new ones from what it says now."""
    for f in _with_descendants([fld]):
        try:
            if "/AP" in f:
                del f["/AP"]
        except Exception:
            continue


def _scrub_fields(fields: list, match, finder, marker: str, bump) -> None:
    """What a form field says about itself, apart from its value: tooltip,
    mapping name, default value, option list and captions holding a term
    become the marker (``field_text``); a field whose own name holds one is
    renamed to a neutral name no other field has (``field_names``). The
    name is not deleted: a field without one is not a valid field."""
    taken = set()
    for fld in fields:
        try:
            name = fld.get("/T")
            if isinstance(name, pikepdf.String):
                taken.add(bytes(name))
        except Exception:
            continue
    for fld in fields:
        try:
            redrawn = False
            for key in ("/TU", "/TM", "/DV"):
                value = fld.get(key)
                if isinstance(value, pikepdf.String):
                    if match(value):
                        fld[key] = pikepdf.String(marker)
                        bump("field_text")
                        redrawn = redrawn or key == "/DV"
                elif isinstance(value, pikepdf.Array):       # a list's default selection
                    n = _redact_strings(value, match, marker=marker)
                    bump("field_text", n)
                    redrawn = redrawn or bool(n)
            options = fld.get("/Opt")
            if isinstance(options, pikepdf.Array):
                n = _redact_strings(options, match, marker=marker)
                bump("field_text", n)
                redrawn = redrawn or bool(n)
            captions = fld.get("/MK")
            if isinstance(captions, pikepdf.Dictionary):
                for key in ("/CA", "/RC", "/AC"):            # normal, rollover, down
                    value = captions.get(key)
                    if isinstance(value, pikepdf.String) and match(value):
                        captions[key] = pikepdf.String(marker)
                        bump("field_text")
                        redrawn = True
            name = fld.get("/T")
            if isinstance(name, pikepdf.String) and match(name):
                fld["/T"] = pikepdf.String(
                    _neutral_name(("field", "item", "entry", "x"), taken, finder))
                bump("field_names")
            if redrawn:
                _drop_appearances(fld)
        except Exception:
            continue


def _scrub_pdftex(objects: list, finder) -> int:
    """Delete every /PTEX.* entry that holds a term. pdfTeX records, on each
    figure it includes, the name of the file it came from (/PTEX.FileName)
    and that file's own document information (/PTEX.InfoDict); a term in
    either is a term in the output. Returns the number of entries deleted."""
    n = 0
    for obj in objects:
        try:
            for key in [k for k in obj.keys() if k.startswith("/PTEX.")]:
                if _holds_term(obj[key], finder):
                    del obj[key]
                    n += 1
        except Exception:
            continue
    return n


def _scrub_xfa(pdf, finder) -> int:
    """Drop the form's /XFA entry when a packet holds a term, so the form
    falls back to its AcroForm fields. XFA is XML the form is rebuilt from;
    a value cannot be redacted inside it and leave a form that still works.

    Raises RedactionNotPossibleError when there are no AcroForm fields to
    fall back on: the form exists only as XFA, and removing that would
    remove the form.
    """
    acro = pdf.Root.get("/AcroForm")
    if not isinstance(acro, pikepdf.Dictionary) or "/XFA" not in acro:
        return 0
    if not _holds_term(acro["/XFA"], finder):
        return 0
    fields = acro.get("/Fields")
    if not (isinstance(fields, pikepdf.Array)
            and any(isinstance(f, pikepdf.Dictionary) for f in fields)):
        raise RedactionNotPossibleError(_XFA_ONLY)
    del acro["/XFA"]
    return 1


def _is_signature(obj) -> bool:
    return (isinstance(obj, pikepdf.Dictionary)
            and (_name_of(obj, "/Type") in ("/Sig", "/DocTimeStamp")
                 or ("/ByteRange" in obj and "/Contents" in obj)))


def _scrub_signatures(pdf, objects: list, finder) -> int:
    """Remove the value of every signature that holds a term anywhere in it
    — the signer's name, the reason, the place, the certificate bytes — so
    the field is left unsigned. Redaction changes the signed bytes, so the
    signature was void in any case; what it must not do is go on naming
    someone. Covers signature fields and the catalog's /Perms (usage
    rights, certification). Returns the number of signatures removed."""
    removed: set = set()
    n = 0

    def holds(sig) -> bool:
        # /Reference leads to the objects the signature covers, not to
        # anything the signature itself says.
        return _holds_term(sig, finder, skip=("/Reference",))

    def count(sig) -> None:
        nonlocal n
        ident = tuple(sig.objgen) if sig.is_indirect else None
        if ident is None or ident not in removed:
            n += 1
        if ident is not None:
            removed.add(ident)

    for obj in objects:
        try:
            sig = obj.get("/V")
            if _is_signature(sig) and holds(sig):
                del obj["/V"]
                _drop_appearances(obj)       # "Digitally signed by ..."
                count(sig)
        except Exception:
            continue
    try:
        perms = pdf.Root.get("/Perms")
        if isinstance(perms, pikepdf.Dictionary):
            for key in list(perms.keys()):
                sig = perms[key]
                if _is_signature(sig) and holds(sig):
                    del perms[key]
                    count(sig)
            if not len(perms.keys()):
                del pdf.Root["/Perms"]
    except Exception:
        pass
    return n


#: /Type of the dictionaries that make up the structure tree.
_STRUCTURE_TYPES = ("/StructTreeRoot", "/StructElem", "/MCR", "/OBJR")


def _structure_stream(kid):
    """The stream a kid of a structure element names — the content stream
    of a marked-content reference (/Stm), the XObject of an object
    reference (/Obj) — or None."""
    if isinstance(kid, pikepdf.Dictionary):
        for key in ("/Stm", "/Obj"):
            target = kid.get(key)
            if isinstance(target, pikepdf.Stream):
                return target
    return None


def _drop_stale_structure_refs(pdf) -> int:
    """Remove every reference the structure tree makes to a stream that
    nothing else in the file leads to; returns how many went.

    Redaction does not edit a form XObject or an image in place. The page
    is given a redacted copy, and the original is dropped when the file is
    written — unless something still refers to it. In a tagged file the
    structure tree does: a marked-content reference names the stream its
    content is in (/Stm), an object reference names the XObject (/Obj).
    Either one kept the original in the output, unredacted, where no page
    paints it and no check of the pages sees it.

    A kid of a structure element that names such a stream is taken out of
    the element's /K. "Nothing else" is tested by walking the file from the
    trailer without entering the structure tree: a stream that walk reaches
    is still somebody's content (a page that was not redacted paints it,
    say) and its reference is still true. This runs on every redaction, by
    term or by box, as the last thing before the output is written; an
    object reference to an annotation is not a stream and is not touched
    (the scrub deals with those).
    """
    try:
        root = pdf.Root.get("/StructTreeRoot")
    except Exception:
        return 0
    if not isinstance(root, pikepdf.Dictionary):
        return 0

    def kids_of(elem) -> list:
        kids = elem.get("/K")
        return list(kids) if isinstance(kids, pikepdf.Array) else [kids]

    # Every dictionary of the tree that has kids naming a stream.
    holders, named = [], set()
    seen, stack = set(), [root]
    while stack:
        cur = stack.pop()
        try:
            if not isinstance(cur, (pikepdf.Dictionary, pikepdf.Array)):
                continue
            if cur.is_indirect:
                ident = tuple(cur.objgen)
                if ident in seen:
                    continue
                seen.add(ident)
            if isinstance(cur, pikepdf.Array):
                stack.extend(cur)
                continue
            if _name_of(cur, "/Type") in ("/Page", "/Pages", "/Catalog"):
                continue
            if "/K" in cur:
                streams = [s for s in map(_structure_stream, kids_of(cur)) if s is not None]
                if streams:
                    holders.append(cur)
                    named.update(tuple(s.objgen) for s in streams)
            stack.extend(cur[k] for k in cur.keys() if k not in _STRUCTURE_WAYS_OUT)
        except Exception:
            continue
    if not named:
        return 0

    # Which of them the rest of the file still leads to.
    alive: set = set()
    seen, stack = set(), [pdf.trailer]
    while stack and len(alive) < len(named):
        cur = stack.pop()
        try:
            if not isinstance(cur, _CONTAINERS):
                continue
            if cur.is_indirect:
                ident = tuple(cur.objgen)
                if ident in seen:
                    continue
                seen.add(ident)
                if ident in named:
                    alive.add(ident)
            if isinstance(cur, pikepdf.Array):
                stack.extend(cur)
                continue
            if _name_of(cur, "/Type") in _STRUCTURE_TYPES:
                continue
            # /SE is a bookmark's way into the tree.
            stack.extend(cur[k] for k in cur.keys() if k not in ("/StructTreeRoot", "/SE"))
        except Exception:
            continue

    removed = 0
    for holder in holders:
        try:
            kids = kids_of(holder)
            keep = []
            for kid in kids:
                stream = _structure_stream(kid)
                if stream is not None and tuple(stream.objgen) not in alive:
                    removed += 1
                else:
                    keep.append(kid)
            if len(keep) == len(kids):
                continue
            if keep and isinstance(holder.get("/K"), pikepdf.Array):
                holder["/K"] = pikepdf.Array(keep)
            else:
                del holder["/K"]
        except Exception:
            continue
    return removed


def _scrub_document_surfaces(path: str, terms: list[str], case_sensitive: bool,
                             password: str | None = None, *,
                             gone_attachments: tuple | list = ()) -> dict[str, int]:
    """Whole-value removal of *terms* from every non-page-content surface, in
    place on *path* via pikepdf — the surfaces verify_redaction checks but
    apply_redactions never touches (#99). Returns a surface->count dict.

    D2: matched values are removed wholesale, never spliced. D3: docinfo and
    XMP are stripped wholesale (not surgically RDF-edited) the moment any
    term appears in them — losing metadata during redaction is expected.
    Metadata that carries no term is left exactly as found. Mirrors
    pdf_verify._pikepdf_scan's surface set so the scrub cleans exactly where
    verification looks.

    Beyond those, the rules that let an ordinary file pass the verifier's
    whole-file term sweep (#119), each counted under its own key:

      * ``structure`` — structure-tree text (/Alt, /ActualText, a title)
        holding a term becomes the marker, as a bookmark title does;
      * ``layers`` — so does a layer's name;
      * ``piece_info`` — an application's private data (/PieceInfo) with a
        term anywhere under it is deleted whole;
      * ``xmp`` / ``metadata`` — a metadata stream holding a term in any
        encoding is deleted, on the catalog or anywhere else. One that
        cannot be read is deleted too: it cannot be shown free of the term;
      * ``scripts`` — a script whose text holds a term is removed, the way
        Sanitize removes scripts (pdf_analyze.remove_scripts);
      * ``destinations`` — a named destination whose name holds a term is
        renamed, and the links that use it follow.

    And the places real files turned out to keep a term (#119, R1b). These
    work on every annotation and field dictionary in the file, on a page or
    off it — redaction takes an annotation under a box off its page, and a
    tagged file's structure tree goes on pointing at it:

      * ``rich_text`` — a rich-text entry (/RC, /RV) holding a term is
        deleted;
      * ``field_text`` / ``field_names`` — a field's tooltip, mapping name,
        default value, option list and captions become the marker; a field
        whose own name holds a term is renamed;
      * ``links`` — a URI action whose address holds a term is removed the
        way Sanitize removes that action, wherever it hangs;
      * ``pdftex`` — a /PTEX.* entry holding a term is deleted;
      * ``page_labels`` — a page-label prefix becomes the marker;
      * ``xfa`` — the form's /XFA entry is dropped when a packet holds a
        term, so the form falls back to its AcroForm fields. A form with no
        such fields is refused here, with RedactionNotPossibleError: it
        cannot be term-redacted;
      * ``embedded_files`` — an attachment whose name, description or bytes
        hold a term is removed whole (pdf_analyze.remove_embedded_files).
        Bytes as stored: a zip, a docx or an xlsx is compressed inside and
        is not opened;
      * ``signatures`` — a signature holding a term anywhere in it has its
        value removed, leaving the field unsigned.

    Two things go with what a rule removes and are not counted on their
    own. An annotation that is on no page and had its text scrubbed loses
    its appearance stream, which drew that text (_scrub_objects). And a
    go-to action into an attachment that has been removed — here, or by
    name before this ran (*gone_attachments*) — is removed with it.

    There is no rule for anything else: web-capture names, an output
    intent's strings, the document security store, the appearance stream of
    an annotation that is on a page, a form held by a private key. A term
    left there is found by the sweep, and the redaction is refused.
    """
    if not terms:
        return {}
    match = _make_term_matcher(terms, case_sensitive)
    finder = TermFinder(terms, case_sensitive)
    marker = _marker_for(finder)         # "[redacted]", unless that holds a term
    counts: dict[str, int] = {}

    def bump(key: str, n: int = 1):
        if n:
            counts[key] = counts.get(key, 0) + n

    kwargs = {"password": password} if password else {}
    with pikepdf.open(path, allow_overwriting_input=True, **kwargs) as pdf:
        # -- docinfo (D3 wholesale) ------------------------------------
        try:
            info = pdf.trailer.get("/Info")
            if info is not None:
                hit = sum(1 for k in info.keys()
                          if match(info[k]))
                if hit:
                    del pdf.trailer["/Info"]
                    bump("docinfo", hit)
        except Exception:
            pass

        # -- metadata streams, wherever they hang (D3 wholesale) --------
        # Delete the stream itself. Never clear a parsed view and let
        # pikepdf write it back: its recovering parser accepts malformed
        # packets it cannot edit, so clear()-then-save re-emits the
        # recovered tree with the term still in it. Merely entering the
        # metadata context is a write too — it invents a packet where the
        # file had none and rewrites docinfo from it.
        try:
            catalog_md = pdf.Root.get("/Metadata")
            catalog_id = (tuple(catalog_md.objgen)
                          if isinstance(catalog_md, pikepdf.Stream) else None)

            def metadata_hit(stream) -> bool:
                # The catalog packet is also read the ways XMP is: decoded,
                # and through pikepdf's parser, which resolves entities.
                if (tuple(stream.objgen) == catalog_id
                        and any(match(v) for v in _xmp_text_views(pdf, stream))):
                    return True
                try:
                    return finder(stream.read_bytes())
                except Exception:
                    return True      # unreadable: cannot be shown free of it

            gone = remove_metadata_streams(pdf, metadata_hit)
            bump("xmp", int(catalog_id in gone))
            bump("metadata", len(gone) - int(catalog_id in gone))
        except Exception:
            pass

        # -- bookmarks / outline titles (D2 whole value) ---------------
        try:
            with pdf.open_outline() as outline:
                bump("bookmarks", _scrub_outline_items(outline.root, match, marker))
        except Exception:
            pass

        # Every dictionary and stream in the file, once. The rules from
        # here on work on these, wherever they hang: an annotation or a
        # field that is off its page is scrubbed like one that is on it.
        try:
            objects = _all_objects(pdf)
        except Exception:
            objects = []
        tree_ids = {tuple(f.objgen) for f in _field_tree(pdf) if f.is_indirect}
        fields = [o for o in objects if _is_field(o, tree_ids)]
        annotations = [o for o in objects if _is_annotation(o)]
        owners = annotations + [f for f in fields if not _is_annotation(f)]

        # -- XFA: dropped when a packet holds a term. A form that is only
        #    XFA is the one refusal the scrub itself makes. -----------------
        try:
            bump("xfa", _scrub_xfa(pdf, finder))
        except RedactionNotPossibleError:
            raise
        except Exception:
            pass

        # -- signatures that name a term (before the field values: a
        #    signature is a field's value) ----------------------------------
        try:
            bump("signatures", _scrub_signatures(pdf, objects, finder))
        except Exception:
            pass

        # -- annotations and form fields: text, rich text, names, values -
        try:
            bump_map = _scrub_objects(objects, annotations, fields, tree_ids, match, finder,
                                      marker, _listed_annotations(pdf))
            for k, v in bump_map.items():
                bump(k, v)
        except Exception:
            pass

        # -- links: a web address holding a term goes the way Sanitize
        #    removes that action, wherever it hangs; then the file targets
        #    (/F) of the actions that are left --------------------------------
        try:
            bump("links", remove_links(pdf, match, also=owners))
        except Exception:
            pass
        for annot in annotations:
            try:
                if "/A" in annot:
                    bump("links", _scrub_action_uri(annot["/A"], match))
            except Exception:
                pass

        # -- structure text and layer names (D2 whole value) -----------
        try:
            tree = pdf.Root.get("/StructTreeRoot")
            if isinstance(tree, pikepdf.Dictionary):
                bump("structure", _redact_strings(tree, match, _STRUCTURE_WAYS_OUT, marker))
        except Exception:
            pass
        try:
            layers = pdf.Root.get("/OCProperties")
            if isinstance(layers, pikepdf.Dictionary):
                bump("layers", _redact_strings(layers, match, marker=marker))
        except Exception:
            pass

        # -- private application data, pdfTeX's records, scripts, named
        #    destinations, page labels --------------------------------------
        try:
            bump("piece_info", _scrub_piece_info(objects, finder))
        except Exception:
            pass
        try:
            bump("pdftex", _scrub_pdftex(objects, finder))
        except Exception:
            pass
        try:
            def script_hit(js) -> bool:
                if isinstance(js, pikepdf.String):
                    return finder(bytes(js))
                try:
                    return finder(js.read_bytes())
                except Exception:
                    return True      # unreadable: cannot be shown free of it
            bump("scripts", remove_scripts(pdf, script_hit, also=owners))
        except Exception:
            pass
        try:
            bump("destinations", _rename_destinations(pdf, objects, finder))
        except Exception:
            pass
        try:
            labels = pdf.Root.get("/PageLabels")
            if isinstance(labels, pikepdf.Dictionary):
                bump("page_labels", _redact_strings(labels, match, marker=marker))
        except Exception:
            pass

        # -- attachments: one whose name, description or bytes hold a term
        #    is removed whole. Bytes as stored: a zip, a docx or an xlsx is
        #    compressed inside and is not opened. One that cannot be read is
        #    left for the verifier, which refuses the run. A go-to action
        #    that opened a removed attachment goes with it. ------------------
        try:
            bump("embedded_files", remove_embedded_files(
                pdf, lambda thing: _holds_term(thing, finder, unreadable=False),
                also=owners, gone=gone_attachments))
        except Exception:
            pass

        out_dir = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
        os.close(fd)
        try:
            # fix_metadata_version re-serializes any XMP packet on the way
            # out; a packet that survived the scrub must survive untouched.
            pdf.save(tmp, fix_metadata_version=False)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    return counts


def _xmp_text_views(pdf, stream) -> list[str]:
    """Every reading of the catalog XMP packet a term could be found in.

    The raw bytes as UTF-8 are what verify_redaction (and anyone dumping the
    stream) sees, well-formed or not. XMP may also be UTF-16/32: decoding
    that, and separately dropping the NUL padding, exposes it. pikepdf's
    parsed view resolves character entities. All three are read-only — the
    metadata object is never entered as a context, so nothing is written
    back.
    """
    views: list[str] = []
    try:
        raw = stream.read_bytes()
    except Exception:
        try: raw = stream.read_raw_bytes()
        except Exception: raw = b""
    if raw:
        views.append(raw.decode("utf-8", "replace"))
        if b"\x00" in raw:
            views.append(raw.decode("utf-16", "replace"))
            views.append(raw.replace(b"\x00", b"").decode("utf-8", "replace"))
    try:
        views.append(str(pdf.open_metadata(set_pikepdf_as_editor=False)))
    except Exception:
        pass
    return views


def _scrub_outline_items(items, match, marker: str = _REDACTED_MARKER, depth: int = 0) -> int:
    """Recursively replace any outline title matching *match* with the whole-
    value redaction *marker* (D2). Returns the number of titles replaced."""
    if depth > 50:
        return 0
    n = 0
    for it in items:
        try:
            if it.title and match(str(it.title)):
                it.title = marker
                n += 1
        except Exception:
            pass
        try:
            n += _scrub_outline_items(it.children, match, marker, depth + 1)
        except Exception:
            pass
    return n


def _scrub_objects(objects: list, annotations: list, fields: list, tree_ids: set,
                   match, finder, marker: str = _REDACTED_MARKER,
                   listed: set | None = None) -> dict[str, int]:
    """Whole-value scrub (D2) of what annotations and form fields say, on
    every such dictionary in the file — on a page or off it.

      * ``field_text`` / ``field_names`` — see _scrub_fields;
      * ``rich_text`` — a rich-text entry (/RC on an annotation, /RV on a
        field) holding a term is deleted; it is the styled twin of the plain
        text beside it, which is scrubbed here too;
      * ``annotations`` — /Contents, /T and /Subj holding a term are deleted
        (a field's /T is its name, and is renamed instead);
      * ``form_fields`` — a value (/V) holding a term is emptied, and the
        appearances that drew it, on the field and on every widget below
        it, are dropped.

    An annotation that had text scrubbed and is on no page — its id is not
    in *listed*, the ids every page's /Annots holds — loses its appearance
    (/AP) as well: it drew that text, no page shows it any more, and
    nothing else would take it out of the file. One that is on a page keeps
    its appearance; that is what the reader sees there, and if it draws a
    term the verifier refuses the run and offers the page for flattening.

    Mirrors what verify_redaction reads on each surface.
    """
    counts: dict[str, int] = {}
    scrubbed: list = []          # the annotations that had text removed

    def bump(key: str, n: int = 1):
        if n:
            counts[key] = counts.get(key, 0) + n

    _scrub_fields(fields, match, finder, marker, bump)

    for obj in annotations + [f for f in fields if not _is_annotation(f)]:
        for key in ("/RC", "/RV"):
            try:
                value = obj.get(key)
                if value is not None and _holds_term(value, finder):
                    del obj[key]
                    bump("rich_text")
                    scrubbed.append(obj)
            except Exception:
                pass

    for annot in annotations:
        is_field = _is_field(annot, tree_ids)
        for key in ("/Contents", "/T", "/Subj"):
            if key == "/T" and is_field:
                continue
            try:
                if key in annot and match(annot[key]):
                    del annot[key]
                    bump("annotations")
                    scrubbed.append(annot)
            except Exception:
                pass

    if listed is not None:
        for annot in scrubbed:
            try:
                if (_is_annotation(annot) and annot.is_indirect
                        and tuple(annot.objgen) not in listed and "/AP" in annot):
                    del annot["/AP"]
            except Exception:
                pass

    for obj in objects:
        try:
            if not isinstance(obj, pikepdf.Dictionary) or "/V" not in obj:
                continue
            value = obj["/V"]
            if (_holds_term(value, finder) if isinstance(value, pikepdf.Stream)
                    else match(value)):
                obj["/V"] = pikepdf.String("")
                _drop_appearances(obj)       # stale: they drew the old value
                bump("form_fields")
        except Exception:
            pass
    return counts


def _scrub_action_uri(action, match, depth: int = 0) -> int:
    """Remove a matched /URI (or /F) from an action dict, following /Next.
    Returns the number of targets removed (D2 whole-value)."""
    if depth > 32:
        return 0
    try:
        if not isinstance(action, pikepdf.Dictionary):
            return 0
    except Exception:
        return 0
    n = 0
    for key in ("/URI", "/F"):
        try:
            if key in action and match(action[key]):
                del action[key]
                n += 1
        except Exception:
            pass
    try:
        nxt = action.get("/Next")
        if isinstance(nxt, pikepdf.Array):
            for sub in nxt:
                n += _scrub_action_uri(sub, match, depth + 1)
        elif nxt is not None:
            n += _scrub_action_uri(nxt, match, depth + 1)
    except Exception:
        pass
    return n


def _scrub_embedded_file_names(doc, terms: list[str], case_sensitive: bool) -> list[str]:
    """Delete embedded files whose name matches a term (D2 whole-value),
    using PyMuPDF's embfile API — the exact names verify_redaction reads via
    doc.embfile_names(). Returns the names removed, so the actions that
    opened those files can go with them (pdf_analyze.remove_embedded_files).
    Runs on the open fitz doc before save (fitz owns embedded-file
    structures cleanly)."""
    if not terms:
        return []
    match = _make_term_matcher(terms, case_sensitive)
    removed: list[str] = []
    try:
        names = list(doc.embfile_names())
    except Exception:
        return []
    for name in names:
        try:
            if match(str(name)):
                doc.embfile_del(name)
                removed.append(str(name))
        except Exception:
            continue
    return removed


def _flatten_pages_to_image(src_path: str, dst_path: str,
                            page_indices: list[int], dpi: int = 150) -> None:
    """Write *dst_path* as a copy of *src_path* with only *page_indices*
    (0-based) rasterized to a full-page image (D1 flatten fallback).

    Non-flagged pages are copied faithfully (insert_pdf preserves their text,
    annotations, and links); flagged pages become a single image at *dpi*
    (matching pdf_to_images), losing any extractable text layer while
    keeping page size and orientation. Only the named pages are touched —
    never the whole document. (Callers hold 1-based page numbers from the
    verification report; convert before calling.)
    """
    flag = set(page_indices)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    src = fitz.open(src_path)
    out = fitz.open()
    try:
        for i in range(len(src)):
            if i in flag:
                p = src[i]
                pix = p.get_pixmap(matrix=mat, alpha=False)
                np_ = out.new_page(width=p.rect.width, height=p.rect.height)
                np_.insert_image(np_.rect, pixmap=pix)
            else:
                out.insert_pdf(src, from_page=i, to_page=i)
        out.save(dst_path, garbage=4, deflate=True, clean=True)
    finally:
        out.close()
        src.close()


def _source_file_ids(doc, input_path: str, password: str | None = None) -> list[str]:
    """Trailer /ID elements (lowercase hex) of the already-open source *doc*.

    Read through the handle redact_pdf holds anyway, so the ordinary case
    needs no second open that could fail on an input fitz accepted. fitz
    returns the value as PDF syntax — hex or escaped-literal strings — and
    qpdf's own parser decodes it, so the result compares byte-for-byte with
    what the verifier reads from the output (pdf_analyze.id_elements on both
    sides).

    No shape of /ID is refused. A bare string is one element; its syntax
    comes from _trailer_with_only_id. Elements held as indirect references
    are the one thing the handle cannot print resolved, so for those the
    trailer is read from the file with pikepdf, exactly as the verifier reads
    the output's.
    """
    kind, val = doc.xref_get_key(-1, "ID")          # xref -1 is the trailer
    if kind == "null":
        return []
    try:
        if kind == "string":
            return id_elements(pikepdf.Object.parse(
                _trailer_with_only_id(doc).encode("latin-1")).get("/ID"))
        if kind == "xref":
            val = doc.xref_object(int(val.split()[0]), compressed=True)
        return id_elements(pikepdf.Object.parse(val.encode("latin-1")))
    except Exception:
        kwargs = {"password": password} if password else {}
        with pikepdf.open(input_path, **kwargs) as pdf:
            return trailer_ids(pdf)


def _trailer_with_only_id(doc) -> str:
    """PDF syntax of *doc*'s trailer dictionary reduced to its /ID entry.

    For a /ID that is a bare string. fitz reports a string value as decoded
    text, which is not its bytes (<FEFF0041> comes back as "A"). The
    trailer's source has the real syntax, but also indirect references,
    which parse only inside a document — so it is loaded into a scratch one,
    every other key is nulled, and what is left is printed.
    """
    tmp = fitz.open()
    try:
        xref = tmp.get_new_xref()
        tmp.update_object(xref, doc.xref_object(-1, compressed=True))
        for key in tmp.xref_get_keys(xref):
            if key != "ID":
                tmp.xref_set_key(xref, key, "null")
        return tmp.xref_object(xref, compressed=True)
    finally:
        tmp.close()


#: How many times a page is searched and redacted: once, and then again for
#: as long as removing text leaves new matches behind, up to this many.
_REDACT_PASSES = 3


def _box_key(rect) -> tuple:
    return tuple(round(v, 2) for v in rect)


def _redact_boxes(page, boxes: list) -> int:
    """Redact *boxes* on *page* and return how many there were.

    Form field (AcroForm widget) values live in /V and render via a
    separate appearance stream -- apply_redactions() only strips page
    CONTENT (text/images drawn in the content stream), so a redaction box
    over a fillable field would otherwise leave the value fully intact and
    extractable (verified: still readable via both the widget API and plain
    get_text() after apply_redactions() alone). Any widget whose rect
    intersects a box -- from a term match or a user-drawn rect -- is
    neutralized before the content-level redaction runs.

    PDF_REDACT_IMAGE_PIXELS blacks out only the pixels under each box;
    PDF_REDACT_IMAGE_REMOVE deletes any image the box touches -- on a
    scanned page (one full-page image) that blanks the WHOLE page while
    still reporting success (RED-01).
    """
    if not boxes:
        return 0
    for box in boxes:
        page.add_redact_annot(box, fill=(0, 0, 0))
    for wdg in list(page.widgets()):
        if any(r.intersects(wdg.rect) for r in boxes):
            try:
                wdg.field_value = ""
                wdg.update()
            except Exception:
                pass
            page.delete_widget(wdg)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS)
    return len(boxes)


def _refusal_message(failed) -> str:
    """One-line reason for a refused redaction, from its failed checks."""
    if failed and all(c.id == "redaction.file_id" for c in failed):
        return ("Redaction could not be verified: the output would still "
                "carry the source file's identifier.")
    if failed and all(c.unevaluated for c in failed):
        # Nothing was found: something could not be read, which is a
        # different thing to tell the user (#119).
        return ("Redaction could not be verified: part of the output could "
                "not be read, so it could not be checked.")
    return ("Redaction could not be verified: sensitive content is still "
            "present in the output.")


def redact_pdf(
    input_path: str,
    output_path: str,
    *,
    search_terms: list[str] | None = None,
    rects: list[dict] | None = None,
    case_sensitive: bool = False,
    pages: list[int] | None = None,
    password: str | None = None,
    flatten_pages: list[int] | None = None,   # 1-based page numbers (D1 retry)
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> RedactResult:
    """Redact a PDF, then PROVE the targets are gone or refuse (fail-closed).

    True redaction: add_redact_annot()/apply_redactions() physically strips
    the text and image data under each area from the page content -- it
    isn't painted over, and it isn't recoverable from the saved file
    afterward. Replaces the previous regex-on-raw-content-stream approach,
    which only matched literal parenthesized text operands and silently
    missed anything kerned, hex-encoded, or laid out via TJ arrays --
    meaning it could report success while leaving the real text fully
    extractable from the "redacted" output.

    Two ways to select what to redact, combinable, at least one required:
      * search_terms: text found by the page-text matcher the verifier also
        uses (pdf_verify.term_boxes, #119) -- the page's own characters
        searched across line breaks, line-end hyphens, letter-spacing and
        ligatures, with page.search_for()'s hits beside them. Every match
        is redacted, widened to the whole words it touches, and the page is
        searched again for as long as removing text leaves a new match.
      * rects: user-drawn boxes, each {"page": int (0-based), "x0", "y0",
        "x1", "y1": float} in PDF point coordinates -- for the visual
        redaction UI (Part 2); unused by the current search-term-only
        frontend but wired through end-to-end.

    Beyond the page content apply_redactions() reaches, search_terms are also
    scrubbed WHOLE-VALUE from every document-level surface verification checks
    -- docinfo, XMP, bookmark titles, annotation text, form-field values,
    link URIs, embedded-file names (#99) -- so a term surviving only in the
    /Title (never on any page) is still removed and still counts as a hit.
    So are structure-tree text, layer names, an application's private data,
    metadata streams on any object, scripts, named destinations, rich text,
    what a form field says about itself, web addresses wherever they hang,
    pdfTeX's records, page labels, XFA data, attachments and signatures
    (#119; see _scrub_document_surfaces). A term anywhere else in the file
    -- a stream held by a private key, an appearance a page shows -- has no
    rule: the verifier's whole-file sweep finds it and the run is refused,
    never passed. A form that exists only as XFA and holds a term in it
    raises RedactionNotPossibleError (a ValueError): it cannot be
    term-redacted.

    On every run, by term or by box, the structure tree's references to the
    original of a form or image the pages now have a redacted copy of are
    removed (_drop_stale_structure_refs, counted as ``structure_refs``):
    they kept the unredacted original in the output. And a file MuPDF
    repairs while it is being saved, which undoes the redaction, is
    redacted again from the repaired copy.

    Verify, then commit. The redacted candidate is written to a temp file
    beside output_path, given a fresh content-derived trailer /ID as its last
    write (so it cannot be matched to the source by identifier), and handed
    to verify_redaction(), which must prove every target unextractable. Only
    a verified candidate is renamed onto output_path. If it cannot be
    verified:
      * every residual is page-content (text/annotation on a page) and the
        caller passed flatten_pages covering them -> those pages are
        rasterized to image and re-verified;
      * otherwise RedactionVerificationError is raised, carrying the report,
        the flatten-eligible pages (or None for a document-level residual,
        which flatten cannot fix), and whether the residual was
        document-level. Never returns success with a target still findable.
    A refused run never creates, replaces, or deletes output_path -- when it
    is the input itself (redacting in place), the original is untouched (D1).

    Returns a RedactResult with redaction_count (page-content redactions --
    unchanged meaning), pages_affected, a per-surface surface_counts
    breakdown, the verification report dict, and any flattened_pages.
    """
    scrub_terms = [t for t in (search_terms or []) if t and t.strip()]
    if not scrub_terms and not rects:
        raise ValueError("Provide search_terms and/or rects to redact.")

    doc = fitz.open(input_path)
    if password and doc.needs_pass:
        if not doc.authenticate(password):
            doc.close()
            raise ValueError("Wrong password.")

    # The source's identity, captured while it is open: the verifier is
    # handed these as plain values and never opens the input itself.
    try:
        source_ids = _source_file_ids(doc, input_path, password)
        source_sha = file_sha256(input_path)
    except Exception as exc:
        doc.close()
        raise ValueError(f"Could not read the source file's identity: {exc}") from exc

    rects_by_page: dict[int, list[dict]] = {}
    for r in (rects or []):
        rects_by_page.setdefault(int(r["page"]), []).append(r)
    target = list(pages) if pages is not None else list(range(len(doc)))

    def redact_pages(doc) -> tuple[int, set]:
        """Redact the page content of *doc*; returns (rectangles drawn,
        pages touched)."""
        total, drawn, touched = len(doc), 0, set()
        for i, pno in enumerate(target):
            if cancel and cancel.is_set():
                doc.close()
                raise ValueError("Cancelled")
            if on_progress:
                on_progress(i + 1, len(target))
            if pno < 0 or pno >= total:
                continue

            page = doc[pno]
            # Term hits come from the one matcher the verifier also uses
            # (pdf_verify.term_boxes): its own views of the page's
            # characters, and search_for's hits beside them, each widened to
            # the whole words it touches. It honours case_sensitive itself.
            boxes: list[fitz.Rect] = term_boxes(page, scrub_terms, case_sensitive)
            for rr in rects_by_page.get(pno, []):
                rect = fitz.Rect(rr["x0"], rr["y0"], rr["x1"], rr["y1"])
                if not rect.is_empty and rect.is_valid:
                    boxes.append(rect)

            added = _redact_boxes(page, boxes)
            # Taking a word out can leave its neighbours reading as the term
            # ("a" [removed] "b" for the term "a b"). The verifier would find
            # that and refuse; look again and box it instead. Bounded: a box
            # that was already applied and removed nothing is not tried twice.
            applied = {_box_key(b) for b in boxes}
            for _ in range(_REDACT_PASSES - 1):
                if not (added and scrub_terms):
                    break
                again = [b for b in term_boxes(page, scrub_terms, case_sensitive)
                         if _box_key(b) not in applied]
                if not again:
                    break
                applied.update(map(_box_key, again))
                added += _redact_boxes(page, again)
            if added:
                drawn += added
                touched.add(pno)
        return drawn, touched

    # MuPDF checks a file's objects as it comes to read them. One it finds
    # damaged makes it go back to the file and rebuild everything from there
    # ("repairing PDF document") — and what had been changed in memory is
    # gone, without a word. If that happens between here and the end of the
    # save below, the redaction is what is gone.
    repaired_at_start = bool(doc.is_repaired)
    hit_count, pages_touched = redact_pages(doc)

    # The page-content redaction is done; now widen the scrub and write the
    # output atomically. Saved even when hit_count == 0 -- a term can live
    # only in a document-level surface (e.g. the /Title), which still counts
    # as something to remove (#99, §3).
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    temps: list[str] = []
    surface_counts: dict[str, int] = {}
    flattened: list[int] = []

    def _mktmp() -> str:
        fd, t = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
        os.close(fd)
        temps.append(t)
        return t

    def _cleanup_temps():
        for t in temps:
            if os.path.exists(t):
                try: os.unlink(t)
                except Exception: pass

    try:
        # Embedded files are scrubbed on the open fitz doc (its embfile API
        # matches exactly what verify_redaction reads); every other
        # document-level surface is scrubbed via pikepdf on the saved temp.
        emb_gone = _scrub_embedded_file_names(doc, scrub_terms, case_sensitive)
        work = _mktmp()
        doc.save(work, garbage=4, deflate=True, clean=True)
        lost = bool(doc.is_repaired) and not repaired_at_start
        doc.close()
        if lost:
            # Repaired on the way (see above): what was just written is the
            # source, mended, with nothing redacted. It is a sound file now,
            # so the same work done on it holds. Once: it cannot be repaired
            # twice.
            doc = fitz.open(work)
            hit_count, pages_touched = redact_pages(doc)
            emb_gone = _scrub_embedded_file_names(doc, scrub_terms, case_sensitive)
            work = _mktmp()
            doc.save(work, garbage=4, deflate=True, clean=True)
            doc.close()
        emb_hits = len(emb_gone)

        if hit_count:
            surface_counts["page_content"] = hit_count
        if emb_hits:
            surface_counts["embedded_files"] = emb_hits
        # The temp is fitz-saved from the authenticated doc — always
        # plaintext, so the scrub (and verify below) need no password.
        doc_scrub = _scrub_document_surfaces(work, scrub_terms, case_sensitive,
                                             gone_attachments=emb_gone)
        for k, v in doc_scrub.items():
            surface_counts[k] = surface_counts.get(k, 0) + v

        if hit_count == 0 and emb_hits == 0 and sum(doc_scrub.values()) == 0:
            raise ValueError("No matching content found to redact.")

        final_src = work
        if flatten_pages:
            flat = _mktmp()
            # flatten_pages are 1-based (as the verification report and UI
            # count pages); _flatten_pages_to_image takes 0-based indices.
            _flatten_pages_to_image(work, flat, [int(p) - 1 for p in flatten_pages])
            final_src = flat
            flattened = sorted(set(int(p) for p in flatten_pages))

        # The identifier is written last, on every path. fitz carries the
        # source's /ID[0] through its save and the scrub's save keeps it
        # (the rects-only path never reaches the scrub at all), so without
        # this pass the output stays linkable to the original.
        candidate = _mktmp()
        with pikepdf.open(final_src) as pdf:
            # The original of a form or an image the pages now have a
            # redacted copy of must not ride along on the structure tree.
            # Here, not in the scrub: a box drawn by hand leaves one too.
            stale = _drop_stale_structure_refs(pdf)
            if stale:
                surface_counts["structure_refs"] = stale
            save_with_content_id(pdf, candidate, fix_metadata_version=False)

        # Verify, then commit (fail-closed, #99): every target must be
        # proven gone from the candidate before anything touches
        # output_path. A refusal leaves the destination exactly as it was —
        # which matters most when it is the input (redacting in place).
        report = verify_redaction(
            candidate,
            terms=scrub_terms or None,
            rects=rects,
            case_sensitive=case_sensitive,
            input_path=input_path,
            source_ids=source_ids,
            source_sha256=source_sha,
        )
        report.retarget(output_path)
        if not report.verified:
            failed = report.failed_checks()
            doc_level = any(c.is_document_level for c in failed)
            # D1: offer flatten only on the first attempt (flatten_pages is
            # None) and only for a purely page-content residual. Once we've
            # already flattened, there is no further fallback.
            offer = None if flatten_pages else report.flatten_target_pages()
            raise RedactionVerificationError(
                _refusal_message(failed),
                report=report.to_dict(),
                flatten_pages=offer,
                document_level=doc_level,
            )
        os.replace(candidate, output_path)
    except Exception:
        try: doc.close()
        except Exception: pass
        raise
    finally:
        _cleanup_temps()

    return RedactResult(
        input_path=input_path,
        output_path=output_path,
        redaction_count=hit_count,
        pages_affected=len(pages_touched),
        surface_counts=surface_counts,
        verification=report.to_dict(),
        flattened_pages=flattened,
    )
