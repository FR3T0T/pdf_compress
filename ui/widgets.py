"""Reusable widgets — SizeBar, PresetCard, FileRow, DropZone, ToolCard."""

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QFont, QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QProgressBar,
)

from engine import (
    PRESETS, Preset, PDFAnalysis, Result, fmt_size,
)
from .theme import Theme, FONT


# ═══════════════════════════════════════════════════════════════════
#  Size bar widget
# ═══════════════════════════════════════════════════════════════════

class SizeBar(QWidget):
    """Horizontal bar showing original vs estimated/actual size."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self.ratio = 1.0
        self.color_fg = "#6366f1"
        self.color_bg = "#e2e8f0"

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
        self.setFixedHeight(48)
        self._theme = theme

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
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(0)

        # Selection dot
        self.dot = QLabel()
        self.dot.setFixedSize(16, 16)
        layout.addWidget(self.dot)
        layout.addSpacing(10)

        self.name_lbl = QLabel(preset.name)
        self.name_lbl.setFont(QFont(FONT, 10, QFont.DemiBold))
        layout.addWidget(self.name_lbl)

        layout.addSpacing(14)

        self.desc_lbl = QLabel(preset.description)
        self.desc_lbl.setFont(QFont(FONT, 10))
        layout.addWidget(self.desc_lbl, 1)

        self._update_style()

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
                f"QFrame {{ background: {t.accent_soft}; "
                f"border: 2px solid {t.accent}; border-radius: 10px; }}"
            )
            nc = t.text
            dc = t.text2
            self.dot.setStyleSheet(
                f"background: {t.accent}; border: none; border-radius: 7px; "
                f"min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; "
                f"margin: 1px;"
            )
        else:
            self.setStyleSheet(
                f"QFrame {{ background: {t.card_bg}; "
                f"border: 1px solid {t.card_border}; border-radius: 10px; }}"
                f"QFrame:hover {{ border-color: {t.accent_m}; "
                f"background: {t.surface2}; }}"
            )
            nc = t.text2
            dc = t.text3
            self.dot.setStyleSheet(
                f"background: transparent; border: 2px solid {t.border2}; border-radius: 7px; "
                f"min-width: 14px; max-width: 14px; min-height: 14px; max-height: 14px; "
                f"margin: 1px;"
            )
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
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(0)

        name = os.path.basename(filepath)
        self.name_lbl = QLabel(name if len(name) < 55 else name[:52] + "\u2026")
        self.name_lbl.setFont(QFont(FONT, 10, QFont.DemiBold))
        self.name_lbl.setToolTip(filepath)
        top.addWidget(self.name_lbl, 1)

        # PDF/A badge
        self.pdfa_lbl = QLabel("")
        self.pdfa_lbl.setFont(QFont(FONT, 8, QFont.Bold))
        self.pdfa_lbl.setVisible(False)
        top.addWidget(self.pdfa_lbl)

        # Invalid PDF badge
        self.invalid_lbl = QLabel("")
        self.invalid_lbl.setFont(QFont(FONT, 8, QFont.Bold))
        self.invalid_lbl.setVisible(False)
        top.addWidget(self.invalid_lbl)

        self.info_btn = QPushButton("i")
        self.info_btn.setObjectName("removeBtn")
        self.info_btn.setFixedSize(26, 26)
        self.info_btn.setCursor(Qt.PointingHandCursor)
        self.info_btn.setToolTip("Space audit \u2014 click to see size breakdown")
        top.addWidget(self.info_btn)

        self.rm_btn = QPushButton("\u00d7")
        self.rm_btn.setObjectName("removeBtn")
        self.rm_btn.setFixedSize(26, 26)
        self.rm_btn.setCursor(Qt.PointingHandCursor)
        self.rm_btn.clicked.connect(lambda: self.remove_clicked.emit(self))
        top.addWidget(self.rm_btn)

        layout.addLayout(top)

        self.status_lbl = QLabel("")
        self.status_lbl.setFont(QFont(FONT, 9))
        layout.addWidget(self.status_lbl)

        # Per-file progress bar
        self.file_progress = QProgressBar()
        self.file_progress.setFixedHeight(4)
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
                f"color: white; background: {t.red}; border-radius: 4px; "
                f"padding: 2px 6px; margin-right: 4px;"
            )
            self.invalid_lbl.setVisible(True)
        if a.pdfa_conformance:
            self.pdfa_lbl.setText(f" {a.pdfa_conformance} ")
            self.pdfa_lbl.setToolTip(
                f"This file is {a.pdfa_conformance} compliant.\n"
                "Metadata stripping may break compliance."
            )
            self.pdfa_lbl.setStyleSheet(
                f"color: white; background: {t.accent}; border-radius: 4px; "
                f"padding: 2px 6px; margin-right: 4px;"
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
            f"QProgressBar {{ background: {theme.bar_bg}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {theme.accent}; border-radius: 2px; }}"
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
                f"{fmt_size(self.analysis.file_size)}  \u00b7  "
                "Not a valid PDF file"
            )
            self.status_lbl.setStyleSheet(f"color: {t.red}; background: transparent;")
            self.bar.set_ratio(1.0, fg=t.red, bg=t.bar_bg)
            return

        # Handle encrypted PDFs
        if self.analysis.is_encrypted:
            self.status_lbl.setText(
                f"{fmt_size(self.analysis.file_size)}  \u00b7  "
                "Password-protected \u2014 cannot compress"
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
            parts.append(f"\u2192 ~{fmt_size(est)}")
            parts.append(f"~{pct:.0f}% smaller")
        else:
            parts.append("no images to compress")
        parts.append(f"{a.page_count} pg \u00b7 {a.image_count} img \u00b7 {a.font_count} fonts")

        # PDF/A warning
        if a.pdfa_conformance and preset.strip_metadata:
            parts.append(f"\u26a0 {a.pdfa_conformance}")

        self.status_lbl.setText("  \u00b7  ".join(parts))
        self.status_lbl.setStyleSheet(f"color: {t.text2}; background: transparent;")
        self.bar.set_ratio(ratio, fg=t.bar_fg, bg=t.bar_bg)

    def set_working(self):
        self.completed = False
        t = self._theme
        self.status_lbl.setText("Compressing\u2026")
        self.status_lbl.setStyleSheet(f"color: {t.accent}; background: transparent;")
        self.rm_btn.setEnabled(False)
        self.file_progress.setVisible(True)
        self.file_progress.setValue(0)
        self.bar.set_ratio(1.0, fg=t.accent, bg=t.bar_bg)

    def set_progress(self, cur: int, total: int, status: str):
        self.status_lbl.setText(f"Image {cur}/{total}  \u00b7  {status}")
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
                f"{fmt_size(result.original_size)}  \u00b7  Already optimized \u2014 original kept"
            )
            self.status_lbl.setStyleSheet(f"color: {t.amber}; background: transparent;")
            self.bar.set_ratio(1.0, fg=t.amber, bg=t.bar_bg)
        else:
            ratio = result.compressed_size / result.original_size
            parts = [
                f"{fmt_size(result.original_size)} \u2192 {fmt_size(result.compressed_size)}",
                f"{result.saved_pct:.1f}% smaller",
            ]
            if result.backup_path:
                parts.append("backup saved")
            if result.pdfa_warning:
                parts.append(f"\u26a0 {result.pdfa_conformance} broken")
            self.status_lbl.setText("  \u00b7  ".join(parts))
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


# ═══════════════════════════════════════════════════════════════════
#  Drop zone
# ═══════════════════════════════════════════════════════════════════

class DropZone(QFrame):
    clicked = Signal()

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(180)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(12)

        # Upload icon — larger for visual impact
        from .icons import draw_icon
        self.upload_icon = QLabel()
        self.upload_icon.setFixedSize(52, 52)
        self.upload_icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.upload_icon, 0, Qt.AlignCenter)

        self.title_lbl = QLabel("Drop PDFs here")
        self.title_lbl.setFont(QFont(FONT, 15, QFont.Bold))
        self.title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_lbl)

        self.hint_lbl = QLabel("or click to browse files and folders")
        self.hint_lbl.setFont(QFont(FONT, 10))
        self.hint_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint_lbl)

        # Supported formats label
        self.format_lbl = QLabel("PDF")
        self.format_lbl.setFont(QFont(FONT, 8, QFont.Bold))
        self.format_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.format_lbl)

        self.apply_theme(theme)

    def apply_theme(self, t: Theme):
        self._theme = t
        self.setStyleSheet(
            f"QFrame {{ background: {t.surface}; "
            f"border: 2px dashed {t.border2}; border-radius: 16px; }}"
            f"QFrame:hover {{ border-color: {t.accent}; "
            f"background: {t.accent_soft}; }}"
        )
        self.title_lbl.setStyleSheet(f"color: {t.text}; border: none; background: transparent;")
        self.hint_lbl.setStyleSheet(f"color: {t.text3}; border: none; background: transparent;")
        self.format_lbl.setStyleSheet(
            f"color: {t.accent}; border: none; background: {t.accent_soft}; "
            f"border-radius: 4px; padding: 2px 10px;"
        )

        from .icons import draw_icon
        from PySide6.QtGui import QColor
        pm = draw_icon("upload", 52, QColor(t.accent))
        self.upload_icon.setPixmap(pm)
        self.upload_icon.setStyleSheet("border: none; background: transparent;")

    def mousePressEvent(self, e):
        self.clicked.emit()


# ═══════════════════════════════════════════════════════════════════
#  Tool card (for dashboard)
# ═══════════════════════════════════════════════════════════════════

class ToolCard(QFrame):
    """Clickable card representing a tool on the dashboard."""
    clicked = Signal(str)  # emits tool key

    def __init__(self, key: str, title: str, description: str,
                 icon_name: str, theme: Theme, parent=None):
        super().__init__(parent)
        self._key = key
        self._theme = theme
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("toolCard")
        self.setFixedHeight(88)
        self.setMinimumWidth(180)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        # Icon in a rounded badge
        self._icon_name = icon_name
        self.icon_badge = QFrame()
        self.icon_badge.setFixedSize(44, 44)
        icon_badge_layout = QHBoxLayout(self.icon_badge)
        icon_badge_layout.setContentsMargins(0, 0, 0, 0)
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(26, 26)
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        icon_badge_layout.addWidget(self.icon_lbl, 0, Qt.AlignCenter)
        layout.addWidget(self.icon_badge)

        # Text stack
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self.title_lbl = QLabel(title)
        self.title_lbl.setFont(QFont(FONT, 10, QFont.Bold))
        text_layout.addWidget(self.title_lbl)

        self.desc_lbl = QLabel(description)
        self.desc_lbl.setFont(QFont(FONT, 9))
        self.desc_lbl.setWordWrap(True)
        text_layout.addWidget(self.desc_lbl)

        layout.addLayout(text_layout, 1)

        # Arrow indicator
        self.arrow_lbl = QLabel("\u203a")
        self.arrow_lbl.setFont(QFont(FONT, 16))
        self.arrow_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.arrow_lbl)

        self.apply_theme(theme)

    def _update_icon(self, theme: Theme):
        from .icons import draw_icon
        from PySide6.QtGui import QColor
        pm = draw_icon(self._icon_name, 26, QColor(theme.accent))
        self.icon_lbl.setPixmap(pm)

    def apply_theme(self, t: Theme):
        self._theme = t
        self._update_icon(t)
        self.icon_badge.setStyleSheet(
            f"QFrame {{ background: {t.accent_soft}; border: none; border-radius: 12px; }}"
        )
        self.setStyleSheet(
            f"QFrame#toolCard {{ background: {t.card_bg}; "
            f"border: 1px solid {t.card_border}; border-radius: 12px; }}"
            f"QFrame#toolCard:hover {{ border-color: {t.accent}; "
            f"background: {t.accent_soft}; }}"
        )
        self.title_lbl.setStyleSheet(f"color: {t.text}; background: transparent; border: none;")
        self.desc_lbl.setStyleSheet(f"color: {t.text3}; background: transparent; border: none;")
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")
        self.arrow_lbl.setStyleSheet(f"color: {t.text3}; background: transparent; border: none;")

    def mousePressEvent(self, e):
        self.clicked.emit(self._key)
