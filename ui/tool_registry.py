"""Tool registry — centralized tool definitions for dashboard and sidebar."""

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class ToolDef:
    key: str
    title: str
    description: str
    icon: str
    category: str
    page_factory: Callable  # Callable[[shell], BasePage]
    accepted_extensions: list[str] = field(default_factory=lambda: [".pdf"])


CATEGORIES = OrderedDict([
    ("compress", "Compress & Optimize"),
    ("merge",    "Merge & Split"),
    ("convert",  "Convert"),
    ("security", "Security"),
    ("pages",    "Page Operations"),
    ("content",  "Content & Watermark"),
    ("extract",  "Extract"),
    ("repair",   "Repair & Analysis"),
])


def _tools() -> list[ToolDef]:
    """Build tool list with lazy imports to avoid circular deps."""
    from .pages.compress_page import CompressPage
    from .pages.merge_page import MergePage
    from .pages.split_page import SplitPage
    from .pages.page_ops_page import PageOpsPage
    from .pages.protect_page import ProtectPage
    from .pages.unlock_page import UnlockPage
    from .pages.pdf_to_images_page import PdfToImagesPage
    from .pages.images_to_pdf_page import ImagesToPdfPage
    from .pages.pdf_to_word_page import PdfToWordPage
    from .pages.watermark_page import WatermarkPage
    from .pages.page_numbers_page import PageNumbersPage
    from .pages.metadata_page import MetadataPage
    from .pages.extract_images_page import ExtractImagesPage
    from .pages.extract_text_page import ExtractTextPage
    from .pages.crop_page import CropPage
    from .pages.flatten_page import FlattenPage
    from .pages.nup_page import NupPage
    from .pages.repair_page import RepairPage
    from .pages.compare_page import ComparePage
    from .pages.redact_page import RedactPage

    return [
        # ── Compress & Optimize ──
        ToolDef("compress", "Compress PDF", "Reduce file size with smart image recompression",
                "compress", "compress", lambda s: CompressPage(s)),

        # ── Merge & Split ──
        ToolDef("merge", "Merge PDFs", "Combine multiple PDFs into one document",
                "merge", "merge", lambda s: MergePage(s)),
        ToolDef("split", "Split PDF", "Divide a PDF into separate files",
                "split", "merge", lambda s: SplitPage(s)),

        # ── Convert ──
        ToolDef("pdf_to_images", "PDF to Images", "Export pages as PNG or JPEG",
                "image", "convert", lambda s: PdfToImagesPage(s)),
        ToolDef("images_to_pdf", "Images to PDF", "Convert images into a PDF document",
                "image_to_pdf", "convert", lambda s: ImagesToPdfPage(s),
                accepted_extensions=[".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"]),
        ToolDef("pdf_to_word", "PDF to Word", "Extract text to a Word document",
                "word", "convert", lambda s: PdfToWordPage(s)),

        # ── Security ──
        ToolDef("protect", "Protect PDF", "Add password protection with standard or enhanced encryption",
                "lock", "security", lambda s: ProtectPage(s)),
        ToolDef("unlock", "Unlock PDF", "Remove password protection from PDF or EPDF files",
                "unlock", "security", lambda s: UnlockPage(s),
                accepted_extensions=[".pdf", ".epdf"]),
        ToolDef("redact", "Redact PDF", "Permanently remove sensitive text",
                "redact", "security", lambda s: RedactPage(s)),

        # ── Page Operations ──
        ToolDef("page_ops", "Rotate & Reorder", "Rotate, reorder, or delete pages",
                "pages", "pages", lambda s: PageOpsPage(s)),
        ToolDef("crop", "Crop Pages", "Trim page margins",
                "crop", "pages", lambda s: CropPage(s)),
        ToolDef("flatten", "Flatten PDF", "Remove annotations and form fields",
                "flatten", "pages", lambda s: FlattenPage(s)),
        ToolDef("nup", "N-up Layout", "Arrange multiple pages per sheet",
                "grid", "pages", lambda s: NupPage(s)),

        # ── Content & Watermark ──
        ToolDef("watermark", "Add Watermark", "Batch text watermarking with presets and positioning",
                "watermark", "content", lambda s: WatermarkPage(s)),
        ToolDef("page_numbers", "Add Page Numbers", "Insert page numbering",
                "numbers", "content", lambda s: PageNumbersPage(s)),
        ToolDef("metadata", "Edit Metadata", "View and edit PDF properties",
                "metadata", "content", lambda s: MetadataPage(s)),

        # ── Extract ──
        ToolDef("extract_images", "Extract Images", "Pull all images from a PDF",
                "extract_img", "extract", lambda s: ExtractImagesPage(s)),
        ToolDef("extract_text", "Extract Text", "Export text content to a file",
                "extract_text", "extract", lambda s: ExtractTextPage(s)),

        # ── Repair & Analysis ──
        ToolDef("repair", "Repair PDF", "Fix corrupted PDF files",
                "repair", "repair", lambda s: RepairPage(s)),
        ToolDef("compare", "Compare PDFs", "Find differences between two PDFs",
                "compare", "repair", lambda s: ComparePage(s)),
    ]


# Cached tool list — populated on first access
_TOOLS: list[ToolDef] | None = None


def get_tools() -> list[ToolDef]:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = _tools()
    return _TOOLS


def get_tool(key: str) -> ToolDef | None:
    for t in get_tools():
        if t.key == key:
            return t
    return None
