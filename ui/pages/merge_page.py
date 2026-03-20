"""Merge page — combine multiple PDFs into one."""

import os
import time
import threading
import logging

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QFileDialog, QProgressBar, QLineEdit, QMenu, QMessageBox,
)

from engine import fmt_size
from pdf_ops import merge_pdfs, MergeResult
from ..theme import Theme, FONT
from ..widgets import DropZone
from ..batch_helpers import (
    load_recent_files, save_recent_files, show_recent_menu,
    setup_standard_shortcuts, notify_tray_if_minimized,
)
from .base import BasePage

log = logging.getLogger(__name__)


class _MergeSignals(QObject):
    progress = Signal(int, int)       # current_file, total_files
    done = Signal(object)             # MergeResult
    error = Signal(str)               # error message


class _MergeFileRow(QWidget):
    """A row in the merge file list showing filename, size, page count, and controls."""
    remove_clicked = Signal(object)
    move_up = Signal(object)
    move_down = Signal(object)

    def __init__(self, filepath: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self._theme = theme

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 8, 6)
        layout.setSpacing(8)

        # Page count + size (determined after load)
        self._pages = 0
        self._size = os.path.getsize(filepath)

        name = os.path.basename(filepath)
        self.name_lbl = QLabel(name if len(name) < 50 else name[:47] + "\u2026")
        self.name_lbl.setFont(QFont(FONT, 9, QFont.DemiBold))
        self.name_lbl.setToolTip(filepath)
        layout.addWidget(self.name_lbl, 1)

        self.info_lbl = QLabel(fmt_size(self._size))
        self.info_lbl.setFont(QFont(FONT, 9))
        layout.addWidget(self.info_lbl)

        # Move up/down buttons
        self.up_btn = QPushButton("\u25b2")
        self.up_btn.setObjectName("removeBtn")
        self.up_btn.setFixedSize(24, 24)
        self.up_btn.setCursor(Qt.PointingHandCursor)
        self.up_btn.setToolTip("Move up")
        self.up_btn.clicked.connect(lambda: self.move_up.emit(self))
        layout.addWidget(self.up_btn)

        self.down_btn = QPushButton("\u25bc")
        self.down_btn.setObjectName("removeBtn")
        self.down_btn.setFixedSize(24, 24)
        self.down_btn.setCursor(Qt.PointingHandCursor)
        self.down_btn.setToolTip("Move down")
        self.down_btn.clicked.connect(lambda: self.move_down.emit(self))
        layout.addWidget(self.down_btn)

        self.rm_btn = QPushButton("\u00d7")
        self.rm_btn.setObjectName("removeBtn")
        self.rm_btn.setFixedSize(24, 24)
        self.rm_btn.setCursor(Qt.PointingHandCursor)
        self.rm_btn.clicked.connect(lambda: self.remove_clicked.emit(self))
        layout.addWidget(self.rm_btn)

        self.border_line = QFrame()
        self.border_line.setFixedHeight(1)

        self.apply_theme(theme)

    def set_page_count(self, pages: int):
        self._pages = pages
        self.info_lbl.setText(f"{pages} pg  \u00b7  {fmt_size(self._size)}")

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet("background: transparent; border: none;")
        self.name_lbl.setStyleSheet(f"color: {theme.text}; background: transparent;")
        self.info_lbl.setStyleSheet(f"color: {theme.text2}; background: transparent;")
        self.border_line.setStyleSheet(f"background: {theme.border};")


