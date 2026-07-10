#!/usr/bin/env python3
"""
PDF Toolkit — Desktop GUI (PySide6)
Requires: pip install PySide6 pikepdf pillow
"""

import logging
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from engine import setup_file_logging
from ui import MainWindow
from ui.theme import FONT

log = logging.getLogger(__name__)

VERSION = "4.23"


def _app_icon() -> QIcon:
    """Load the application icon (pdf_toolkit.ico, beside this file).

    Resolves in both run modes: from source this directory is the repo
    root; in the frozen build PyInstaller places __file__ under
    sys._MEIPASS (the _internal dir), where pdf_toolkit.spec bundles the
    .ico as a data file. The built .exe additionally embeds the same
    icon (spec: EXE(icon=...)) for Explorer/taskbar/Alt-Tab.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_toolkit.ico")
    if not os.path.isfile(path):
        log.warning("App icon not found at %s", path)
    return QIcon(path)


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

    app.setWindowIcon(_app_icon())

    initial = [f for f in sys.argv[1:]
               if f.lower().endswith(".pdf") and os.path.isfile(f)]

    window = MainWindow(initial_files=initial or None)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
