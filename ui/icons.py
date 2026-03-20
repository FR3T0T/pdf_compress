"""QPainter-drawn icon system — theme-aware, DPI-independent, zero external deps."""

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QPainterPath, QFont


def draw_icon(name: str, size: int, color: QColor) -> QPixmap:
    """Render a named icon as a QPixmap."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    s = size
    fn = _ICONS.get(name, _draw_unknown)
    fn(p, s, color)
    p.end()
    return pixmap


def _pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


# ═══════════════════════════════════════════════════════════════════
#  Individual icon draw functions
# ═══════════════════════════════════════════════════════════════════

def _draw_home(p: QPainter, s: int, c: QColor):
    """House shape."""
    p.setPen(_pen(c, s * 0.07))
    p.setBrush(Qt.NoBrush)
    m = s * 0.16
    # Roof (triangle)
    roof = QPainterPath()
    roof.moveTo(s * 0.5, m)
    roof.lineTo(s - m, s * 0.44)
    roof.lineTo(m, s * 0.44)
    roof.closeSubpath()
    p.drawPath(roof)
    # Body (rectangle)
    p.drawRect(QRectF(s * 0.24, s * 0.44, s * 0.52, s * 0.42))


def _draw_compress(p: QPainter, s: int, c: QColor):
    """Down arrow with squeeze lines."""
    p.setPen(_pen(c, s * 0.07))
    cx = s * 0.5
    # Arrow shaft
    p.drawLine(QPointF(cx, s * 0.14), QPointF(cx, s * 0.68))
    # Arrow head
    p.drawLine(QPointF(cx - s * 0.18, s * 0.50), QPointF(cx, s * 0.68))
    p.drawLine(QPointF(cx + s * 0.18, s * 0.50), QPointF(cx, s * 0.68))
    # Squeeze line
    p.drawLine(QPointF(s * 0.2, s * 0.84), QPointF(s * 0.8, s * 0.84))


def _draw_merge(p: QPainter, s: int, c: QColor):
    """Two docs merging into one."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    # Left doc
    p.drawRoundedRect(QRectF(s * 0.08, s * 0.08, s * 0.35, s * 0.45), 3, 3)
    # Right doc
    p.drawRoundedRect(QRectF(s * 0.57, s * 0.08, s * 0.35, s * 0.45), 3, 3)
    # Arrow down
    cx = s * 0.5
    p.drawLine(QPointF(cx, s * 0.58), QPointF(cx, s * 0.78))
    p.drawLine(QPointF(cx - s * 0.1, s * 0.70), QPointF(cx, s * 0.78))
    p.drawLine(QPointF(cx + s * 0.1, s * 0.70), QPointF(cx, s * 0.78))
    # Result doc
    p.drawRoundedRect(QRectF(s * 0.28, s * 0.78, s * 0.44, s * 0.16), 3, 3)


def _draw_split(p: QPainter, s: int, c: QColor):
    """Split symbol."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    # Top doc
    p.drawRoundedRect(QRectF(s * 0.28, s * 0.04, s * 0.44, s * 0.22), 3, 3)
    # Dashed line
    pen = _pen(c, s * 0.04)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.14, s * 0.38), QPointF(s * 0.86, s * 0.38))
    p.setPen(_pen(c, s * 0.06))
    # Bottom docs
    p.drawRoundedRect(QRectF(s * 0.06, s * 0.52, s * 0.38, s * 0.42), 3, 3)
    p.drawRoundedRect(QRectF(s * 0.56, s * 0.52, s * 0.38, s * 0.42), 3, 3)


def _draw_lock(p: QPainter, s: int, c: QColor):
    """Padlock."""
    p.setPen(_pen(c, s * 0.07))
    p.setBrush(Qt.NoBrush)
    # Shackle
    p.drawArc(QRectF(s * 0.28, s * 0.08, s * 0.44, s * 0.40), 0, 180 * 16)
    # Body
    p.drawRoundedRect(QRectF(s * 0.18, s * 0.42, s * 0.64, s * 0.46), 4, 4)
    # Keyhole
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(s * 0.5, s * 0.58), s * 0.06, s * 0.06)
    p.drawRect(QRectF(s * 0.47, s * 0.58, s * 0.06, s * 0.14))


def _draw_unlock(p: QPainter, s: int, c: QColor):
    """Open padlock."""
    p.setPen(_pen(c, s * 0.07))
    p.setBrush(Qt.NoBrush)
    # Shackle (open)
    path = QPainterPath()
    path.moveTo(s * 0.28, s * 0.42)
    path.lineTo(s * 0.28, s * 0.26)
    path.cubicTo(s * 0.28, s * 0.08, s * 0.72, s * 0.08, s * 0.72, s * 0.20)
    p.drawPath(path)
    # Body
    p.drawRoundedRect(QRectF(s * 0.18, s * 0.42, s * 0.64, s * 0.46), 4, 4)


def _draw_image(p: QPainter, s: int, c: QColor):
    """Picture frame / landscape."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    m = s * 0.13
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), 4, 4)
    # Mountain
    path = QPainterPath()
    path.moveTo(m, s * 0.75)
    path.lineTo(s * 0.35, s * 0.38)
    path.lineTo(s * 0.55, s * 0.53)
    path.lineTo(s * 0.7, s * 0.33)
    path.lineTo(s - m, s * 0.75)
    p.drawPath(path)
    # Sun
    p.drawEllipse(QPointF(s * 0.72, s * 0.28), s * 0.06, s * 0.06)


