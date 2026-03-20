"""Images to PDF — convert image files to a PDF document."""

import os, threading, logging
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QFileDialog, QProgressBar, QComboBox, QSpinBox,
)
from engine import fmt_size
from pdf_ops import images_to_pdf
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage

log = logging.getLogger(__name__)

class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)

class ImagesToPdfPage(BasePage):
    page_title = "Images to PDF"
    page_icon = "image_to_pdf"
    page_key = "images_to_pdf"
    accepted_extensions = [".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif"]

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.running = False
        self._file_paths = []
        self.signals = _Signals()
        self.signals.done.connect(self._on_done)
        self.signals.error.connect(self._on_error)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(0)

        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Add images to convert")
        self.drop_zone.hint_lbl.setText("PNG, JPEG, TIFF, BMP  \u00b7  click or drag and drop")
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

        root.addSpacing(8)
        self.summary_lbl = QLabel("")
        self.summary_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.summary_lbl)

        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        # Page size
        sz_row = QHBoxLayout()
        sz_row.addWidget(QLabel("Page size:"))
        self.size_combo = QComboBox()
        self.size_combo.addItems(["Auto (fit to image)", "A4", "Letter", "Legal"])
        sz_row.addWidget(self.size_combo, 1)
        root.addLayout(sz_row)
        root.addSpacing(4)

        # Margin
        mg_row = QHBoxLayout()
        mg_row.addWidget(QLabel("Margin (mm):"))
        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 50)
        self.margin_spin.setValue(0)
        mg_row.addWidget(self.margin_spin, 1)
        root.addLayout(mg_row)

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
        self.btn_convert = QPushButton("Convert")
        self.btn_convert.setObjectName("primary")
        self.btn_convert.setCursor(Qt.PointingHandCursor)
        self.btn_convert.clicked.connect(self._run)
        bar.addWidget(self.btn_convert)
        root.addLayout(bar)

    def _sep(self):
        s = QFrame(); s.setObjectName("separator"); s.setFixedHeight(1); return s

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select images", "",
            "Image files (*.png *.jpg *.jpeg *.tiff *.bmp *.gif);;All files (*)")
        if paths: self._add_files(paths)

    def _add_files(self, paths):
        existing = set(self._file_paths)
        new = [p for p in paths if p not in existing]
        if not new: return
        self._file_paths.extend(new)
        for p in new:
            row = QLabel(f"  {os.path.basename(p)}  \u00b7  {fmt_size(os.path.getsize(p))}")
            row.setFont(QFont(FONT, 9))
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self._update_view()

    def _clear(self):
        self._file_paths.clear()
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.result_lbl.setText("")
        self._update_view()

    def _update_view(self):
        has = len(self._file_paths) > 0
        self.drop_zone.setVisible(not has)
        self.scroll_frame.setVisible(has)
        n = len(self._file_paths)
        self.summary_lbl.setText(f"{n} image{'s' if n != 1 else ''}" if n else "")

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF", self._output_path or "output.pdf", "PDF files (*.pdf)")
        if path: self._output_path = path; self.out_lbl.setText(os.path.basename(path))

    def _run(self):
        if self.running or not self._file_paths: return
        output = self._output_path
        if not output:
            d = os.path.dirname(self._file_paths[0])
            output = os.path.join(d, "images.pdf")
        size_map = {"Auto (fit to image)": "auto", "A4": "a4", "Letter": "letter", "Legal": "legal"}
        page_size = size_map.get(self.size_combo.currentText(), "auto")
        margin = self.margin_spin.value()

        self.running = True
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        self.btn_convert.setEnabled(False)
        self.result_lbl.setText("")
        paths = list(self._file_paths)
        signals = self.signals

        def _worker():
            try:
                result = images_to_pdf(paths, output, page_size, margin)
                signals.done.emit(result)
            except Exception as e:
                signals.error.emit(str(e))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, result):
        self.running = False
        self.progress.setVisible(False)
        self.btn_convert.setEnabled(True)
        t = self.shell.theme
        n = len(self._file_paths)
        self.result_lbl.setText(f"Converted {n} image{'s' if n != 1 else ''} to PDF")
        self.result_lbl.setStyleSheet(f"color: {t.green};")

    def _on_error(self, msg):
        self.running = False
        self.progress.setVisible(False)
        self.btn_convert.setEnabled(True)
        t = self.shell.theme
        self.result_lbl.setText(f"Error: {msg[:80]}")
        self.result_lbl.setStyleSheet(f"color: {t.red};")

    def is_busy(self): return self.running
    def handle_drop(self, paths): self._add_files(paths)
    def apply_theme(self, theme):
        t = theme
        self.summary_lbl.setStyleSheet(f"color: {t.text2};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.drop_zone.apply_theme(t)
        self.list_widget.setStyleSheet(f"background: {t.surface};")
