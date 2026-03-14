#!/usr/bin/env python3
"""
PDF Compress — Desktop GUI (PySide6)
Requires: pip install PySide6 pikepdf pillow
"""

import os, sys, json, time
from pathlib import Path

from PySide6.QtCore import (
    Qt, Signal, QObject, QSize, QTimer, QSettings, QPropertyAnimation,
    QEasingCurve, Property, QRectF,
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QIcon, QKeySequence,
    QShortcut, QAction, QPainterPath, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QFrame, QScrollArea, QFileDialog,
    QProgressBar, QSizePolicy, QSpacerItem, QDialog, QDialogButtonBox,
    QHeaderView, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QGraphicsDropShadowEffect, QMenu, QLineEdit, QCheckBox,
    QSystemTrayIcon, QToolTip, QComboBox, QMessageBox,
)

from engine import (
    PRESETS, PRESET_ORDER, Preset, analyze_pdf, PDFAnalysis,
    compress_pdf, Result, fmt_size, EncryptedPDFError,
    CancelledError, FileTooLargeError, InvalidPDFError,
    find_ghostscript, setup_file_logging, create_backup,
)

import subprocess
import threading
import logging

log = logging.getLogger(__name__)

VERSION = "3.0.0"


# ═══════════════════════════════════════════════════════════════════
#  Theme system
# ═══════════════════════════════════════════════════════════════════

class Theme:
    def __init__(self, name, bg, surface, surface2, border, accent, accent_h,
                 accent_m, text, text2, text3, green, red, amber,
                 bar_bg, bar_fg, card_bg, card_border, card_sel):
        self.name = name
        self.bg = bg
        self.surface = surface
        self.surface2 = surface2
        self.border = border
        self.accent = accent
        self.accent_h = accent_h
        self.accent_m = accent_m
        self.text = text
        self.text2 = text2
        self.text3 = text3
        self.green = green
        self.red = red
        self.amber = amber
        self.bar_bg = bar_bg
        self.bar_fg = bar_fg
        self.card_bg = card_bg
        self.card_border = card_border
        self.card_sel = card_sel


LIGHT = Theme(
    name="light",
    bg="#f7f6f3",
    surface="#ffffff",
    surface2="#f0efec",
    border="#e2e0db",
    accent="#8a7750",
    accent_h="#a08a5e",
    accent_m="#c4b896",
    text="#1a1a1a",
    text2="#6b6966",
    text3="#9e9b96",
    green="#3d8c62",
    red="#c44040",
    amber="#a07828",
    bar_bg="#e8e6e1",
    bar_fg="#8a7750",
    card_bg="#ffffff",
    card_border="#e2e0db",
    card_sel="#f5f0e6",
)

DARK = Theme(
    name="dark",
    bg="#101012",
    surface="#18181b",
    surface2="#1f1f23",
    border="#26262b",
    accent="#9e8a60",
    accent_h="#b9a06e",
    accent_m="#6d5f40",
    text="#e6e4df",
    text2="#94938f",
    text3="#58585c",
    green="#5c9e78",
    red="#bf5555",
    amber="#b8942e",
    bar_bg="#26262b",
    bar_fg="#9e8a60",
    card_bg="#18181b",
    card_border="#26262b",
    card_sel="#1f1d17",
)

FONT = "Segoe UI" if sys.platform == "win32" else ".AppleSystemUIFont" if sys.platform == "darwin" else "Cantarell"


def build_stylesheet(t: Theme) -> str:
    return f"""
    QMainWindow, QWidget#central {{
        background: {t.bg};
    }}
    QLabel {{
        color: {t.text};
        background: transparent;
        font-family: "{FONT}";
    }}
    QFrame#separator {{
        background: {t.border};
        max-height: 1px; min-height: 1px;
    }}
    QFrame#fileListFrame {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 5px;
    }}
    QScrollArea {{
        background: {t.surface};
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 5px;
        margin: 2px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {t.border};
        min-height: 30px;
        border-radius: 2px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t.text3};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none; height: 0;
    }}
    QPushButton {{
        font-family: "{FONT}"; font-size: 11px;
        border: none; border-radius: 4px;
        padding: 7px 16px;
        color: {t.text}; background: {t.surface2};
    }}
    QPushButton:hover {{ background: {t.border}; }}
    QPushButton:disabled {{ color: {t.text3}; background: {t.surface}; }}
    QPushButton#primary {{
        background: {t.accent}; color: {"#0c0c0d" if t.name == "dark" else "#ffffff"};
        font-weight: 600; font-size: 12px; padding: 9px 24px;
    }}
    QPushButton#primary:hover {{ background: {t.accent_h}; }}
    QPushButton#primary:disabled {{ background: {t.accent_m}; color: {t.text3}; }}
    QPushButton#ghost {{
        background: transparent; color: {t.text3}; padding: 7px 10px;
    }}
    QPushButton#ghost:hover {{ color: {t.text2}; background: {t.surface2}; }}
    QPushButton#removeBtn {{
        background: transparent; color: {t.text3};
        font-size: 13px; padding: 2px 6px; border-radius: 3px;
    }}
    QPushButton#removeBtn:hover {{ color: {t.text}; background: {t.surface2}; }}
    QPushButton#themeBtn {{
        background: transparent; color: {t.text3};
        font-size: 14px; padding: 4px 8px; border-radius: 4px;
    }}
    QPushButton#themeBtn:hover {{ background: {t.surface2}; color: {t.text2}; }}
    QProgressBar {{
        background: {t.border}; border: none; border-radius: 2px;
        max-height: 3px; min-height: 3px;
    }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 2px; }}
    QCheckBox {{ color: {t.text2}; font-family: "{FONT}"; font-size: 9px; spacing: 6px; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px; border: 1px solid {t.border}; background: {t.surface}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
    QDialog {{ background: {t.bg}; }}
    QTableWidget {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; border-radius: 4px;
        gridline-color: {t.border};
        font-family: "{FONT}"; font-size: 10px;
    }}
    QTableWidget::item {{ padding: 4px 8px; }}
    QHeaderView::section {{
        background: {t.surface2}; color: {t.text2};
        border: none; border-bottom: 1px solid {t.border};
        padding: 6px 8px; font-weight: 600; font-size: 10px;
    }}
    """


# ═══════════════════════════════════════════════════════════════════
#  Signal bridge
# ═══════════════════════════════════════════════════════════════════

class Signals(QObject):
    progress = Signal(int, int, int, str)
    file_done = Signal(int, object)
    all_done = Signal(float)
    analysis_done = Signal(list)       # list of (path, PDFAnalysis) tuples


# ═══════════════════════════════════════════════════════════════════
#  Size bar widget
# ═══════════════════════════════════════════════════════════════════