def _draw_image_to_pdf(p: QPainter, s: int, c: QColor):
    """Image with arrow to doc."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    # Image frame
    p.drawRoundedRect(QRectF(s * 0.04, s * 0.14, s * 0.38, s * 0.36), 3, 3)
    # Arrow
    p.drawLine(QPointF(s * 0.47, s * 0.32), QPointF(s * 0.57, s * 0.32))
    p.drawLine(QPointF(s * 0.52, s * 0.25), QPointF(s * 0.57, s * 0.32))
    p.drawLine(QPointF(s * 0.52, s * 0.39), QPointF(s * 0.57, s * 0.32))
    # Doc
    p.drawRoundedRect(QRectF(s * 0.57, s * 0.14, s * 0.38, s * 0.46), 3, 3)
    # PDF label
    f = QFont("Segoe UI", max(1, int(s * 0.12)), QFont.Bold)
    p.setFont(f)
    p.setPen(c)
    p.drawText(QRectF(s * 0.57, s * 0.60, s * 0.38, s * 0.22), Qt.AlignCenter, "PDF")


def _draw_word(p: QPainter, s: int, c: QColor):
    """W for Word doc."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    m = s * 0.13
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), 4, 4)
    f = QFont("Segoe UI", max(1, int(s * 0.28)), QFont.Bold)
    p.setFont(f)
    p.setPen(c)
    p.drawText(QRectF(m, m, s - 2 * m, s - 2 * m), Qt.AlignCenter, "W")


def _draw_pages(p: QPainter, s: int, c: QColor):
    """Stacked pages."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    # Back page
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.08, s * 0.62, s * 0.62), 3, 3)
    # Front page
    p.drawRoundedRect(QRectF(s * 0.10, s * 0.20, s * 0.62, s * 0.62), 3, 3)
    # Lines on front page
    p.drawLine(QPointF(s * 0.20, s * 0.40), QPointF(s * 0.60, s * 0.40))
    p.drawLine(QPointF(s * 0.20, s * 0.52), QPointF(s * 0.55, s * 0.52))
    p.drawLine(QPointF(s * 0.20, s * 0.64), QPointF(s * 0.48, s * 0.64))


def _draw_crop(p: QPainter, s: int, c: QColor):
    """Crop marks."""
    p.setPen(_pen(c, s * 0.07))
    # Top-left L
    p.drawLine(QPointF(s * 0.28, s * 0.08), QPointF(s * 0.28, s * 0.28))
    p.drawLine(QPointF(s * 0.08, s * 0.28), QPointF(s * 0.28, s * 0.28))
    # Bottom-right L
    p.drawLine(QPointF(s * 0.72, s * 0.92), QPointF(s * 0.72, s * 0.72))
    p.drawLine(QPointF(s * 0.92, s * 0.72), QPointF(s * 0.72, s * 0.72))
    # Dashed box
    pen = _pen(c, s * 0.04)
    pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.drawRect(QRectF(s * 0.28, s * 0.28, s * 0.44, s * 0.44))


def _draw_flatten(p: QPainter, s: int, c: QColor):
    """Flatten symbol."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    # Stack becoming flat
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.14, s * 0.72, s * 0.16), 3, 3)
    p.drawLine(QPointF(s * 0.5, s * 0.34), QPointF(s * 0.5, s * 0.54))
    p.drawLine(QPointF(s * 0.40, s * 0.46), QPointF(s * 0.5, s * 0.54))
    p.drawLine(QPointF(s * 0.60, s * 0.46), QPointF(s * 0.5, s * 0.54))
    # Flat line
    p.setPen(_pen(c, s * 0.08))
    p.drawLine(QPointF(s * 0.14, s * 0.74), QPointF(s * 0.86, s * 0.74))


