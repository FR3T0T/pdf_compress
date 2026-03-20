"""Metadata Editor — view and edit PDF properties."""
import os, threading, logging
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QFileDialog, QProgressBar, QLineEdit)
from engine import fmt_size
from pdf_ops import read_metadata, write_metadata
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage
log = logging.getLogger(__name__)
class _Signals(QObject):
    loaded = Signal(dict)
    done = Signal(str)
    error = Signal(str)
class MetadataPage(BasePage):
    page_title = "Metadata"
    page_icon = "metadata"
    page_key = "metadata"
    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell; self.running = False; self._input_path = None
        self.signals = _Signals(); self.signals.loaded.connect(self._on_loaded); self.signals.done.connect(self._on_done); self.signals.error.connect(self._on_error)
        self._build()
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(28, 16, 28, 20); root.setSpacing(0)
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Select a PDF"); self.drop_zone.hint_lbl.setText("view and edit document metadata")
        self.drop_zone.clicked.connect(self._browse); root.addWidget(self.drop_zone)
        self.file_frame = QFrame(); fl = QHBoxLayout(self.file_frame); fl.setContentsMargins(0, 8, 0, 8)
        self.file_lbl = QLabel(""); self.file_lbl.setFont(QFont(FONT, 10, QFont.DemiBold)); fl.addWidget(self.file_lbl, 1)
        btn_change = QPushButton("Change"); btn_change.setObjectName("ghost"); btn_change.setFont(QFont(FONT, 9)); btn_change.setCursor(Qt.PointingHandCursor); btn_change.clicked.connect(self._browse); fl.addWidget(btn_change)
        self.file_frame.setVisible(False); root.addWidget(self.file_frame)
        root.addSpacing(8); root.addWidget(self._sep()); root.addSpacing(8)
        fields_lbl = QLabel("DOCUMENT PROPERTIES"); fields_lbl.setFont(QFont(FONT, 8, QFont.Bold)); root.addWidget(fields_lbl); self._fields_lbl = fields_lbl; root.addSpacing(6)
        self._field_inputs = {}
        for field_name in ["Title", "Author", "Subject", "Keywords", "Creator", "Producer"]:
            row = QHBoxLayout(); lbl = QLabel(f"{field_name}:"); lbl.setFont(QFont(FONT, 9)); lbl.setFixedWidth(70); row.addWidget(lbl)
            inp = QLineEdit(); inp.setFont(QFont(FONT, 9)); row.addWidget(inp, 1)
            self._field_inputs[field_name.lower()] = inp; root.addLayout(row); root.addSpacing(3)
        root.addSpacing(8); root.addWidget(self._sep()); root.addSpacing(8)
        out_row = QHBoxLayout(); self.out_title = QLabel("Output"); self.out_title.setFont(QFont(FONT, 9, QFont.DemiBold)); out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Choose output"); self.out_lbl.setFont(QFont(FONT, 9)); out_row.addWidget(self.out_lbl); out_row.addStretch()
        out_change = QPushButton("Change"); out_change.setObjectName("ghost"); out_change.setFont(QFont(FONT, 9)); out_change.setCursor(Qt.PointingHandCursor); out_change.clicked.connect(self._pick_output); out_row.addWidget(out_change)
        root.addLayout(out_row); self._output_path = None
        root.addSpacing(8); self.progress = QProgressBar(); self.progress.setVisible(False); self.progress.setTextVisible(False); root.addWidget(self.progress)
        self.result_lbl = QLabel(""); self.result_lbl.setFont(QFont(FONT, 9)); root.addWidget(self.result_lbl)
        root.addStretch(); root.addWidget(self._sep()); root.addSpacing(12)
        bar = QHBoxLayout(); bar.addStretch()
        self.btn_save = QPushButton("Save"); self.btn_save.setObjectName("primary"); self.btn_save.setCursor(Qt.PointingHandCursor); self.btn_save.clicked.connect(self._run); bar.addWidget(self.btn_save); root.addLayout(bar)
    def _sep(self): s = QFrame(); s.setObjectName("separator"); s.setFixedHeight(1); return s
    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF files (*.pdf)")
        if path: self._load_file(path)
    def _load_file(self, path):
        self._input_path = path; name = os.path.basename(path)
        self.file_lbl.setText(name if len(name) < 45 else name[:42] + "\u2026"); self.file_lbl.setToolTip(path)
        self.drop_zone.setVisible(False); self.file_frame.setVisible(True); self.result_lbl.setText("")
        base, ext = os.path.splitext(path); self._output_path = base + "_edited" + ext; self.out_lbl.setText(os.path.basename(self._output_path))
        signals = self.signals
        def _probe():
            try:
                meta = read_metadata(path); signals.loaded.emit(meta)
            except Exception as e: signals.error.emit(str(e))
        threading.Thread(target=_probe, daemon=True).start()
    def _on_loaded(self, meta):
        for key, inp in self._field_inputs.items():
            inp.setText(meta.get(key, ""))
    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save", self._output_path or "", "PDF files (*.pdf)")
        if path: self._output_path = path; self.out_lbl.setText(os.path.basename(path))
    def _run(self):
        if self.running or not self._input_path: return
        self.running = True; self.progress.setVisible(True); self.progress.setMaximum(0); self.btn_save.setEnabled(False); self.result_lbl.setText("")
        meta = {k: inp.text() for k, inp in self._field_inputs.items()}
        path, output, signals = self._input_path, self._output_path, self.signals
        def _worker():
            try: write_metadata(path, output, meta); signals.done.emit(output)
            except Exception as e: signals.error.emit(str(e))
        threading.Thread(target=_worker, daemon=True).start()
    def _on_done(self, output):
        self.running = False; self.progress.setVisible(False); self.btn_save.setEnabled(True)
        self.result_lbl.setText(f"Metadata saved \u2192 {os.path.basename(output)}"); self.result_lbl.setStyleSheet(f"color: {self.shell.theme.green};")
    def _on_error(self, msg):
        self.running = False; self.progress.setVisible(False); self.btn_save.setEnabled(True)
        self.result_lbl.setText(f"Error: {msg[:80]}"); self.result_lbl.setStyleSheet(f"color: {self.shell.theme.red};")
    def is_busy(self): return self.running
    def handle_drop(self, paths):
        if paths: self._load_file(paths[0])
    def apply_theme(self, theme):
        t = theme; self.file_lbl.setStyleSheet(f"color: {t.text};"); self._fields_lbl.setStyleSheet(f"color: {t.text2};")
        self.out_title.setStyleSheet(f"color: {t.text2};"); self.out_lbl.setStyleSheet(f"color: {t.text3};"); self.result_lbl.setStyleSheet(f"color: {t.green};"); self.drop_zone.apply_theme(t)
