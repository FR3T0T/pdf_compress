"""PDF to Word — extract text to a Word document."""

import os, threading, logging
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QFileDialog, QProgressBar,
)
from engine import fmt_size
from pdf_ops import pdf_to_word
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage

log = logging.getLogger(__name__)

class _Signals(QObject):
    done = Signal(str)
    error = Signal(str)

class PdfToWordPage(BasePage):
    page_title = "PDF to Word"
    page_icon = "word"
    page_key = "pdf_to_word"

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.running = False
        self._input_path = None
        self.signals = _Signals()
        self.signals.done.connect(self._on_done)
        self.signals.error.connect(self._on_error)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(0)

        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Select a PDF to convert")
        self.drop_zone.hint_lbl.setText("extract text to a Word document (.docx)")
        self.drop_zone.clicked.connect(self._browse)
        root.addWidget(self.drop_zone)

        self.file_frame = QFrame()
        fl = QHBoxLayout(self.file_frame)
        fl.setContentsMargins(0, 8, 0, 8)
        self.file_lbl = QLabel("")
        self.file_lbl.setFont(QFont(FONT, 10, QFont.DemiBold))
        fl.addWidget(self.file_lbl, 1)
        self.file_info_lbl = QLabel("")
        self.file_info_lbl.setFont(QFont(FONT, 9))
        fl.addWidget(self.file_info_lbl)
        btn_change = QPushButton("Change")
        btn_change.setObjectName("ghost")
        btn_change.setFont(QFont(FONT, 9))
        btn_change.setCursor(Qt.PointingHandCursor)
        btn_change.clicked.connect(self._browse)
        fl.addWidget(btn_change)
        self.file_frame.setVisible(False)
        root.addWidget(self.file_frame)

        root.addSpacing(12)
        self.note_lbl = QLabel("Note: Best-effort text extraction. Complex layouts, tables, and images may not convert perfectly.")
        self.note_lbl.setFont(QFont(FONT, 9))
        self.note_lbl.setWordWrap(True)
        root.addWidget(self.note_lbl)

        # Output
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        out_row = QHBoxLayout()
        self.out_title = QLabel("Output")
        self.out_title.setFont(QFont(FONT, 9, QFont.DemiBold))
        out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Choose output file")
        self.out_lbl.setFont(QFont(FONT, 9))
        out_row.addWidget(self.out_lbl)
        out_row.addStretch()
        out_change = QPushButton("Change")
        out_change.setObjectName("ghost")
        out_change.setFont(QFont(FONT, 9))
        out_change.setCursor(Qt.PointingHandCursor)
        out_change.clicked.connect(self._pick_output)
        out_row.addWidget(out_change)
        root.addLayout(out_row)
        self._output_path = None

        root.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)
        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.result_lbl)

        root.addStretch()
        root.addWidget(self._sep())
        root.addSpacing(12)
        bar = QHBoxLayout()
        bar.addStretch()
        self.btn_convert = QPushButton("Convert")
        self.btn_convert.setObjectName("primary")
        self.btn_convert.setCursor(Qt.PointingHandCursor)
        self.btn_convert.clicked.connect(self._run)
        bar.addWidget(self.btn_convert)
        root.addLayout(bar)

    def _sep(self):
        s = QFrame(); s.setObjectName("separator"); s.setFixedHeight(1); return s

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF files (*.pdf)")
        if path: self._load_file(path)

    def _load_file(self, path):
        self._input_path = path
        name = os.path.basename(path)
        self.file_lbl.setText(name if len(name) < 45 else name[:42] + "\u2026")
        self.file_lbl.setToolTip(path)
        self.file_info_lbl.setText(fmt_size(os.path.getsize(path)))
        self.drop_zone.setVisible(False)
        self.file_frame.setVisible(True)
        self.result_lbl.setText("")
        base = os.path.splitext(path)[0]
        self._output_path = base + ".docx"
        self.out_lbl.setText(os.path.basename(self._output_path))

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Word document", self._output_path or "", "Word documents (*.docx)")
        if path: self._output_path = path; self.out_lbl.setText(os.path.basename(path))

    def _run(self):
        if self.running or not self._input_path: return
        self.running = True
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        self.btn_convert.setEnabled(False)
        self.result_lbl.setText("")
        path = self._input_path
        output = self._output_path
        signals = self.signals

        def _worker():
            try:
                pdf_to_word(path, output)
                signals.done.emit(output)
            except Exception as e:
                signals.error.emit(str(e))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, output):
        self.running = False
        self.progress.setVisible(False)
        self.btn_convert.setEnabled(True)
        t = self.shell.theme
        self.result_lbl.setText(f"Converted \u2192 {os.path.basename(output)}")
        self.result_lbl.setStyleSheet(f"color: {t.green};")

    def _on_error(self, msg):
        self.running = False
        self.progress.setVisible(False)
        self.btn_convert.setEnabled(True)
        t = self.shell.theme
        self.result_lbl.setText(f"Error: {msg[:80]}")
        self.result_lbl.setStyleSheet(f"color: {t.red};")

    def is_busy(self): return self.running
    def handle_drop(self, paths):
        if paths: self._load_file(paths[0])
    def apply_theme(self, theme):
        t = theme
        self.file_lbl.setStyleSheet(f"color: {t.text};")
        self.file_info_lbl.setStyleSheet(f"color: {t.text2};")
        self.note_lbl.setStyleSheet(f"color: {t.text3};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.drop_zone.apply_theme(t)
