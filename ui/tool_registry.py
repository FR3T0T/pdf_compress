"""Tool registry — centralized tool metadata for the web frontend.

The React frontend renders every tool page itself; Python only supplies the
tool catalogue (keys, titles, categories,
accepted extensions) over the QWebChannel bridge. This module therefore holds
pure metadata with no UI-framework dependencies.
"""

from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class ToolDef:
    key: str
    title: str
    description: str
    icon: str
    category: str
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

_IMAGE_EXTS = [".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"]


def _tools() -> list[ToolDef]:
    return [
        # ── Compress & Optimize ──
        ToolDef("compress", "Compress PDF", "Reduce file size with smart image recompression",
                "compress", "compress"),

        # ── Merge & Split ──
        ToolDef("merge", "Merge PDFs", "Combine multiple PDFs into one document",
                "merge", "merge"),
        ToolDef("split", "Split PDF", "Divide a PDF into separate files",
                "split", "merge"),

        # ── Convert ──
        ToolDef("pdf_to_images", "PDF to Images", "Export pages as PNG or JPEG",
                "image", "convert"),
        ToolDef("images_to_pdf", "Images to PDF", "Convert images into a PDF document",
                "image_to_pdf", "convert", accepted_extensions=list(_IMAGE_EXTS)),
        ToolDef("pdf_to_word", "PDF to Word", "Extract text to a Word document",
                "word", "convert"),
        ToolDef("translate", "Translate",
                "Offline translation of PDF text and text in photos/scans",
                "translate", "convert",
                accepted_extensions=[".pdf", *_IMAGE_EXTS]),

        # ── Security ──
        ToolDef("protect", "Protect PDF", "Add password protection with standard or enhanced encryption",
                "lock", "security"),
        ToolDef("unlock", "Unlock PDF", "Remove password protection from PDF or EPDF files",
                "unlock", "security", accepted_extensions=[".pdf", ".epdf"]),
        ToolDef("redact", "Redact PDF", "Permanently remove sensitive text",
                "redact", "security"),

        # ── Page Operations ──
        ToolDef("page_ops", "Rotate & Reorder", "Rotate, reorder, or delete pages",
                "pages", "pages"),
        ToolDef("crop", "Crop Pages", "Trim page margins",
                "crop", "pages"),
        ToolDef("flatten", "Flatten PDF", "Remove annotations and form fields",
                "flatten", "pages"),
        ToolDef("nup", "N-up Layout", "Arrange multiple pages per sheet",
                "grid", "pages"),

        # ── Content & Watermark ──
        ToolDef("watermark", "Add Watermark", "Batch text watermarking with presets and positioning",
                "watermark", "content"),
        ToolDef("page_numbers", "Add Page Numbers", "Insert page numbering",
                "numbers", "content"),
        ToolDef("metadata", "Edit Metadata", "View and edit PDF properties",
                "metadata", "content"),

        # ── Extract ──
        ToolDef("extract_images", "Extract Images", "Pull all images from a PDF",
                "extract_img", "extract"),
        ToolDef("extract_text", "Extract Text", "Export text content to a file",
                "extract_text", "extract"),

        # ── Repair & Analysis ──
        ToolDef("repair", "Repair PDF", "Fix corrupted PDF files",
                "repair", "repair"),
        ToolDef("compare", "Compare PDFs", "Find differences between two PDFs",
                "compare", "repair"),
        ToolDef("analyze", "Analyze Document",
                "Privacy & security audit — find trackers, scripts, hidden data",
                "shield", "repair"),
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
