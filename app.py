#!/usr/bin/env python3
"""
PDF Compress — Desktop GUI (PySide6)
Requires: pip install PySide6 pikepdf pillow
"""

import os, sys, json, time, threading
from pathlib import Path

from PySide6.QtCore import (
    Qt, Signal, QObject, QSize, QTimer, QSettings, QPropertyAnimation,
    QEasingCurve, Property, QRectF,
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QBrush, QIcon, QKeySequence,
    QShortcut, QAction, QPainterPath,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QFrame, QScrollArea, QFileDialog,
    QProgressBar, QSizePolicy, QSpacerItem, QDialog, QDialogButtonBox,
    QHeaderView, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QGraphicsDropShadowEffect, QMenu,
)

from engine import (
    PRESETS, PRESET_ORDER, Preset, analyze_pdf, PDFAnalysis,
    compress_pdf, Result, fmt_size, EncryptedPDFError,
)

import subprocess

VERSION = "2.1.0"


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
        self._result = None
        self._theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(0)

        name = os.path.basename(filepath)
        self.name_lbl = QLabel(name if len(name) < 55 else name[:52] + "…")
        self.name_lbl.setFont(QFont(FONT, 9, QFont.DemiBold))
        top.addWidget(self.name_lbl, 1)

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

        self.bar = SizeBar()
        layout.addWidget(self.bar)

        self.border_line = QFrame()
        self.border_line.setFixedHeight(1)
        layout.addWidget(self.border_line)

        self.apply_theme(theme)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: transparent; border: none;")
        self.name_lbl.setStyleSheet(f"color: {theme.text}; background: transparent;")
        self.border_line.setStyleSheet(f"background: {theme.border};")
        self.bar.color_bg = theme.bar_bg
        self.bar.color_fg = theme.bar_fg
        if not self.completed:
            self.status_lbl.setStyleSheet(f"color: {theme.text2}; background: transparent;")
        self.bar.update()

    def update_estimate(self, preset_key: str):
        if self.completed:
            return
        t = self._theme

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
        parts.append(f"{a.page_count} pg · {a.image_count} img")

        self.status_lbl.setText("  ·  ".join(parts))
        self.status_lbl.setStyleSheet(f"color: {t.text2}; background: transparent;")
        self.bar.set_ratio(ratio, fg=t.bar_fg, bg=t.bar_bg)

    def set_working(self):
        self.completed = False
        t = self._theme
        self.status_lbl.setText("Compressing…")
        self.status_lbl.setStyleSheet(f"color: {t.accent}; background: transparent;")
        self.rm_btn.setEnabled(False)
        self.bar.set_ratio(1.0, fg=t.accent, bg=t.bar_bg)

    def set_progress(self, cur, total, status):
        self.status_lbl.setText(f"Image {cur}/{total}  ·  {status}")

    def set_done(self, result: Result):
        self.completed = True
        self._result = result
        self.rm_btn.setEnabled(True)
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
            self.status_lbl.setText("  ·  ".join(parts))
            self.status_lbl.setStyleSheet(f"color: {t.green}; background: transparent;")
            self.bar.set_ratio(ratio, fg=t.green, bg=t.bar_bg)

    def set_error(self, msg):
        self.completed = True
        self.rm_btn.setEnabled(True)
        t = self._theme
        self.status_lbl.setText(f"Error: {msg[:80]}")
        self.status_lbl.setStyleSheet(f"color: {t.red}; background: transparent;")
        self.bar.set_ratio(1.0, fg=t.red, bg=t.bar_bg)


# ═══════════════════════════════════════════════════════════════════
#  About dialog
# ═══════════════════════════════════════════════════════════════════

