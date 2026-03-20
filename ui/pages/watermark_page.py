"""Add Watermark — overlay text watermark with batch support and presets."""

import os
import json
import time
import threading
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QFileDialog, QProgressBar, QLineEdit, QSpinBox,
    QSlider, QComboBox, QMenu, QMessageBox,
)

from engine import fmt_size
from pdf_ops import add_watermark
from ..theme import Theme, FONT
from ..signals import Signals
from ..widgets import DropZone
from ..widgets_generic import GenericFileRow
from ..dialogs import OverwriteDialog, GenericSummaryDialog
from ..batch_helpers import (
    load_recent_files, save_recent_files, show_recent_menu,
    setup_standard_shortcuts, notify_tray_if_minimized,
)
from .base import BasePage

log = logging.getLogger(__name__)

# Built-in watermark presets
BUILTIN_PRESETS = {
    "CONFIDENTIAL": {
        "text": "CONFIDENTIAL", "font_size": 60, "rotation": -45,
        "opacity": 25, "position": "center",
    },
    "DRAFT": {
        "text": "DRAFT", "font_size": 72, "rotation": -45,
        "opacity": 20, "position": "center",
    },
    "DO NOT COPY": {
        "text": "DO NOT COPY", "font_size": 48, "rotation": -30,
        "opacity": 30, "position": "center",
    },
}


