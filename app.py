#!/usr/bin/env python3
"""
PDF Toolkit — Desktop GUI (PySide6)
Requires: pip install PySide6 pikepdf pillow
"""

import os
import sys
import logging

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QIcon, QPixmap,
    QLinearGradient, QPainterPath,
)
from PySide6.QtWidgets import QApplication

from engine import setup_file_logging
from ui import MainWindow
from ui.theme import FONT

log = logging.getLogger(__name__)

VERSION = "4.0.0"


def _generate_app_icon() -> QIcon:
    """Generate a modern PDF icon programmatically."""
    sizes = [16, 32, 48, 64, 128, 256]
    icon = QIcon()
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        margin = size * 0.06
        rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)

        # Modern indigo gradient background
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor("#6366f1"))
        gradient.setColorAt(1.0, QColor("#4338ca"))
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.NoPen)
        radius = size * 0.18
        painter.drawRoundedRect(rect, radius, radius)

        # Document shape with folded corner
        doc_margin = size * 0.22
        doc_rect = QRectF(doc_margin, doc_margin * 0.9,
                          size - 2 * doc_margin, size - 2 * doc_margin * 0.85)
        fold = size * 0.15
        doc_path = QPainterPath()
        doc_path.moveTo(doc_rect.left(), doc_rect.top() + 3)
        doc_path.lineTo(doc_rect.right() - fold, doc_rect.top())
        doc_path.lineTo(doc_rect.right(), doc_rect.top() + fold)
        doc_path.lineTo(doc_rect.right(), doc_rect.bottom() - 3)
        doc_path.quadTo(doc_rect.right(), doc_rect.bottom(),
                        doc_rect.right() - 3, doc_rect.bottom())
        doc_path.lineTo(doc_rect.left() + 3, doc_rect.bottom())
        doc_path.quadTo(doc_rect.left(), doc_rect.bottom(),
                        doc_rect.left(), doc_rect.bottom() - 3)
        doc_path.closeSubpath()

        painter.setBrush(QBrush(QColor(255, 255, 255, 50)))
        painter.setPen(QPen(QColor(255, 255, 255, 80), max(1, size * 0.02)))
        painter.drawPath(doc_path)

        # "PDF" text
        font_size = max(1, int(size * 0.24))
        font = QFont("Segoe UI", font_size, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(255, 255, 255, 240)))
        text_rect = QRectF(doc_rect.left(), doc_rect.top() + doc_rect.height() * 0.25,
                           doc_rect.width(), doc_rect.height() * 0.5)
        painter.drawText(text_rect, Qt.AlignCenter, "PDF")

        painter.end()
        icon.addPixmap(pixmap)
    return icon


def main():
    try:
        log_file = setup_file_logging()
        log.info("PDF Toolkit %s starting up", VERSION)
        log.info("Log file: %s", log_file)
    except Exception as e:
        print(f"Warning: Could not set up file logging: {e}", file=sys.stderr)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setFont(QFont(FONT, 10))
    app.setApplicationName("PDF Toolkit")
    app.setOrganizationName("PDFCompress")

    app.setWindowIcon(_generate_app_icon())

    initial = [f for f in sys.argv[1:]
               if f.lower().endswith(".pdf") and os.path.isfile(f)]

    window = MainWindow(initial_files=initial or None)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
