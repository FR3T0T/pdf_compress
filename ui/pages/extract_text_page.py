"""Extract Text — export text content to a file."""
import os, threading, logging
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QFileDialog, QProgressBar, QTextEdit)
from engine import fmt_size
from pdf_ops import extract_text
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage
log = logging.getLogger(__name__)
class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
class ExtractTextPage(BasePage):
    page_title = "Extract Text"
    page_icon = "extract_text"
    page_key = "extract_text"
    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell; self.running = False; self._input_path = None
        self.signals = _Signals(); self.signals.done.connect(self._on_done); self.signals.error.connect(self._on_error)
        self._build()
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(28, 16, 28, 20); root.setSpacing(0)
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Select a PDF"); self.drop_zone.hint_lbl.setText("extract all text content")
        self.drop_zone.clicked.connect(self._browse); root.addWidget(self.drop_zone)
        self.file_frame = QFrame(); fl = QHBoxLayout(self.file_frame); fl.setContentsMargins(0, 8, 0, 8)
        self.file_lbl = QLabel(""); self.file_lbl.setFont(QFont(FONT, 10, QFont.DemiBold)); fl.addWidget(self.file_lbl, 1)
        btn_change = QPushButton("Change"); btn_change.setObjectName("ghost"); btn_change.setFont(QFont(FONT, 9)); btn_change.setCursor(Qt.PointingHandCursor); btn_change.clicked.connect(self._browse); fl.addWidget(btn_change)
        self.file_frame.setVisible(False); root.addWidget(self.file_frame)
        root.addSpacing(8); root.addWidget(self._sep()); root.addSpacing(8)
        prev_lbl = QLabel("PREVIEW"); prev_lbl.setFont(QFont(FONT, 8, QFont.Bold)); root.addWidget(prev_lbl); self._prev_lbl = prev_lbl; root.addSpacing(4)
        self.preview = QTextEdit(); self.preview.setReadOnly(True); self.preview.setMaximumHeight(150); self.preview.setPlaceholderText("Text preview will appear here after extraction"); root.addWidget(self.preview)
        root.addSpacing(8); root.addWidget(self._sep()); root.addSpacing(8)
        out_row = QHBoxLayout(); self.out_title = QLabel("Output"); self.out_title.setFont(QFont(FONT, 9, QFont.DemiBold)); out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Choose output"); self.out_lbl.setFont(QFont(FONT, 9)); out_row.addWidget(self.out_lbl); out_row.addStretch()
        out_change = QPushButton("Change"); out_change.setObjectName("ghost"); out_change.setFont(QFont(FONT, 9)); out_change.setCursor(Qt.PointingHandCursor); out_change.clicked.connect(self._pick_output); out_row.addWidget(out_change)
        root.addLayout(out_row); self._output_path = None
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
        self.drop_zone.setVisible(False); self.file_frame.setVisible(True); self.result_lbl.setText(""); self.preview.clear()
        base = os.path.splitext(path)[0]; self._output_path = base + ".txt"; self.out_lbl.setText(os.path.basename(self._output_path))
    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save", self._output_path or "", "Text files (*.txt)")
        if path: self._output_path = path; self.out_lbl.setText(os.path.basename(path))
    def _run(self):
        if self.running or not self._input_path: return
        self.running = True; self.progress.setVisible(True); self.progress.setMaximum(0); self.btn_extract.setEnabled(False); self.result_lbl.setText("")
        path, output, signals = self._input_path, self._output_path, self.signals
        def _worker():
            try: result = extract_text(path, output); signals.done.emit(result)
            except Exception as e: signals.error.emit(str(e))
        threading.Thread(target=_worker, daemon=True).start()
    def _on_done(self, result):
        self.running = False; self.progress.setVisible(False); self.btn_extract.setEnabled(True)
        self.result_lbl.setText(f"Extracted {result.char_count} characters from {result.page_count} pages"); self.result_lbl.setStyleSheet(f"color: {self.shell.theme.green};")
        try:
            with open(result.output_path, "r", encoding="utf-8") as f: self.preview.setPlainText(f.read(2000))
        except: pass
    def _on_error(self, msg):
        self.running = False; self.progress.setVisible(False); self.btn_extract.setEnabled(True)
        self.result_lbl.setText(f"Error: {msg[:80]}"); self.result_lbl.setStyleSheet(f"color: {self.shell.theme.red};")
    def is_busy(self): return self.running
    def handle_drop(self, paths):
        if paths: self._load_file(paths[0])
    def apply_theme(self, theme):
        t = theme; self.file_lbl.setStyleSheet(f"color: {t.text};"); self._prev_lbl.setStyleSheet(f"color: {t.text2};")
        self.out_title.setStyleSheet(f"color: {t.text2};"); self.out_lbl.setStyleSheet(f"color: {t.text3};"); self.result_lbl.setStyleSheet(f"color: {t.green};"); self.drop_zone.apply_theme(t)
