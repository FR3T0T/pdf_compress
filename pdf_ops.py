"""PDF operations — merge, split, and page manipulation."""

import gc
import hashlib
import io
import logging
import math
import os
import re
import secrets
import tempfile
import threading
import zlib
from dataclasses import asdict, dataclass, field
from typing import Callable

import fitz  # PyMuPDF
import pikepdf

import pdf_ocr
from pdf_analyze import (
    embedded_files,
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
from pdf_verify import (
    _MATCH_FLAGS,
    DAMAGE_HEADLINE,
    STRUCTURE_WAYS_OUT,
    PageText,
    TermFinder,
    WalkStatus,
    chars_under,
    page_to_picture_matrix,
    pages_in_words,
    path_lines,
    pdf_to_page_matrix,
    redaction_refusal_detail,
    structure_kids,
    structure_object,
    structure_refs,
    structure_stream,
    term_boxes,
    term_hits,
    upright,
    verify_redaction,
    walk_objects,
)

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
    redaction_count: int          # page-content redactions, OCR's places among them (#128)
    pages_affected: int
    #: per-surface removal breakdown, e.g. {"page_content": 12, "docinfo": 1,
    #: "bookmarks": 2}. page_content is the places found in a page's text or
    #: drawn by hand, ocr_page_content the places OCR found in a page's
    #: picture (#128); the two make redaction_count. The rest are the
    #: document-level scrubs apply_redactions never touches (#99, #119), and
    #: what a run by box alone can have too: structure_refs (the structure
    #: tree's references to an unredacted original), structure_text,
    #: thumbnails, annotations_removed, fields_removed and links_removed.
    #: "annotations" is text scrubbed inside an annotation;
    #: "annotations_removed" is annotations taken off a page, replies with
    #: them (a popup is not counted); "fields_removed" and "links_removed"
    #: are form fields and links taken off a page because a box, drawn by
    #: hand or a term's, lay over them. ("links" is web addresses holding a
    #: term, removed by the scrub.)
    surface_counts: dict = field(default_factory=dict)
    #: verify_redaction(output).to_dict() — the proof-of-removal artifact.
    verification: dict | None = None
    #: pages rasterized by the flatten fallback on this run, if any (D1).
    flattened_pages: list = field(default_factory=list)
    #: {page number (1-based): areas filled black on the picture of that
    #: page before it replaced the page} — what could still be found on it,
    #: and the user's own boxes (#119). Empty when nothing was flattened.
    flattened_areas: dict = field(default_factory=dict)
    #: What this run did not look at: plain facts for the app to word, none
    #: of them a failure (#119). One entry per kind, in this order, each a
    #: dict of plain values with page numbers counted from 1:
    #:   {"kind": "no_text", "pages": [...]} — searched pages with no text
    #:     at all (a scan with no text layer, text drawn as outlines): a
    #:     search finds no words on them. With OCR, only those OCR did not
    #:     read because it is not set up;
    #:   {"kind": "mostly_picture", "pages": [...]} — searched pages that
    #:     are mostly a picture with a few words of text (_search_fact): a
    #:     scan with a typed page number, a stamp or a note on it. The search
    #:     read only those words. With OCR, only those OCR did not read
    #:     because it is not set up (#128);
    #:   {"kind": "ocr_not_ready"} — OCR was asked for and is not set up
    #:     (pdf_ocr.ocr_status): the pages in no_text and mostly_picture
    #:     were not read (#128);
    #:   {"kind": "ocr", "pages": [...], "hits": {page: n}} — pages with no
    #:     text, or mostly a picture, that were read with OCR, and how many
    #:     places it found on each (0 for none). OCR can miss a word: these
    #:     pages want checking by eye, and none of them was searched for
    #:     certain (#128);
    #:   {"kind": "ocr_unread", "pages": [...], "too_large": [...]} — pages
    #:     read with OCR on which it found nothing that looks like words at
    #:     any turn (pdf_ocr.looks_like_words: a photo, a drawing, a page in
    #:     another language), and among them those too large to read
    #:     (pdf_ocr.reading_dpi), which were not drawn at all (#128);
    #:   {"kind": "text_layer", "pages": [...], "hits": {page: n}} — pages
    #:     whose text is an invisible layer over a picture (OCR). The search
    #:     read that layer, and a word the layer got wrong is still in the
    #:     picture; n is how many hits the search had on the page;
    #:   {"kind": "attachments", "names": [...], "compressed": [...]} — the
    #:     attachments in the output. A search reads each as stored and
    #:     opens none; "compressed" are those that hold their contents
    #:     packed (zip, docx, gzip, another PDF), where it could read nothing;
    #:   {"kind": "flattened", "pages": [...], "areas": {page: n}} — pages
    #:     turned into pictures, with flattened_areas;
    #:   {"kind": "field_still_shown", "pages": [...]} — a box drawn by hand
    #:     took a widget of a form field that is shown in another place too:
    #:     the field keeps its value and still shows it on these pages;
    #:   {"kind": "boxes_only", "pages": [...]} — a run by boxes alone: the
    #:     words under the boxes were not searched for anywhere else;
    #:   {"kind": "text_may_move", "version": "..."} — the run was made with
    #:     PyMuPDF 2.x, which does not always keep the text after a removed
    #:     word in place: the rest of the line can move along it (always
    #:     last; see _check_library).
    notes: list = field(default_factory=list)


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
    untouched. ``detail`` is the same thing in plain words for the window
    (pdf_verify.redaction_refusal_detail): a headline, what was found and
    where, and what can be done.
    """

    def __init__(self, message: str, *, report: dict | None = None,
                 flatten_pages: list | None = None,
                 document_level: bool = False, detail: dict | None = None):
        super().__init__(message)
        self.report = report
        self.flatten_pages = flatten_pages
        self.document_level = document_level
        self.detail = detail


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
_STRUCTURE_WAYS_OUT = STRUCTURE_WAYS_OUT

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

_XFA_ONLY = ("This file's form is stored in a format (XFA) that redaction cannot "
             "change, and what you searched for is in that form. Nothing was saved.")


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


# ── Annotations that draw a term (#119, R3) ──────────────────────────
#
#  An annotation is painted above the page. apply_redactions() removes page
#  content under a box and leaves every annotation but its own marks, so a
#  stamp that says "APPROVED" went on saying it: the run was refused, the
#  page was offered for flattening, and the picture of the page showed the
#  stamp. An annotation whose appearance draws a term is taken off its page.

#: An appearance stream can only draw text if it shows some, or paints
#: another form that might.
_SHOWS_TEXT = (b"Tj", b"TJ", b"'", b'"', b"Do")


def _appearance_streams(annot) -> list:
    """Every appearance stream of *annot*: what /AP holds for the normal,
    rollover and down appearances, each a stream or a dictionary of them by
    state."""
    found = []
    try:
        appearance = annot.get("/AP")
        if isinstance(appearance, pikepdf.Dictionary):
            for key in appearance.keys():
                value = appearance[key]
                if isinstance(value, pikepdf.Stream):
                    found.append(value)
                elif isinstance(value, pikepdf.Dictionary):
                    found.extend(v for v in (value[k] for k in value.keys())
                                 if isinstance(v, pikepdf.Stream))
    except Exception:
        pass
    return found


def _form_extent(stream) -> list:
    """A page box that takes in what form XObject *stream* draws: its /BBox
    through its /Matrix, and a wide margin, since text the box clips away
    is still text in the stream."""
    try:
        x0, y0, x1, y1 = (float(v) for v in stream["/BBox"])
    except Exception:
        x0, y0, x1, y1 = 0.0, 0.0, 612.0, 792.0
    try:
        a, b, c, d, e, f = (float(v) for v in stream["/Matrix"])
    except Exception:
        a, b, c, d, e, f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    xs = [a * x + c * y + e for x in (x0, x1) for y in (y0, y1)]
    ys = [b * x + d * y + f for x in (x0, x1) for y in (y0, y1)]
    return [min(xs) - 500, min(ys) - 500, max(xs) + 500, max(ys) + 500]


def _streams_drawing(streams: list, terms: list[str], case_sensitive: bool) -> set:
    """(num, gen) of each of *streams* — form XObjects — in whose text the
    page-text matcher finds one of *terms*.

    "Draws" is asked of the matcher redaction itself searches a page with
    (pdf_verify.PageText), so a term broken across two lines of a stamp, or
    letter-spaced, is found as it would be on a page. To ask it, each
    stream is copied into a scratch document as the whole content of a page
    of its own; that document exists in memory only.
    """
    scratch = pikepdf.Pdf.new()
    order, seen = [], set()
    for stream in streams:
        key = tuple(stream.objgen)
        if key in seen:
            continue
        seen.add(key)
        try:
            data = stream.read_bytes()
            if not any(op in data for op in _SHOWS_TEXT):
                continue
            box, copy = _form_extent(stream), scratch.copy_foreign(stream)
            scratch.pages.append(pikepdf.Page(pikepdf.Dictionary(
                Type=pikepdf.Name.Page, MediaBox=box,
                Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=copy)),
                Contents=scratch.make_stream(b"/Fm0 Do"))))
            order.append(key)
        except Exception:
            continue                # the whole-file search still reads its bytes
    if not order:
        return set()
    buffer = io.BytesIO()
    scratch.save(buffer)
    found = set()
    with fitz.open(stream=buffer.getvalue(), filetype="pdf") as doc:
        for key, page in zip(order, doc, strict=True):
            try:
                text = PageText(page, case_sensitive)
            except Exception:
                continue
            if any(text.find(term) for term in terms):
                found.add(key)
    return found


def _take_off_pages(pdf, doomed: list, draws=None) -> tuple[int, int]:
    """Take the annotations in *doomed* off whatever page lists them, each
    with its popup and with everything that replies to it, however deep.

    *doomed* are annotations that are objects of their own. One written
    into a page's /Annots array itself has no identity to be named by: the
    array is gone through, and such an entry goes if *draws* says so.
    Returns (how many of those two kinds left a page, how many replies went
    with them); a popup is not counted.
    """
    def ident(obj):
        try:
            return tuple(obj.objgen) if obj.is_indirect else None
        except Exception:
            return None

    gone = {ident(a) for a in doomed} - {None}
    pages = []
    for page in pdf.pages:
        try:
            annots = page.obj.get("/Annots")
            entries = [a for a in annots] if isinstance(annots, pikepdf.Array) else []
        except Exception:
            entries = []
        pages.append((page.obj, entries))
    listed = [a for _obj, entries in pages for a in entries
              if isinstance(a, pikepdf.Dictionary) and ident(a) is not None]
    first = len({ident(a) for a in listed} & gone)

    replies, grew = 0, True
    while grew:
        grew = False
        for annot in listed:
            try:
                if ident(annot) not in gone and ident(annot.get("/IRT")) in gone:
                    gone.add(ident(annot))
                    replies += 1
                    grew = True
            except Exception:
                continue
    popups = set()
    for annot in listed:
        try:
            if ident(annot) in gone:
                popups.add(ident(annot.get("/Popup")))
            elif _name_of(annot, "/Subtype") == "/Popup" and ident(annot.get("/Parent")) in gone:
                popups.add(ident(annot))
        except Exception:
            continue

    def stays(entry) -> bool:
        if ident(entry) is not None:
            return ident(entry) not in gone | popups
        try:
            return not (draws is not None and isinstance(entry, pikepdf.Dictionary)
                        and draws(entry))
        except Exception:
            return True

    for obj, entries in pages:
        try:
            keep = [a for a in entries if stays(a)]
            if len(keep) != len(entries):
                first += sum(1 for a in entries if ident(a) is None and not stays(a))
                obj["/Annots"] = pikepdf.Array(keep)
        except Exception:
            continue
    return first, replies


def _lift_annotations_drawing(pdf, annotations: list, terms: list[str],
                              case_sensitive: bool) -> tuple[int, int]:
    """Take every annotation whose appearance draws a term off its page.
    Returns (annotations that left a page, replies included and popups not;
    annotations on no page that lost the appearance that drew it).

    Any of its appearance streams counts: normal, rollover or down, every
    state, shown or hidden. The annotation loses its /AP as well as its
    place on the page, so that whatever still refers to the dictionary — a
    tagged file's structure tree — keeps nothing of what it drew; one that
    was on no page to begin with loses just that. One written into the
    page's /Annots array itself, not an object of its own, is taken out of
    the array like any other. A form field's widget is not touched here: a
    field has its own path (its value is emptied and the appearances that
    drew it are dropped, _scrub_objects).
    """
    owners = []
    for annot in annotations:
        try:
            if _name_of(annot, "/Subtype") == "/Widget" or "/FT" in annot:
                continue
            streams = _appearance_streams(annot)
            if streams:
                owners.append((annot, streams))
        except Exception:
            continue
    if not owners:
        return 0, 0
    drawing = _streams_drawing([s for _a, streams in owners for s in streams],
                               terms, case_sensitive)

    def draws(annot) -> bool:
        return (_name_of(annot, "/Subtype") != "/Widget" and "/FT" not in annot
                and any(tuple(s.objgen) in drawing for s in _appearance_streams(annot)))

    doomed = [a for a, _streams in owners if draws(a)]
    if not doomed:
        return 0, 0
    # Off the pages first: an entry of a page's own list is known by what it draws.
    first, replies = _take_off_pages(pdf, [a for a in doomed if a.is_indirect], draws)
    for annot in doomed:
        try:
            del annot["/AP"]
        except Exception:
            continue
    return first + replies, max(0, len(doomed) - first)


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


def _on_a_page(obj, listed: set) -> bool:
    """Is annotation or field *obj* on a page — listed in some page's
    /Annots (*listed*, from _listed_annotations), or, for a parent field,
    shown there by one of the widgets below it?"""
    stack, seen = [obj], set()
    while stack:
        cur = stack.pop()
        try:
            if not isinstance(cur, pikepdf.Dictionary) or not cur.is_indirect:
                continue
            key = tuple(cur.objgen)
            if key in listed:
                return True
            if key in seen:
                continue
            seen.add(key)
            kids = cur.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                stack.extend(kids)
        except Exception:
            continue
    return False


def _drop_stale_structure_refs(pdf) -> int:
    """Remove every reference the structure tree makes to a stream that
    nothing else in the file leads to, and to an annotation or form field
    that is on no page; returns how many went.

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
    say) and its reference is still true.

    The same goes for an annotation or a form field the tree names with an
    object reference (#119, R4). One taken off its page — a stamp under a
    box, a link or a field MuPDF removes under one, an annotation that drew
    a term — stayed in the file whole when the tree pointed at it: its
    appearance, its text, its address or its value. A reference to an
    annotation or field that is on no page (_on_a_page) is removed,
    whatever else still leads to it; the verifier checks that nothing is
    left that only the tree leads to.

    This runs on every redaction, by term or by box, as the last thing
    before the output is written.
    """
    holders, named, alive, objects = structure_refs(pdf)
    if not named:
        return 0
    listed = _listed_annotations(pdf) if objects else set()

    removed = 0
    for holder in holders:
        try:
            kids = structure_kids(holder)
            keep = []
            for kid in kids:
                stream, thing = structure_stream(kid), structure_object(kid)
                if stream is not None and tuple(stream.objgen) not in alive:
                    removed += 1
                elif thing is not None and not _on_a_page(thing, listed):
                    removed += 1                 # written into the reference: on no page
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


# ── What a tagged file says of content a box removed (#119, R3) ──────
#
#  A structure element can describe its content in words: /Alt on a figure,
#  /ActualText, the expansion of an abbreviation (/E), a title (/T). A box
#  over the figure removed the picture and left the description. The
#  elements whose own content a box removed are found by comparing what
#  each marked-content sequence paints, before the redaction and after.

#: Operators that put something on the page: text, a painted path, a shading.
_SHOW_OPS = frozenset({"Tj", "TJ", "'", '"'})
_PAINT_OPS = frozenset({"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "sh"})
_STRUCTURE_TEXT = ("/Alt", "/ActualText", "/E", "/T")


def _stream_digest(stream) -> bytes:
    """A digest of an image's data: decoded where that is possible, as
    stored otherwise (the same bytes before and after, if it was not
    touched)."""
    try:
        data = stream.read_bytes()
    except Exception:
        try: data = stream.read_raw_bytes()
        except Exception: data = b"?"
    return hashlib.sha256(data).digest()


def _painted(holder, resources, marks: dict, depth: int = 0) -> bytes:
    """A digest of everything content stream *holder* (a page or a form
    XObject) paints, and into *marks*, {MCID: digest}, the same for each of
    its marked-content sequences that has one.

    What is compared is what is painted, not how it is written: the bytes
    of the text shown, each image's data, each form's own digest, and how
    many paths and shadings are painted. MuPDF rewrites a redacted page's
    whole content stream — other operators, other spacing, strings cut up
    differently — and leaves those the same wherever it removed nothing.
    Returns b"" when the content cannot be parsed; the caller takes that as
    changed.
    """
    try:
        instructions = pikepdf.parse_content_stream(holder)
    except Exception:
        return b""
    def lookup(kind: str, name):
        try:
            return resources[kind][str(name)]
        except Exception:
            return None

    total, open_marks, sums = hashlib.sha256(), [], {}
    for instruction in instructions:
        try:
            operator, operands = str(instruction.operator), instruction.operands
        except Exception:
            continue
        piece = None
        if operator in ("BDC", "BMC"):
            mcid = None
            if operator == "BDC" and len(operands) > 1:
                props = operands[1]
                if isinstance(props, pikepdf.Name):
                    props = lookup("/Properties", props)
                if isinstance(props, pikepdf.Dictionary) and isinstance(props.get("/MCID"), int):
                    mcid = int(props["/MCID"])
            open_marks.append(mcid)
        elif operator == "EMC":
            if open_marks:
                open_marks.pop()
        elif operator in _SHOW_OPS:
            shown = [bytes(x) for o in operands
                     for x in (o if isinstance(o, pikepdf.Array) else [o])
                     if isinstance(x, pikepdf.String)]
            if any(shown):
                piece = b"T" + b"".join(shown)
        elif operator in _PAINT_OPS:
            piece = b"P"
        elif operator == "INLINE IMAGE":
            try: data = instruction.iimage.read_bytes()
            except Exception: data = repr(instruction).encode("utf-8", "replace")
            piece = b"I" + hashlib.sha256(data).digest()
        elif operator == "Do" and operands:
            target = lookup("/XObject", operands[0])
            if isinstance(target, pikepdf.Stream):
                if _name_of(target, "/Subtype") == "/Form" and depth < 12:
                    inner = target.get("/Resources")
                    piece = b"F" + _painted(target, inner if inner is not None else resources,
                                            {}, depth + 1)
                else:
                    piece = b"I" + _stream_digest(target)
        if piece is not None:
            total.update(piece)
            for mcid in {m for m in open_marks if m is not None}:
                sums.setdefault(mcid, hashlib.sha256()).update(piece)
    marks.update({mcid: s.digest() for mcid, s in sums.items()})
    return total.digest()


def _page_marks(page) -> dict:
    """{MCID: digest} for the marked-content sequences of *page*'s own
    content (not of the forms it paints), or None if it cannot be read."""
    marks: dict = {}
    try:
        node, resources = page.obj, None
        for _ in range(64):
            if "/Resources" in node:
                resources = node["/Resources"]
                break
            node = node.get("/Parent")
            if not isinstance(node, pikepdf.Dictionary):
                break
        return marks if _painted(page, resources, marks) else None
    except Exception:
        return None


def _painted_forms(page) -> list:
    """Every form XObject *page* can paint, through its resources and the
    resources of those forms: the redacted copies, after a redaction."""
    found, seen, stack = [], set(), []
    try:
        stack.append(page.obj.get("/Resources"))
    except Exception:
        return found
    while stack:
        resources = stack.pop()
        try:
            xobjects = resources.get("/XObject") if resources is not None else None
            if not isinstance(xobjects, pikepdf.Dictionary):
                continue
            for key in xobjects.keys():
                form = xobjects[key]
                if (isinstance(form, pikepdf.Stream) and _name_of(form, "/Subtype") == "/Form"
                        and tuple(form.objgen) not in seen):
                    seen.add(tuple(form.objgen))
                    found.append(form)
                    stack.append(form.get("/Resources"))
        except Exception:
            continue
    return found


def _drop_structure_text(pdf, source_path: str, password: str | None, touched: set) -> int:
    """Delete what each structure element of *pdf* (the redacted document)
    says in words of content a box removed; returns how many entries went.

    An element whose own content was removed loses /Alt, /ActualText, /E
    and /T. Every element above it loses /Alt, /ActualText and /E, which
    speak for an element and all that is under it; a title is its own.
    "Its own content" is what its /K names directly, and "removed" is:

      * marked content of a page — the sequence paints something else now
        than it does in the source, opened here from *source_path* for the
        comparison (_painted), on a page that had a box (*touched*, page
        indices);
      * marked content of a form XObject (a reference with /Stm) — the form
        is no longer anybody's content (the page has a redacted copy), and
        the copy, found among the forms the page paints by its
        /StructParents, paints something else in that sequence, or cannot
        be found;
      * an object reference — to an annotation that is on no page any more,
        or to an XObject that is no longer anybody's content.

    Whatever cannot be compared — a page that will not parse, a source that
    will not open — counts as removed. Runs on every redaction, by term or
    by box, before the stale references themselves are dropped.
    """
    try:
        root = pdf.Root.get("/StructTreeRoot")
    except Exception:
        return 0
    if not isinstance(root, pikepdf.Dictionary):
        return 0

    def ident(obj):
        try:
            return tuple(obj.objgen) if obj.is_indirect else None
        except Exception:
            return None

    # The elements, each with the chain of elements above it.
    elements, seen, stack = [], set(), [(root, ())]
    while stack:
        elem, above = stack.pop()
        try:
            if not isinstance(elem, pikepdf.Dictionary):
                continue
            key = ident(elem)
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            if _name_of(elem, "/Type") in ("/MCR", "/OBJR") or structure_stream(elem) is not None:
                continue
            if elem is not root and (key is None or key != ident(root)):
                elements.append((elem, above))
                above = above + (elem,)
            for kid in structure_kids(elem):
                if isinstance(kid, pikepdf.Dictionary):
                    stack.append((kid, above))
        except Exception:
            continue
    if not any(k in elem for elem, _above in elements for k in _STRUCTURE_TEXT):
        return 0

    page_of = {ident(page.obj): n for n, page in enumerate(pdf.pages)}
    listed = _listed_annotations(pdf)
    _holders, named, alive, _objects = structure_refs(pdf)
    before: dict = {}
    after: dict = {}
    source: list = []

    def changed_on_page(pno: int, mcid) -> bool:
        if pno not in touched:
            return False
        if pno not in after:
            after[pno] = _page_marks(pdf.pages[pno])
            try:
                if not source:
                    source.append(pikepdf.open(source_path, **(
                        {"password": password} if password else {})))
                before[pno] = _page_marks(source[0].pages[pno])
            except Exception:
                before[pno] = None
        if before[pno] is None or after[pno] is None:
            return True
        return before[pno].get(mcid) != after[pno].get(mcid)

    def changed_in_form(original, pno, mcid) -> bool:
        if ident(original) in alive:
            return False                    # still somebody's content, as it was
        number = original.get("/StructParents")
        pages = [pno] if pno is not None else sorted(touched)
        for n in pages:
            for copy in (_painted_forms(pdf.pages[n]) if 0 <= n < len(pdf.pages) else []):
                if number is None or copy.get("/StructParents") != number:
                    continue
                was, now = {}, {}
                if not (_painted(original, original.get("/Resources"), was)
                        and _painted(copy, copy.get("/Resources"), now)):
                    return True
                return was.get(mcid) != now.get(mcid)
        return True                         # no copy of it is painted: it went

    def own_content_removed(elem, above) -> bool:
        page = next((page_of.get(ident(e.get("/Pg"))) for e in (elem,) + above[::-1]
                     if isinstance(e.get("/Pg"), pikepdf.Dictionary)), None)
        for kid in structure_kids(elem):
            if isinstance(kid, int) and not isinstance(kid, bool):
                if page is not None and changed_on_page(page, kid):
                    return True
            elif isinstance(kid, pikepdf.Dictionary):
                where = page_of.get(ident(kid.get("/Pg")), page)
                target, mcid = kid.get("/Obj"), kid.get("/MCID")
                if isinstance(kid.get("/Stm"), pikepdf.Stream):
                    if changed_in_form(kid["/Stm"], where, mcid):
                        return True
                elif isinstance(mcid, int):
                    if where is not None and changed_on_page(where, mcid):
                        return True
                elif isinstance(target, pikepdf.Stream):
                    if ident(target) in named and ident(target) not in alive:
                        return True
                elif isinstance(target, pikepdf.Dictionary) and _is_annotation(target):
                    if ident(target) is not None and ident(target) not in listed:
                        return True
        return False

    dropped = 0
    try:
        for elem, above in elements:
            try:
                if not (any(k in elem for k in _STRUCTURE_TEXT)
                        or any(k in e for e in above for k in _STRUCTURE_TEXT[:3])):
                    continue
                if not own_content_removed(elem, above):
                    continue
                for holder, keys in [(elem, _STRUCTURE_TEXT)] + [(e, _STRUCTURE_TEXT[:3])
                                                                 for e in above]:
                    for key in keys:
                        if key in holder:
                            del holder[key]
                            dropped += 1
            except Exception:
                continue
    finally:
        for opened in source:
            opened.close()
    return dropped


def _drop_thumbnails(pdf) -> int:
    """Delete every page's /Thumb — a small picture of the page as it was —
    and return how many went. Every page of a redacted output, not only the
    ones a box was on: it is one entry, and nobody misses it."""
    removed = 0
    for obj in list(pdf.objects):
        try:
            if (isinstance(obj, pikepdf.Dictionary) and "/Thumb" in obj
                    and _name_of(obj, "/Type") == "/Page"):
                del obj["/Thumb"]
                removed += 1
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

        # -- annotations whose appearance draws a term leave their page ----
        try:
            lifted, undrawn = _lift_annotations_drawing(pdf, annotations, terms, case_sensitive)
            bump("annotations_removed", lifted)
            bump("annotations", undrawn)
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
                            page_indices: list[int], dpi: int = 150, *,
                            terms: list[str] | None = None, case_sensitive: bool = False,
                            boxes: dict | None = None) -> dict[int, int]:
    """Write *dst_path* as a copy of *src_path* with only *page_indices*
    (0-based) rasterized to a full-page image (D1 flatten fallback).

    Non-flagged pages are copied faithfully (insert_pdf preserves their text,
    annotations, and links); flagged pages become a single image at *dpi*
    (matching pdf_to_images), losing any extractable text layer while
    keeping page size and orientation. Only the named pages are touched —
    never the whole document. (Callers hold 1-based page numbers from the
    verification report; convert before calling.)

    A page is blacked out before it becomes a picture (#119). Once it is
    one, no check has text to find on it, so whatever is still drawn there
    is in the output for good — and a page is flattened exactly because
    something could not be removed from it. Every place the matcher still
    finds one of *terms* on the page (pdf_verify.term_boxes: the page's
    text, text a clip hides, and what a shown annotation or form field
    draws, which is the same reading of the page the picture is made from)
    and every rectangle in *boxes* ({page index: [fitz.Rect]}, the user's
    own) is filled black on the pixmap itself. Not on the page: there an
    annotation could be painted over the fill. Returns {page index: how
    many areas were filled} for the flattened pages.

    A residual the matcher cannot put anywhere on the page — bytes in a
    content stream that are no text it reads — is not covered by this.
    """
    flag = set(page_indices)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    filled: dict[int, int] = {}
    src = fitz.open(src_path)
    out = fitz.open()
    try:
        for i in range(len(src)):
            if i in flag:
                p = src[i]
                areas = list(term_boxes(p, terms, case_sensitive)) if terms else []
                areas += list((boxes or {}).get(i, []))
                pix = p.get_pixmap(matrix=mat, alpha=False)
                # Rectangles are in the page's own coordinates, before any
                # /Rotate; the picture is of the page as it is shown.
                to_picture = page_to_picture_matrix(p) * mat
                for area in areas:
                    spot = fitz.Rect(area) * to_picture
                    spot.normalize()
                    spot = fitz.IRect(math.floor(spot.x0) - 1, math.floor(spot.y0) - 1,
                                      math.ceil(spot.x1) + 1, math.ceil(spot.y1) + 1) & pix.irect
                    if not spot.is_empty:
                        pix.set_rect(spot, (0,) * pix.n)
                filled[i] = len(areas)
                np_ = out.new_page(width=p.rect.width, height=p.rect.height)
                np_.insert_image(np_.rect, pixmap=pix)
            else:
                out.insert_pdf(src, from_page=i, to_page=i)
        out.save(dst_path, garbage=4, deflate=True, clean=True)
    finally:
        out.close()
        src.close()
    return filled


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
#: The same for a page read with OCR (#128): read once, and again after its
#: boxes are applied, at each turn the term was found at.
_OCR_PASSES = 2
#: The kinds of page (_search_fact) a search reads with OCR when asked to:
#: those it has no text to read on, and those it has only a few words of.
_OCR_READS = ("no_text", "mostly_picture")


def _apart_from(boxes: list, hits: list) -> list:
    """The places OCR found (*boxes*) that lie apart from every place the
    search found in the page's own text (*hits*, term_hits'): where OCR
    reads the term in a page's typed words, the search has it already, and
    its box takes those characters and nothing else (#128)."""
    found = [fitz.Rect(box) for box, _trimmed in hits]
    return [box for box in boxes if not any(fitz.Rect(box).intersects(h) for h in found)]


def _box_key(rect) -> tuple:
    return tuple(round(v, 2) for v in rect)


#: Kinds of annotation that are not counted when they leave a page: a link
#: and a popup draw nothing of their own, a form field's widget is counted
#: as a field, and a redaction mark is this code's own.
_UNCOUNTED_KINDS = (fitz.PDF_ANNOT_LINK, fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_WIDGET,
                    fitz.PDF_ANNOT_REDACT)


def _annotations_on(page) -> tuple[set, int]:
    """(object numbers, how many with none) of the annotations on *page*
    that are counted when they leave it."""
    xrefs, unnumbered = set(), 0
    for xref, kind, _name in page.annot_xrefs():
        if kind in _UNCOUNTED_KINDS:
            continue
        if xref:
            xrefs.add(xref)
        else:
            unnumbered += 1
    return xrefs, unnumbered


def _fields_on(page) -> int:
    """How many form-field widgets *page* lists."""
    return sum(1 for _xref, kind, _name in page.annot_xrefs() if kind == fitz.PDF_ANNOT_WIDGET)


def _replies_to(page, gone: set) -> set:
    """The annotations on *page* that reply to one of *gone* (object
    numbers), or to a reply to one, however deep."""
    doc, replies, grew = page.parent, set(), True
    while grew:
        grew = False
        for xref, _kind, _name in page.annot_xrefs():
            if not xref or xref in gone or xref in replies:
                continue
            kind, value = doc.xref_get_key(xref, "IRT")
            if kind == "xref" and (int(value.split()[0]) in gone
                                   or int(value.split()[0]) in replies):
                replies.add(xref)
                grew = True
    return replies


def _take_off(page, xrefs) -> None:
    """Delete the annotations with these object numbers from *page*
    (PyMuPDF takes each one's popup with it)."""
    for xref in sorted(xrefs):
        try:
            annot = page.load_annot(xref)
        except Exception:
            continue                     # it went with the one it replied to
        if annot is not None:
            page.delete_annot(annot)


def _links_on(page) -> tuple[set, int]:
    """(object numbers, how many with none) of the links *page* lists."""
    xrefs, unnumbered = set(), 0
    for xref, kind, _name in page.annot_xrefs():
        if kind == fitz.PDF_ANNOT_LINK:
            if xref:
                xrefs.add(xref)
            else:
                unnumbered += 1
    return xrefs, unnumbered


def _apply(page, tally: dict | None = None, **options) -> None:
    """page.apply_redactions(**options), adding to *tally* what left the
    page with it: "annotations" and "links".

    MuPDF takes two kinds of annotation off the page itself while it
    redacts: every link a redaction rectangle overlaps, and a free-text
    note that any rectangle touches (probed on 1.27 and 2.1; every other
    kind stays). A free-text note is counted like one this code takes off,
    and whatever replied to it goes too and is counted — left behind, it
    would be a reply to nothing. A link is counted apart.
    """
    try:
        before, unnumbered = _annotations_on(page)
        links, unnumbered_links = _links_on(page)
    except Exception:
        before, unnumbered, links, unnumbered_links = set(), 0, set(), 0
    # Upright while it redacts: PyMuPDF draws the black fill with the
    # matrix that is wrong on a turned page whose page box does not start
    # at 0 0 (pdf_verify.upright). The text is removed in the right place
    # either way.
    with upright(page):
        page.apply_redactions(**options)
    if tally is None:
        tally = {"annotations": 0, "links": 0}
    try:
        after, unnumbered_after = _annotations_on(page)
        gone = before - after
        replies = _replies_to(page, gone) if gone else set()
        _take_off(page, replies)
        tally["annotations"] += len(gone) + len(replies) + max(0, unnumbered - unnumbered_after)
        links_after, unnumbered_links_after = _links_on(page)
        tally["links"] += (len(links - links_after)
                           + max(0, unnumbered_links - unnumbered_links_after))
    except Exception:
        return


def _redact_boxes(page, boxes: list, graphics: int | None = None, text: int | None = None,
                  tally: dict | None = None, covered: set | None = None) -> int:
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

    *graphics* and *text* are passed to apply_redactions when given: how
    line art under the boxes is treated (_line_art_mode), and whether text
    is removed at all (_redact_term_boxes keeps it in its second step).

    *covered*, when given, is added to: the object numbers of the fields
    whose widgets the boxes lay over, for a field that is shown by widgets
    below it (_still_shown); one that is its own widget is shown only there.

    *tally*, when given, is added to: "fields" by the form fields that left
    the page here (counted by how many fewer the page then lists: PyMuPDF
    1.27 does not delete a widget that has no object number, and that one
    is redact_pdf's to take off and count later), "annotations" by what
    _apply reports.
    """
    if not boxes:
        return 0
    for box in boxes:
        page.add_redact_annot(box, fill=(0, 0, 0))
    under = [wdg for wdg in page.widgets() if any(r.intersects(wdg.rect) for r in boxes)]
    fields = _fields_on(page) if under else 0
    if covered is not None:
        for wdg in under:
            try:
                kind, value = page.parent.xref_get_key(wdg.xref, "Parent")
                if kind == "xref":
                    covered.add(int(value.split()[0]))
            except Exception:
                continue
    for wdg in under:
        try:
            wdg.field_value = ""
            wdg.update()
        except Exception:
            pass
        page.delete_widget(wdg)
    if under and tally is not None:
        tally["fields"] += max(0, fields - _fields_on(page))
    options = {"images": fitz.PDF_REDACT_IMAGE_PIXELS}
    if graphics is not None:
        options["graphics"] = graphics
    if text is not None:
        options["text"] = text
    _apply(page, tally, **options)
    return len(boxes)


def _redact_term_boxes(page, hits: list, tally: dict | None = None) -> int:
    """Redact the term hits *hits* — (box, trimmed box) pairs from
    pdf_verify.term_hits — and return how many there were. *tally* is
    _redact_boxes'.

    In two steps, so that a term box takes only its own characters. A
    character is removed if its box overlaps a redaction rectangle at all,
    and the box of a word is as tall as its line: in type set close, the
    whole box takes letters, or words, from the lines above and below.
    First the text is removed with each box trimmed, top and bottom, to
    where the neighbours stop reaching into it; nothing is filled and no
    image or drawing is touched. Then the whole box is applied with the
    text kept, so the black fill, the pixels of a scan under it and the
    line art are treated exactly as they were with one step.

    Should a hit survive the trimmed step, the page's re-search finds it
    and redact_pdf applies the whole box to it with the text removed
    (_redact_boxes): trimming cannot cost a removal. A PyMuPDF too old to
    keep text does it in one step, as before.
    """
    if not hits:
        return 0
    whole = [box for box, _trimmed in hits]
    keep_text = getattr(fitz, "PDF_REDACT_TEXT_NONE", None)
    if keep_text is None:
        return _redact_boxes(page, whole, tally=tally)
    for _box, trimmed in hits:
        page.add_redact_annot(trimmed, fill=False)
    _apply(page, tally, images=fitz.PDF_REDACT_IMAGE_NONE,
           graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    return _redact_boxes(page, whole, text=keep_text, tally=tally)


def _under_keys(page, boxes: list) -> set:
    """What identifies each character now under *boxes* (chars_under): the
    character and where its text box is, to a tenth of a point."""
    if not boxes:
        return set()
    try:
        return {(c, tuple(round(v, 1) for v in b)) for c, b in chars_under(page, boxes)}
    except Exception:
        return set()


def _redact_chars_left_under(page, boxes: list, were_under: set,
                             tally: dict | None = None) -> None:
    """Remove the characters the first pass left under *boxes*, boxes the
    user drew (pdf_verify.chars_under: its centre in a box, or a fifth or
    more of its text box). *were_under* is _under_keys of the page before
    anything on it was redacted.

    MuPDF removes a character when a box reaches a tenth of the way into
    the letter as it measures the letter: by the font's own advance, not
    the width the PDF gives it, and by the font's own height. Where the
    two disagree — a font with wider /Widths than its letters, one with no
    metrics of its own — the text box can be half under a box that MuPDF
    finds clear of the letter. Such a character is boxed and removed here,
    text only: nothing is filled, no image or drawing is touched. First by
    the middle half of its text box, which takes nothing of a neighbour's;
    if it is still there, by the whole of it. Bounded like the term
    re-search; what cannot be removed is the verifier's to refuse.

    Only a character that was under a box to begin with, and is still where
    it was. Removing text can move the text after it on the line (MuPDF
    1.28, PyMuPDF 2.1, does not always keep its place; 1.27 does), and a
    letter that slides under a box that way was never the user's to
    remove: taking it would move the next one in. It is left, and the
    verifier, which goes by where things are, refuses the page.
    """
    if not boxes or not were_under:
        return
    tried: dict = {}
    for _ in range(_REDACT_PASSES - 1):
        marks = []
        for key in sorted(_under_keys(page, boxes) & were_under):
            b = key[1]
            attempt = tried.get(key, 0)
            tried[key] = attempt + 1
            w, h = b[2] - b[0], b[3] - b[1]
            if attempt > 1 or w <= 0 or h <= 0:
                continue                 # tried both ways, or nothing to box
            marks.append(fitz.Rect(b) if attempt else
                         fitz.Rect(b[0] + w / 4, b[1] + h / 4, b[2] - w / 4, b[3] - h / 4))
        if not marks:
            return
        for mark in marks:
            page.add_redact_annot(mark, fill=False)
        _apply(page, tally, images=fitz.PDF_REDACT_IMAGE_NONE,
               graphics=fitz.PDF_REDACT_LINE_ART_NONE)


def _line_art_mode(page, boxes: list) -> int | None:
    """How apply_redactions should treat line art under *boxes*, boxes the
    user drew: fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED, or None for the
    default.

    The default removes a drawing that lies wholly inside a box as MuPDF
    measures that, which for a curve is by its control points: a curve that
    runs inside the box with a control point outside it stays in the
    content, under the fill. "Remove if touched" takes that curve — and
    every other drawing whose bounding box overlaps the box, a table's
    frame or a long rule included. So it is used only when nothing else
    would go: when every drawing that reaches a box (its bounding box,
    widened by its line width) lies wholly inside one, by where its path
    runs. Otherwise the default stays, and what is left under a box fails
    the verifier's line-art check.

    A straight line that runs right through a box is not under it
    (pdf_verify.has_edge_inside), and needs no removing. It still keeps
    the default here: "remove if touched" would delete that line whole,
    from one end of the page to the other, to be rid of something else
    under the box. So a rule through a box verifies when nothing else is
    under it, and is never the price of removing what is.
    """
    touched = getattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED", None)
    if touched is None or not boxes:
        return None
    try:
        drawings = page.get_drawings()
    except Exception:
        return None
    reached = False
    for drawing in drawings:
        try:
            reach = fitz.Rect(drawing["rect"])
            margin = float(drawing.get("width") or 0) + 1
            reach = (reach.x0 - margin, reach.y0 - margin, reach.x1 + margin, reach.y1 + margin)
            if not any(reach[0] < b.x1 and reach[2] > b.x0 and reach[1] < b.y1 and reach[3] > b.y0
                       for b in boxes):
                continue
            points = [p for line in path_lines(drawing) for p in line]
            if not points or not any(all(b.x0 <= x <= b.x1 and b.y0 <= y <= b.y1
                                         for x, y in points) for b in boxes):
                return None
            reached = True
        except Exception:
            return None
    return touched if reached else None


def _annotation_area(page, xref: int, to_page=None):
    """The rectangle of the annotation with object number *xref*, read
    from its own /Rect and put into the coordinates boxes are given in
    (the page before any /Rotate) with the page's own matrix, as the
    verifier reads it: pdf_verify.pdf_to_page_matrix, or *to_page* when
    the caller has it already. None if it has no plain array there."""
    try:
        what, value = page.parent.xref_get_key(xref, "Rect")
        if what != "array":
            return None
        area = fitz.Rect(*(float(v) for v in value.strip("[]").split()))
        area.normalize()
        area = area * (to_page if to_page is not None else pdf_to_page_matrix(page))
        area.normalize()
        return area
    except Exception:
        return None


def _annotations_under(page, boxes: list) -> int:
    """Take off *page* every annotation whose rectangle overlaps one of
    *boxes* — boxes the user drew — and return how many went.

    apply_redactions() leaves annotations alone (its own marks and links
    aside), and an annotation is painted above the page: a stamp, a picture
    of a signature or a note's icon lying over a box is painted above the
    box. With each goes its popup and whatever replies to it, however deep
    (PyMuPDF takes the popup and the first level of replies itself).
    Widgets are _redact_boxes' to deal with; a popup is not judged by its
    own rectangle, it draws nothing on the page. Links are MuPDF's to
    remove, and _links_under's after it.

    Called before the boxes are applied: MuPDF takes a free-text note that
    a box touches off the page itself, and then nothing here could find
    what replied to it. And once more after: an annotation that has no
    object number is placed by PyMuPDF's own measure of it, and for one
    with nothing but a rectangle (a highlight with no quads, an ink note
    with no strokes) that measure is no rectangle at all until the page
    has been redrawn. One that has an object number is placed by its own
    /Rect (_annotation_area), before and after alike.
    """
    if not boxes:
        return 0
    doomed, to_page = set(), pdf_to_page_matrix(page)
    for xref, kind, _name in page.annot_xrefs():
        if not xref or kind in _UNCOUNTED_KINDS:
            continue
        area = _annotation_area(page, xref, to_page)
        if area is None:
            try:
                area = page.load_annot(xref).rect
            except Exception:
                continue
        if any(box.intersects(area) for box in boxes):
            doomed.add(xref)
    if doomed:
        doomed |= _replies_to(page, doomed)
        _take_off(page, doomed)
    # One written into the page's /Annots array itself has no object number,
    # and page.annots() leaves it out. Walking from the first annotation
    # reaches it; it is judged the same, and nothing can reply to it.
    direct = 0
    while direct < 10000:
        target, annot = None, page.first_annot
        while annot is not None:
            if (annot.xref == 0 and annot.type[0] not in (fitz.PDF_ANNOT_POPUP,
                                                           fitz.PDF_ANNOT_WIDGET)
                    and any(box.intersects(annot.rect) for box in boxes)):
                target = annot
                break
            annot = annot.next
        if target is None:
            break
        page.delete_annot(target)
        direct += 1
    return len(doomed) + direct


def _links_under(page, boxes: list) -> int:
    """Take off *page* any link whose rectangle still overlaps one of
    *boxes*, boxes the user drew, after they have been applied; returns
    how many went.

    MuPDF removes the links a redaction rectangle overlaps itself, all at
    once (probed on 1.27 and 2.1: wholly under, half under, a corner
    under); this is for one it should leave. After, not before: PyMuPDF
    reads the page's whole list of links again after each one it deletes,
    and asking it for the links (page.get_links()) goes through that list
    once per link, so on a page with thousands of links, a box over a
    few hundred of them ran for minutes. Here the list is read once, and a
    link's rectangle is put into the boxes' coordinates with the page's
    own matrix, as the verifier does it (get_links() gives it on the page
    as turned, and on a turned page links nowhere near a box were taken).
    """
    removed = 0
    if not boxes:
        return removed
    to_page = pdf_to_page_matrix(page)
    for xref, kind, name in page.annot_xrefs():
        if kind != fitz.PDF_ANNOT_LINK or not xref:
            continue
        try:
            area = _annotation_area(page, xref, to_page)
            if area is not None and any(box.intersects(area) for box in boxes):
                page.delete_link({"xref": xref, "id": name})
                removed += 1
        except Exception:
            continue
    return removed


def _unlisted_annotations_under(pdf, boxes: dict) -> tuple[int, int, int]:
    """Take out of each page's /Annots array every annotation that is
    written into the array itself — not an object of its own — and overlaps
    one of the user's boxes on that page; returns (annotations, form
    fields, links) that went. *boxes* is {page index: [(x0, y0, x1, y1)]}
    in the page's own PDF coordinates.

    The pikepdf side of _annotations_under, for what PyMuPDF does not take
    off the page: 1.27 does not delete a form field's widget that has no
    object number when asked to (2.1 does), and it stayed under the box.
    Nothing can refer to such a dictionary, so nothing goes with it; a
    popup is not judged by its own rectangle here either.
    """
    removed, fields, links = 0, 0, 0
    for pno, rects in boxes.items():
        try:
            page = pdf.pages[pno].obj
            annots = page.get("/Annots")
            if not isinstance(annots, pikepdf.Array):
                continue
            keep = []
            for entry in annots:
                under = False
                try:
                    if (isinstance(entry, pikepdf.Dictionary) and not entry.is_indirect
                            and _name_of(entry, "/Subtype") != "/Popup"):
                        x0, y0, x1, y1 = (float(v) for v in entry["/Rect"])
                        x0, x1, y0, y1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
                        under = any(x0 < b[2] and x1 > b[0] and y0 < b[3] and y1 > b[1]
                                    for b in rects)
                except Exception:
                    under = False
                if not under:
                    keep.append(entry)
                elif _name_of(entry, "/Subtype") == "/Widget":
                    fields += 1
                elif _name_of(entry, "/Subtype") == "/Link":
                    links += 1
                else:
                    removed += 1
            if len(keep) != len(annots):
                page["/Annots"] = pikepdf.Array(keep)
        except Exception:
            continue
    return removed, fields, links


def _drop_unshown_fields(pdf) -> int:
    """Take out of the form's field tree every field whose kids have all
    gone; returns how many went.

    A field shown by a widget below it (a kid) keeps its value on itself.
    When a box takes that widget off the page, PyMuPDF 1.27 takes it out of
    the field's /Kids and leaves the field in /AcroForm /Fields, value and
    all, shown nowhere (2.1 takes the field out too): a box over a filled
    field verified with what it said still in the file. A field whose /Kids
    is empty is removed from the tree, from its parent's /Kids or from
    /Fields; a parent left with no kids by that goes too. The calculation
    order (/CO) then keeps only fields that are still in the tree. Run only
    after a field has left a page (fields_removed), so a form is otherwise
    left as it was.
    """
    try:
        form = pdf.Root.get("/AcroForm")
        fields = form.get("/Fields") if isinstance(form, pikepdf.Dictionary) else None
    except Exception:
        return 0
    if not isinstance(fields, pikepdf.Array):
        return 0
    gone: set = set()

    def pruned(array, depth: int, seen: set):
        keep, changed = [], False
        for fld in array:
            try:
                if isinstance(fld, pikepdf.Dictionary) and "/Kids" in fld:
                    key = tuple(fld.objgen) if fld.is_indirect else None
                    kids = fld["/Kids"]
                    if isinstance(kids, pikepdf.Array) and depth < 50 and key not in seen:
                        new = pruned(kids, depth + 1, seen | {key})
                        if new is not None:
                            fld["/Kids"] = kids = new
                    if isinstance(kids, pikepdf.Array) and len(kids) == 0:
                        gone.add(key)
                        changed = True
                        continue
            except Exception:
                pass
            keep.append(fld)
        return pikepdf.Array(keep) if changed else None

    new = pruned(fields, 0, set())
    if new is not None:
        form["/Fields"] = fields = new
    # The calculation order lists fields too, and kept one alive after it
    # had left the tree (here, or by PyMuPDF 2.1, which takes a field out of
    # its parent's kids itself). Only fields still in the tree stay in it.
    try:
        order = form.get("/CO")
        if isinstance(order, pikepdf.Array):
            in_tree = {tuple(f.objgen) for f in _with_descendants(list(fields))
                       if isinstance(f, pikepdf.Dictionary) and f.is_indirect}
            keep = [f for f in order if isinstance(f, pikepdf.Dictionary)
                    and f.is_indirect and tuple(f.objgen) in in_tree]
            if len(keep) != len(order):
                form["/CO"] = pikepdf.Array(keep)
    except Exception:
        pass
    return len(gone)


#: A page is mostly a picture when pictures are drawn on MOSTLY_PICTURE of
#: it or more and the search reads FEW_CHARACTERS characters of text on it
#: or fewer, spaces not counted (_search_fact, #128). Measured on 2,111
#: pages of 1,133 real files: of the 30 pages pictures cover 30% or more of
#: that have text, 12 are such pages — scans and photos with a title, a
#: caption, labels or nothing the search can read at all — and the rest
#: are pages of text with a picture.
MOSTLY_PICTURE = 0.5
FEW_CHARACTERS = 200
_PICTURE_GRID = 40


def _picture_share(page, boxes: list) -> float:
    """How much of *page* the rectangles *boxes* (the pictures it draws, in
    page space) cover together: the share of a 40 x 40 grid of points on
    the page that lie in one of them."""
    width, height = abs(page.rect.width), abs(page.rect.height)
    if page.rotation % 180:
        width, height = height, width
    if not width or not height:
        return 0.0
    cells = bytearray(_PICTURE_GRID * _PICTURE_GRID)
    step_x, step_y = width / _PICTURE_GRID, height / _PICTURE_GRID
    for box in boxes:
        x0 = max(0, math.ceil(box.x0 / step_x - 0.5))
        x1 = min(_PICTURE_GRID - 1, math.floor(box.x1 / step_x - 0.5))
        y0 = max(0, math.ceil(box.y0 / step_y - 0.5))
        y1 = min(_PICTURE_GRID - 1, math.floor(box.y1 / step_y - 0.5))
        for row in range(y0, y1 + 1):
            start = row * _PICTURE_GRID
            cells[start + x0:start + x1 + 1] = b"\x01" * max(0, x1 - x0 + 1)
    return sum(cells) / len(cells)


def _search_fact(page) -> str | None:
    """What a search cannot read of *page*, if anything:

    ``no_text``
        It draws something and none of it is text (a scan with no text
        layer, type drawn as outlines).
    ``text_layer``
        It shows a picture and most of its text is drawn in the invisible
        mode, the way OCR lays what it read over a scan. The search reads
        that layer; a word the layer got wrong is still in the picture.
    ``mostly_picture``
        Pictures cover most of it (MOSTLY_PICTURE) and the text the search
        reads on it is short (FEW_CHARACTERS): a scan with a typed page or
        Bates number, a stamp, a "Scanned with" line or a note on it, a
        photo with a caption (#128). The search reads only those few words.

    From MuPDF's own account of what the page draws, in order (the same
    reading the matcher has: an annotation's text, and a form field's, is
    the page's text here too). An empty page is none of them; nor is
    ordinary type beside a picture with a hidden word somewhere. A fact for
    RedactResult.notes, never a check: a page that cannot be read this way
    is simply not noted.
    """
    try:
        drawn = page.get_bboxlog()
        kinds = {entry[0] for entry in drawn}
        if not any(kind.endswith("text") for kind in kinds):
            return "no_text" if drawn else None
        pictures = [fitz.Rect(box) for kind, box in drawn if kind in ("fill-image", "fill-imgmask")]
        if not pictures:
            return None
        if "ignore-text" in kinds and "fill-image" in kinds:
            hidden = shown = 0
            for span in page.get_texttrace():
                letters = sum(1 for ch in span.get("chars", ()) if ch[0] > 32)
                if span.get("type") == 3:            # the invisible mode
                    hidden += letters
                else:
                    shown += letters
            if hidden > shown:
                return "text_layer"
        text = page.get_text("text", flags=_MATCH_FLAGS)
        if sum(1 for ch in text if not ch.isspace()) > FEW_CHARACTERS:
            return None
        return "mostly_picture" if _picture_share(page, pictures) >= MOSTLY_PICTURE else None
    except Exception:
        return None


def _page_ranges(numbers) -> str:
    """Page numbers as text, runs joined: [1, 2, 3, 7] -> "1-3, 7"."""
    runs: list[list[int]] = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def _still_shown(doc, fields: set) -> list[int]:
    """The pages, counted from 1, on which one of *fields* — fields that
    had a widget under a box drawn by hand — is still shown by a widget.

    A box takes the widget under it and nothing more: a field shown in
    another place keeps its value and goes on showing it there (RedactResult
    .notes, "field_still_shown"). By the field's /Kids as they are after the
    boxes, read through each page's own list of annotations.
    """
    if not fields:
        return []
    kids: set = set()
    for xref in fields:
        try:
            kind, value = doc.xref_get_key(xref, "Kids")
            if kind == "xref":
                value = doc.xref_object(int(value.split()[0]), compressed=True)
            kids.update(int(n) for n in re.findall(r"(\d+) \d+ R", value))
        except Exception:
            continue
    pages = []
    for pno in range(len(doc)):
        try:
            if any(xref in kids for xref, kind, _name in doc[pno].annot_xrefs()
                   if kind == fitz.PDF_ANNOT_WIDGET):
                pages.append(pno + 1)
        except Exception:
            continue
    return pages


def _notes(seen: dict | None, attached: list, flattened: list, flattened_areas: dict,
           boxed_only: list, still_shown: list = (), ocr_not_ready: bool = False) -> list[dict]:
    """RedactResult.notes for one run (see the field for each kind).
    *seen* is what redact_pages told of a search's pages, None for a run
    with no search; *attached* is pdf_analyze.embedded_files of the output;
    *boxed_only* the 0-based pages the boxes were on in a run by boxes
    alone; *ocr_not_ready*, that OCR was asked for and is not set up."""
    notes: list[dict] = []
    if seen is not None:
        if seen["no_text"]:
            notes.append({"kind": "no_text", "pages": sorted(p + 1 for p in seen["no_text"])})
        if seen.get("mostly_picture"):
            notes.append({"kind": "mostly_picture",
                          "pages": sorted(p + 1 for p in seen["mostly_picture"])})
        if ocr_not_ready and (seen["no_text"] or seen.get("mostly_picture")):
            notes.append({"kind": "ocr_not_ready"})
        ocr = seen.get("ocr") or {}
        read = sorted(p for p, r in ocr.items() if r["status"] == "read")
        if read:
            notes.append({"kind": "ocr", "pages": [p + 1 for p in read],
                          "hits": {p + 1: ocr[p]["places"] for p in read}})
        unread = sorted(p for p, r in ocr.items() if r["status"] != "read")
        if unread:
            notes.append({"kind": "ocr_unread", "pages": [p + 1 for p in unread],
                          "too_large": [p + 1 for p in unread if ocr[p]["status"] == "too_large"]})
        if seen["text_layer"]:
            layer = sorted(seen["text_layer"].items())
            notes.append({"kind": "text_layer", "pages": [p + 1 for p, _n in layer],
                          "hits": {p + 1: n for p, n in layer}})
        if attached:
            notes.append({"kind": "attachments", "names": [name for name, _packed in attached],
                          "compressed": [name for name, packed in attached if packed]})
    if flattened:
        notes.append({"kind": "flattened", "pages": list(flattened),
                      "areas": dict(flattened_areas)})
    if still_shown:
        notes.append({"kind": "field_still_shown", "pages": list(still_shown)})
    if boxed_only:
        notes.append({"kind": "boxes_only", "pages": [p + 1 for p in boxed_only]})
    return notes


def _unread_in_words(seen: dict, ocr_not_ready: bool) -> str:
    """For a search that found nothing at all: the pages it had no text to
    read on, and what OCR made of those it read (#128). OCR can miss a
    word, so a page it read is never said to be searched for certain."""
    def pages(ps: list) -> tuple[str, bool]:
        return _page_ranges(p + 1 for p in ps), len(ps) > 1

    words = ""
    if seen["no_text"]:
        where, many = pages(seen["no_text"])
        words += (f" {'Pages' if many else 'Page'} {where} {'have' if many else 'has'} no text: "
                  f"a search cannot read words in a scan or a picture.")
    if seen.get("mostly_picture"):
        where, many = pages(seen["mostly_picture"])
        words += (f" {'Pages' if many else 'Page'} {where} {'are' if many else 'is'} mostly "
                  f"{'pictures' if many else 'a picture'} with a few words of text: the search "
                  f"read only those words.")
    if ocr_not_ready and (seen["no_text"] or seen.get("mostly_picture")):
        words += " OCR, which can read the words in a picture, is not set up."
    ocr = seen.get("ocr") or {}
    read = sorted(p for p, r in ocr.items() if r["status"] == "read")
    unread = sorted(p for p, r in ocr.items() if r["status"] == "unread")
    large = sorted(p for p, r in ocr.items() if r["status"] == "too_large")
    if read:
        where, many = pages(read)
        words += (f" {'Pages' if many else 'Page'} {where} {'were' if many else 'was'} read by "
                  f"OCR, which can miss words: check {'them' if many else 'it'} by eye.")
    if unread:
        where, many = pages(unread)
        words += (f" OCR found no words it could read on {'pages' if many else 'page'} {where}: "
                  f"check {'them' if many else 'it'} by eye.")
    if large:
        where, many = pages(large)
        words += (f" {'Pages' if many else 'Page'} {where} {'are' if many else 'is'} too large "
                  f"to read with OCR.")
    return words


def rects_from_page_pictures(input_path: str, rects, password: str | None = None):
    """Boxes drawn on the pictures of pages, put into the space redact_pdf
    and verify_redaction take them in. What the redact slot does with the
    boxes the app sends, before it hands them to redact_pdf (#119, R4).

    The app's Draw-boxes mode shows each page as a picture of the page as it
    is shown — turned by its /Rotate, cut to its crop box — and sends a box
    as it lies on that picture, in points from the picture's top left
    (getPageImages; RedactPage's boxesToRects). redact_pdf works in
    PyMuPDF's page space: the page before any /Rotate, from the top left of
    its crop box, where search_for and get_text put a word. The two are the
    same on an upright page; on a turned one a box was applied somewhere
    else, the word under it stayed, and the run verified. A box is put back
    with the inverse of pdf_verify.page_to_picture_matrix, which takes a
    rectangle onto the picture as MuPDF draws it (PyMuPDF's own
    derotation matrix is wrong on a turned page with a user unit, or with a
    crop box that is not inside its media box).

    *rects* is the list redact_pdf takes ({"page": int, "x0", "y0", "x1",
    "y1"}); each entry comes back with its four corners in page space and
    every other key as it was. An entry that cannot be read as a box on a
    page of the file — malformed, for a page the file does not have, empty
    or inverted — is handed on as it came: redact_pdf skips it and the
    verifier fails the run for it, as before. None and [] come back as
    they are. A file that needs a password it is not given raises
    ValueError("Wrong password."), as redact_pdf does.
    """
    if not rects:
        return rects
    doc = fitz.open(input_path)
    try:
        if doc.needs_pass and not (password and doc.authenticate(password)):
            raise ValueError("Wrong password.")
        found = []
        for entry in rects:
            try:
                pno = int(entry["page"])
                box = fitz.Rect(float(entry["x0"]), float(entry["y0"]),
                                float(entry["x1"]), float(entry["y1"]))
                if not 0 <= pno < len(doc) or not box.is_valid or box.is_empty:
                    raise ValueError
                page = doc[pno]
            except Exception:
                found.append(entry)
                continue
            if not page.rotation:
                found.append(entry)          # upright: the picture is the page
                continue
            box = box * ~page_to_picture_matrix(page)
            box.normalize()
            found.append({**entry, "x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1})
        return found
    finally:
        doc.close()


#: The one version of PyMuPDF redaction is supported on (#119, R4).
SUPPORTED_PYMUPDF = "1.27.2.3"


def _library_version() -> str:
    """The version of PyMuPDF in use, as it gives it (fitz.VersionBind)."""
    return str(getattr(fitz, "VersionBind", "") or "")


def _check_library() -> list[dict]:
    """Refuse to redact on a version of PyMuPDF that destroys what it should
    leave; return the notes a version that can be used with care adds.

    PyMuPDF 1.28 removes other text from a page when it redacts: on real
    files, thousands of words a page had nothing to do with the term, and
    some such runs still verified. RedactionNotPossibleError, before
    anything is opened. 2.x (MuPDF 1.28.6 and later) does not always keep
    the text after a removed word in place: the rest of the line can move
    along it. It runs, and the result says so (RedactResult.notes,
    "text_may_move"). Other versions run as they are.
    """
    version = _library_version()
    parts = version.split(".")
    if parts[:2] == ["1", "28"]:
        raise RedactionNotPossibleError(
            f"Redaction is turned off: the PDF library installed here (PyMuPDF {version}) "
            f"removes other text from a page when it redacts. Install PyMuPDF "
            f"{SUPPORTED_PYMUPDF} to redact. Nothing was saved.")
    if parts[0] == "2":
        return [{"kind": "text_may_move", "version": version}]
    return []


def _let_go() -> None:
    """Release what PyMuPDF still holds of documents that have been closed.

    A page and an annotation PyMuPDF has loaded keep each other alive (the
    page's record of its annotations, the annotation's parent), and with
    them the document, after the document is closed — until Python's cycle
    collector runs. On Windows a file that is open cannot be replaced or
    renamed: redacting in place a file in which a form field is shown in
    two places failed, because the source was still open when the redacted
    copy was put in its place. Called before that, and after a refusal.
    """
    gc.collect()


# ── What a finished redaction says (#119, R4) ────────────────────────

#: surface_counts key -> (one, many): what else a run removed, in words. In
#: the order the window lists them; page_content is the headline.
_REMOVED_WORDS = {
    "annotations_removed": ("note, stamp or other mark taken off a page",
                            "notes, stamps or other marks taken off the pages"),
    "fields_removed": ("form field taken off a page", "form fields taken off the pages"),
    "links_removed": ("link taken off a page", "links taken off the pages"),
    "thumbnails": ("small preview picture of a page", "small preview pictures of pages"),
    "structure_text": ("description of removed content, the kind screen readers read aloud",
                       "descriptions of removed content, the kind screen readers read aloud"),
    "structure_refs": ("hidden copy of removed content that the file still pointed to",
                       "hidden copies of removed content that the file still pointed to"),
    "docinfo": ("entry in the document's details, such as the title or author",
                "entries in the document's details, such as the title or author"),
    "xmp": ("set of hidden document details (XMP)", "sets of hidden document details (XMP)"),
    "metadata": ("other block of hidden details", "other blocks of hidden details"),
    "bookmarks": ("bookmark title", "bookmark titles"),
    "annotations": ("piece of text in a note or comment", "pieces of text in notes or comments"),
    "form_fields": ("value typed into a form field", "values typed into form fields"),
    "field_text": ("piece of text about a form field, such as its tooltip",
                   "pieces of text about form fields, such as their tooltips"),
    "field_names": ("form field name", "form field names"),
    "rich_text": ("piece of formatted text in a note or field",
                  "pieces of formatted text in notes or fields"),
    "links": ("web address", "web addresses"),
    "embedded_files": ("attached file", "attached files"),
    "structure": ("piece of text in the file's structure",
                  "pieces of text in the file's structure"),
    "layers": ("layer name", "layer names"),
    "piece_info": ("block of data left by another program",
                   "blocks of data left by other programs"),
    "scripts": ("script", "scripts"),
    "destinations": ("named place in the file", "named places in the file"),
    "pdftex": ("record left by the program that made the file",
               "records left by the program that made the file"),
    "page_labels": ("page label", "page labels"),
    "xfa": ("set of XFA form data", "sets of XFA form data"),
    "signatures": ("digital signature", "digital signatures"),
}


def _how_many(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _names_in_words(names: list, cap: int = 5) -> str:
    names = [str(n) for n in names]
    if len(names) > cap:
        return ", ".join(names[:cap]) + f" and {len(names) - cap:,} more"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _total(by_page) -> int:
    try:
        return sum(int(v) for v in dict(by_page or {}).values())
    except Exception:
        return 0


def _note_in_words(note: dict) -> str | None:
    """One note of RedactResult.notes as a sentence; None for a kind this
    does not know (it is not shown as code)."""
    kind, pages = note.get("kind"), note.get("pages") or []
    many = len(set(pages)) > 1
    where = pages_in_words(pages)
    Where = where[:1].upper() + where[1:]
    if kind == "no_text":
        return (f"{Where} {'have' if many else 'has'} no text to search. "
                f"{'They may be scans or pictures' if many else 'It may be a scan or a picture'}"
                f", so any words in {'them' if many else 'it'} were not searched.")
    if kind == "text_layer":
        hits = _total(note.get("hits"))
        found = ("found no matches" if hits == 0 else
                 f"found {_how_many(hits, 'match', 'matches')}")
        return (f"{Where} {'are scans' if many else 'is a scan'} with a hidden text layer. "
                f"The search read that layer and {found} there. If the layer misread a word, "
                f"that word is still in the picture.")
    if kind == "attachments":
        names, packed = list(note.get("names") or []), list(note.get("compressed") or [])
        if not names:
            return None
        line = (f"This file has {_how_many(len(names), 'attached file', 'attached files')}: "
                f"{_names_in_words(names)}. Attached files were searched as they are stored, "
                f"and were not opened.")
        if packed:
            who = (_names_in_words(packed) if len(packed) <= 5
                   else f"{len(packed):,} of them")
            several = len(packed) > 1
            line += (f" {who} {'are' if several else 'is'} packed (like a zip or Word file), "
                     f"so nothing inside {'them' if several else 'it'} could be searched.")
        return line
    if kind == "flattened":
        areas = _total(note.get("areas"))
        # No area on the picture when what was found had already been
        # removed (and filled black) from the page, or lay off it.
        blacked = (f", with {_how_many(areas, 'area', 'areas')} blacked out on "
                   f"{'them' if many else 'it'}" if areas else "")
        return (f"{Where} {'were' if many else 'was'} turned into "
                f"{'pictures' if many else 'a picture'}{blacked}"
                f". {'Their' if many else 'Its'} text can no longer be selected or searched.")
    if kind == "field_still_shown":
        return (f"A form field under one of your boxes is also shown elsewhere, on {where}. "
                f"It is still shown there, and anything filled into it is still in the file.")
    if kind == "boxes_only":
        return ("Only the boxes you drew were redacted. The words under them were not searched "
                "for anywhere else in the file.")
    if kind == "text_may_move":
        return (f"This run used version {note.get('version', '')} of the PDF library. With "
                f"this version, text after a removed word can shift along its line.")
    # What OCR did (#128). It can miss words: every page it read is named,
    # and nothing it did is said for certain.
    if kind == "mostly_picture":
        return (f"{Where} {'are mostly pictures' if many else 'is mostly a picture'} with a few "
                f"words of text. The search read only those words, so any words in "
                f"{'the pictures' if many else 'the picture'} were not searched.")
    if kind == "ocr_not_ready":
        return ("OCR, which can read the words in a picture, is not set up, so those pages were "
                "not read.")
    if kind == "ocr":
        every = pages_in_words(pages, cap=len(pages) + 1)
        hits = _total(note.get("hits"))
        found = (f"found {_how_many(hits, 'place', 'places')} to black out" if hits else
                 "found nothing to black out")
        return (f"OCR read {every} and {found} {'on them' if many else 'there'}. OCR can miss "
                f"words: check {'them' if many else 'it'} by eye.")
    if kind == "ocr_unread":
        large = sorted({int(p) for p in note.get("too_large") or []})
        unread = sorted({int(p) for p in pages} - set(large))
        lines = []
        if unread:
            some = len(unread) > 1
            lines.append(f"OCR found no words it could read on {pages_in_words(unread, cap=len(unread) + 1)}. "
                         f"{'They may be photos or drawings' if some else 'It may be a photo or a drawing'}, "
                         f"or {'their' if some else 'its'} words may not be in English: check "
                         f"{'them' if some else 'it'} by eye.")
        if large:
            some = len(large) > 1
            where_large = pages_in_words(large, cap=len(large) + 1)
            lines.append(f"{where_large[:1].upper()}{where_large[1:]} {'are' if some else 'is'} too "
                         f"large to read with OCR, and {'were' if some else 'was'} not read: check "
                         f"{'them' if some else 'it'} by eye.")
        return " ".join(lines) or None
    return None


def redaction_summary(result: "RedactResult") -> dict:
    """A finished redaction in plain words, for the window (#119, R4).

    ``headline``
        How many places were redacted, on how many pages.
    ``checked``
        That the new file was checked before it was saved.
    ``removed``
        What else was removed, one line per kind, with how many; a count
        can be 1 or 32,768, and large numbers are grouped by thousands.
    ``notes``
        What to know about what the run did not look at, one sentence per
        note of RedactResult.notes, in their order.

    Like the words of a refusal (pdf_verify.redaction_refusal_detail), made
    here so that they are in one place and tested; the window shows the
    lines it is given.
    """
    count, pages = int(result.redaction_count or 0), int(result.pages_affected or 0)
    headline = (f"Redacted {_how_many(count, 'place', 'places')} on "
                f"{_how_many(pages, 'page', 'pages')}." if count else
                "Nothing on the pages matched. What you searched for was removed from other "
                "parts of the file.")
    counts = dict(result.surface_counts or {})
    removed = [_how_many(int(counts[key]), *words) for key, words in _REMOVED_WORDS.items()
               if counts.get(key)]
    removed += [_how_many(int(n), "other item", "other items") + f" ({key.replace('_', ' ')})"
                for key, n in counts.items()
                if n and key not in ("page_content", "ocr_page_content")
                and key not in _REMOVED_WORDS]
    notes = [line for line in map(_note_in_words, result.notes or []) if line]
    checked = "Checked before saving: what you asked to remove is gone from the new file."
    kinds = {n.get("kind") for n in result.notes or []}
    if kinds & {"ocr", "ocr_unread"}:
        # What OCR read is a picture: the search checks the file, and OCR
        # reads again the pages it found places on. Nothing more is said.
        read = next((n for n in result.notes if n.get("kind") == "ocr"), {})
        try:
            found = sorted(int(p) for p, n in dict(read.get("hits") or {}).items() if int(n))
        except (TypeError, ValueError):
            found = []
        checked = ("Checked before saving: a search of the new file no longer finds what you "
                   "asked to remove"
                   + (f", and OCR no longer reads it on {pages_in_words(found, cap=len(found) + 1)}"
                      if found else "") + ".")
    return {"headline": headline, "checked": checked, "removed": removed, "notes": notes}


def redact_result_payload(result: "RedactResult") -> dict:
    """What the redact slot hands the window for a finished run: the result,
    as plain data, with its plain-words summary (redaction_summary) under
    ``summary``."""
    return {**asdict(result), "summary": redaction_summary(result)}


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
    if failed and all(c.id == "redaction.ocr_outside" for c in failed):
        # Damage, not a leak: the page would have changed outside the
        # places OCR found (#128). As redaction_refusal_detail says it.
        return DAMAGE_HEADLINE
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
    ocr: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    on_status: Callable[[str], None] | None = None,
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
        The text of a match is removed with its box trimmed to its own
        characters, so that words on the lines above and below are not
        taken with it; the fill is the whole box (_redact_term_boxes).
      * rects: user-drawn boxes, each {"page": int (0-based), "x0", "y0",
        "x1", "y1": float} in PyMuPDF's point coordinates: from the page's
        top left, on the page as it is before any /Rotate. A box takes
        everything it covers. Every character under it goes
        (pdf_verify.chars_under: its centre in the box, or a fifth or more
        of its text box; what MuPDF's own pass leaves of those is removed
        by _redact_chars_left_under). Every annotation that overlaps it
        leaves the page, with its replies (_annotations_under), and so
        does a form field. Line art under it is removed where that takes
        nothing else with it (_line_art_mode); a straight line that runs
        right through the box is left, and hides nothing.

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

    With ocr=True, a search also reads with OCR every page it has no text
    to read on (_search_fact's "no_text": a scan with no text layer, type
    drawn as outlines) or only a few words of ("mostly_picture": a scan
    with a typed page number, a stamp or a note on it), and redacts the
    term where OCR finds it (#128). Where OCR reads the term in such a
    page's own words, the search has found it there too, and that place is
    left to the search.
    pdf_ocr.read_page draws the page as shown and reads it, at other turns
    too when what it reads does not look like words, and finds the term on
    what it read with the matcher above. Each place found is a box, grown a
    little, that goes the way of a box drawn by hand: whatever lies over it
    and under it goes, and the pixels of a picture under it are blanked.
    The page is read again once its boxes are applied, and what is found
    then is redacted too. A term found by OCR is redacted on that page
    alone; each page is read on its own. Such places are counted as
    ``ocr_page_content``, apart from ``page_content``. The verifier has
    checks of its own for them (pdf_verify._ocr_checks): what the page
    shows, what its pictures hold, and what OCR reads on it now. Boxes
    alone never use OCR, nor does a page with a text layer. OCR needs its
    language file (pdf_ocr.ocr_status); without it the run goes ahead as
    without OCR, and says so. Off by default. Before a page is read with
    OCR, *on_status* is given what is happening in words ("Reading page 3
    of 12 with OCR"), for the progress line; *on_progress* still counts the
    pages. A cancel stops the run before the next reading.

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
    breakdown, the verification report dict, any flattened_pages, and
    ``notes``: what the run did not look at, as plain facts for the app to
    word (pages with no text, pages whose text is a layer over a picture,
    attachments, pages turned into pictures, a run by boxes alone; see
    RedactResult). A search that finds nothing at all has no result to
    carry a note, so its ValueError names the pages it could not read.
    """
    scrub_terms = [t for t in (search_terms or []) if t and t.strip()]
    if not scrub_terms and not rects:
        raise ValueError("Provide search_terms and/or rects to redact.")
    # A version of the PDF library that removes what it should leave is
    # refused before anything is opened (#119, R4).
    library_notes = _check_library()

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

    #: {page index: the user's boxes on it, in the page's own PDF coordinates}:
    #: for the one thing under a box that only pikepdf can take off the page.
    #: The boxes of the places OCR found are here too (#128).
    hand_boxes: dict[int, list] = {}

    # OCR, when asked for and set up (#128): the folder of its language file.
    ocr_state = pdf_ocr.ocr_status() if (ocr and scrub_terms) else None
    tessdata = ocr_state["tessdata"] if ocr_state and ocr_state["ready"] else None

    def apply_boxes(page, boxes: list, seen: dict, were_under: set) -> int:
        """Apply *boxes* the way a box drawn by hand is applied, and return
        how many there were: what lies over them goes first (nothing is
        drawn above a box, see _annotations_under), then everything under
        them — line art where that costs nothing else on the page, and any
        character the first pass leaves of *were_under* (_under_keys of the
        page before anything on it was redacted)."""
        seen["annotations"] += _annotations_under(page, boxes)
        n = _redact_boxes(page, boxes, graphics=_line_art_mode(page, boxes), tally=seen,
                          covered=seen["covered"])
        _redact_chars_left_under(page, boxes, were_under, seen)
        seen["annotations"] += _annotations_under(page, boxes)
        seen["links"] += _links_under(page, boxes)
        return n

    def read_with_ocr(doc, page):
        """pdf_ocr.read_page for this run; a cancel ends the run."""
        if on_status and pdf_ocr.reading_dpi(page) is not None:
            on_status(f"Reading page {page.number + 1} of {len(doc)} with OCR")
        try:
            return pdf_ocr.read_page(page, scrub_terms, case_sensitive, tessdata, cancel=cancel)
        except InterruptedError:
            doc.close()
            _let_go()
            raise ValueError("Cancelled") from None

    def redact_pages(doc) -> tuple[int, set, dict]:
        """Redact the page content of *doc*; returns (rectangles drawn,
        pages touched, what else is to be told of the pages): the
        "annotations" and form "fields" that left a page, and for a search
        the pages with "no_text" and the hits on each page whose text is a
        "text_layer" over a picture (_search_fact); and with OCR, "ocr":
        {page index: what OCR made of it} and "ocr_found", how many places
        it found (not among the rectangles drawn)."""
        total, drawn, touched = len(doc), 0, set()
        seen: dict = {"annotations": 0, "fields": 0, "links": 0, "no_text": [],
                      "mostly_picture": [], "text_layer": {}, "covered": set(),
                      "still_shown": [], "ocr": {}, "ocr_found": 0}
        hand_boxes.clear()
        for i, pno in enumerate(target):
            if cancel and cancel.is_set():
                doc.close()
                _let_go()
                raise ValueError("Cancelled")
            if on_progress:
                on_progress(i + 1, len(target))
            if pno < 0 or pno >= total:
                continue

            page = doc[pno]
            # What a search cannot read of this page, before it is changed.
            fact = _search_fact(page) if scrub_terms else None
            # A page with nothing to search, or only a few words on a
            # picture, is read with OCR when asked for.
            reading = read_with_ocr(doc, page) if fact in _OCR_READS and tessdata else None
            # Term hits come from the one matcher the verifier also uses
            # (pdf_verify.term_hits): its own views of the page's
            # characters, and search_for's hits beside them, each widened to
            # the whole words it touches. It honours case_sensitive itself.
            hits = term_hits(page, scrub_terms, case_sensitive)
            # Where OCR reads the term in the page's own text, the search
            # has found it too: that place is left to the search, whose box
            # takes the term's characters and nothing else. The page's
            # fingerprint is taken while it is as it was.
            ocr_boxes = _apart_from(reading.boxes, hits) if reading else []
            before = pdf_ocr.fingerprint(page) if ocr_boxes else None
            drawn_by_hand = []
            for rr in rects_by_page.get(pno, []):
                rect = fitz.Rect(rr["x0"], rr["y0"], rr["x1"], rr["y1"])
                if not rect.is_empty and rect.is_valid:
                    drawn_by_hand.append(rect)
            were_under = _under_keys(page, drawn_by_hand + ocr_boxes)

            # A term box takes only its own characters.
            added = found = _redact_term_boxes(page, hits, seen)
            # A box drawn by hand, or round a place OCR found, takes all it
            # covers. What draws a term is found by what it draws, later, in
            # the scrub.
            added += apply_boxes(page, drawn_by_hand + ocr_boxes, seen, were_under) - len(ocr_boxes)
            # A page read with OCR is read again, as it is now, at each turn
            # the term was found at; what is found then goes too, and is a
            # new place only where it lies apart from those already found.
            places = len(ocr_boxes)
            for _ in range(_OCR_PASSES - 1):
                if not ocr_boxes:
                    break
                if cancel and cancel.is_set():
                    doc.close()
                    _let_go()
                    raise ValueError("Cancelled")
                again = _apart_from([box for turn in reading.hit_turns
                                     for box in pdf_ocr.read_at(page, scrub_terms, case_sensitive,
                                                                tessdata, turn, reading.dpi).boxes],
                                    hits)
                if not again:
                    break
                apply_boxes(page, again, seen, _under_keys(page, again))
                places += sum(1 for box in again if not any(box.intersects(o) for o in ocr_boxes))
                ocr_boxes += again
            if drawn_by_hand or ocr_boxes:
                to_pdf = ~pdf_to_page_matrix(page)
                hand_boxes[pno] = [tuple((box * to_pdf).normalize())
                                   for box in drawn_by_hand + ocr_boxes]
            if reading is not None:
                seen["ocr"][pno] = {"status": reading.status, "dpi": reading.dpi,
                                    "turns": reading.turns, "hit_turns": reading.hit_turns,
                                    "boxes": [tuple(box) for box in ocr_boxes], "places": places,
                                    "search_boxes": [tuple(box) for box, _trimmed in hits],
                                    "seconds": reading.seconds, "before": before}
                seen["ocr_found"] += places
                if ocr_boxes:
                    touched.add(pno)
            # Taking a word out can leave its neighbours reading as the term
            # ("a" [removed] "b" for the term "a b"), and a hit can survive
            # its trimmed box. The verifier would find either and refuse;
            # look again, and give what is found the whole box, text and
            # all. Bounded: a whole box that removed nothing is not tried
            # twice, and a box is counted once.
            counted = {_box_key(box) for box, _trimmed in hits}
            applied: set = set()
            for _ in range(_REDACT_PASSES - 1):
                if not (added and scrub_terms):
                    break
                again = [b for b in term_boxes(page, scrub_terms, case_sensitive)
                         if _box_key(b) not in applied]
                if not again:
                    break
                applied.update(map(_box_key, again))
                _redact_boxes(page, again, tally=seen)
                new = sum(1 for b in again if _box_key(b) not in counted)
                added, found = added + new, found + new
                counted.update(map(_box_key, again))
            if added:
                drawn += added
                touched.add(pno)
            if fact in _OCR_READS and reading is None:
                seen[fact].append(pno)
            elif fact == "text_layer":
                seen["text_layer"][pno] = found
        seen["still_shown"] = _still_shown(doc, seen["covered"])
        return drawn, touched, seen

    # MuPDF checks a file's objects as it comes to read them. One it finds
    # damaged makes it go back to the file and rebuild everything from there
    # ("repairing PDF document") — and what had been changed in memory is
    # gone, without a word. If that happens between here and the end of the
    # save below, the redaction is what is gone.
    repaired_at_start = bool(doc.is_repaired)
    hit_count, pages_touched, seen = redact_pages(doc)

    # The page-content redaction is done; now widen the scrub and write the
    # output atomically. Saved even when hit_count == 0 -- a term can live
    # only in a document-level surface (e.g. the /Title), which still counts
    # as something to remove (#99, §3).
    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    temps: list[str] = []
    surface_counts: dict[str, int] = {}
    flattened: list[int] = []
    flattened_areas: dict[int, int] = {}

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
            hit_count, pages_touched, seen = redact_pages(doc)
            emb_gone = _scrub_embedded_file_names(doc, scrub_terms, case_sensitive)
            work = _mktmp()
            doc.save(work, garbage=4, deflate=True, clean=True)
            doc.close()
        emb_hits = len(emb_gone)
        ocr_count = seen["ocr_found"]

        if hit_count:
            surface_counts["page_content"] = hit_count
        if ocr_count:
            surface_counts["ocr_page_content"] = ocr_count
        if emb_hits:
            surface_counts["embedded_files"] = emb_hits
        if seen["annotations"]:
            surface_counts["annotations_removed"] = seen["annotations"]
        if seen["fields"]:
            surface_counts["fields_removed"] = seen["fields"]
        if seen["links"]:
            surface_counts["links_removed"] = seen["links"]
        # The temp is fitz-saved from the authenticated doc — always
        # plaintext, so the scrub (and verify below) need no password.
        doc_scrub = _scrub_document_surfaces(work, scrub_terms, case_sensitive,
                                             gone_attachments=emb_gone)
        for k, v in doc_scrub.items():
            surface_counts[k] = surface_counts.get(k, 0) + v

        if hit_count == 0 and ocr_count == 0 and emb_hits == 0 and sum(doc_scrub.values()) == 0:
            # No result to carry a note, so the facts that explain an empty
            # search go in the message: pages it could not read, and pages
            # OCR read, which can miss words.
            raise ValueError("No matching content found to redact."
                             + _unread_in_words(seen, tessdata is None and ocr_state is not None))

        final_src = work
        if flatten_pages:
            flat = _mktmp()
            # flatten_pages are 1-based (as the verification report and UI
            # count pages); _flatten_pages_to_image takes 0-based indices.
            # What can still be found on a page, the user's own boxes and
            # the places OCR found are blacked out on the picture itself.
            by_hand: dict[int, list] = {}
            for pno, entries in rects_by_page.items():
                for rr in entries:
                    rect = fitz.Rect(rr["x0"], rr["y0"], rr["x1"], rr["y1"])
                    if not rect.is_empty and rect.is_valid:
                        by_hand.setdefault(pno, []).append(rect)
            for pno, read in seen["ocr"].items():
                by_hand.setdefault(pno, []).extend(fitz.Rect(b) for b in read["boxes"])
            filled = _flatten_pages_to_image(
                work, flat, [int(p) - 1 for p in flatten_pages],
                terms=scrub_terms, case_sensitive=case_sensitive, boxes=by_hand)
            flattened_areas = {pno + 1: n for pno, n in sorted(filled.items())}
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
            # First what the tree says in words of content a box removed
            # (it is told from the originals), then the thumbnails.
            for key, n in zip(("annotations_removed", "fields_removed", "links_removed"),
                              _unlisted_annotations_under(pdf, hand_boxes), strict=True):
                if n:
                    surface_counts[key] = surface_counts.get(key, 0) + n
            if surface_counts.get("fields_removed"):
                _drop_unshown_fields(pdf)        # counted already, as fields_removed
            for key, n in (("structure_text", _drop_structure_text(
                               pdf, input_path, password, pages_touched)),
                           ("structure_refs", _drop_stale_structure_refs(pdf)),
                           ("thumbnails", _drop_thumbnails(pdf))):
                if n:
                    surface_counts[key] = n
            # What a search did not open: the attachments still in the file.
            attached = embedded_files(pdf) if scrub_terms else []
            save_with_content_id(pdf, candidate, fix_metadata_version=False)

        # Verify, then commit (fail-closed, #99): every target must be
        # proven gone from the candidate before anything touches
        # output_path. A refusal leaves the destination exactly as it was —
        # which matters most when it is the input (redacting in place).
        # The places OCR found are boxes to the box checks too, and have
        # checks of their own; a page turned into a picture has changed
        # everywhere, so it is not held to its fingerprint.
        ocr_rects = [{"page": pno, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]}
                     for pno, read in sorted(seen["ocr"].items()) for b in read["boxes"]]
        ocr_pages = [{"page": pno, "boxes": read["boxes"], "dpi": read["dpi"],
                      "turns": read["hit_turns"], "search_boxes": read["search_boxes"],
                      "before": None if pno + 1 in flattened else read["before"]}
                     for pno, read in sorted(seen["ocr"].items()) if read["boxes"]]
        report = verify_redaction(
            candidate,
            terms=scrub_terms or None,
            rects=(list(rects or []) + ocr_rects) or rects,
            case_sensitive=case_sensitive,
            input_path=input_path,
            source_ids=source_ids,
            source_sha256=source_sha,
            ocr_pages=ocr_pages or None,
            tessdata=tessdata,
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
                detail=redaction_refusal_detail(report, offer),
            )
        # Nothing may still hold the source open when the copy takes its
        # place (redacting in place, on Windows): see _let_go.
        _let_go()
        os.replace(candidate, output_path)
    except Exception:
        try: doc.close()
        except Exception: pass
        _let_go()
        raise
    finally:
        _cleanup_temps()

    return RedactResult(
        input_path=input_path,
        output_path=output_path,
        redaction_count=hit_count + ocr_count,
        pages_affected=len(pages_touched),
        surface_counts=surface_counts,
        verification=report.to_dict(),
        flattened_pages=flattened,
        flattened_areas=flattened_areas,
        notes=_notes(seen if scrub_terms else None, attached, flattened, flattened_areas,
                     sorted(hand_boxes) if not scrub_terms else [],
                     seen["still_shown"],
                     ocr_not_ready=ocr_state is not None and tessdata is None)
              + library_notes,
    )
