"""Page operations — rotate, reorder, delete pages."""

import os
import threading
import logging
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QFileDialog, QProgressBar, QComboBox, QCheckBox,
    QMessageBox,
)

from engine import fmt_size
from pdf_ops import apply_page_operations, PageOpResult
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage

log = logging.getLogger(__name__)


class _PageOpsSignals(QObject):
    done = Signal(object)   # PageOpResult
    error = Signal(str)
    pages_loaded = Signal(list)  # list of (page_num, current_rotation)


class _PageRow(QWidget):
    """A row representing a single page with rotation + delete controls."""

    def __init__(self, page_num: int, rotation: int, theme: Theme, parent=None):
        super().__init__(parent)
        self.page_num = page_num  # 1-indexed
        self._theme = theme
        self.marked_delete = False
        self.rotation_delta = 0  # additional rotation to apply

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 8, 4)
        layout.setSpacing(8)

        self.page_lbl = QLabel(f"Page {page_num}")
        self.page_lbl.setFont(QFont(FONT, 9, QFont.DemiBold))
        layout.addWidget(self.page_lbl, 1)

        self.rot_lbl = QLabel(f"{rotation}\u00b0")
        self.rot_lbl.setFont(QFont(FONT, 9))
        self.rot_lbl.setToolTip("Current rotation")
        layout.addWidget(self.rot_lbl)

        self.rot_combo = QComboBox()
        self.rot_combo.addItems(["No change", "+90\u00b0", "+180\u00b0", "+270\u00b0"])
        self.rot_combo.setFont(QFont(FONT, 9))
        self.rot_combo.currentIndexChanged.connect(self._on_rot_changed)
        layout.addWidget(self.rot_combo)

        self.del_chk = QCheckBox("Delete")
        self.del_chk.setFont(QFont(FONT, 9))
        self.del_chk.toggled.connect(self._on_delete_toggled)
        layout.addWidget(self.del_chk)

        # Move up/down
        self.up_btn = QPushButton("\u25b2")
        self.up_btn.setObjectName("removeBtn")
        self.up_btn.setFixedSize(24, 24)
        self.up_btn.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.up_btn)

        self.down_btn = QPushButton("\u25bc")
        self.down_btn.setObjectName("removeBtn")
        self.down_btn.setFixedSize(24, 24)
        self.down_btn.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.down_btn)

        self.border_line = QFrame()
        self.border_line.setFixedHeight(1)

        self.apply_theme(theme)

    def _on_rot_changed(self, index: int):
        self.rotation_delta = [0, 90, 180, 270][index]

    def _on_delete_toggled(self, checked: bool):
        self.marked_delete = checked
        self.page_lbl.setStyleSheet(
            f"color: {self._theme.text3}; text-decoration: line-through;"
            if checked else
            f"color: {self._theme.text};"
        )

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet("background: transparent; border: none;")
        self.page_lbl.setStyleSheet(
            f"color: {theme.text3}; text-decoration: line-through;"
            if self.marked_delete else
            f"color: {theme.text};"
        )
        self.rot_lbl.setStyleSheet(f"color: {theme.text2};")
        self.border_line.setStyleSheet(f"background: {theme.border};")