class AboutDialog(QDialog):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About PDF Compress")
        self.setFixedSize(360, 280)
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
            "and decompression bomb protection.\n\n"
            "No files leave your machine.\n"
            "No account required. No tracking."
        )
        desc.setFont(QFont(FONT, 9))
        desc.setStyleSheet(f"color: {t.text2};")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addStretch()

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {t.border};")
        layout.addWidget(sep)

        layout.addSpacing(12)
        footer = QLabel("MIT License  ·  Frederik © 2026")
        footer.setFont(QFont(FONT, 8))
        footer.setStyleSheet(f"color: {t.text3};")
        footer.setAlignment(Qt.AlignCenter)
        layout.addWidget(footer)


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
            table.setItem(i, 1, QTableWidgetItem(fmt_size(r.original_size)))
            if r.skipped:
                table.setItem(i, 2, QTableWidgetItem("—"))
                item3 = QTableWidgetItem("already optimized")
                item3.setForeground(QColor(t.amber))
            else:
                table.setItem(i, 2, QTableWidgetItem(fmt_size(r.compressed_size)))
                item3 = QTableWidgetItem(f"−{r.saved_pct:.1f}%")
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

        self.hint_lbl = QLabel("click  ·  or drag and drop files onto window")
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

        self.settings = QSettings("PDFCompress", "PDFCompress")
        self._load_settings()

        saved_theme = self.settings.value("theme", "light")
        self.theme = LIGHT if saved_theme == "light" else DARK

        self.signals = Signals()
        self.signals.progress.connect(self._on_progress)
        self.signals.file_done.connect(self._on_file_done)
        self.signals.all_done.connect(self._on_all_done)

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

    def _save_settings(self):
        self.settings.setValue("preset", self.preset_key)
        self.settings.setValue("theme", self.theme.name)

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
        files = [url.toLocalFile() for url in event.mimeData().urls()
                 if url.toLocalFile().lower().endswith(".pdf") and os.path.isfile(url.toLocalFile())]
        if files:
            self._add_files(files)

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

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("ghost")
        self.btn_clear.setFont(QFont(FONT, 9))
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)
        bar.addWidget(self.btn_clear)

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
        if not self.rows:
            self._show_list()
        for path in new:
            analysis = analyze_pdf(path)
            row = FileRow(path, analysis, self.theme)
            row.update_estimate(self.preset_key)
            row.remove_clicked.connect(self._remove_row)
            idx = self.list_layout.count() - 1
            self.list_layout.insertWidget(idx, row)
            self.rows.append(row)
        self._update_summary()
        self.btn_go.setEnabled(True)
        self.result_lbl.setText("")
        self.btn_open.setVisible(False)

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

    def _open_folder(self):
        if not self.rows:
            return
        folder = self.out_dir or os.path.dirname(self.rows[0].filepath)
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
        self.running = True
        self._results = []
        self.btn_go.setEnabled(False)
        self.btn_go.setText("Compressing…")
        self.btn_add.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.btn_open.setVisible(False)
        self.result_lbl.setText("")
        self.progress.setVisible(True)
        self.progress.setValue(0)

        for card in self.preset_cards.values():
            card.setEnabled(False)

        pk = self.preset_key
        threading.Thread(target=self._worker, args=(pk,), daemon=True).start()

    def _worker(self, preset_key):
        n = len(self.rows)
        t0 = time.time()

        for i, row in enumerate(self.rows):
            # Skip encrypted files in the worker
            if row.analysis.is_encrypted:
                self.signals.file_done.emit(
                    i, EncryptedPDFError("Password-protected — skipped"))
                continue

            if self.out_dir:
                nm, ext = os.path.splitext(os.path.basename(row.filepath))
                out = os.path.join(self.out_dir, f"{nm}_compressed{ext}")
            else:
                base, ext = os.path.splitext(row.filepath)
                out = f"{base}_compressed{ext}"

            self.signals.progress.emit(i, 0, 0, "Starting…")

            def cb(cur, total, status, _i=i):
                self.signals.progress.emit(_i, cur, total, status)

            try:
                result = compress_pdf(row.filepath, out, preset_key=preset_key,
                                      on_progress=cb)
                self.signals.file_done.emit(i, result)
            except Exception as e:
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
        self.running = False
        self.progress.setVisible(False)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("Compress")
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
        if n_ok:   parts.append(f"{n_ok} compressed")
        if n_skip: parts.append(f"{n_skip} already optimized")
        if n_err:  parts.append(f"{n_err} failed")
        if total_saved > 0 and total_orig > 0:
            parts.append(f"saved {fmt_size(total_saved)} ({total_saved/total_orig*100:.0f}%)")
        parts.append(f"{elapsed:.1f}s")

        color = t.green if n_ok else (t.amber if n_skip else t.red)
        self.result_lbl.setStyleSheet(f"color: {color};")
        self.result_lbl.setText("  ·  ".join(parts))
        self.summary_lbl.setText("  ·  ".join(parts))
        self.btn_open.setVisible(True)

        if len(results) >= 2:
            QTimer.singleShot(300, lambda: SummaryDialog(
                results, elapsed, self.theme, self
            ).exec())


# ═══════════════════════════════════════════════════════════════════
#  Entry
# ═══════════════════════════════════════════════════════════════════

def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setFont(QFont(FONT, 9))
    app.setApplicationName("PDF Compress")
    app.setOrganizationName("PDFCompress")

    initial = [f for f in sys.argv[1:]
               if f.lower().endswith(".pdf") and os.path.isfile(f)]

    window = MainWindow(initial_files=initial or None)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
