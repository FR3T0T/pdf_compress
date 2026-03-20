"""PDF to Images — export pages as PNG or JPEG."""

import os, threading, logging
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QFileDialog, QProgressBar, QComboBox, QSpinBox, QSlider,
)
from engine import fmt_size
from pdf_ops import pdf_to_images
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage

log = logging.getLogger(__name__)

class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    info = Signal(int)

class PdfToImagesPage(BasePage):
    page_title = "PDF to Images"
    page_icon = "image"
    page_key = "pdf_to_images"

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.running = False
        self._input_path = None
        self.signals = _Signals()
        self.signals.done.connect(self._on_done)
        self.signals.error.connect(self._on_error)
        self.signals.info.connect(self._on_info)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(0)

        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Select a PDF to export")
        self.drop_zone.hint_lbl.setText("export pages as PNG or JPEG images")
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

        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        # Format
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["PNG", "JPEG"])
        self.fmt_combo.currentIndexChanged.connect(self._on_format_changed)
        fmt_row.addWidget(self.fmt_combo, 1)
        root.addLayout(fmt_row)
        root.addSpacing(4)

        # DPI
        dpi_row = QHBoxLayout()
        dpi_row.addWidget(QLabel("DPI:"))
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 600)
        self.dpi_spin.setValue(150)
        self.dpi_spin.setSingleStep(10)
        dpi_row.addWidget(self.dpi_spin, 1)
        root.addLayout(dpi_row)
        root.addSpacing(4)

        # JPEG quality
        self.quality_row = QHBoxLayout()
        self.quality_lbl = QLabel("Quality: 85")
        self.quality_row.addWidget(self.quality_lbl)
        self.quality_slider = QSlider(Qt.Horizontal)
        self.quality_slider.setRange(10, 100)
        self.quality_slider.setValue(85)
        self.quality_slider.valueChanged.connect(lambda v: self.quality_lbl.setText(f"Quality: {v}"))
        self.quality_row.addWidget(self.quality_slider, 1)
        root.addLayout(self.quality_row)
        self.quality_lbl.setVisible(False)
        self.quality_slider.setVisible(False)

        # Page range
        root.addSpacing(4)
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Pages:"))
        self.range_input = QComboBox()
        self.range_input.setEditable(True)
        self.range_input.addItems(["All pages", "1-5", "1,3,5"])
        self.range_input.setCurrentText("All pages")
        range_row.addWidget(self.range_input, 1)
        root.addLayout(range_row)

        # Output
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        out_row = QHBoxLayout()
        self.out_title = QLabel("Output folder")
        self.out_title.setFont(QFont(FONT, 9, QFont.DemiBold))
        out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Same folder as input")
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
        self._output_dir = None

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
        self.btn_export = QPushButton("Export")
        self.btn_export.setObjectName("primary")
        self.btn_export.setCursor(Qt.PointingHandCursor)
        self.btn_export.clicked.connect(self._run)
        bar.addWidget(self.btn_export)
        root.addLayout(bar)

    def _sep(self):
        s = QFrame(); s.setObjectName("separator"); s.setFixedHeight(1); return s

    def _on_format_changed(self, idx):
        is_jpeg = idx == 1
        self.quality_lbl.setVisible(is_jpeg)
        self.quality_slider.setVisible(is_jpeg)

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
        signals = self.signals
        def _probe():
            try:
                import pikepdf
                pdf = pikepdf.open(path); count = len(pdf.pages); pdf.close()
                signals.info.emit(count)
            except: signals.info.emit(0)
        threading.Thread(target=_probe, daemon=True).start()

    def _on_info(self, count):
        self.file_info_lbl.setText(f"{count} pages  \u00b7  {fmt_size(os.path.getsize(self._input_path))}")

    def _pick_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output folder")
        if d: self._output_dir = d; self.out_lbl.setText(os.path.basename(d) or d)

    def _run(self):
        if self.running or not self._input_path: return
        output_dir = self._output_dir or os.path.dirname(self._input_path)
        fmt = self.fmt_combo.currentText().lower()
        dpi = self.dpi_spin.value()
        quality = self.quality_slider.value() if fmt == "jpeg" else 95
        page_range = None
        range_text = self.range_input.currentText().strip()
        if range_text and range_text != "All pages":
            page_range = range_text

        self.running = True
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        self.btn_export.setEnabled(False)
        self.result_lbl.setText("")
        path = self._input_path
        signals = self.signals

        def _worker():
            try:
                result = pdf_to_images(path, output_dir, fmt, dpi, page_range, quality)
                signals.done.emit(result)
            except Exception as e:
                signals.error.emit(str(e))
        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, result):
        self.running = False
        self.progress.setVisible(False)
        self.btn_export.setEnabled(True)
        t = self.shell.theme
        n = result.image_count if hasattr(result, 'image_count') else len(result.image_paths)
        self.result_lbl.setText(f"Exported {n} image{'s' if n != 1 else ''}")
        self.result_lbl.setStyleSheet(f"color: {t.green};")

    def _on_error(self, msg):
        self.running = False
        self.progress.setVisible(False)
        self.btn_export.setEnabled(True)
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
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.drop_zone.apply_theme(t)
