"""Generic file row widget — reusable across non-compression batch pages."""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
)

from engine import fmt_size
from .theme import Theme, FONT


class GenericFileRow(QFrame):
    """Simplified file row for batch operations (protect, unlock, watermark, etc.).

    Unlike FileRow (compress-specific), this widget has no dependency on
    PDFAnalysis or compression presets.
    """
    remove_clicked = Signal(object)

    def __init__(self, filepath: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.completed = False
        self._theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(5)

        # ── Top row: filename + buttons ──
        top = QHBoxLayout()
        top.setSpacing(0)

        name = os.path.basename(filepath)
        self.name_lbl = QLabel(name if len(name) < 55 else name[:52] + "\u2026")
        self.name_lbl.setFont(QFont(FONT, 10, QFont.DemiBold))
        self.name_lbl.setToolTip(filepath)
        top.addWidget(self.name_lbl, 1)

        # Optional badge (e.g., "EPDF", "ENCRYPTED")
        self.badge_lbl = QLabel("")
        self.badge_lbl.setFont(QFont(FONT, 8, QFont.Bold))
        self.badge_lbl.setVisible(False)
        top.addWidget(self.badge_lbl)

        self.rm_btn = QPushButton("\u00d7")
        self.rm_btn.setObjectName("removeBtn")
        self.rm_btn.setFixedSize(26, 26)
        self.rm_btn.setCursor(Qt.PointingHandCursor)
        self.rm_btn.clicked.connect(lambda: self.remove_clicked.emit(self))
        top.addWidget(self.rm_btn)

        layout.addLayout(top)

        # ── Status label ──
        self.status_lbl = QLabel("")
        self.status_lbl.setFont(QFont(FONT, 9))
        layout.addWidget(self.status_lbl)

        # ── Per-file progress bar (hidden by default) ──
        self.file_progress = QProgressBar()
        self.file_progress.setFixedHeight(4)
        self.file_progress.setTextVisible(False)
        self.file_progress.setVisible(False)
        layout.addWidget(self.file_progress)

        # ── Bottom border ──
        self.border_line = QFrame()
        self.border_line.setFixedHeight(1)
        layout.addWidget(self.border_line)

        # Show file size
        try:
            size = os.path.getsize(filepath)
            self.status_lbl.setText(fmt_size(size))
        except OSError:
            self.status_lbl.setText("unknown size")

        self.apply_theme(theme)

    def set_badge(self, text: str, color: str = ""):
        """Show a small badge next to the filename (e.g., 'EPDF', 'AES-256')."""
        t = self._theme
        bg = color or t.accent
        self.badge_lbl.setText(f" {text} ")
        self.badge_lbl.setStyleSheet(
            f"color: white; background: {bg}; border-radius: 4px; "
            f"padding: 2px 6px; margin-right: 4px;"
        )
        self.badge_lbl.setVisible(True)

    def set_status(self, text: str, color: str = ""):
        """Update the status label with optional color."""
        t = self._theme
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(
            f"color: {color or t.text2}; background: transparent;"
        )

    def set_working(self, text: str = "Processing\u2026"):
        """Show working state with progress bar."""
        self.completed = False
        t = self._theme
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(f"color: {t.accent}; background: transparent;")
        self.rm_btn.setEnabled(False)
        self.file_progress.setVisible(True)
        self.file_progress.setMaximum(0)  # indeterminate
        self.file_progress.setValue(0)

    def set_progress(self, cur: int, total: int, status: str = ""):
        """Update progress bar and status text."""
        if total > 0:
            self.file_progress.setMaximum(100)
            self.file_progress.setValue(int(cur / total * 100))
        if status:
            self.status_lbl.setText(status)

    def set_done(self, text: str, color: str = ""):
        """Mark as completed with success message."""
        self.completed = True
        t = self._theme
        self.rm_btn.setEnabled(True)
        self.file_progress.setVisible(False)
        self.status_lbl.setText(text)
        self.status_lbl.setStyleSheet(
            f"color: {color or t.green}; background: transparent;"
        )

    def set_error(self, msg: str):
        """Mark as completed with error."""
        self.completed = True
        t = self._theme
        self.rm_btn.setEnabled(True)
        self.file_progress.setVisible(False)
        self.status_lbl.setText(f"Error: {msg[:80]}")
        self.status_lbl.setStyleSheet(f"color: {t.red}; background: transparent;")

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet("background: transparent; border: none;")
        self.name_lbl.setStyleSheet(f"color: {theme.text}; background: transparent;")
        self.border_line.setStyleSheet(f"background: {theme.border};")
        self.file_progress.setStyleSheet(
            f"QProgressBar {{ background: {theme.bar_bg}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {theme.accent}; border-radius: 2px; }}"
        )
        if not self.completed:
            self.status_lbl.setStyleSheet(
                f"color: {theme.text2}; background: transparent;"
            )