class MergePage(BasePage):
    page_title = "Merge"
    page_icon = "\u2b82"  # ⮂

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.rows: list[_MergeFileRow] = []
        self.running = False
        self._cancel_event: threading.Event | None = None
        self._merge_start_time = 0.0

        self.signals = _MergeSignals()
        self.signals.progress.connect(self._on_progress)
        self.signals.done.connect(self._on_done)
        self.signals.error.connect(self._on_error)

        self._load_settings()
        self._build()
        setup_standard_shortcuts(self, self._browse, self._run, self._clear)

    def _load_settings(self):
        s = self.shell.settings
        self._naming = s.value("merge/naming", "merged")

    def _save_settings(self):
        s = self.shell.settings
        s.setValue("merge/naming", self._naming)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(0)

        # ── Drop zone ──
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Add PDFs to merge")
        self.drop_zone.hint_lbl.setText("click  \u00b7  or drag and drop  \u00b7  files will be merged in order")
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
        self.summary_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.summary_lbl)

        # ── Output ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        out_row = QHBoxLayout()
        self.out_title = QLabel("Output")
        self.out_title.setFont(QFont(FONT, 9, QFont.DemiBold))
        out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Same folder as first file")
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

        # ── Progress ──
        root.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        # ── Result label ──
        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.result_lbl)

        # ── Action bar ──
        root.addSpacing(10)
        root.addWidget(self._sep())
        root.addSpacing(12)

        # ── Naming template ──
        root.addSpacing(6)
        name_row = QHBoxLayout()
        name_lbl = QLabel("Output name:")
        name_lbl.setFont(QFont(FONT, 9))
        name_row.addWidget(name_lbl)
        self.naming_input = QLineEdit(self._naming)
        self.naming_input.setFont(QFont(FONT, 9))
        self.naming_input.setPlaceholderText("merged")
        self.naming_input.setToolTip("Output filename (without extension)")
        self.naming_input.textChanged.connect(self._on_naming_changed)
        name_row.addWidget(self.naming_input, 1)
        root.addLayout(name_row)

        # ── Action bar ──
        bar = QHBoxLayout()
        self.btn_add = QPushButton("+ Add")
        self.btn_add.setFont(QFont(FONT, 10))
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
        self.btn_clear.setFont(QFont(FONT, 9))
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)
        bar.addWidget(self.btn_clear)

        bar.addStretch()

        self.btn_open = QPushButton("Open folder")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setFont(QFont(FONT, 10))
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(self._open_output_folder)
        self.btn_open.setVisible(False)
        bar.addWidget(self.btn_open)

        self.btn_merge = QPushButton("Merge")
        self.btn_merge.setObjectName("primary")
        self.btn_merge.setCursor(Qt.PointingHandCursor)
        self.btn_merge.clicked.connect(self._run)
        bar.addWidget(self.btn_merge)

        root.addLayout(bar)

        # ── Result ──
        root.addSpacing(6)
        self.result_detail_lbl = QLabel("")
        self.result_detail_lbl.setFont(QFont(FONT, 10))
        self.result_detail_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(self.result_detail_lbl)

    def _sep(self) -> QFrame:
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        return sep

    # ── File management ───────────────────────────────────────────

    def _browse(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs to merge", "",
            "PDF files (*.pdf);;All files (*)",
        )
        if paths:
            self._add_files(paths)

    def _show_recent(self):
        show_recent_menu(self, self.btn_recent, self.shell.settings,
                         "merge", self._add_recent_file)

    def _add_recent_file(self, path):
        if os.path.isfile(path):
            self._add_files([path])

    def _on_naming_changed(self, text):
        raw = text.strip() or "merged"
        self._naming = raw.replace("/", "").replace("\\", "").replace("..", "")
        self._save_settings()

    def _open_output_folder(self):
        output = self._output_path or self._default_output()
        folder = os.path.dirname(os.path.abspath(output))
        if os.path.isdir(folder):
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _add_files(self, paths: list[str]):
        existing = {r.filepath for r in self.rows}
        new_paths = [p for p in paths if p not in existing]
        if not new_paths:
            return

        # Save to recent files
        save_recent_files(self.shell.settings, "merge", new_paths)

        for p in new_paths:
            row = _MergeFileRow(p, self.shell.theme)
            row.remove_clicked.connect(self._remove_row)
            row.move_up.connect(self._move_up)
            row.move_down.connect(self._move_down)
            self.rows.append(row)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row.border_line)

        # Get page counts in background
        self._probe_pages(new_paths)
        self._update_view()

    def _probe_pages(self, paths: list[str]):
        """Get page counts for newly added files."""
        import pikepdf

        def _probe():
            for row in self.rows:
                if row.filepath in paths:
                    try:
                        pdf = pikepdf.open(row.filepath)
                        pages = len(pdf.pages)
                        pdf.close()
                        QTimer.singleShot(0, lambda r=row, p=pages: r.set_page_count(p))
                    except Exception:
                        pass
            QTimer.singleShot(0, self._update_summary)

        threading.Thread(target=_probe, daemon=True).start()

    def _remove_row(self, row: _MergeFileRow):
        if self.running:
            return
        self.rows.remove(row)
        self.list_layout.removeWidget(row.border_line)
        row.border_line.deleteLater()
        self.list_layout.removeWidget(row)
        row.deleteLater()
        self._update_view()

    def _move_up(self, row: _MergeFileRow):
        idx = self.rows.index(row)
        if idx == 0:
            return
        self.rows[idx], self.rows[idx - 1] = self.rows[idx - 1], self.rows[idx]
        self._rebuild_list()

    def _move_down(self, row: _MergeFileRow):
        idx = self.rows.index(row)
        if idx >= len(self.rows) - 1:
            return
        self.rows[idx], self.rows[idx + 1] = self.rows[idx + 1], self.rows[idx]
        self._rebuild_list()

    def _rebuild_list(self):
        """Rebuild the list widget to match self.rows order."""
        # Remove all widgets (except the stretch)
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            # Don't delete — we're just reordering
        for row in self.rows:
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row.border_line)
        self._update_summary()

    def _clear(self):
        if self.running:
            return
        for row in self.rows:
            self.list_layout.removeWidget(row.border_line)
            row.border_line.deleteLater()
            self.list_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        self.result_lbl.setText("")
        self._update_view()

    def _update_view(self):
        has_files = len(self.rows) > 0
        self.drop_zone.setVisible(not has_files)
        self.scroll_frame.setVisible(has_files)
        self._update_summary()

    def _update_summary(self):
        n = len(self.rows)
        if not n:
            self.summary_lbl.setText("")
            return
        total_size = sum(r._size for r in self.rows)
        total_pages = sum(r._pages for r in self.rows)
        parts = [f"{n} file{'s' if n != 1 else ''}"]
        if total_pages > 0:
            parts.append(f"{total_pages} pages total")
        parts.append(fmt_size(total_size))
        self.summary_lbl.setText("  \u00b7  ".join(parts))

    # ── Output path ───────────────────────────────────────────────

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save merged PDF as", self._default_output(),
            "PDF files (*.pdf)",
        )
        if path:
            self._output_path = path
            self.out_lbl.setText(os.path.basename(path))

    def _default_output(self) -> str:
        if self._output_path:
            return self._output_path
        name = self._naming or "merged"
        if self.rows:
            d = os.path.dirname(self.rows[0].filepath)
            return os.path.join(d, f"{name}.pdf")
        return f"{name}.pdf"

    # ── Merge operation ───────────────────────────────────────────

    def _run(self):
        if self.running or len(self.rows) < 2:
            return

        output = self._output_path or self._default_output()

        # Check if output exists
        if os.path.exists(output):
            r = QMessageBox.question(
                self, "Overwrite?",
                f"{os.path.basename(output)} already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return

        self.running = True
        self._merge_start_time = time.time()
        self._cancel_event = threading.Event()
        self.progress.setVisible(True)
        self.progress.setMaximum(len(self.rows))
        self.progress.setValue(0)
        self.btn_merge.setText("Cancel")
        self.btn_merge.clicked.disconnect()
        self.btn_merge.clicked.connect(self._cancel)
        self.btn_add.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.btn_open.setVisible(False)
        self.result_lbl.setText("")
        self.result_detail_lbl.setText("")

        paths = [r.filepath for r in self.rows]
        cancel = self._cancel_event
        signals = self.signals

        def _worker():
            try:
                result = merge_pdfs(
                    paths, output,
                    on_progress=lambda cur, total: signals.progress.emit(cur, total),
                    cancel=cancel,
                )
                signals.done.emit(result)
            except Exception as e:
                signals.error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _cancel(self):
        if self._cancel_event:
            self._cancel_event.set()

    def _on_progress(self, current: int, total: int):
        self.progress.setValue(current)

    def _on_done(self, result: MergeResult):
        elapsed = time.time() - self._merge_start_time
        self.running = False
        self.progress.setVisible(False)
        self._restore_merge_btn()
        t = self.shell.theme

        msg = (f"Merged {result.total_pages} pages \u2192 "
               f"{fmt_size(result.output_size)}  \u00b7  {elapsed:.1f}s")
        self.result_lbl.setText(msg)
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.btn_open.setVisible(True)

        # Total input size for comparison
        total_input = sum(r._size for r in self.rows)
        self.result_detail_lbl.setText(
            f"{len(self.rows)} files ({fmt_size(total_input)}) \u2192 "
            f"1 file ({fmt_size(result.output_size)})"
        )
        self.result_detail_lbl.setStyleSheet(f"color: {t.text2};")

        notify_tray_if_minimized(self.shell, msg)
        self._save_settings()

    def _on_error(self, msg: str):
        self.running = False
        self.progress.setVisible(False)
        self._restore_merge_btn()
        t = self.shell.theme
        self.result_lbl.setText(f"Error: {msg[:80]}")
        self.result_lbl.setStyleSheet(f"color: {t.red};")

    def _restore_merge_btn(self):
        self.btn_merge.setText("Merge")
        try:
            self.btn_merge.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_merge.clicked.connect(self._run)
        self.btn_add.setEnabled(True)
        self.btn_clear.setEnabled(True)

    # ── BasePage interface ────────────────────────────────────────

    def is_busy(self) -> bool:
        return self.running

    def handle_drop(self, paths: list[str]):
        self._add_files(paths)

    def apply_theme(self, theme: Theme):
        t = theme
        self.summary_lbl.setStyleSheet(f"color: {t.text2};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.drop_zone.apply_theme(t)
        self.list_widget.setStyleSheet(f"background: {t.surface};")
        for row in self.rows:
            row.apply_theme(t)
