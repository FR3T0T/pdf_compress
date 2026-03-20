"""Split page — split a PDF into multiple files."""

import os
import time
import threading
import logging
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QFileDialog, QProgressBar, QLineEdit, QComboBox, QMessageBox,
)

from engine import fmt_size
from pdf_ops import split_pdf, SplitResult
from ..theme import Theme, FONT
from ..widgets import DropZone
from ..batch_helpers import (
    load_recent_files, save_recent_files, show_recent_menu,
    setup_standard_shortcuts, notify_tray_if_minimized,
)
from .base import BasePage

log = logging.getLogger(__name__)


class _SplitSignals(QObject):
    done = Signal(object)   # SplitResult
    error = Signal(str)
    info = Signal(int)      # page count from probe


class SplitPage(BasePage):
    page_title = "Split"
    page_icon = "\u2702"  # ✂

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.running = False
        self._cancel_event: threading.Event | None = None
        self._input_path: str | None = None
        self._page_count = 0
        self._file_size = 0

        self._split_start_time = 0.0

        self.signals = _SplitSignals()
        self.signals.done.connect(self._on_done)
        self.signals.error.connect(self._on_error)
        self.signals.info.connect(self._on_info)

        self._load_settings()
        self._build()
        setup_standard_shortcuts(self, self._browse, self._run, lambda: None)

    def _load_settings(self):
        s = self.shell.settings
        self._last_mode = int(s.value("split/mode", "0"))

    def _save_settings(self):
        s = self.shell.settings
        s.setValue("split/mode", str(self.mode_combo.currentIndex()))

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(0)

        # ── Drop zone for single file ──
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Select a PDF to split")
        self.drop_zone.hint_lbl.setText("click  \u00b7  or drag and drop a single PDF")
        self.drop_zone.clicked.connect(self._browse)
        root.addWidget(self.drop_zone)

        # ── File info (shown after loading) ──
        self.file_frame = QFrame()
        file_layout = QHBoxLayout(self.file_frame)
        file_layout.setContentsMargins(0, 8, 0, 8)

        self.file_lbl = QLabel("")
        self.file_lbl.setFont(QFont(FONT, 10, QFont.DemiBold))
        file_layout.addWidget(self.file_lbl, 1)

        self.file_info_lbl = QLabel("")
        self.file_info_lbl.setFont(QFont(FONT, 9))
        file_layout.addWidget(self.file_info_lbl)

        btn_change = QPushButton("Change")
        btn_change.setObjectName("ghost")
        btn_change.setFont(QFont(FONT, 9))
        btn_change.setCursor(Qt.PointingHandCursor)
        btn_change.clicked.connect(self._browse)
        file_layout.addWidget(btn_change)

        self.file_frame.setVisible(False)
        root.addWidget(self.file_frame)

        # ── Split mode ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        mode_row = QHBoxLayout()
        mode_lbl = QLabel("Mode")
        mode_lbl.setFont(QFont(FONT, 9, QFont.DemiBold))
        mode_row.addWidget(mode_lbl)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Every page", "Page ranges", "Every N pages"])
        self.mode_combo.setFont(QFont(FONT, 9))
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        root.addLayout(mode_row)

        # ── Range / N input ──
        root.addSpacing(6)
        self.range_row = QHBoxLayout()
        self.range_lbl = QLabel("Ranges:")
        self.range_lbl.setFont(QFont(FONT, 9))
        self.range_row.addWidget(self.range_lbl)

        self.range_input = QLineEdit()
        self.range_input.setFont(QFont(FONT, 9))
        self.range_input.setPlaceholderText("e.g. 1-3, 5, 8-10")
        self.range_row.addWidget(self.range_input, 1)
        root.addLayout(self.range_row)

        self.range_lbl.setVisible(False)
        self.range_input.setVisible(False)

        # ── Output directory ──
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

        self._output_dir: str | None = None

        # ── Progress ──
        root.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        # ── Result ──
        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.result_lbl)

        # ── Spacer + action bar ──
        root.addStretch()
        root.addWidget(self._sep())
        root.addSpacing(12)

        bar = QHBoxLayout()

        self.btn_recent = QPushButton("Recent")
        self.btn_recent.setObjectName("ghost")
        self.btn_recent.setFont(QFont(FONT, 10))
        self.btn_recent.setCursor(Qt.PointingHandCursor)
        self.btn_recent.clicked.connect(self._show_recent)
        bar.addWidget(self.btn_recent)

        bar.addStretch()

        self.btn_open = QPushButton("Open folder")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setFont(QFont(FONT, 9))
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(self._open_output)
        self.btn_open.setVisible(False)
        bar.addWidget(self.btn_open)

        self.btn_split = QPushButton("Split")
        self.btn_split.setObjectName("primary")
        self.btn_split.setCursor(Qt.PointingHandCursor)
        self.btn_split.clicked.connect(self._run)
        bar.addWidget(self.btn_split)
        root.addLayout(bar)

    def _sep(self) -> QFrame:
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        return sep

    # ── File selection ────────────────────────────────────────────

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF to split", "",
            "PDF files (*.pdf);;All files (*)",
        )
        if path:
            self._load_file(path)
            save_recent_files(self.shell.settings, "split", [path])

    def _load_file(self, path: str):
        self._input_path = path
        self._file_size = os.path.getsize(path)
        name = os.path.basename(path)
        self.file_lbl.setText(name if len(name) < 45 else name[:42] + "\u2026")
        self.file_lbl.setToolTip(path)
        self.file_info_lbl.setText(fmt_size(self._file_size))
        self.drop_zone.setVisible(False)
        self.file_frame.setVisible(True)
        self.result_lbl.setText("")
        self.btn_open.setVisible(False)

        # Probe page count
        signals = self.signals

        def _probe():
            try:
                import pikepdf
                pdf = pikepdf.open(path)
                count = len(pdf.pages)
                pdf.close()
                signals.info.emit(count)
            except Exception:
                signals.info.emit(0)

        threading.Thread(target=_probe, daemon=True).start()

    def _on_info(self, page_count: int):
        self._page_count = page_count
        info = f"{page_count} pages  \u00b7  {fmt_size(self._file_size)}"
        self.file_info_lbl.setText(info)

    # ── Mode switching ────────────────────────────────────────────

    def _show_recent(self):
        show_recent_menu(self, self.btn_recent, self.shell.settings,
                         "split", self._add_recent_file)

    def _add_recent_file(self, path):
        if os.path.isfile(path):
            self._load_file(path)

    def _on_mode_changed(self, index: int):
        show_range = index in (1, 2)  # ranges or every_n
        self.range_lbl.setVisible(show_range)
        self.range_input.setVisible(show_range)
        if index == 1:
            self.range_lbl.setText("Ranges:")
            self.range_input.setPlaceholderText("e.g. 1-3, 5, 8-10")
        elif index == 2:
            self.range_lbl.setText("Pages per file:")
            self.range_input.setPlaceholderText("e.g. 5")

    # ── Output ────────────────────────────────────────────────────

    def _pick_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output folder")
        if d:
            self._output_dir = d
            self.out_lbl.setText(os.path.basename(d) or d)

    def _open_output(self):
        d = self._output_dir
        if not d and self._input_path:
            d = os.path.dirname(self._input_path)
        if d and os.path.isdir(d):
            if sys.platform == "win32":
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])

    # ── Split operation ───────────────────────────────────────────

    def _run(self):
        if self.running or not self._input_path:
            return

        mode_idx = self.mode_combo.currentIndex()
        mode = ["all", "ranges", "every_n"][mode_idx]
        ranges_str = None
        every_n = 1

        if mode == "ranges":
            ranges_str = self.range_input.text().strip()
            if not ranges_str:
                QMessageBox.warning(self, "Input needed", "Enter page ranges.")
                return
        elif mode == "every_n":
            try:
                every_n = int(self.range_input.text().strip())
                if every_n < 1:
                    raise ValueError
            except ValueError:
                QMessageBox.warning(self, "Input needed", "Enter a valid number of pages.")
                return

        output_dir = self._output_dir or os.path.dirname(self._input_path)

        self.running = True
        self._split_start_time = time.time()
        self._cancel_event = threading.Event()
        self.progress.setVisible(True)
        self.progress.setMaximum(0)  # indeterminate
        self.btn_split.setEnabled(False)
        self.result_lbl.setText("")
        self.btn_open.setVisible(False)

        path = self._input_path
        cancel = self._cancel_event
        signals = self.signals

        def _worker():
            try:
                result = split_pdf(
                    path, output_dir,
                    mode=mode, ranges=ranges_str, every_n=every_n,
                    cancel=cancel,
                )
                signals.done.emit(result)
            except Exception as e:
                signals.error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, result: SplitResult):
        elapsed = time.time() - self._split_start_time
        self.running = False
        self.progress.setVisible(False)
        self.btn_split.setEnabled(True)
        t = self.shell.theme
        n = len(result.output_paths)
        total_pages = sum(result.pages_per_output)
        msg = (f"Split into {n} file{'s' if n != 1 else ''} "
               f"({total_pages} pages)  \u00b7  {elapsed:.1f}s")
        self.result_lbl.setText(msg)
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.btn_open.setVisible(True)

        notify_tray_if_minimized(self.shell, msg)
        self._save_settings()

    def _on_error(self, msg: str):
        self.running = False
        self.progress.setVisible(False)
        self.btn_split.setEnabled(True)
        t = self.shell.theme
        self.result_lbl.setText(f"Error: {msg[:80]}")
        self.result_lbl.setStyleSheet(f"color: {t.red};")

    # ── BasePage interface ────────────────────────────────────────

    def is_busy(self) -> bool:
        return self.running

    def handle_drop(self, paths: list[str]):
        if paths:
            self._load_file(paths[0])

    def apply_theme(self, theme: Theme):
        t = theme
        self.file_lbl.setStyleSheet(f"color: {t.text};")
        self.file_info_lbl.setStyleSheet(f"color: {t.text2};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.drop_zone.apply_theme(t)