class SizeBar(QWidget):
    """Horizontal bar showing original vs estimated/actual size."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self.ratio = 1.0
        self.color_fg = LIGHT.bar_fg
        self.color_bg = LIGHT.bar_bg

    def set_ratio(self, ratio, fg=None, bg=None):
        self.ratio = max(0.01, min(1.0, ratio))
        if fg: self.color_fg = fg
        if bg: self.color_bg = bg
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color_bg))
        path_bg = QPainterPath()
        path_bg.addRoundedRect(QRectF(0, 0, w, h), r, r)
        p.drawPath(path_bg)

        fw = max(h, w * self.ratio)
        p.setBrush(QColor(self.color_fg))
        path_fg = QPainterPath()
        path_fg.addRoundedRect(QRectF(0, 0, fw, h), r, r)
        p.drawPath(path_fg)
        p.end()


# ═══════════════════════════════════════════════════════════════════
#  Preset card widget
# ═══════════════════════════════════════════════════════════════════

class PresetCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, preset: Preset, theme: Theme, parent=None):
        super().__init__(parent)
        self.key = key
        self.preset = preset
        self.selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(42)
        self._theme = theme
        self._update_style()

        # Detailed tooltip for power users
        tooltip_parts = [
            f"Color DPI: {preset.target_dpi}",
            f"JPEG quality: {preset.jpeg_quality}%",
        ]
        if preset.gray_dpi > 0:
            tooltip_parts.append(f"Grayscale DPI: {preset.gray_dpi}")
        if preset.mono_dpi > 0:
            tooltip_parts.append(f"Monochrome DPI: {preset.mono_dpi}")
        tooltip_parts.append(f"Skip images below: {preset.skip_below_px}px")
        if preset.strip_metadata:
            tooltip_parts.append("Strips metadata (XMP, Info, thumbnails)")
        else:
            tooltip_parts.append("Preserves metadata")
        if preset.force_grayscale:
            tooltip_parts.append("Forces grayscale conversion")
        self.setToolTip("\n".join(tooltip_parts))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(0)

        self.name_lbl = QLabel(preset.name)
        self.name_lbl.setFont(QFont(FONT, 10, QFont.DemiBold))
        layout.addWidget(self.name_lbl)

        layout.addSpacing(12)

        self.desc_lbl = QLabel(preset.description)
        self.desc_lbl.setFont(QFont(FONT, 9))
        layout.addWidget(self.desc_lbl, 1)

    def set_selected(self, selected: bool):
        self.selected = selected
        self._update_style()

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._update_style()

    def _update_style(self):
        t = self._theme
        if self.selected:
            self.setStyleSheet(
                f"QFrame {{ background: {t.card_sel}; border: 1px solid {t.accent}; border-radius: 5px; }}"
            )
            nc = t.text
            dc = t.text2
        else:
            self.setStyleSheet(
                f"QFrame {{ background: {t.card_bg}; border: 1px solid {t.card_border}; border-radius: 5px; }}"
                f"QFrame:hover {{ border-color: {t.accent_m}; }}"
            )
            nc = t.text2
            dc = t.text3
        if hasattr(self, 'name_lbl'):
            self.name_lbl.setStyleSheet(f"color: {nc}; border: none; background: transparent;")
            self.desc_lbl.setStyleSheet(f"color: {dc}; border: none; background: transparent;")

    def mousePressEvent(self, event):
        self.clicked.emit(self.key)


# ═══════════════════════════════════════════════════════════════════
#  File row
# ═══════════════════════════════════════════════════════════════════

class FileRow(QFrame):
    remove_clicked = Signal(object)

    def __init__(self, filepath: str, analysis: PDFAnalysis, theme: Theme, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.analysis = analysis
        self.completed = False
        self._result: Optional[Result] = None
        self._theme = theme
        self.password: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(0)

        name = os.path.basename(filepath)
        self.name_lbl = QLabel(name if len(name) < 55 else name[:52] + "…")
        self.name_lbl.setFont(QFont(FONT, 9, QFont.DemiBold))
        self.name_lbl.setToolTip(filepath)
        top.addWidget(self.name_lbl, 1)

        # PDF/A badge
        self.pdfa_lbl = QLabel("")
        self.pdfa_lbl.setFont(QFont(FONT, 7, QFont.Bold))
        self.pdfa_lbl.setVisible(False)
        top.addWidget(self.pdfa_lbl)

        # Invalid PDF badge
        self.invalid_lbl = QLabel("")
        self.invalid_lbl.setFont(QFont(FONT, 7, QFont.Bold))
        self.invalid_lbl.setVisible(False)
        top.addWidget(self.invalid_lbl)

        self.info_btn = QPushButton("i")
        self.info_btn.setObjectName("removeBtn")
        self.info_btn.setFixedSize(24, 24)
        self.info_btn.setCursor(Qt.PointingHandCursor)
        self.info_btn.setToolTip("Space audit — click to see size breakdown")
        self.info_btn.clicked.connect(self._show_audit)
        top.addWidget(self.info_btn)

        self.rm_btn = QPushButton("×")
        self.rm_btn.setObjectName("removeBtn")
        self.rm_btn.setFixedSize(24, 24)
        self.rm_btn.setCursor(Qt.PointingHandCursor)
        self.rm_btn.clicked.connect(lambda: self.remove_clicked.emit(self))
        top.addWidget(self.rm_btn)

        layout.addLayout(top)

        self.status_lbl = QLabel("")
        self.status_lbl.setFont(QFont(FONT, 8))
        layout.addWidget(self.status_lbl)

        # Per-file progress bar
        self.file_progress = QProgressBar()
        self.file_progress.setFixedHeight(3)
        self.file_progress.setTextVisible(False)
        self.file_progress.setVisible(False)
        layout.addWidget(self.file_progress)

        self.bar = SizeBar()
        layout.addWidget(self.bar)

        self.border_line = QFrame()
        self.border_line.setFixedHeight(1)
        layout.addWidget(self.border_line)

        self._show_badges()
        self.apply_theme(theme)

    def _show_badges(self):
        a = self.analysis
        t = self._theme
        if not a.is_valid_pdf:
            self.invalid_lbl.setText(" NOT A PDF ")
            self.invalid_lbl.setStyleSheet(
                f"color: white; background: {t.red}; border-radius: 3px; "
                f"padding: 1px 4px; margin-right: 4px;"
            )
            self.invalid_lbl.setVisible(True)
        if a.pdfa_conformance:
            self.pdfa_lbl.setText(f" {a.pdfa_conformance} ")
            self.pdfa_lbl.setToolTip(
                f"This file is {a.pdfa_conformance} compliant.\n"
                "Metadata stripping may break compliance."
            )
            self.pdfa_lbl.setStyleSheet(
                f"color: white; background: {t.accent}; border-radius: 3px; "
                f"padding: 1px 4px; margin-right: 4px;"
            )
            self.pdfa_lbl.setVisible(True)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: transparent; border: none;")
        self.name_lbl.setStyleSheet(f"color: {theme.text}; background: transparent;")
        self.border_line.setStyleSheet(f"background: {theme.border};")
        self.bar.color_bg = theme.bar_bg
        self.bar.color_fg = theme.bar_fg
        if not self.completed:
            self.status_lbl.setStyleSheet(f"color: {theme.text2}; background: transparent;")
        self.file_progress.setStyleSheet(
            f"QProgressBar {{ background: {theme.bar_bg}; border: none; border-radius: 1px; }}"
            f"QProgressBar::chunk {{ background: {theme.accent}; border-radius: 1px; }}"
        )
        self._show_badges()
        self.bar.update()

    def update_estimate(self, preset_key: str):
        if self.completed:
            return
        t = self._theme

        # Handle invalid PDF files
        if not self.analysis.is_valid_pdf:
            self.status_lbl.setText(
                f"{fmt_size(self.analysis.file_size)}  ·  "
                "Not a valid PDF file"
            )
            self.status_lbl.setStyleSheet(f"color: {t.red}; background: transparent;")
            self.bar.set_ratio(1.0, fg=t.red, bg=t.bar_bg)
            return

        # Handle encrypted PDFs
        if self.analysis.is_encrypted:
            self.status_lbl.setText(
                f"{fmt_size(self.analysis.file_size)}  ·  "
                "Password-protected — cannot compress"
            )
            self.status_lbl.setStyleSheet(f"color: {t.red}; background: transparent;")
            self.bar.set_ratio(1.0, fg=t.red, bg=t.bar_bg)
            return

        preset = PRESETS[preset_key]
        a = self.analysis
        est = a.estimate_output(preset)
        ratio = est / a.file_size if a.file_size > 0 else 1.0
        pct = max(0, (1 - ratio) * 100)

        parts = [fmt_size(a.file_size)]
        if a.image_count > 0:
            parts.append(f"→ ~{fmt_size(est)}")
            parts.append(f"~{pct:.0f}% smaller")
        else:
            parts.append("no images to compress")
        parts.append(f"{a.page_count} pg · {a.image_count} img · {a.font_count} fonts")

        # PDF/A warning
        if a.pdfa_conformance and preset.strip_metadata:
            parts.append(f"⚠ {a.pdfa_conformance}")

        self.status_lbl.setText("  ·  ".join(parts))
        self.status_lbl.setStyleSheet(f"color: {t.text2}; background: transparent;")
        self.bar.set_ratio(ratio, fg=t.bar_fg, bg=t.bar_bg)

    def set_working(self):
        self.completed = False
        t = self._theme
        self.status_lbl.setText("Compressing…")
        self.status_lbl.setStyleSheet(f"color: {t.accent}; background: transparent;")
        self.rm_btn.setEnabled(False)
        self.file_progress.setVisible(True)
        self.file_progress.setValue(0)
        self.bar.set_ratio(1.0, fg=t.accent, bg=t.bar_bg)

    def set_progress(self, cur: int, total: int, status: str):
        self.status_lbl.setText(f"Image {cur}/{total}  ·  {status}")
        if total > 0:
            self.file_progress.setValue(int(cur / total * 100))

    def set_done(self, result: Result):
        self.completed = True
        self._result = result
        self.rm_btn.setEnabled(True)
        self.file_progress.setVisible(False)
        t = self._theme

        if result.skipped:
            self.status_lbl.setText(
                f"{fmt_size(result.original_size)}  ·  Already optimized — original kept"
            )
            self.status_lbl.setStyleSheet(f"color: {t.amber}; background: transparent;")
            self.bar.set_ratio(1.0, fg=t.amber, bg=t.bar_bg)
        else:
            ratio = result.compressed_size / result.original_size
            parts = [
                f"{fmt_size(result.original_size)} → {fmt_size(result.compressed_size)}",
                f"{result.saved_pct:.1f}% smaller",
            ]
            if result.backup_path:
                parts.append("backup saved")
            if result.pdfa_warning:
                parts.append(f"⚠ {result.pdfa_conformance} broken")
            self.status_lbl.setText("  ·  ".join(parts))
            self.status_lbl.setStyleSheet(f"color: {t.green}; background: transparent;")
            self.bar.set_ratio(ratio, fg=t.green, bg=t.bar_bg)

    def set_error(self, msg: str):
        self.completed = True
        self.rm_btn.setEnabled(True)
        self.file_progress.setVisible(False)
        t = self._theme
        self.status_lbl.setText(f"Error: {msg[:80]}")
        self.status_lbl.setStyleSheet(f"color: {t.red}; background: transparent;")
        self.bar.set_ratio(1.0, fg=t.red, bg=t.bar_bg)

    def _show_audit(self):
        dlg = SpaceAuditDialog(self.filepath, self.analysis, self._theme, self.window())
        dlg.exec()


# ═══════════════════════════════════════════════════════════════════
#  About dialog
# ═══════════════════════════════════════════════════════════════════

class AboutDialog(QDialog):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About PDF Compress")
        self.setFixedSize(360, 340 if sys.platform == "win32" else 280)
        t = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(0)

        title = QLabel("PDF Compress")
        title.setFont(QFont(FONT, 16, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(4)
        ver = QLabel(f"Version {VERSION}")
        ver.setFont(QFont(FONT, 10))
        ver.setStyleSheet(f"color: {t.text3};")
        ver.setAlignment(Qt.AlignCenter)
        layout.addWidget(ver)

        layout.addSpacing(20)
        desc = QLabel(
            "Fully offline PDF compression.\n"
            "DPI-aware image recompression with\n"
            "grayscale preservation, transparency handling,\n"
            "decompression bomb protection, and PDF/A detection.\n\n"
            "No files leave your machine.\n"
            "No account required. No tracking."
        )
        desc.setFont(QFont(FONT, 9))
        desc.setStyleSheet(f"color: {t.text2};")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Windows context menu registration
        if sys.platform == "win32":
            layout.addSpacing(12)
            self.ctx_btn = QPushButton("Register Windows context menu")
            self.ctx_btn.setFont(QFont(FONT, 9))
            self.ctx_btn.setCursor(Qt.PointingHandCursor)
            self.ctx_btn.clicked.connect(self._register_context_menu)
            self.ctx_btn.setStyleSheet(
                f"QPushButton {{ background: {t.surface2}; color: {t.text}; "
                f"border: 1px solid {t.border}; border-radius: 4px; padding: 7px 16px; }}"
                f"QPushButton:hover {{ background: {t.border}; }}"
            )
            layout.addWidget(self.ctx_btn)

        layout.addStretch()

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {t.border};")
        layout.addWidget(sep)

        layout.addSpacing(12)
        footer = QLabel("MIT License  ·  Frederik (C) 2026")
        footer.setFont(QFont(FONT, 8))
        footer.setStyleSheet(f"color: {t.text3};")
        footer.setAlignment(Qt.AlignCenter)
        layout.addWidget(footer)

    def _register_context_menu(self):
        """Register a Windows Explorer context menu entry for .pdf files."""
        from PySide6.QtWidgets import QMessageBox
        script_path = os.path.abspath(sys.argv[0])
        python_path = sys.executable
        cmd = (
            f'reg add "HKCU\\Software\\Classes\\SystemFileAssociations\\.pdf\\shell'
            f'\\CompressWithPDFCompress" /ve /d "Compress with PDF Compress" /f'
        )
        cmd2 = (
            f'reg add "HKCU\\Software\\Classes\\SystemFileAssociations\\.pdf\\shell'
            f'\\CompressWithPDFCompress\\command" /ve /d '
            f'"\\"{ python_path}\\" \\"{ script_path}\\" \\"%1\\"" /f'
        )
        try:
            subprocess.run(cmd, shell=True, check=True,
                           capture_output=True, text=True)
            subprocess.run(cmd2, shell=True, check=True,
                           capture_output=True, text=True)
            QMessageBox.information(
                self, "Success",
                "Context menu registered successfully.\n"
                "Right-click any .pdf file to see\n"
                "\"Compress with PDF Compress\"."
            )
        except subprocess.CalledProcessError as e:
            QMessageBox.warning(
                self, "Failed",
                f"Could not register context menu.\n{e.stderr or str(e)}"
            )


# ═══════════════════════════════════════════════════════════════════
#  Password dialog
# ═══════════════════════════════════════════════════════════════════

class PasswordDialog(QDialog):
    """Prompt for a PDF password."""

    def __init__(self, filename: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Password required")
        self.setFixedSize(380, 180)
        t = theme
        self.password = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)

        title = QLabel(f"Password required")
        title.setFont(QFont(FONT, 12, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        short_name = filename if len(filename) < 50 else filename[:47] + "…"
        desc = QLabel(f"{short_name} is password-protected.\nEnter the password to compress it.")
        desc.setFont(QFont(FONT, 9))
        desc.setStyleSheet(f"color: {t.text2};")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.pw_input = QLineEdit()
        self.pw_input.setEchoMode(QLineEdit.Password)
        self.pw_input.setPlaceholderText("Password")
        self.pw_input.setFont(QFont(FONT, 10))
        self.pw_input.setStyleSheet(
            f"QLineEdit {{ background: {t.surface}; color: {t.text}; "
            f"border: 1px solid {t.border}; border-radius: 4px; padding: 7px 10px; }}"
            f"QLineEdit:focus {{ border-color: {t.accent}; }}"
        )
        self.pw_input.returnPressed.connect(self._accept)
        layout.addWidget(self.pw_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        skip_btn = QPushButton("Skip")
        skip_btn.setObjectName("ghost")
        skip_btn.setCursor(Qt.PointingHandCursor)
        skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(skip_btn)

        ok_btn = QPushButton("Unlock")
        ok_btn.setObjectName("primary")
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.clicked.connect(self._accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

        QTimer.singleShot(100, self.pw_input.setFocus)

    def _accept(self):
        text = self.pw_input.text().strip()
        if text:
            self.password = text
            self.accept()


# ═══════════════════════════════════════════════════════════════════
#  Overwrite confirmation dialog
# ═══════════════════════════════════════════════════════════════════

class OverwriteDialog(QDialog):
    """Warn when output files already exist."""

    # Result codes
    OVERWRITE = 1
    SKIP = 2
    CANCEL = 0

    def __init__(self, filenames: list[str], theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Files already exist")
        self.setFixedWidth(420)
        t = theme
        self.result_action = self.CANCEL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)

        count = len(filenames)
        title = QLabel(
            f"{count} output file{'s' if count != 1 else ''} already "
            f"exist{'s' if count == 1 else ''}"
        )
        title.setFont(QFont(FONT, 12, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        names_text = "\n".join(
            os.path.basename(f) for f in filenames[:8]
        )
        if count > 8:
            names_text += f"\n… and {count - 8} more"
        names = QLabel(names_text)
        names.setFont(QFont(FONT, 9))
        names.setStyleSheet(f"color: {t.text2};")
        names.setWordWrap(True)
        layout.addWidget(names)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        overwrite_btn = QPushButton("Overwrite")
        overwrite_btn.setObjectName("primary")
        overwrite_btn.setCursor(Qt.PointingHandCursor)
        overwrite_btn.clicked.connect(self._overwrite)
        btn_row.addWidget(overwrite_btn)

        layout.addLayout(btn_row)

    def _overwrite(self):
        self.result_action = self.OVERWRITE
        self.accept()


# ═══════════════════════════════════════════════════════════════════
#  Space audit dialog
# ═══════════════════════════════════════════════════════════════════

class SpaceAuditDialog(QDialog):
    """Shows a breakdown of space usage inside a PDF."""

    def __init__(self, filepath: str, analysis: PDFAnalysis, theme: Theme, parent=None):
        super().__init__(parent)
        name = os.path.basename(filepath)
        self.setWindowTitle(f"Space Audit — {name}")
        self.setFixedSize(440, 300)
        t = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel(name if len(name) < 55 else name[:52] + "...")
        title.setFont(QFont(FONT, 12, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        total_lbl = QLabel(f"Total size: {fmt_size(analysis.file_size)}")
        total_lbl.setFont(QFont(FONT, 9))
        total_lbl.setStyleSheet(f"color: {t.text2};")
        layout.addWidget(total_lbl)

        # Build category data
        img_bytes = analysis.image_bytes
        fnt_bytes = analysis.font_bytes
        other_bytes = max(0, analysis.file_size - img_bytes - fnt_bytes)
        total = analysis.file_size or 1

        categories = [
            ("Images", img_bytes),
            ("Fonts (est.)", fnt_bytes),
            ("Other (structure, text, etc.)", other_bytes),
        ]

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Category", "Size", "Percentage", ""])
        table.setRowCount(len(categories))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setFixedHeight(120)

        bar_colors = [t.accent, t.green, t.text3]

        for i, (cat, size) in enumerate(categories):
            table.setItem(i, 0, QTableWidgetItem(cat))
            table.setItem(i, 1, QTableWidgetItem(fmt_size(size)))
            pct = (size / total * 100) if total > 0 else 0
            table.setItem(i, 2, QTableWidgetItem(f"{pct:.1f}%"))

            # Bar chart visualization
            bar_widget = QWidget()
            bar_layout = QHBoxLayout(bar_widget)
            bar_layout.setContentsMargins(4, 6, 4, 6)
            bar_frame = QFrame()
            bar_ratio = size / total if total > 0 else 0
            bar_width = max(2, int(bar_ratio * 120))
            bar_frame.setFixedSize(bar_width, 10)
            bar_frame.setStyleSheet(
                f"background: {bar_colors[i]}; border-radius: 3px;"
            )
            bar_layout.addWidget(bar_frame)
            bar_layout.addStretch()
            table.setCellWidget(i, 3, bar_widget)

            for col in [1, 2]:
                item = table.item(i, col)
                if item:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(table, 1)

        close = QPushButton("Close")
        close.setObjectName("primary")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedWidth(100)
        close.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close)
        layout.addLayout(btn_row)


# ═══════════════════════════════════════════════════════════════════
#  Batch summary dialog
# ═══════════════════════════════════════════════════════════════════

class SummaryDialog(QDialog):
    def __init__(self, results: list, elapsed: float, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compression Summary")
        self.setMinimumSize(520, 360)
        t = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        n_ok = sum(1 for r in results if isinstance(r, Result) and not r.skipped)
        n_skip = sum(1 for r in results if isinstance(r, Result) and r.skipped)
        total_orig = sum(r.original_size for r in results if isinstance(r, Result))
        total_comp = sum(r.compressed_size for r in results if isinstance(r, Result))
        total_saved = total_orig - total_comp

        header_parts = []
        if n_ok:
            header_parts.append(f"{n_ok} compressed")
        if n_skip:
            header_parts.append(f"{n_skip} already optimized")

        title = QLabel("  ·  ".join(header_parts) if header_parts else "Done")
        title.setFont(QFont(FONT, 13, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        stats_parts = []
        if total_saved > 0 and total_orig > 0:
            pct = total_saved / total_orig * 100
            stats_parts.append(f"Saved {fmt_size(total_saved)} ({pct:.0f}%)")
        stats_parts.append(f"{elapsed:.1f} seconds")
        stats = QLabel("  ·  ".join(stats_parts))
        stats.setFont(QFont(FONT, 10))
        stats.setStyleSheet(f"color: {t.text2};")
        layout.addWidget(stats)

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["File", "Original", "Compressed", "Saving"])
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSortIndicatorShown(True)
        alt_color = t.surface2 if t.name == "dark" else "#fafaf8"
        table.setStyleSheet(
            table.styleSheet() +
            f"QTableWidget {{ alternate-background-color: {alt_color}; }}"
        )

        valid = [r for r in results if isinstance(r, Result)]
        table.setRowCount(len(valid))

        for i, r in enumerate(valid):
            name = os.path.basename(r.input_path)
            table.setItem(i, 0, QTableWidgetItem(name))

            item1 = QTableWidgetItem(fmt_size(r.original_size))
            item1.setData(Qt.UserRole, r.original_size)
            table.setItem(i, 1, item1)

            if r.skipped:
                item2 = QTableWidgetItem("—")
                item2.setData(Qt.UserRole, r.original_size)
                table.setItem(i, 2, item2)
                item3 = QTableWidgetItem("already optimized")
                item3.setData(Qt.UserRole, 0)
                item3.setForeground(QColor(t.amber))
            else:
                item2 = QTableWidgetItem(fmt_size(r.compressed_size))
                item2.setData(Qt.UserRole, r.compressed_size)
                table.setItem(i, 2, item2)
                item3 = QTableWidgetItem(f"−{r.saved_pct:.1f}%")
                item3.setData(Qt.UserRole, r.saved_bytes)
                item3.setForeground(QColor(t.green))
            table.setItem(i, 3, item3)

            for col in [1, 2, 3]:
                item = table.item(i, col)
                if item:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(table, 1)

        close = QPushButton("Close")
        close.setObjectName("primary")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedWidth(100)
        close.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close)
        layout.addLayout(btn_row)


# ═══════════════════════════════════════════════════════════════════
#  Drop zone
# ═══════════════════════════════════════════════════════════════════

class DropZone(QFrame):
    clicked = Signal()

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(100)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(4)

        self.title_lbl = QLabel("Select PDFs")
        self.title_lbl.setFont(QFont(FONT, 11))
        self.title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_lbl)

        self.hint_lbl = QLabel("click  ·  or drag and drop files or folders")
        self.hint_lbl.setFont(QFont(FONT, 8))
        self.hint_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint_lbl)

        self.apply_theme(theme)

    def apply_theme(self, t: Theme):
        self._theme = t
        self.setStyleSheet(
            f"QFrame {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 5px; }}"
            f"QFrame:hover {{ border-color: {t.accent_m}; }}"
        )
        self.title_lbl.setStyleSheet(f"color: {t.text2}; border: none; background: transparent;")
        self.hint_lbl.setStyleSheet(f"color: {t.text3}; border: none; background: transparent;")

    def mousePressEvent(self, e):
        self.clicked.emit()


# ═══════════════════════════════════════════════════════════════════
#  Main window
# ═══════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self, initial_files=None):
        super().__init__()
        self.setWindowTitle("PDF Compress")
        self.resize(500, 680)
        self.setMinimumSize(420, 520)
        self.setAcceptDrops(True)

        self.rows: list[FileRow] = []
        self.preset_key = "standard"
        self.out_dir = None
        self.running = False
        self._results = []
        self._cancel_event: threading.Event | None = None

        self.settings = QSettings("PDFCompress", "PDFCompress")
        self._load_settings()

        saved_theme = self.settings.value("theme", "light")
        self.theme = LIGHT if saved_theme == "light" else DARK

        self.signals = Signals()
        self.signals.progress.connect(self._on_progress)
        self.signals.file_done.connect(self._on_file_done)
        self.signals.all_done.connect(self._on_all_done)
        self.signals.analysis_done.connect(self._on_analysis_done)

        # System tray icon for background notifications
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("PDF Compress")
        app_icon = QApplication.instance().windowIcon()
        if not app_icon.isNull():
            self.tray_icon.setIcon(app_icon)
        else:
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().StandardPixmap.SP_FileDialogDetailedView))
        self.tray_icon.setVisible(True)

        self._build()
        self._apply_theme()
        self._setup_shortcuts()

        if initial_files:
            QTimer.singleShot(100, lambda: self._add_files(initial_files))

    # ── Settings persistence ─────────────────────────────────────

    def _load_settings(self):
        saved_preset = self.settings.value("preset", "standard")
        if saved_preset in PRESETS:
            self.preset_key = saved_preset
        self.linearize = self.settings.value("linearize", "false") == "true"
        self.use_gs = self.settings.value("use_gs", "false") == "true"
        self.replace_original = self.settings.value("replace_original", "false") == "true"
        self._replace_warned = self.settings.value("replace_warned", "false") == "true"
        self.backup_enabled = self.settings.value("backup_enabled", "true") == "true"
        self.naming_template = self.settings.value("naming_template", "{name}_compressed")
        self._sort_key = self.settings.value("sort_key", "none")

    def _save_settings(self):
        self.settings.setValue("preset", self.preset_key)
        self.settings.setValue("theme", self.theme.name)
        self.settings.setValue("linearize", "true" if self.linearize else "false")
        self.settings.setValue("use_gs", "true" if self.use_gs else "false")
        self.settings.setValue("replace_original", "true" if self.replace_original else "false")
        self.settings.setValue("replace_warned", "true" if self._replace_warned else "false")
        self.settings.setValue("backup_enabled", "true" if self.backup_enabled else "false")
        self.settings.setValue("naming_template", self.naming_template)
        self.settings.setValue("sort_key", self._sort_key)

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)

    # ── Keyboard shortcuts ───────────────────────────────────────

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+O"), self, self._browse)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._run)
        QShortcut(QKeySequence("Escape"), self, self._clear)
        QShortcut(QKeySequence("Ctrl+T"), self, self._toggle_theme)
        QShortcut(QKeySequence("Ctrl+,"), self, self._show_about)

    # ── Drag and drop ────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.running:
            return
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if os.path.isfile(local) and local.lower().endswith(".pdf"):
                paths.append(local)
            elif os.path.isdir(local):
                # Recursively collect PDFs from dropped folders
                for root, _dirs, files in os.walk(local):
                    for f in sorted(files):
                        if f.lower().endswith(".pdf"):
                            paths.append(os.path.join(root, f))
        if paths:
            self._add_files(paths)

    # ── Build ────────────────────────────────────────────────────

    def _build(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(0)

        # ── Header row ──
        hdr = QHBoxLayout()
        self.title_lbl = QLabel("PDF Compress")
        self.title_lbl.setFont(QFont(FONT, 14, QFont.DemiBold))
        hdr.addWidget(self.title_lbl)
        hdr.addStretch()

        self.theme_btn = QPushButton("☾")
        self.theme_btn.setObjectName("themeBtn")
        self.theme_btn.setToolTip("Toggle dark/light mode  (Ctrl+T)")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(self._toggle_theme)
        hdr.addWidget(self.theme_btn)

        self.about_btn = QPushButton("?")
        self.about_btn.setObjectName("themeBtn")
        self.about_btn.setToolTip("About  (Ctrl+,)")
        self.about_btn.setCursor(Qt.PointingHandCursor)
        self.about_btn.clicked.connect(self._show_about)
        hdr.addWidget(self.about_btn)

        self.subtitle_lbl = QLabel("offline · no uploads · no tracking")
        self.subtitle_lbl.setFont(QFont(FONT, 8))
        root.addLayout(hdr)
        root.addSpacing(2)

        sub_row = QHBoxLayout()
        sub_row.addWidget(self.subtitle_lbl)
        sub_row.addStretch()

        self.shortcut_hint = QLabel("Ctrl+O add  ·  Ctrl+Enter compress")
        self.shortcut_hint.setFont(QFont(FONT, 7))
        sub_row.addWidget(self.shortcut_hint)
        root.addLayout(sub_row)

        root.addSpacing(12)
        root.addWidget(self._sep())

        # ── File area ──
        root.addSpacing(12)
        self.drop_zone = DropZone(self.theme)
        self.drop_zone.clicked.connect(self._browse)
        root.addWidget(self.drop_zone)

        self.scroll_frame = QFrame()
        self.scroll_frame.setObjectName("fileListFrame")
        sf_layout = QVBoxLayout(self.scroll_frame)
        sf_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.list_layout.addStretch()

        self.scroll.setWidget(self.list_widget)
        sf_layout.addWidget(self.scroll)

        self.scroll_frame.setVisible(False)
        root.addWidget(self.scroll_frame, 1)

        # ── Summary ──
        root.addSpacing(8)
        self.summary_lbl = QLabel("")
        self.summary_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.summary_lbl)

        # ── Preset cards ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(10)

        q_hdr = QHBoxLayout()
        self.q_title = QLabel("QUALITY")
        self.q_title.setFont(QFont(FONT, 8, QFont.Bold))
        q_hdr.addWidget(self.q_title)
        q_hdr.addStretch()
        self.q_detail = QLabel("")
        self.q_detail.setFont(QFont(FONT, 9))
        q_hdr.addWidget(self.q_detail)
        root.addLayout(q_hdr)
        root.addSpacing(6)

        self.preset_cards = {}
        self.presets_layout = QVBoxLayout()
        self.presets_layout.setSpacing(4)
        for key in PRESET_ORDER:
            card = PresetCard(key, PRESETS[key], self.theme)
            card.clicked.connect(self._on_preset)
            if key == self.preset_key:
                card.set_selected(True)
            self.preset_cards[key] = card
            self.presets_layout.addWidget(card)
        root.addLayout(self.presets_layout)
        self._update_q_detail()

        # ── Output ──
        root.addSpacing(10)
        root.addWidget(self._sep())
        root.addSpacing(8)

        out_row = QHBoxLayout()
        self.out_title = QLabel("Output")
        self.out_title.setFont(QFont(FONT, 9, QFont.DemiBold))
        out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Same folder as input")
        self.out_lbl.setFont(QFont(FONT, 9))
        out_row.addWidget(self.out_lbl)
        out_row.addStretch()

        out_change = QPushButton("Change")
        out_change.setObjectName("ghost")
        out_change.setFont(QFont(FONT, 8))
        out_change.setCursor(Qt.PointingHandCursor)
        out_change.clicked.connect(self._pick_out)
        out_row.addWidget(out_change)

        out_reset = QPushButton("Reset")
        out_reset.setObjectName("ghost")
        out_reset.setFont(QFont(FONT, 8))
        out_reset.setCursor(Qt.PointingHandCursor)
        out_reset.clicked.connect(self._reset_out)
        out_row.addWidget(out_reset)
        root.addLayout(out_row)

        # ── Naming template ──
        root.addSpacing(6)
        name_row = QHBoxLayout()
        name_lbl = QLabel("Naming:")
        name_lbl.setFont(QFont(FONT, 8))
        name_row.addWidget(name_lbl)
        self.naming_input = QLineEdit(self.naming_template)
        self.naming_input.setFont(QFont(FONT, 8))
        self.naming_input.setPlaceholderText("{name}_compressed")
        self.naming_input.setToolTip(
            "Output filename template. Variables:\n"
            "  {name} — original filename without extension\n"
            "  {preset} — preset name (e.g. standard)\n"
            "  {dpi} — target DPI\n"
            "Example: {name}_{preset}_{dpi}dpi"
        )
        self.naming_input.textChanged.connect(self._on_naming_changed)
        name_row.addWidget(self.naming_input, 1)
        root.addLayout(name_row)

        # ── Option checkboxes ──
        root.addSpacing(4)

        self.chk_linearize = QCheckBox("Web-optimized (linearized)")
        self.chk_linearize.setChecked(self.linearize)
        self.chk_linearize.toggled.connect(self._on_linearize_toggled)
        root.addWidget(self.chk_linearize)

        self.chk_gs = QCheckBox("Full optimization (requires Ghostscript)")
        self._gs_path = find_ghostscript()
        if self._gs_path:
            self.chk_gs.setChecked(self.use_gs)
        else:
            self.chk_gs.setChecked(False)
            self.chk_gs.setEnabled(False)
            self.chk_gs.setToolTip("Ghostscript not found — install from ghostscript.com")
            self.use_gs = False
        self.chk_gs.toggled.connect(self._on_gs_toggled)
        root.addWidget(self.chk_gs)

        self.chk_replace = QCheckBox("Replace original files")
        self.chk_replace.setChecked(self.replace_original)
        self.chk_replace.toggled.connect(self._on_replace_toggled)
        root.addWidget(self.chk_replace)

        self.chk_backup = QCheckBox("Create backup when replacing originals")
        self.chk_backup.setChecked(self.backup_enabled)
        self.chk_backup.setToolTip("Saves a .backup copy before overwriting the original")
        self.chk_backup.toggled.connect(self._on_backup_toggled)
        self.chk_backup.setVisible(self.replace_original)
        root.addWidget(self.chk_backup)

        # ── Progress ──
        root.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        # ── Action bar ──
        root.addSpacing(10)
        root.addWidget(self._sep())
        root.addSpacing(12)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("+ Add")
        self.btn_add.setFont(QFont(FONT, 10))
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self._browse)
        bar.addWidget(self.btn_add)

        self.btn_recent = QPushButton("Recent")
        self.btn_recent.setObjectName("ghost")
        self.btn_recent.setFont(QFont(FONT, 9))
        self.btn_recent.setCursor(Qt.PointingHandCursor)
        self.btn_recent.clicked.connect(self._show_recent_menu)
        bar.addWidget(self.btn_recent)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("ghost")
        self.btn_clear.setFont(QFont(FONT, 9))
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)
        bar.addWidget(self.btn_clear)

        self.btn_sort = QPushButton("Sort")
        self.btn_sort.setObjectName("ghost")
        self.btn_sort.setFont(QFont(FONT, 9))
        self.btn_sort.setCursor(Qt.PointingHandCursor)
        self.btn_sort.setToolTip("Sort file list")
        self.btn_sort.clicked.connect(self._show_sort_menu)
        bar.addWidget(self.btn_sort)

        bar.addStretch()

        self.btn_open = QPushButton("Open folder")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setFont(QFont(FONT, 9))
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(self._open_folder)
        self.btn_open.setVisible(False)
        bar.addWidget(self.btn_open)

        self.btn_go = QPushButton("Compress")
        self.btn_go.setObjectName("primary")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self._run)
        bar.addWidget(self.btn_go)
        root.addLayout(bar)

        # ── Status ──
        root.addSpacing(6)
        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 9))
        self.result_lbl.setAlignment(Qt.AlignCenter)
        self.result_lbl.setWordWrap(True)
        root.addWidget(self.result_lbl)

    def _sep(self):
        s = QFrame()
        s.setObjectName("separator")
        s.setFrameShape(QFrame.HLine)
        return s

    # ── Theme ────────────────────────────────────────────────────

    def _toggle_theme(self):
        self.theme = DARK if self.theme.name == "light" else LIGHT
        self._apply_theme()
        self._save_settings()

    def _apply_theme(self):
        t = self.theme
        QApplication.instance().setStyleSheet(build_stylesheet(t))

        self.theme_btn.setText("☀" if t.name == "dark" else "☾")
        self.title_lbl.setStyleSheet(f"color: {t.text};")
        self.subtitle_lbl.setStyleSheet(f"color: {t.text3};")
        self.shortcut_hint.setStyleSheet(f"color: {t.text3};")
        self.summary_lbl.setStyleSheet(f"color: {t.text2};")
        self.q_title.setStyleSheet(f"color: {t.text2};")
        self.q_detail.setStyleSheet(f"color: {t.text3};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")

        self.drop_zone.apply_theme(t)
        self.list_widget.setStyleSheet(f"background: {t.surface};")

        for card in self.preset_cards.values():
            card.apply_theme(t)

        for row in self.rows:
            row.apply_theme(t)

    # ── View switching ───────────────────────────────────────────

    def _show_drop(self):
        self.scroll_frame.setVisible(False)
        self.drop_zone.setVisible(True)

    def _show_list(self):
        self.drop_zone.setVisible(False)
        self.scroll_frame.setVisible(True)

    def _update_summary(self):
        n = len(self.rows)
        if not n:
            self.summary_lbl.setText("")
            return

        # Filter out encrypted files from estimates
        compressible = [r for r in self.rows if not r.analysis.is_encrypted]
        encrypted = n - len(compressible)

        if not compressible:
            self.summary_lbl.setText(
                f"{n} file{'s' if n != 1 else ''}  ·  all password-protected"
            )
            return

        total = sum(r.analysis.file_size for r in compressible)
        preset = PRESETS[self.preset_key]
        est = sum(r.analysis.estimate_output(preset) for r in compressible)
        pct = (1 - est / total) * 100 if total > 0 else 0
        total_img = sum(r.analysis.image_count for r in compressible)

        parts = [f"{n} file{'s' if n != 1 else ''}"]
        if encrypted:
            parts.append(f"{encrypted} locked")
        parts.append(f"{fmt_size(total)}  →  ~{fmt_size(est)}")
        parts.append(f"~{pct:.0f}% est. saving")
        parts.append(f"{total_img} images")
        self.summary_lbl.setText("  ·  ".join(parts))

    # ── Files ────────────────────────────────────────────────────

    def _browse(self):
        if self.running:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs", "", "PDF files (*.pdf);;All files (*)")
        if files:
            self._add_files(files)

    def _add_files(self, paths):
        existing = {r.filepath for r in self.rows}
        new = [p for p in paths
               if p.lower().endswith(".pdf") and os.path.isfile(p) and p not in existing]
        if not new:
            return

        # Run analysis in background thread to avoid freezing UI
        self.btn_add.setEnabled(False)
        self.btn_add.setText("Analyzing…")

        def _analyze():
            results = []
            for p in new:
                results.append((p, analyze_pdf(p)))
            self.signals.analysis_done.emit(results)

        threading.Thread(target=_analyze, daemon=True).start()

    def _on_analysis_done(self, results):
        """Called on main thread when background analysis finishes."""
        self.btn_add.setEnabled(True)
        self.btn_add.setText("+ Add")

        if not results:
            return
        if not self.rows:
            self._show_list()

        new_paths = []
        for path, analysis in results:
            # Check again in case duplicates were added while analyzing
            if any(r.filepath == path for r in self.rows):
                continue
            row = FileRow(path, analysis, self.theme)
            row.update_estimate(self.preset_key)
            row.remove_clicked.connect(self._remove_row)
            idx = self.list_layout.count() - 1
            self.list_layout.insertWidget(idx, row)
            self.rows.append(row)
            new_paths.append(path)

        # Save to recent files
        if new_paths:
            self._save_recent_files(new_paths)

        self._update_summary()
        self.btn_go.setEnabled(bool(self.rows))
        self.result_lbl.setText("")
        self.btn_open.setVisible(False)

    def _load_recent_files(self):
        raw = self.settings.value("recent_files", [])
        if isinstance(raw, str):
            raw = [raw] if raw else []
        return [f for f in raw if isinstance(f, str)]

    def _save_recent_files(self, new_paths):
        recent = self._load_recent_files()
        for p in new_paths:
            if p in recent:
                recent.remove(p)
            recent.insert(0, p)
        recent = recent[:20]
        self.settings.setValue("recent_files", recent)

    def _show_recent_menu(self):
        recent = self._load_recent_files()
        if not recent:
            return
        menu = QMenu(self)
        for path in recent:
            name = os.path.basename(path)
            action = menu.addAction(name)
            action.setToolTip(path)
            action.triggered.connect(lambda checked, p=path: self._add_recent_file(p))
        menu.exec(self.btn_recent.mapToGlobal(
            self.btn_recent.rect().bottomLeft()))

    def _add_recent_file(self, path):
        if os.path.isfile(path):
            self._add_files([path])

    def _remove_row(self, row):
        if self.running:
            return
        self.list_layout.removeWidget(row)
        row.deleteLater()
        self.rows.remove(row)
        if not self.rows:
            self._show_drop()
            self.btn_go.setEnabled(False)
        self._update_summary()

    def _clear(self):
        if self.running:
            return
        for row in list(self.rows):
            self.list_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        self._show_drop()
        self.btn_go.setEnabled(False)
        self._update_summary()
        self.result_lbl.setText("")
        self.btn_open.setVisible(False)

    # ── Output ───────────────────────────────────────────────────

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self.out_dir = d
            self.out_lbl.setText(d if len(d) < 38 else "…" + d[-35:])

    def _reset_out(self):
        self.out_dir = None
        self.out_lbl.setText("Same folder as input")

    def _on_linearize_toggled(self, checked):
        self.linearize = checked
        self._save_settings()

    def _on_gs_toggled(self, checked):
        self.use_gs = checked
        self._save_settings()

    def _on_replace_toggled(self, checked):
        if checked and not self._replace_warned:
            msg = QMessageBox(self)
            msg.setWindowTitle("Warning")
            msg.setText(
                "This will overwrite your original PDF files with the "
                "compressed versions.\n\n"
                "A .backup copy will be created if the backup option is enabled.\n\n"
                "Are you sure you want to enable this?"
            )
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg.setDefaultButton(QMessageBox.No)
            if msg.exec() != QMessageBox.Yes:
                self.chk_replace.setChecked(False)
                return
            self._replace_warned = True
        self.replace_original = checked
        self.chk_backup.setVisible(checked)
        self._save_settings()

    def _on_backup_toggled(self, checked: bool):
        self.backup_enabled = checked
        self._save_settings()

    def _on_naming_changed(self, text: str):
        self.naming_template = text.strip() or "{name}_compressed"
        self._save_settings()

    def _show_sort_menu(self):
        menu = QMenu(self)
        for key, label in [
            ("name", "Sort by name"),
            ("size", "Sort by size (largest first)"),
            ("pages", "Sort by page count"),
        ]:
            action = menu.addAction(label)
            action.triggered.connect(lambda checked, k=key: self._sort_files(k))
        menu.exec(self.btn_sort.mapToGlobal(
            self.btn_sort.rect().bottomLeft()))

    def _sort_files(self, key: str):
        if not self.rows or self.running:
            return
        self._sort_key = key

        if key == "name":
            self.rows.sort(key=lambda r: os.path.basename(r.filepath).lower())
        elif key == "size":
            self.rows.sort(key=lambda r: r.analysis.file_size, reverse=True)
        elif key == "pages":
            self.rows.sort(key=lambda r: r.analysis.page_count, reverse=True)

        # Re-order widgets in layout
        for i, row in enumerate(self.rows):
            self.list_layout.removeWidget(row)
        for i, row in enumerate(self.rows):
            self.list_layout.insertWidget(i, row)
        self._save_settings()

    def _build_output_name(self, filepath: str) -> str:
        """Build output filename from naming template."""
        name, ext = os.path.splitext(os.path.basename(filepath))
        preset = PRESETS[self.preset_key]
        template = self.naming_template or "{name}_compressed"

        try:
            output_name = template.format(
                name=name,
                preset=self.preset_key,
                dpi=preset.target_dpi,
            )
        except (KeyError, IndexError):
            output_name = f"{name}_compressed"

        return output_name + ext

    def _open_folder(self):
        if not self.rows:
            return
        folder = self.out_dir or os.path.dirname(self.rows[0].filepath)
        # Security: validate the path is actually a directory before opening
        if not os.path.isdir(folder):
            log.warning("Attempted to open non-directory path: %s", folder)
            return
        folder = os.path.abspath(folder)
        if sys.platform == "win32":    os.startfile(folder)
        elif sys.platform == "darwin": subprocess.Popen(["open", folder])
        else:                          subprocess.Popen(["xdg-open", folder])

    # ── Presets ──────────────────────────────────────────────────

    def _on_preset(self, key):
        self.preset_key = key
        for k, card in self.preset_cards.items():
            card.set_selected(k == key)
        self._update_q_detail()
        for row in self.rows:
            row.update_estimate(key)
        self._update_summary()
        self._save_settings()

    def _update_q_detail(self):
        p = PRESETS[self.preset_key]
        meta = "  ·  strips metadata" if p.strip_metadata else ""
        self.q_detail.setText(f"{p.target_dpi} DPI  ·  JPEG {p.jpeg_quality}%{meta}")

    # ── About ────────────────────────────────────────────────────

    def _show_about(self):
        dlg = AboutDialog(self.theme, self)
        dlg.exec()

    # ── Compression ──────────────────────────────────────────────

    def _run(self):
        if self.running or not self.rows:
            return

        # ── Warn about invalid PDFs ──────────────────────────────
        invalid = [r for r in self.rows if not r.analysis.is_valid_pdf]
        if invalid:
            names = ", ".join(os.path.basename(r.filepath) for r in invalid[:3])
            if len(invalid) > 3:
                names += f" (+{len(invalid) - 3} more)"
            msg = QMessageBox(self)
            msg.setWindowTitle("Invalid Files")
            msg.setText(
                f"The following files are not valid PDFs and will be skipped:\n\n{names}"
            )
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec()

        # ── Warn about PDF/A + metadata stripping ────────────────
        preset = PRESETS[self.preset_key]
        if preset.strip_metadata:
            pdfa_rows = [r for r in self.rows if r.analysis.pdfa_conformance]
            if pdfa_rows:
                names = ", ".join(
                    f"{os.path.basename(r.filepath)} ({r.analysis.pdfa_conformance})"
                    for r in pdfa_rows[:3]
                )
                msg = QMessageBox(self)
                msg.setWindowTitle("PDF/A Warning")
                msg.setText(
                    f"The selected preset strips metadata, which will break "
                    f"PDF/A compliance on:\n\n{names}\n\n"
                    "Continue anyway?"
                )
                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                msg.setDefaultButton(QMessageBox.Yes)
                if msg.exec() != QMessageBox.Yes:
                    return

        # ── Check for existing output files ──────────────────────
        existing_outputs = []
        if not self.replace_original:
            for row in self.rows:
                if row.analysis.is_encrypted and row.password is None:
                    continue
                if not row.analysis.is_valid_pdf:
                    continue
                if self.out_dir:
                    out_name = self._build_output_name(row.filepath)
                    out = os.path.join(self.out_dir, out_name)
                else:
                    name, ext = os.path.splitext(row.filepath)
                    out_name = self._build_output_name(row.filepath)
                    out = os.path.join(os.path.dirname(row.filepath), out_name)
                if os.path.exists(out):
                    existing_outputs.append(out)

        if existing_outputs:
            dlg = OverwriteDialog(existing_outputs, self.theme, self)
            dlg.exec()
            if dlg.result_action != OverwriteDialog.OVERWRITE:
                return

        # ── Prompt for passwords on encrypted files ──────────────
        for row in self.rows:
            if row.analysis.is_encrypted and row.password is None:
                dlg = PasswordDialog(os.path.basename(row.filepath), self.theme, self)
                if dlg.exec() == QDialog.Accepted and dlg.password:
                    row.password = dlg.password
                    # Re-analyze with password in background thread
                    self._reanalyze_with_password(row)

        self.running = True
        self._results = []
        self._cancel_event = threading.Event()
        self.btn_go.setText("Cancel")
        self.btn_go.setEnabled(True)
        self.btn_go.setStyleSheet("")
        try:
            self.btn_go.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_go.clicked.connect(self._cancel)
        self.btn_add.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.btn_open.setVisible(False)
        self.result_lbl.setText("")
        self.progress.setVisible(True)
        self.progress.setValue(0)

        for card in self.preset_cards.values():
            card.setEnabled(False)

        pk = self.preset_key
        use_gs = self.use_gs
        linearize = self.linearize
        replace_orig = self.replace_original
        backup = self.backup_enabled and replace_orig
        naming = self.naming_template
        row_snapshot = [
            (r.filepath, r.password, r.analysis.is_encrypted, r.analysis.is_valid_pdf)
            for r in self.rows
        ]
        threading.Thread(
            target=self._worker,
            args=(pk, row_snapshot, use_gs, linearize, replace_orig, backup, naming),
            daemon=True,
        ).start()

    def _reanalyze_with_password(self, row):
        """Re-analyze an encrypted PDF with password (synchronous for now during _run)."""
        try:
            row.analysis = analyze_pdf(row.filepath, password=row.password)
            row.update_estimate(self.preset_key)
        except Exception as e:
            log.warning("Re-analysis with password failed: %s", e)

    def _cancel(self):
        """User clicked Cancel during compression."""
        if self._cancel_event:
            self._cancel_event.set()
        self.btn_go.setEnabled(False)
        self.btn_go.setText("Cancelling…")

    def _worker(self, preset_key, row_snapshot, use_gs, linearize,
                replace_orig, backup_enabled, naming_template):
        t0 = time.time()
        cancel = self._cancel_event
        preset = PRESETS[preset_key]

        for i, (filepath, password, is_encrypted, is_valid) in enumerate(row_snapshot):
            if cancel and cancel.is_set():
                break

            # Skip invalid PDF files
            if not is_valid:
                self.signals.file_done.emit(
                    i, InvalidPDFError("Not a valid PDF file"))
                continue

            # Skip encrypted files without password
            if is_encrypted and password is None:
                self.signals.file_done.emit(
                    i, EncryptedPDFError("Password-protected — skipped"))
                continue

            if replace_orig:
                out = filepath
            elif self.out_dir:
                name, ext = os.path.splitext(os.path.basename(filepath))
                try:
                    out_name = (naming_template or "{name}_compressed").format(
                        name=name, preset=preset_key, dpi=preset.target_dpi,
                    ) + ext
                except (KeyError, IndexError):
                    out_name = f"{name}_compressed{ext}"
                out = os.path.join(self.out_dir, out_name)
            else:
                name, ext = os.path.splitext(os.path.basename(filepath))
                try:
                    out_name = (naming_template or "{name}_compressed").format(
                        name=name, preset=preset_key, dpi=preset.target_dpi,
                    ) + ext
                except (KeyError, IndexError):
                    out_name = f"{name}_compressed{ext}"
                out = os.path.join(os.path.dirname(filepath), out_name)

            self.signals.progress.emit(i, 0, 0, "Starting...")

            def cb(cur, total, status, _i=i):
                self.signals.progress.emit(_i, cur, total, status)

            try:
                result = compress_pdf(
                    filepath, out, preset_key=preset_key,
                    on_progress=cb, cancel=cancel, password=password,
                    use_ghostscript=use_gs, linearize=linearize,
                    backup_on_overwrite=backup_enabled,
                )
                self.signals.file_done.emit(i, result)
            except CancelledError:
                break
            except Exception as e:
                log.error("Compression failed for %s: %s", filepath, e)
                self.signals.file_done.emit(i, e)

        elapsed = time.time() - t0
        self.signals.all_done.emit(elapsed)

    def _on_progress(self, fi, cur, total, status):
        row = self.rows[fi]
        if cur == 0 and total == 0:
            row.set_working()
        else:
            row.set_progress(cur, total, status)
        n = len(self.rows)
        pct = (fi / n) * 100
        if total > 0:
            pct += (cur / total) * (100 / n)
        self.progress.setValue(int(min(100, pct)))

    def _on_file_done(self, fi, result):
        row = self.rows[fi]
        if isinstance(result, EncryptedPDFError):
            row.set_error(str(result))
            self._results.append(result)
        elif isinstance(result, Exception):
            row.set_error(str(result))
            self._results.append(result)
        else:
            row.set_done(result)
            self._results.append(result)

    def _on_all_done(self, elapsed):
        was_cancelled = self._cancel_event is not None and self._cancel_event.is_set()
        self.running = False
        self._cancel_event = None
        self.progress.setVisible(False)

        # Restore Compress button (disconnect cancel handler)
        try:
            self.btn_go.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_go.clicked.connect(self._run)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("Compress")
        self.btn_go.setStyleSheet("")

        self.btn_add.setEnabled(True)
        self.btn_clear.setEnabled(True)
        for card in self.preset_cards.values():
            card.setEnabled(True)

        t = self.theme
        results = [r for r in self._results if isinstance(r, Result)]
        n_ok = sum(1 for r in results if not r.skipped)
        n_skip = sum(1 for r in results if r.skipped)
        n_err = sum(1 for r in self._results if isinstance(r, Exception))
        total_saved = sum(r.saved_bytes for r in results)
        total_orig = sum(r.original_size for r in results)

        parts = []
        if was_cancelled:
            parts.append("Cancelled")
        if n_ok:   parts.append(f"{n_ok} compressed")
        if n_skip: parts.append(f"{n_skip} already optimized")
        if n_err:  parts.append(f"{n_err} failed")
        if total_saved > 0 and total_orig > 0:
            parts.append(f"saved {fmt_size(total_saved)} ({total_saved/total_orig*100:.0f}%)")
        parts.append(f"{elapsed:.1f}s")

        color = t.amber if was_cancelled else (t.green if n_ok else (t.amber if n_skip else t.red))
        self.result_lbl.setStyleSheet(f"color: {color};")
        self.result_lbl.setText("  ·  ".join(parts))
        self.summary_lbl.setText("  ·  ".join(parts))
        if n_ok or n_skip:
            self.btn_open.setVisible(True)

        if len(results) >= 2 and not was_cancelled:
            QTimer.singleShot(300, lambda: SummaryDialog(
                results, elapsed, self.theme, self
            ).exec())

        # System tray notification if window is minimized
        if self.isMinimized() and self.tray_icon.isVisible():
            tray_msg = "  ·  ".join(parts)
            self.tray_icon.showMessage(
                "PDF Compress", tray_msg,
                QSystemTrayIcon.MessageIcon.Information, 5000
            )


# ═══════════════════════════════════════════════════════════════════
#  Entry
# ═══════════════════════════════════════════════════════════════════

def _generate_app_icon() -> QIcon:
    """Generate a simple PDF icon programmatically (no external file needed)."""
    sizes = [16, 32, 48, 64, 128, 256]
    icon = QIcon()
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background rounded rectangle
        margin = size * 0.08
        rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
        painter.setBrush(QBrush(QColor("#8a7750")))
        painter.setPen(Qt.NoPen)
        radius = size * 0.15
        painter.drawRoundedRect(rect, radius, radius)

        # "PDF" text
        font = QFont("Segoe UI", max(1, int(size * 0.28)), QFont.Bold)
        painter.setFont(font)
        painter.setPen(QPen(QColor("white")))
        painter.drawText(rect, Qt.AlignCenter, "PDF")

        # Compression arrow (down-left)
        arrow_size = size * 0.18
        ax = size * 0.75
        ay = size * 0.75
        painter.setPen(QPen(QColor(255, 255, 255, 180), max(1, size * 0.04)))
        painter.drawLine(int(ax), int(ay - arrow_size), int(ax), int(ay))
        painter.drawLine(int(ax - arrow_size * 0.5), int(ay - arrow_size * 0.4),
                         int(ax), int(ay))

        painter.end()
        icon.addPixmap(pixmap)
    return icon


def main():
    # Set up file logging before anything else
    try:
        log_file = setup_file_logging()
        log.info("PDF Compress %s starting up", VERSION)
        log.info("Log file: %s", log_file)
    except Exception as e:
        print(f"Warning: Could not set up file logging: {e}", file=sys.stderr)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setFont(QFont(FONT, 9))
    app.setApplicationName("PDF Compress")
    app.setOrganizationName("PDFCompress")

    # Generate and set app icon
    app_icon = _generate_app_icon()
    app.setWindowIcon(app_icon)

    initial = [f for f in sys.argv[1:]
               if f.lower().endswith(".pdf") and os.path.isfile(f)]

    window = MainWindow(initial_files=initial or None)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
