"""Extract Images — pull all images from a PDF."""
import os, threading, logging
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QFileDialog, QProgressBar, QComboBox, QSpinBox)
from engine import fmt_size
from pdf_ops import extract_images
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage
log = logging.getLogger(__name__)
class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
class ExtractImagesPage(BasePage):
    page_title = "Extract Images"
    page_icon = "extract_img"
    page_key = "extract_images"
    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell; self.running = False; self._input_path = None
        self.signals = _Signals(); self.signals.done.connect(self._on_done); self.signals.error.connect(self._on_error)
        self._build()
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(28, 16, 28, 20); root.setSpacing(0)
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Select a PDF"); self.drop_zone.hint_lbl.setText("extract all embedded images")
        self.drop_zone.clicked.connect(self._browse); root.addWidget(self.drop_zone)
        self.file_frame = QFrame(); fl = QHBoxLayout(self.file_frame); fl.setContentsMargins(0, 8, 0, 8)
        self.file_lbl = QLabel(""); self.file_lbl.setFont(QFont(FONT, 10, QFont.DemiBold)); fl.addWidget(self.file_lbl, 1)
        btn_change = QPushButton("Change"); btn_change.setObjectName("ghost"); btn_change.setFont(QFont(FONT, 9)); btn_change.setCursor(Qt.PointingHandCursor); btn_change.clicked.connect(self._browse); fl.addWidget(btn_change)
        self.file_frame.setVisible(False); root.addWidget(self.file_frame)
        root.addSpacing(8); root.addWidget(self._sep()); root.addSpacing(8)
        r1 = QHBoxLayout(); r1.addWidget(QLabel("Format:"))
        self.fmt_combo = QComboBox(); self.fmt_combo.addItems(["PNG", "JPEG", "TIFF"]); r1.addWidget(self.fmt_combo, 1); root.addLayout(r1); root.addSpacing(4)
        r2 = QHBoxLayout(); r2.addWidget(QLabel("Min size (px):"))
        self.min_spin = QSpinBox(); self.min_spin.setRange(0, 1000); self.min_spin.setValue(100); r2.addWidget(self.min_spin, 1); root.addLayout(r2)
        root.addSpacing(8); root.addWidget(self._sep()); root.addSpacing(8)
        out_row = QHBoxLayout(); self.out_title = QLabel("Output folder"); self.out_title.setFont(QFont(FONT, 9, QFont.DemiBold)); out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Same folder as input"); self.out_lbl.setFont(QFont(FONT, 9)); out_row.addWidget(self.out_lbl); out_row.addStretch()
        out_change = QPushButton("Change"); out_change.setObjectName("ghost"); out_change.setFont(QFont(FONT, 9)); out_change.setCursor(Qt.PointingHandCursor); out_change.clicked.connect(self._pick_output); out_row.addWidget(out_change)
        root.addLayout(out_row); self._output_dir = None
        root.addSpacing(8); self.progress = QProgressBar(); self.progress.setVisible(False); self.progress.setTextVisible(False); root.addWidget(self.progress)
        self.result_lbl = QLabel(""); self.result_lbl.setFont(QFont(FONT, 9)); root.addWidget(self.result_lbl)
        root.addStretch(); root.addWidget(self._sep()); root.addSpacing(12)
        bar = QHBoxLayout(); bar.addStretch()
        self.btn_extract = QPushButton("Extract"); self.btn_extract.setObjectName("primary"); self.btn_extract.setCursor(Qt.PointingHandCursor); self.btn_extract.clicked.connect(self._run); bar.addWidget(self.btn_extract); root.addLayout(bar)
    def _sep(self): s = QFrame(); s.setObjectName("separator"); s.setFixedHeight(1); return s
    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF files (*.pdf)")
        if path: self._load_file(path)
    def _load_file(self, path):
        self._input_path = path; name = os.path.basename(path)
        self.file_lbl.setText(name if len(name) < 45 else name[:42] + "\u2026"); self.file_lbl.setToolTip(path)
        self.drop_zone.setVisible(False); self.file_frame.setVisible(True); self.result_lbl.setText("")
    def _pick_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output folder")
        if d: self._output_dir = d; self.out_lbl.setText(os.path.basename(d) or d)
    def _run(self):
        if self.running or not self._input_path: return
        output_dir = self._output_dir or os.path.dirname(self._input_path)
        self.running = True; self.progress.setVisible(True); self.progress.setMaximum(0); self.btn_extract.setEnabled(False); self.result_lbl.setText("")
        path, fmt, min_size, signals = self._input_path, self.fmt_combo.currentText().lower(), self.min_spin.value(), self.signals
        def _worker():
            try: result = extract_images(path, output_dir, fmt, min_size); signals.done.emit(result)
            except Exception as e: signals.error.emit(str(e))
        threading.Thread(target=_worker, daemon=True).start()
    def _on_done(self, result):
        self.running = False; self.progress.setVisible(False); self.btn_extract.setEnabled(True)
        n = result.total_images
        self.result_lbl.setText(f"Extracted {n} image{'s' if n != 1 else ''}"); self.result_lbl.setStyleSheet(f"color: {self.shell.theme.green};")
    def _on_error(self, msg):
        self.running = False; self.progress.setVisible(False); self.btn_extract.setEnabled(True)
        self.result_lbl.setText(f"Error: {msg[:80]}"); self.result_lbl.setStyleSheet(f"color: {self.shell.theme.red};")
    def is_busy(self): return self.running
    def handle_drop(self, paths):
        if paths: self._load_file(paths[0])
    def apply_theme(self, theme):
        t = theme; self.file_lbl.setStyleSheet(f"color: {t.text};"); self.out_title.setStyleSheet(f"color: {t.text2};"); self.out_lbl.setStyleSheet(f"color: {t.text3};"); self.result_lbl.setStyleSheet(f"color: {t.green};"); self.drop_zone.apply_theme(t)