def _draw_grid(p: QPainter, s: int, c: QColor):
    """2x2 grid for N-up."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    m = s * 0.13
    gap = s * 0.06
    w = (s - 2 * m - gap) / 2
    h = (s - 2 * m - gap) / 2
    for r in range(2):
        for col in range(2):
            x = m + col * (w + gap)
            y = m + r * (h + gap)
            p.drawRoundedRect(QRectF(x, y, w, h), 3, 3)


def _draw_watermark(p: QPainter, s: int, c: QColor):
    """Diagonal text on page."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    m = s * 0.13
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), 4, 4)
    # Diagonal lines representing watermark text
    pale = QColor(c)
    pale.setAlpha(120)
    p.setPen(_pen(pale, s * 0.05))
    p.drawLine(QPointF(s * 0.22, s * 0.70), QPointF(s * 0.78, s * 0.30))
    p.drawLine(QPointF(s * 0.22, s * 0.80), QPointF(s * 0.68, s * 0.40))


def _draw_numbers(p: QPainter, s: int, c: QColor):
    """Page with # number."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    m = s * 0.13
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), 4, 4)
    f = QFont("Segoe UI", max(1, int(s * 0.22)), QFont.Bold)
    p.setFont(f)
    p.setPen(c)
    p.drawText(QRectF(m, m, s - 2 * m, s - 2 * m), Qt.AlignCenter, "#")


def _draw_metadata(p: QPainter, s: int, c: QColor):
    """Info / i in circle."""
    p.setPen(_pen(c, s * 0.07))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68))
    f = QFont("Segoe UI", max(1, int(s * 0.30)), QFont.Bold)
    p.setFont(f)
    p.setPen(c)
    p.drawText(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68), Qt.AlignCenter, "i")


def _draw_extract_img(p: QPainter, s: int, c: QColor):
    """Image coming out of document."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    # Doc
    p.drawRoundedRect(QRectF(s * 0.08, s * 0.13, s * 0.50, s * 0.72), 3, 3)
    # Arrow out
    p.drawLine(QPointF(s * 0.58, s * 0.50), QPointF(s * 0.84, s * 0.50))
    p.drawLine(QPointF(s * 0.76, s * 0.42), QPointF(s * 0.84, s * 0.50))
    p.drawLine(QPointF(s * 0.76, s * 0.58), QPointF(s * 0.84, s * 0.50))
    # Small image
    p.drawRoundedRect(QRectF(s * 0.60, s * 0.18, s * 0.32, s * 0.22), 2, 2)


def _draw_extract_text(p: QPainter, s: int, c: QColor):
    """Text lines coming out of doc."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.08, s * 0.13, s * 0.48, s * 0.72), 3, 3)
    # Text lines
    for i, w in enumerate([0.32, 0.26, 0.20]):
        y = s * (0.34 + i * 0.14)
        p.drawLine(QPointF(s * 0.62, y), QPointF(s * (0.62 + w), y))


def _draw_repair(p: QPainter, s: int, c: QColor):
    """Wrench."""
    p.setPen(_pen(c, s * 0.07))
    p.setBrush(Qt.NoBrush)
    # Wrench handle
    p.drawLine(QPointF(s * 0.28, s * 0.72), QPointF(s * 0.64, s * 0.34))
    # Wrench head
    p.drawEllipse(QPointF(s * 0.70, s * 0.28), s * 0.15, s * 0.15)
    inner = QColor(0, 0, 0, 0)
    p.setBrush(inner)
    p.drawEllipse(QPointF(s * 0.70, s * 0.28), s * 0.06, s * 0.06)


def _draw_compare(p: QPainter, s: int, c: QColor):
    """Two docs side by side."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.04, s * 0.13, s * 0.38, s * 0.72), 3, 3)
    p.drawRoundedRect(QRectF(s * 0.58, s * 0.13, s * 0.38, s * 0.72), 3, 3)
    # Arrows between
    p.drawLine(QPointF(s * 0.44, s * 0.42), QPointF(s * 0.56, s * 0.42))
    p.drawLine(QPointF(s * 0.56, s * 0.58), QPointF(s * 0.44, s * 0.58))


