"""Unlock PDF — remove password protection with batch support.

Supports both standard PDF encryption and .epdf enhanced encryption.
Auto-detects file type and uses the appropriate decryption method.
"""

import os
import time
import threading
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QFileDialog, QProgressBar, QLineEdit, QCheckBox,
    QMenu, QMessageBox,
)

from engine import fmt_size
from pdf_ops import unlock_pdf
from epdf_crypto import (
    is_epdf, epdf_decrypt, epdf_read_metadata,
    EPDFPasswordError, EPDFFormatError,
)
from ..theme import Theme, FONT
from ..signals import Signals
from ..widgets import DropZone
from ..widgets_generic import GenericFileRow
from ..dialogs import OverwriteDialog, GenericSummaryDialog, EPDFInfoDialog
from ..batch_helpers import (
    load_recent_files, save_recent_files, show_recent_menu,
    setup_standard_shortcuts, notify_tray_if_minimized,
)
from .base import BasePage

log = logging.getLogger(__name__)


class UnlockPage(BasePage):
    page_title = "Unlock PDF"
    page_icon = "unlock"
    page_key = "unlock"
    accepted_extensions = [".pdf", ".epdf"]

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.rows: list[GenericFileRow] = []
        self.running = False
        self._results = []
        self._cancel_event: threading.Event | None = None

        self.signals = Signals()
        self.signals.progress.connect(self._on_progress)
        self.signals.file_done.connect(self._on_file_done)
        self.signals.all_done.connect(self._on_all_done)

        self._load_settings()
        self._build()
        setup_standard_shortcuts(self, self._browse, self._run, self._clear)

    # ── Settings ────────────────────────────────────────────────────

    def _load_settings(self):
        s = self.shell.settings
        self._naming = s.value("unlock/naming", "{name}_unlocked")

    def _save_settings(self):
        s = self.shell.settings
        s.setValue("unlock/naming", self._naming)

    # ── Build UI ────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 20, 32, 24)
        root.setSpacing(0)

        # ── Drop zone ──
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Drop PDFs or EPDFs to unlock")
        self.drop_zone.hint_lbl.setText("remove password protection from PDF or .epdf files")
        self.drop_zone.format_lbl.setText("PDF  \u00b7  EPDF")
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
        self.summary_lbl.setFont(QFont(FONT, 10))
        root.addWidget(self.summary_lbl)

        # ── Password ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        pw_title = QLabel("PASSWORD")
        pw_title.setFont(QFont(FONT, 9, QFont.Bold))
        root.addWidget(pw_title)
        self._pw_title = pw_title
        root.addSpacing(6)

        pw_row = QHBoxLayout()
        pw_row.addWidget(QLabel("Password:"))
        self.pw_input = QLineEdit()
        self.pw_input.setEchoMode(QLineEdit.Password)
        self.pw_input.setPlaceholderText("Enter password to decrypt")
        pw_row.addWidget(self.pw_input, 1)
        root.addLayout(pw_row)
        root.addSpacing(4)

        self.chk_show = QCheckBox("Show password")
        self.chk_show.toggled.connect(lambda show: self.pw_input.setEchoMode(
            QLineEdit.Normal if show else QLineEdit.Password))
        root.addWidget(self.chk_show)

        # ── Output ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        out_row = QHBoxLayout()
        self.out_title = QLabel("Output")
        self.out_title.setFont(QFont(FONT, 10, QFont.DemiBold))
        out_row.addWidget(self.out_title)
        self.out_lbl = QLabel("Same folder as input")
        self.out_lbl.setFont(QFont(FONT, 10))
        out_row.addWidget(self.out_lbl)
        out_row.addStretch()

        out_change = QPushButton("Change")
        out_change.setObjectName("ghost")
        out_change.setFont(QFont(FONT, 9))
        out_change.setCursor(Qt.PointingHandCursor)
        out_change.clicked.connect(self._pick_out)
        out_row.addWidget(out_change)

        out_reset = QPushButton("Reset")
        out_reset.setObjectName("ghost")
        out_reset.setFont(QFont(FONT, 9))
        out_reset.setCursor(Qt.PointingHandCursor)
        out_reset.clicked.connect(self._reset_out)
        out_row.addWidget(out_reset)
        root.addLayout(out_row)
        self.out_dir = None

        # Naming template
        root.addSpacing(6)
        name_row = QHBoxLayout()
        name_lbl = QLabel("Naming:")
        name_lbl.setFont(QFont(FONT, 9))
        name_row.addWidget(name_lbl)
        self.naming_input = QLineEdit(self._naming)
        self.naming_input.setFont(QFont(FONT, 9))
        self.naming_input.setPlaceholderText("{name}_unlocked")
        self.naming_input.setToolTip(
            "Output filename template. Variables:\n"
            "  {name} \u2014 original filename without extension"
        )
        self.naming_input.textChanged.connect(self._on_naming_changed)
        name_row.addWidget(self.naming_input, 1)
        root.addLayout(name_row)

        # ── Progress ──
        root.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        # ── Action bar ──
        root.addSpacing(10)
        root.addWidget(self._sep())
        root.addSpacing(12)

        bar = QHBoxLayout()
        self.btn_add = QPushButton("+ Add")
        self.btn_add.setFont(QFont(FONT, 11))
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
        self.btn_clear.setFont(QFont(FONT, 10))
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear)
        bar.addWidget(self.btn_clear)

        self.btn_sort = QPushButton("Sort")
        self.btn_sort.setObjectName("ghost")
        self.btn_sort.setFont(QFont(FONT, 10))
        self.btn_sort.setCursor(Qt.PointingHandCursor)
        self.btn_sort.clicked.connect(self._show_sort_menu)
        bar.addWidget(self.btn_sort)

        bar.addStretch()

        self.btn_open = QPushButton("Open folder")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setFont(QFont(FONT, 10))
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.clicked.connect(self._open_folder)
        self.btn_open.setVisible(False)
        bar.addWidget(self.btn_open)

        self.btn_go = QPushButton("Unlock")
        self.btn_go.setObjectName("primary")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self._run)
        bar.addWidget(self.btn_go)
        root.addLayout(bar)

        # ── Result ──
        root.addSpacing(6)
        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 10))
        self.result_lbl.setAlignment(Qt.AlignCenter)
        self.result_lbl.setWordWrap(True)
        root.addWidget(self.result_lbl)

    def _sep(self):
        s = QFrame()
        s.setObjectName("separator")
        s.setFrameShape(QFrame.HLine)
        return s

    # ── File management ─────────────────────────────────────────────

    def _browse(self):
        if self.running:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select files", "",
            "PDF & EPDF files (*.pdf *.epdf);;PDF files (*.pdf);;"
            "EPDF files (*.epdf);;All files (*)")
        if files:
            self._add_files(files)

    def _add_files(self, paths):
        existing = {r.filepath for r in self.rows}
        valid_ext = (".pdf", ".epdf")
        new = [p for p in paths
               if p.lower().endswith(valid_ext) and os.path.isfile(p)
               and p not in existing]
        if not new:
            return

        if not self.rows:
            self._show_list()

        new_paths = []
        for path in new:
            row = GenericFileRow(path, self.shell.theme)
            row.remove_clicked.connect(self._remove_row)

            # Auto-detect file type and show badge
            if is_epdf(path):
                row.set_badge("EPDF")
                try:
                    meta = epdf_read_metadata(path)
                    cipher = meta.get("cipher", "unknown")
                    row.set_status(
                        f"{fmt_size(os.path.getsize(path))}  \u00b7  "
                        f"Enhanced encryption ({cipher})"
                    )
                except Exception:
                    row.set_status(f"{fmt_size(os.path.getsize(path))}  \u00b7  Enhanced encryption")
            else:
                row.set_status(f"{fmt_size(os.path.getsize(path))}  \u00b7  Standard PDF encryption")

            idx = self.list_layout.count() - 1
            self.list_layout.insertWidget(idx, row)
            self.rows.append(row)
            new_paths.append(path)

        if new_paths:
            save_recent_files(self.shell.settings, "unlock", new_paths)

        self._update_summary()
        self.btn_go.setEnabled(bool(self.rows))
        self.result_lbl.setText("")
        self.btn_open.setVisible(False)

    def _remove_row(self, row):
        if self.running:
            return
        self.list_layout.removeWidget(row)
        row.deleteLater()
        self.rows.remove(row)
        if not self.rows:
            self._show_drop()
            self.btn_go.setEnabled(False)
        self._update_summary()

    def _clear(self):
        if self.running:
            return
        for row in list(self.rows):
            self.list_layout.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        self._show_drop()
        self.btn_go.setEnabled(False)
        self._update_summary()
        self.result_lbl.setText("")
        self.btn_open.setVisible(False)

    def _show_drop(self):
        self.scroll_frame.setVisible(False)
        self.drop_zone.setVisible(True)

    def _show_list(self):
        self.drop_zone.setVisible(False)
        self.scroll_frame.setVisible(True)

    def _update_summary(self):
        n = len(self.rows)
        if not n:
            self.summary_lbl.setText("")
            return
        n_epdf = sum(1 for r in self.rows if is_epdf(r.filepath))
        n_pdf = n - n_epdf
        parts = [f"{n} file{'s' if n != 1 else ''}"]
        if n_pdf:
            parts.append(f"{n_pdf} PDF")
        if n_epdf:
            parts.append(f"{n_epdf} EPDF")
        total = sum(os.path.getsize(r.filepath) for r in self.rows
                    if os.path.isfile(r.filepath))
        parts.append(fmt_size(total))
        self.summary_lbl.setText("  \u00b7  ".join(parts))

    def _show_recent(self):
        show_recent_menu(self, self.btn_recent, self.shell.settings,
                         "unlock", self._add_recent_file)

    def _add_recent_file(self, path):
        if os.path.isfile(path):
            self._add_files([path])

    def _show_sort_menu(self):
        menu = QMenu(self)
        for key, label in [
            ("name", "Sort by name"),
            ("size", "Sort by size (largest first)"),
        ]:
            action = menu.addAction(label)
            action.triggered.connect(lambda checked, k=key: self._sort_files(k))
        menu.exec(self.btn_sort.mapToGlobal(self.btn_sort.rect().bottomLeft()))

    def _sort_files(self, key):
        if not self.rows or self.running:
            return
        if key == "name":
            self.rows.sort(key=lambda r: os.path.basename(r.filepath).lower())
        elif key == "size":
            self.rows.sort(key=lambda r: os.path.getsize(r.filepath), reverse=True)
        for row in self.rows:
            self.list_layout.removeWidget(row)
        for i, row in enumerate(self.rows):
            self.list_layout.insertWidget(i, row)

    # ── Output ──────────────────────────────────────────────────────

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self.out_dir = d
            self.out_lbl.setText(d if len(d) < 38 else "\u2026" + d[-35:])

    def _reset_out(self):
        self.out_dir = None
        self.out_lbl.setText("Same folder as input")

    def _on_naming_changed(self, text):
        import re
        raw = text.strip() or "{name}_unlocked"
        sanitized = raw.replace("/", "").replace("\\", "").replace("..", "")
        allowed = re.sub(r'\{(?!name\})[^}]*\}', '', sanitized)
        self._naming = allowed or "{name}_unlocked"
        self._save_settings()

    def _build_output_path(self, filepath: str) -> str:
        name, _ext = os.path.splitext(os.path.basename(filepath))
        template = self._naming or "{name}_unlocked"
        try:
            output_name = template.format(name=name)
        except (KeyError, IndexError):
            output_name = f"{name}_unlocked"
        folder = self.out_dir or os.path.dirname(filepath)
        return os.path.join(folder, output_name + ".pdf")

    def _open_folder(self):
        if not self.rows:
            return
        folder = self.out_dir or os.path.dirname(self.rows[0].filepath)
        folder = os.path.realpath(os.path.abspath(folder))
        if not os.path.isdir(folder):
            return
        if "\x00" in folder or ".." in os.path.basename(folder):
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ── Execution ───────────────────────────────────────────────────

    def _run(self):
        if self.running or not self.rows:
            return

        password = self.pw_input.text()
        if not password:
            QMessageBox.warning(self, "Password needed",
                                "Enter the decryption password.")
            return

        # Check for existing output files
        existing = []
        for row in self.rows:
            out = self._build_output_path(row.filepath)
            if os.path.exists(out):
                existing.append(out)
        if existing:
            dlg = OverwriteDialog(existing, self.shell.theme, self.window())
            dlg.exec()
            if dlg.result_action != OverwriteDialog.OVERWRITE:
                return

        self.running = True
        self._results = []
        self._cancel_event = threading.Event()
        self.btn_go.setText("Cancel")
        self.btn_go.setEnabled(True)
        try:
            self.btn_go.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_go.clicked.connect(self._cancel)
        self.btn_add.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.btn_open.setVisible(False)
        self.result_lbl.setText("")
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress.setMaximum(len(self.rows))

        snapshot = [(r.filepath, is_epdf(r.filepath)) for r in self.rows]

        threading.Thread(
            target=self._worker,
            args=(snapshot, password),
            daemon=True,
        ).start()

    def _cancel(self):
        if self._cancel_event:
            self._cancel_event.set()
        self.btn_go.setEnabled(False)
        self.btn_go.setText("Cancelling\u2026")

    def _worker(self, snapshot, password):
        t0 = time.time()
        cancel = self._cancel_event

        for i, (filepath, file_is_epdf) in enumerate(snapshot):
            if cancel and cancel.is_set():
                break

            self.signals.progress.emit(i, 0, 0, "Starting...")
            output = self._build_output_path(filepath)

            try:
                if file_is_epdf:
                    epdf_decrypt(filepath, output, password)
                    detail = "EPDF decrypted"
                else:
                    unlock_pdf(filepath, output, password)
                    detail = "PDF unlocked"

                result = {
                    "file": os.path.basename(filepath),
                    "status": "OK",
                    "details": detail,
                    "output": output,
                }
                self.signals.file_done.emit(i, result)
            except EPDFPasswordError as e:
                log.warning("Wrong password for %s", filepath)
                result = {
                    "file": os.path.basename(filepath),
                    "status": "Error",
                    "details": "Wrong password",
                }
                self.signals.file_done.emit(i, e)
            except Exception as e:
                log.error("Unlock failed for %s: %s", filepath, e)
                result = {
                    "file": os.path.basename(filepath),
                    "status": "Error",
                    "details": str(e)[:80],
                }
                self.signals.file_done.emit(i, e)

            self._results.append(result)

        elapsed = time.time() - t0
        self.signals.all_done.emit(elapsed)

    def _on_progress(self, fi, cur, total, status):
        if fi < len(self.rows):
            self.rows[fi].set_working("Decrypting\u2026")
        self.progress.setValue(fi)

    def _on_file_done(self, fi, result):
        if fi >= len(self.rows):
            return
        row = self.rows[fi]
        if isinstance(result, Exception):
            row.set_error(str(result))
        else:
            out_name = os.path.basename(result.get("output", ""))
            row.set_done(f"Unlocked \u2192 {out_name}")
        self.progress.setValue(fi + 1)

    def _on_all_done(self, elapsed):
        was_cancelled = self._cancel_event and self._cancel_event.is_set()
        self.running = False
        self._cancel_event = None
        self.progress.setVisible(False)

        try:
            self.btn_go.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_go.clicked.connect(self._run)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("Unlock")
        self.btn_add.setEnabled(True)
        self.btn_clear.setEnabled(True)

        t = self.shell.theme
        n_ok = sum(1 for r in self._results if r.get("status") != "Error")
        n_err = sum(1 for r in self._results if r.get("status") == "Error")

        parts = []
        if was_cancelled:
            parts.append("Cancelled")
        if n_ok:
            parts.append(f"{n_ok} unlocked")
        if n_err:
            parts.append(f"{n_err} failed")
        parts.append(f"{elapsed:.1f}s")

        color = t.amber if was_cancelled else (t.green if n_ok else t.red)
        self.result_lbl.setStyleSheet(f"color: {color};")
        self.result_lbl.setText("  \u00b7  ".join(parts))
        self.summary_lbl.setText("  \u00b7  ".join(parts))

        if n_ok:
            self.btn_open.setVisible(True)

        if len(self._results) >= 2 and not was_cancelled:
            columns = [
                ("file", "File"),
                ("status", "Status"),
                ("details", "Details"),
            ]
            QTimer.singleShot(300, lambda: GenericSummaryDialog(
                "Unlock Complete", self._results, columns,
                elapsed, self.shell.theme, self.window()
            ).exec())

        msg = "  \u00b7  ".join(parts)
        notify_tray_if_minimized(self.shell, msg)
        self._save_settings()

    # ── BasePage interface ──────────────────────────────────────────

    def is_busy(self):
        return self.running

    def handle_drop(self, paths):
        if not self.running and paths:
            self._add_files(paths)

    def apply_theme(self, theme):
        t = theme
        self.summary_lbl.setStyleSheet(f"color: {t.text2};")
        self._pw_title.setStyleSheet(f"color: {t.text2};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self.drop_zone.apply_theme(t)
        self.list_widget.setStyleSheet(f"background: {t.surface};")
        for row in self.rows:
            row.apply_theme(t)
