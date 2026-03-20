"""Shared test fixtures for the PDF Toolkit test suite."""

import io
import os
import sys

import pikepdf
import pytest
from PIL import Image

# Add project root to sys.path so tests can import engine, pdf_ops, etc.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_jpeg_bytes(width: int = 100, height: int = 80) -> bytes:
    """Generate a small JPEG image as raw bytes."""
    img = Image.new("RGB", (width, height), color=(120, 80, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def _make_simple_pdf(path: str, *, num_pages: int = 1,
                     with_image: bool = False) -> str:
    """Create a minimal valid PDF at *path*.

    Args:
        path: Output file path.
        num_pages: Number of blank pages to add.
        with_image: If True, embed a small JPEG on page 1.

    Returns:
        The path (for convenience).
    """
    pdf = pikepdf.Pdf.new()

    for _ in range(num_pages):
        page = pikepdf.Page(pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=[0, 0, 612, 792],  # US Letter
        ))
        pdf.pages.append(page)

    if with_image and len(pdf.pages) > 0:
        jpeg_data = _make_jpeg_bytes()
        img_stream = pdf.make_stream(jpeg_data)
        img_stream["/Type"] = pikepdf.Name("/XObject")
        img_stream["/Subtype"] = pikepdf.Name("/Image")
        img_stream["/Width"] = 100
        img_stream["/Height"] = 80
        img_stream["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
        img_stream["/BitsPerComponent"] = 8
        img_stream["/Filter"] = pikepdf.Name("/DCTDecode")

        # Add to first page resources + paint via content stream
        page0 = pdf.pages[0]
        if "/Resources" not in page0:
            page0["/Resources"] = pikepdf.Dictionary()
        res = page0["/Resources"]
        if "/XObject" not in res:
            res["/XObject"] = pikepdf.Dictionary()
        res["/XObject"]["/Img0"] = img_stream

        content = b"q 100 0 0 80 50 650 cm /Img0 Do Q"
        page0["/Contents"] = pdf.make_stream(content)

    pdf.save(path)
    pdf.close()
    return path


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_pdf(tmp_path):
    """A single-page PDF with an embedded JPEG image."""
    return _make_simple_pdf(str(tmp_path / "sample.pdf"),
                            num_pages=1, with_image=True)


@pytest.fixture
def text_only_pdf(tmp_path):
    """A single-page PDF with no images."""
    return _make_simple_pdf(str(tmp_path / "text_only.pdf"),
                            num_pages=1, with_image=False)


@pytest.fixture
def multi_page_pdf(tmp_path):
    """A 3-page PDF (no images)."""
    return _make_simple_pdf(str(tmp_path / "multi.pdf"),
                            num_pages=3, with_image=False)


@pytest.fixture
def encrypted_pdf(tmp_path):
    """A PDF encrypted with password 'testpass123'."""
    plain = str(tmp_path / "plain.pdf")
    enc = str(tmp_path / "encrypted.pdf")
    _make_simple_pdf(plain, num_pages=1)

    pdf = pikepdf.open(plain)
    pdf.save(enc, encryption=pikepdf.Encryption(
        owner="ownerpass",
        user="testpass123",
    ))
    pdf.close()
    return enc


@pytest.fixture
def invalid_file(tmp_path):
    """A file that is NOT a valid PDF (random bytes)."""
    path = str(tmp_path / "not_a_pdf.pdf")
    with open(path, "wb") as f:
        f.write(b"THIS IS NOT A PDF FILE AT ALL\x00\xff\xfe")
    return path


@pytest.fixture
def sample_image(tmp_path):
    """A small PNG image file on disk."""
    path = str(tmp_path / "test_image.png")
    img = Image.new("RGB", (200, 150), color=(50, 150, 250))
    img.save(path, format="PNG")
    return path
