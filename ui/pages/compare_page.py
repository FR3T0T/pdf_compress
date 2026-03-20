"""Compare PDFs — find differences between two PDFs."""
import os, threading, logging
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QFileDialog, QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView)
from pdf_ops import compare_pdfs
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage
log = logging.getLogger(__name__)

class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)

class ComparePage(BasePage):
    page_title = "Compare PDFs"
    page_icon = "compare"
    page_key = "compare"

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.running = False
        self._path_a = None
        self._path_b = None
        self.signals = _Signals()
        self.signals.done.connect(self._on_done)
        self.signals.error.connect(self._on_error)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(0)

        # Two file selection zones side by side
        files_row = QHBoxLayout()
        files_row.setSpacing(12)

        # File A
        col_a = QVBoxLayout()
        lbl_a = QLabel("DOCUMENT A")
        lbl_a.setFont(QFont(FONT, 8, QFont.Bold))
        col_a.addWidget(lbl_a)
        self._lbl_a = lbl_a

        self.drop_a = DropZone(self.shell.theme)
        self.drop_a.title_lbl.setText("First PDF")
        self.drop_a.hint_lbl.setText("click to select")
        self.drop_a.clicked.connect(self._browse_a)
        self.drop_a.setMaximumHeight(80)
        col_a.addWidget(self.drop_a)

        self.file_a_lbl = QLabel("")
        self.file_a_lbl.setFont(QFont(FONT, 9))
        self.file_a_lbl.setVisible(False)
        col_a.addWidget(self.file_a_lbl)
        files_row.addLayout(col_a, 1)

        # File B
        col_b = QVBoxLayout()
        lbl_b = QLabel("DOCUMENT B")
        lbl_b.setFont(QFont(FONT, 8, QFont.Bold))
        col_b.addWidget(lbl_b)
        self._lbl_b = lbl_b

        self.drop_b = DropZone(self.shell.theme)
        self.drop_b.title_lbl.setText("Second PDF")
        self.drop_b.hint_lbl.setText("click to select")
        self.drop_b.clicked.connect(self._browse_b)
        self.drop_b.setMaximumHeight(80)
        col_b.addWidget(self.drop_b)

        self.file_b_lbl = QLabel("")
        self.file_b_lbl.setFont(QFont(FONT, 9))
        self.file_b_lbl.setVisible(False)
        col_b.addWidget(self.file_b_lbl)
        files_row.addLayout(col_b, 1)

        root.addLayout(files_row)

        # Results
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        res_lbl = QLabel("RESULTS")
        res_lbl.setFont(QFont(FONT, 8, QFont.Bold))
        root.addWidget(res_lbl)
        self._res_lbl = res_lbl
        root.addSpacing(4)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Page", "Status", "Details"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        root.addWidget(self.table, 1)

        root.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.result_lbl)

        root.addWidget(self._sep())
        root.addSpacing(12)

        bar = QHBoxLayout()
        bar.addStretch()
        self.btn_compare = QPushButton("Compare")
        self.btn_compare.setObjectName("primary")
        self.btn_compare.setCursor(Qt.PointingHandCursor)
        self.btn_compare.clicked.connect(self._run)
        bar.addWidget(self.btn_compare)
        root.addLayout(bar)

    def _sep(self):
        s = QFrame()
        s.setObjectName("separator")
        s.setFixedHeight(1)
        return s

    def _browse_a(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select first PDF", "", "PDF files (*.pdf)")
        if path:
            self._path_a = path
            name = os.path.basename(path)
            self.file_a_lbl.setText(name if len(name) < 40 else name[:37] + "\u2026")
            self.file_a_lbl.setToolTip(path)
            self.file_a_lbl.setVisible(True)
            self.drop_a.setVisible(False)
            self.result_lbl.setText("")

    def _browse_b(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select second PDF", "", "PDF files (*.pdf)")
        if path:
            self._path_b = path
            name = os.path.basename(path)
            self.file_b_lbl.setText(name if len(name) < 40 else name[:37] + "\u2026")
            self.file_b_lbl.setToolTip(path)
            self.file_b_lbl.setVisible(True)
            self.drop_b.setVisible(False)
            self.result_lbl.setText("")

    def _run(self):
        if self.running or not self._path_a or not self._path_b:
            return
        self.running = True
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        self.btn_compare.setEnabled(False)
        self.result_lbl.setText("")
        self.table.setRowCount(0)

        path_a, path_b, signals = self._path_a, self._path_b, self.signals

        def _worker():
            try:
                result = compare_pdfs(path_a, path_b)
                signals.done.emit(result)
            except Exception as e:
                signals.error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, result):
        self.running = False
        self.progress.setVisible(False)
        self.btn_compare.setEnabled(True)

        diffs = result.page_diffs
        self.table.setRowCount(len(diffs))
        for i, d in enumerate(diffs):
            self.table.setItem(i, 0, QTableWidgetItem(str(d.get("page", i + 1))))
            self.table.setItem(i, 1, QTableWidgetItem(d.get("status", "")))
            self.table.setItem(i, 2, QTableWidgetItem(d.get("details", "")))

        identical = all(d.get("status") == "identical" for d in diffs)
        if identical:
            self.result_lbl.setText("Documents are identical")
            self.result_lbl.setStyleSheet(f"color: {self.shell.theme.green};")
        else:
            diff_count = sum(1 for d in diffs if d.get("status") != "identical")
            self.result_lbl.setText(f"Found differences on {diff_count} page(s)")
            self.result_lbl.setStyleSheet(f"color: {self.shell.theme.amber};")

    def _on_error(self, msg):
        self.running = False
        self.progress.setVisible(False)
        self.btn_compare.setEnabled(True)
        self.result_lbl.setText(f"Error: {msg[:80]}")
        self.result_lbl.setStyleSheet(f"color: {self.shell.theme.red};")

    def is_busy(self):
        return self.running

    def accepts_drops(self):
        return False

    def apply_theme(self, theme):
        t = theme
        self._lbl_a.setStyleSheet(f"color: {t.text2};")
        self._lbl_b.setStyleSheet(f"color: {t.text2};")
        self._res_lbl.setStyleSheet(f"color: {t.text2};")
        self.file_a_lbl.setStyleSheet(f"color: {t.text};")
        self.file_b_lbl.setStyleSheet(f"color: {t.text};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.drop_a.apply_theme(t)
        self.drop_b.apply_theme(t)