def _draw_sun(p: QPainter, s: int, c: QColor):
    """Sun icon for light theme toggle."""
    p.setPen(_pen(c, s * 0.07))
    p.setBrush(Qt.NoBrush)
    center = s * 0.5
    # Circle
    r = s * 0.16
    p.drawEllipse(QPointF(center, center), r, r)
    # Rays
    ray_inner = s * 0.28
    ray_outer = s * 0.40
    import math
    for i in range(8):
        angle = i * math.pi / 4
        x1 = center + ray_inner * math.cos(angle)
        y1 = center + ray_inner * math.sin(angle)
        x2 = center + ray_outer * math.cos(angle)
        y2 = center + ray_outer * math.sin(angle)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _draw_moon(p: QPainter, s: int, c: QColor):
    """Moon icon for dark theme toggle."""
    p.setPen(_pen(c, s * 0.07))
    p.setBrush(Qt.NoBrush)
    # Crescent moon using two arcs
    path = QPainterPath()
    path.addEllipse(QRectF(s * 0.18, s * 0.14, s * 0.58, s * 0.72))
    # Subtract circle to create crescent
    subtract = QPainterPath()
    subtract.addEllipse(QRectF(s * 0.34, s * 0.06, s * 0.56, s * 0.64))
    crescent = path - subtract
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawPath(crescent)


def _draw_menu(p: QPainter, s: int, c: QColor):
    """Hamburger menu icon."""
    p.setPen(_pen(c, s * 0.07))
    p.drawLine(QPointF(s * 0.20, s * 0.30), QPointF(s * 0.80, s * 0.30))
    p.drawLine(QPointF(s * 0.20, s * 0.50), QPointF(s * 0.80, s * 0.50))
    p.drawLine(QPointF(s * 0.20, s * 0.70), QPointF(s * 0.80, s * 0.70))


def _draw_upload(p: QPainter, s: int, c: QColor):
    """Upload / cloud icon for drop zone."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    # Cloud shape
    path = QPainterPath()
    path.moveTo(s * 0.22, s * 0.54)
    path.cubicTo(s * 0.08, s * 0.54, s * 0.08, s * 0.28, s * 0.28, s * 0.28)
    path.cubicTo(s * 0.30, s * 0.14, s * 0.50, s * 0.08, s * 0.58, s * 0.20)
    path.cubicTo(s * 0.78, s * 0.14, s * 0.92, s * 0.30, s * 0.78, s * 0.54)
    path.closeSubpath()
    p.drawPath(path)
    # Upload arrow
    cx = s * 0.5
    p.drawLine(QPointF(cx, s * 0.50), QPointF(cx, s * 0.80))
    p.drawLine(QPointF(cx - s * 0.12, s * 0.62), QPointF(cx, s * 0.50))
    p.drawLine(QPointF(cx + s * 0.12, s * 0.62), QPointF(cx, s * 0.50))


def _draw_redact(p: QPainter, s: int, c: QColor):
    """Redaction icon — document with blacked-out lines."""
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.NoBrush)
    m = s * 0.13
    # Document outline
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), 4, 4)
    # Redacted (filled) bars
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(s * 0.24, s * 0.28, s * 0.52, s * 0.08), 2, 2)
    p.drawRoundedRect(QRectF(s * 0.24, s * 0.44, s * 0.38, s * 0.08), 2, 2)
    p.drawRoundedRect(QRectF(s * 0.24, s * 0.60, s * 0.46, s * 0.08), 2, 2)


def _draw_unknown(p: QPainter, s: int, c: QColor):
    """Fallback: question mark."""
    f = QFont("Segoe UI", max(1, int(s * 0.4)), QFont.Bold)
    p.setFont(f)
    p.setPen(c)
    p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "?")


_ICONS = {
    "home": _draw_home,
    "compress": _draw_compress,
    "merge": _draw_merge,
    "split": _draw_split,
    "lock": _draw_lock,
    "unlock": _draw_unlock,
    "image": _draw_image,
    "image_to_pdf": _draw_image_to_pdf,
    "word": _draw_word,
    "pages": _draw_pages,
    "crop": _draw_crop,
    "flatten": _draw_flatten,
    "grid": _draw_grid,
    "watermark": _draw_watermark,
    "numbers": _draw_numbers,
    "metadata": _draw_metadata,
    "extract_img": _draw_extract_img,
    "extract_text": _draw_extract_text,
    "repair": _draw_repair,
    "compare": _draw_compare,
    "sun": _draw_sun,
    "moon": _draw_moon,
    "menu": _draw_menu,
    "upload": _draw_upload,
    "redact": _draw_redact,
}