class WatermarkPage(BasePage):
    page_title = "Add Watermark"
    page_icon = "watermark"
    page_key = "watermark"

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.rows: list[GenericFileRow] = []
        self.running = False
        self._results = []
        self._cancel_event: threading.Event | None = None

        self.signals = Signals()
        self.signals.progress.connect(self._on_progress)
        self.signals.file_done.connect(self._on_file_done)
        self.signals.all_done.connect(self._on_all_done)

        self._load_settings()
        self._build()
        setup_standard_shortcuts(self, self._browse, self._run, self._clear)

    # ── Settings ────────────────────────────────────────────────────

    def _load_settings(self):
        s = self.shell.settings
        self._naming = s.value("watermark/naming", "{name}_watermarked")
        self._last_text = s.value("watermark/text", "")
        self._last_font = int(s.value("watermark/font_size", "48"))
        self._last_rotation = int(s.value("watermark/rotation", "-45"))
        self._last_opacity = int(s.value("watermark/opacity", "30"))
        self._last_position = s.value("watermark/position", "Center")

        # Load user presets
        raw = s.value("watermark/user_presets", "")
        try:
            self._user_presets = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            self._user_presets = {}

    def _save_settings(self):
        s = self.shell.settings
        s.setValue("watermark/naming", self._naming)
        s.setValue("watermark/text", self.text_input.text())
        s.setValue("watermark/font_size", str(self.font_spin.value()))
        s.setValue("watermark/rotation", str(self.rot_spin.value()))
        s.setValue("watermark/opacity", str(self.opacity_slider.value()))
        s.setValue("watermark/position", self.pos_combo.currentText())
        s.setValue("watermark/user_presets", json.dumps(self._user_presets))

    # ── Build UI ────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 20, 32, 24)
        root.setSpacing(0)

        # ── Drop zone ──
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Drop PDFs to watermark")
        self.drop_zone.hint_lbl.setText("add a text watermark to one or more PDFs")
        self.drop_zone.clicked.connect(self._browse)
        root.addWidget(self.drop_zone)

        # ── File list ──
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
        self.summary_lbl.setFont(QFont(FONT, 10))
        root.addWidget(self.summary_lbl)

        # ── Watermark Presets ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        preset_hdr = QHBoxLayout()
        self.wm_title = QLabel("WATERMARK")
        self.wm_title.setFont(QFont(FONT, 9, QFont.Bold))
        preset_hdr.addWidget(self.wm_title)
        preset_hdr.addStretch()

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Custom")
        for name in BUILTIN_PRESETS:
            self.preset_combo.addItem(name)
        for name in self._user_presets:
            self.preset_combo.addItem(f"\u2605 {name}")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_hdr.addWidget(self.preset_combo)

        self.btn_save_preset = QPushButton("Save preset")
        self.btn_save_preset.setObjectName("ghost")
        self.btn_save_preset.setFont(QFont(FONT, 9))
        self.btn_save_preset.setCursor(Qt.PointingHandCursor)
        self.btn_save_preset.clicked.connect(self._save_preset)
        preset_hdr.addWidget(self.btn_save_preset)

        root.addLayout(preset_hdr)
        root.addSpacing(6)

        # ── Watermark settings ──
        self.text_input = QLineEdit(self._last_text)
        self.text_input.setPlaceholderText("e.g. CONFIDENTIAL, DRAFT, DO NOT COPY")
        root.addWidget(self.text_input)
        root.addSpacing(6)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Font size:"))
        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 200)
        self.font_spin.setValue(self._last_font)
        r1.addWidget(self.font_spin)

        r1.addSpacing(12)
        r1.addWidget(QLabel("Rotation:"))
        self.rot_spin = QSpinBox()
        self.rot_spin.setRange(-180, 180)
        self.rot_spin.setValue(self._last_rotation)
        self.rot_spin.setSuffix("\u00b0")
        r1.addWidget(self.rot_spin)
        root.addLayout(r1)
        root.addSpacing(6)

        r2 = QHBoxLayout()
        self.opacity_lbl = QLabel(f"Opacity: {self._last_opacity}%")
        r2.addWidget(self.opacity_lbl)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.setValue(self._last_opacity)
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_lbl.setText(f"Opacity: {v}%"))
        r2.addWidget(self.opacity_slider, 1)
        root.addLayout(r2)
        root.addSpacing(6)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("Position:"))
        self.pos_combo = QComboBox()
        self.pos_combo.addItems([
            "Center", "Top-left", "Top-right",
            "Bottom-left", "Bottom-right",
        ])
        self.pos_combo.setCurrentText(self._last_position)
        r3.addWidget(self.pos_combo, 1)
        root.addLayout(r3)

        # ── Output ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        out_row = QHBoxLayout()
        self.out_title = QLabel("Output")
        self.out_title.setFont(QFont(FONT, 10, QFont.DemiBold))
        out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Same folder as input")
        self.out_lbl.setFont(QFont(FONT, 10))
        out_row.addWidget(self.out_lbl)
        out_row.addStretch()

        out_change = QPushButton("Change")
        out_change.setObjectName("ghost")
        out_change.setFont(QFont(FONT, 9))
        out_change.setCursor(Qt.PointingHandCursor)
        out_change.clicked.connect(self._pick_out)
        out_row.addWidget(out_change)

        out_reset = QPushButton("Reset")
        out_reset.setObjectName("ghost")
        out_reset.setFont(QFont(FONT, 9))
        out_reset.setCursor(Qt.PointingHandCursor)
        out_reset.clicked.connect(self._reset_out)
        out_row.addWidget(out_reset)
        root.addLayout(out_row)
        self.out_dir = None

        # Naming template
        root.addSpacing(6)
        name_row = QHBoxLayout()
        name_lbl = QLabel("Naming:")
        name_lbl.setFont(QFont(FONT, 9))
        name_row.addWidget(name_lbl)
        self.naming_input = QLineEdit(self._naming)
        self.naming_input.setFont(QFont(FONT, 9))
        self.naming_input.setPlaceholderText("{name}_watermarked")
        self.naming_input.setToolTip(
            "Output filename template. Variables:\n"
            "  {name} \u2014 original filename without extension"
        )
        self.naming_input.textChanged.connect(self._on_naming_changed)
        name_row.addWidget(self.naming_input, 1)
        root.addLayout(name_row)

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
        self.btn_add.setFont(QFont(FONT, 11))
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.clicked.connect(self._browse)
        bar.addWidget(self.btn_add)

        self.btn_recent = QPushButton("Recent")
        self.btn_recent.setObjectName("ghost")
        self.btn_recent.setFont(QFont(FONT, 10))
        self.btn_recent.setCursor(Qt.PointingHandCursor)
        self.btn_recent.clicked.connect(self._show_recent)
        bar.addWidget(self.btn_recent)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("ghost")
        self.btn_clear.setFont(QFont(FONT, 10))
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)
        bar.addWidget(self.btn_clear)

        bar.addStretch()

        self.btn_open = QPushButton("Open folder")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setFont(QFont(FONT, 10))
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(self._open_folder)
        self.btn_open.setVisible(False)
        bar.addWidget(self.btn_open)

        self.btn_go = QPushButton("Apply Watermark")
        self.btn_go.setObjectName("primary")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self._run)
        bar.addWidget(self.btn_go)
        root.addLayout(bar)

        # ── Result ──
        root.addSpacing(6)
        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 10))
        self.result_lbl.setAlignment(Qt.AlignCenter)
        self.result_lbl.setWordWrap(True)
        root.addWidget(self.result_lbl)

    def _sep(self):
        s = QFrame()
        s.setObjectName("separator")
        s.setFrameShape(QFrame.HLine)
        return s

    # ── Presets ─────────────────────────────────────────────────────

    def _on_preset_changed(self, index):
        text = self.preset_combo.currentText()
        if text == "Custom":
            return

        # Check built-in presets
        if text in BUILTIN_PRESETS:
            preset = BUILTIN_PRESETS[text]
        elif text.startswith("\u2605 "):
            name = text[2:]
            preset = self._user_presets.get(name, {})
        else:
            return

        self.text_input.setText(preset.get("text", ""))
        self.font_spin.setValue(preset.get("font_size", 48))
        self.rot_spin.setValue(preset.get("rotation", -45))
        self.opacity_slider.setValue(preset.get("opacity", 30))

        pos = preset.get("position", "center")
        pos_text = pos.replace("_", "-").title()
        idx = self.pos_combo.findText(pos_text)
        if idx >= 0:
            self.pos_combo.setCurrentIndex(idx)

    def _save_preset(self):
        text = self.text_input.text().strip()
        if not text:
            QMessageBox.warning(self, "No text",
                                "Enter watermark text before saving a preset.")
            return

        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return

        self._user_presets[name.strip()] = {
            "text": text,
            "font_size": self.font_spin.value(),
            "rotation": self.rot_spin.value(),
            "opacity": self.opacity_slider.value(),
            "position": self.pos_combo.currentText().lower().replace("-", "_"),
        }
        self._save_settings()

        # Rebuild combo
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("Custom")
        for n in BUILTIN_PRESETS:
            self.preset_combo.addItem(n)
        for n in self._user_presets:
            self.preset_combo.addItem(f"\u2605 {n}")
        self.preset_combo.blockSignals(False)
        self.preset_combo.setCurrentText(f"\u2605 {name.strip()}")

    # ── File management ─────────────────────────────────────────────

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
               if p.lower().endswith(".pdf") and os.path.isfile(p)
               and p not in existing]
        if not new:
            return

        if not self.rows:
            self._show_list()

        new_paths = []
        for path in new:
            row = GenericFileRow(path, self.shell.theme)
            row.remove_clicked.connect(self._remove_row)
            idx = self.list_layout.count() - 1
            self.list_layout.insertWidget(idx, row)
            self.rows.append(row)
            new_paths.append(path)

        if new_paths:
            save_recent_files(self.shell.settings, "watermark", new_paths)

        self._update_summary()
        self.btn_go.setEnabled(bool(self.rows))
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
        total = sum(os.path.getsize(r.filepath) for r in self.rows
                    if os.path.isfile(r.filepath))
        self.summary_lbl.setText(
            f"{n} file{'s' if n != 1 else ''}  \u00b7  {fmt_size(total)}"
        )

    def _show_recent(self):
        show_recent_menu(self, self.btn_recent, self.shell.settings,
                         "watermark", self._add_recent_file)

    def _add_recent_file(self, path):
        if os.path.isfile(path):
            self._add_files([path])

    # ── Output ──────────────────────────────────────────────────────

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self.out_dir = d
            self.out_lbl.setText(d if len(d) < 38 else "\u2026" + d[-35:])

    def _reset_out(self):
        self.out_dir = None
        self.out_lbl.setText("Same folder as input")

    def _on_naming_changed(self, text):
        import re
        raw = text.strip() or "{name}_watermarked"
        sanitized = raw.replace("/", "").replace("\\", "").replace("..", "")
        allowed = re.sub(r'\{(?!name\})[^}]*\}', '', sanitized)
        self._naming = allowed or "{name}_watermarked"
        self._save_settings()

    def _build_output_path(self, filepath: str) -> str:
        name, ext = os.path.splitext(os.path.basename(filepath))
        template = self._naming or "{name}_watermarked"
        try:
            output_name = template.format(name=name)
        except (KeyError, IndexError):
            output_name = f"{name}_watermarked"
        folder = self.out_dir or os.path.dirname(filepath)
        return os.path.join(folder, output_name + ext)

    def _open_folder(self):
        if not self.rows:
            return
        folder = self.out_dir or os.path.dirname(self.rows[0].filepath)
        folder = os.path.realpath(os.path.abspath(folder))
        if not os.path.isdir(folder):
            return
        if "\x00" in folder or ".." in os.path.basename(folder):
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ── Execution ───────────────────────────────────────────────────

    def _run(self):
        if self.running or not self.rows:
            return

        text = self.text_input.text().strip()
        if not text:
            QMessageBox.warning(self, "Text needed",
                                "Enter watermark text.")
            return

        # Check for existing outputs
        existing = []
        for row in self.rows:
            out = self._build_output_path(row.filepath)
            if os.path.exists(out):
                existing.append(out)
        if existing:
            dlg = OverwriteDialog(existing, self.shell.theme, self.window())
            dlg.exec()
            if dlg.result_action != OverwriteDialog.OVERWRITE:
                return

        self.running = True
        self._results = []
        self._cancel_event = threading.Event()
        self.btn_go.setText("Cancel")
        self.btn_go.setEnabled(True)
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
        self.progress.setMaximum(len(self.rows))

        config = {
            "text": text,
            "font_size": self.font_spin.value(),
            "rotation": self.rot_spin.value(),
            "opacity": self.opacity_slider.value() / 100.0,
            "position": self.pos_combo.currentText().lower().replace("-", "_"),
        }
        snapshot = [(r.filepath,) for r in self.rows]

        threading.Thread(
            target=self._worker,
            args=(snapshot, config),
            daemon=True,
        ).start()

    def _cancel(self):
        if self._cancel_event:
            self._cancel_event.set()
        self.btn_go.setEnabled(False)
        self.btn_go.setText("Cancelling\u2026")

    def _worker(self, snapshot, config):
        t0 = time.time()
        cancel = self._cancel_event

        for i, (filepath,) in enumerate(snapshot):
            if cancel and cancel.is_set():
                break

            self.signals.progress.emit(i, 0, 0, "Starting...")
            output = self._build_output_path(filepath)

            try:
                add_watermark(filepath, output, config)
                result = {
                    "file": os.path.basename(filepath),
                    "status": "OK",
                    "details": config["text"],
                    "output": output,
                }
                self.signals.file_done.emit(i, result)
            except Exception as e:
                log.error("Watermark failed for %s: %s", filepath, e)
                result = {
                    "file": os.path.basename(filepath),
                    "status": "Error",
                    "details": str(e)[:80],
                }
                self.signals.file_done.emit(i, e)

            self._results.append(result)

        elapsed = time.time() - t0
        self.signals.all_done.emit(elapsed)

    def _on_progress(self, fi, cur, total, status):
        if fi < len(self.rows):
            self.rows[fi].set_working("Applying watermark\u2026")
        self.progress.setValue(fi)

    def _on_file_done(self, fi, result):
        if fi >= len(self.rows):
            return
        row = self.rows[fi]
        if isinstance(result, Exception):
            row.set_error(str(result))
        else:
            out_name = os.path.basename(result.get("output", ""))
            row.set_done(f"Watermarked \u2192 {out_name}")
        self.progress.setValue(fi + 1)

    def _on_all_done(self, elapsed):
        was_cancelled = self._cancel_event and self._cancel_event.is_set()
        self.running = False
        self._cancel_event = None
        self.progress.setVisible(False)

        try:
            self.btn_go.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_go.clicked.connect(self._run)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("Apply Watermark")
        self.btn_add.setEnabled(True)
        self.btn_clear.setEnabled(True)

        t = self.shell.theme
        n_ok = sum(1 for r in self._results if r.get("status") != "Error")
        n_err = sum(1 for r in self._results if r.get("status") == "Error")

        parts = []
        if was_cancelled:
            parts.append("Cancelled")
        if n_ok:
            parts.append(f"{n_ok} watermarked")
        if n_err:
            parts.append(f"{n_err} failed")
        parts.append(f"{elapsed:.1f}s")

        color = t.amber if was_cancelled else (t.green if n_ok else t.red)
        self.result_lbl.setStyleSheet(f"color: {color};")
        self.result_lbl.setText("  \u00b7  ".join(parts))
        self.summary_lbl.setText("  \u00b7  ".join(parts))

        if n_ok:
            self.btn_open.setVisible(True)

        if len(self._results) >= 2 and not was_cancelled:
            columns = [
                ("file", "File"),
                ("status", "Status"),
                ("details", "Watermark"),
            ]
            QTimer.singleShot(300, lambda: GenericSummaryDialog(
                "Watermark Complete", self._results, columns,
                elapsed, self.shell.theme, self.window()
            ).exec())

        msg = "  \u00b7  ".join(parts)
        notify_tray_if_minimized(self.shell, msg)
        self._save_settings()

    # ── BasePage interface ──────────────────────────────────────────

    def is_busy(self):
        return self.running

    def handle_drop(self, paths):
        if not self.running and paths:
            self._add_files(paths)

    def apply_theme(self, theme):
        t = theme
        self.summary_lbl.setStyleSheet(f"color: {t.text2};")
        self.wm_title.setStyleSheet(f"color: {t.text2};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.opacity_lbl.setStyleSheet(f"color: {t.text2};")
        self.drop_zone.apply_theme(t)
        self.list_widget.setStyleSheet(f"background: {t.surface};")
        for row in self.rows:
            row.apply_theme(t)