class PageOpsPage(BasePage):
    page_title = "Pages"
    page_icon = "\u25a6"  # ▦

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.running = False
        self._cancel_event: threading.Event | None = None
        self._input_path: str | None = None
        self._page_rows: list[_PageRow] = []

        self.signals = _PageOpsSignals()
        self.signals.done.connect(self._on_done)
        self.signals.error.connect(self._on_error)
        self.signals.pages_loaded.connect(self._on_pages_loaded)

        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(0)

        # ── Drop zone ──
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Select a PDF")
        self.drop_zone.hint_lbl.setText("rotate, reorder, or delete pages")
        self.drop_zone.clicked.connect(self._browse)
        root.addWidget(self.drop_zone)

        # ── File info ──
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

        # ── Page list ──
        root.addSpacing(4)
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

        # ── Output ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        out_row = QHBoxLayout()
        self.out_title = QLabel("Save as")
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

        self._output_path: str | None = None

        # ── Progress + result ──
        root.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.result_lbl)

        # ── Action bar ──
        root.addSpacing(10)
        root.addWidget(self._sep())
        root.addSpacing(12)

        bar = QHBoxLayout()
        bar.addStretch()

        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setObjectName("primary")
        self.btn_apply.setCursor(Qt.PointingHandCursor)
        self.btn_apply.clicked.connect(self._run)
        bar.addWidget(self.btn_apply)
        root.addLayout(bar)

    def _sep(self) -> QFrame:
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        return sep

    # ── File selection ────────────────────────────────────────────

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF", "",
            "PDF files (*.pdf);;All files (*)",
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        self._input_path = path
        name = os.path.basename(path)
        self.file_lbl.setText(name if len(name) < 45 else name[:42] + "\u2026")
        self.file_lbl.setToolTip(path)
        self.file_info_lbl.setText(fmt_size(os.path.getsize(path)))
        self.drop_zone.setVisible(False)
        self.file_frame.setVisible(True)
        self.result_lbl.setText("")

        # Default output
        base, ext = os.path.splitext(path)
        self._output_path = base + "_edited" + ext
        self.out_lbl.setText(os.path.basename(self._output_path))

        # Load pages in background
        signals = self.signals

        def _probe():
            try:
                import pikepdf
                pdf = pikepdf.open(path)
                pages_info = []
                for i, page in enumerate(pdf.pages):
                    rot = int(page.get("/Rotate", 0))
                    pages_info.append((i + 1, rot))
                pdf.close()
                signals.pages_loaded.emit(pages_info)
            except Exception as e:
                signals.error.emit(str(e))

        threading.Thread(target=_probe, daemon=True).start()

    def _on_pages_loaded(self, pages_info: list):
        # Clear old rows
        self._clear_rows()

        for page_num, rotation in pages_info:
            row = _PageRow(page_num, rotation, self.shell.theme)
            row.up_btn.clicked.connect(lambda _, r=row: self._move_up(r))
            row.down_btn.clicked.connect(lambda _, r=row: self._move_down(r))
            self._page_rows.append(row)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row.border_line)

        self.scroll_frame.setVisible(True)
        self.file_info_lbl.setText(
            f"{len(pages_info)} pages  \u00b7  {fmt_size(os.path.getsize(self._input_path))}"
        )

    def _clear_rows(self):
        for row in self._page_rows:
            self.list_layout.removeWidget(row.border_line)
            row.border_line.deleteLater()
            self.list_layout.removeWidget(row)
            row.deleteLater()
        self._page_rows.clear()

    def _move_up(self, row: _PageRow):
        idx = self._page_rows.index(row)
        if idx == 0:
            return
        self._page_rows[idx], self._page_rows[idx - 1] = self._page_rows[idx - 1], self._page_rows[idx]
        self._rebuild_list()

    def _move_down(self, row: _PageRow):
        idx = self._page_rows.index(row)
        if idx >= len(self._page_rows) - 1:
            return
        self._page_rows[idx], self._page_rows[idx + 1] = self._page_rows[idx + 1], self._page_rows[idx]
        self._rebuild_list()

    def _rebuild_list(self):
        while self.list_layout.count() > 1:
            self.list_layout.takeAt(0)
        for row in self._page_rows:
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row.border_line)

    # ── Output ────────────────────────────────────────────────────

    def _pick_output(self):
        default = self._output_path or ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save edited PDF as", default,
            "PDF files (*.pdf)",
        )
        if path:
            self._output_path = path
            self.out_lbl.setText(os.path.basename(path))

    # ── Apply operations ──────────────────────────────────────────

    def _run(self):
        if self.running or not self._input_path or not self._page_rows:
            return
        if not self._output_path:
            QMessageBox.warning(self, "Output needed", "Choose an output file path.")
            return

        # Build operation parameters
        rotations = {}
        delete_pages = []
        # Map from original 0-indexed page to row position
        for row in self._page_rows:
            original_idx = row.page_num - 1
            if row.rotation_delta:
                rotations[original_idx] = row.rotation_delta
            if row.marked_delete:
                delete_pages.append(original_idx)

        # Determine new order based on row positions (after accounting for deletions)
        # Original indices that are NOT deleted, in current UI order
        remaining_original = [r.page_num - 1 for r in self._page_rows if not r.marked_delete]
        # The default order would be sorted remaining indices
        default_order = sorted(remaining_original)
        new_order = None
        if remaining_original != default_order:
            # Build mapping: new_order[i] = index into the default_order list
            new_order = [default_order.index(idx) for idx in remaining_original]

        if not rotations and not delete_pages and new_order is None:
            QMessageBox.information(self, "No changes", "No operations selected.")
            return

        # Check overwrite
        if os.path.exists(self._output_path):
            r = QMessageBox.question(
                self, "Overwrite?",
                f"{os.path.basename(self._output_path)} already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return

        self.running = True
        self._cancel_event = threading.Event()
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        self.btn_apply.setEnabled(False)
        self.result_lbl.setText("")

        path = self._input_path
        output = self._output_path
        cancel = self._cancel_event
        signals = self.signals

        def _worker():
            try:
                result = apply_page_operations(
                    path, output,
                    rotations=rotations or None,
                    delete_pages=delete_pages or None,
                    new_order=new_order,
                    cancel=cancel,
                )
                signals.done.emit(result)
            except Exception as e:
                signals.error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, result: PageOpResult):
        self.running = False
        self.progress.setVisible(False)
        self.btn_apply.setEnabled(True)
        t = self.shell.theme
        ops = ", ".join(result.operations) if result.operations else "Done"
        size = fmt_size(os.path.getsize(result.output_path))
        self.result_lbl.setText(f"{ops}  \u2192  {size}")
        self.result_lbl.setStyleSheet(f"color: {t.green};")

    def _on_error(self, msg: str):
        self.running = False
        self.progress.setVisible(False)
        self.btn_apply.setEnabled(True)
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
        self.list_widget.setStyleSheet(f"background: {t.surface};")
        for row in self._page_rows:
            row.apply_theme(t)
