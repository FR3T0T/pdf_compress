"""Compress page — PDF compression tool."""

import os
import sys
import time
import threading
import logging
import subprocess

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QFileDialog, QProgressBar, QLineEdit, QCheckBox,
    QMenu, QMessageBox, QDialog,
)

from engine import (
    PRESETS, PRESET_ORDER, compress_pdf, Result, fmt_size,
    EncryptedPDFError, CancelledError, FileTooLargeError, InvalidPDFError,
    find_ghostscript, analyze_pdf,
)
from ..theme import Theme, FONT
from ..signals import Signals
from ..widgets import FileRow, PresetCard, DropZone
from ..dialogs import (
    PasswordDialog, OverwriteDialog, SpaceAuditDialog, SummaryDialog,
)
from .base import BasePage

log = logging.getLogger(__name__)


class CompressPage(BasePage):
    page_title = "Compress"
    page_icon = "\u2193"  # ↓

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.rows: list[FileRow] = []
        self.preset_key = "standard"
        self.out_dir = None
        self.running = False
        self._results = []
        self._cancel_event: threading.Event | None = None

        self.signals = Signals()
        self.signals.progress.connect(self._on_progress)
        self.signals.file_done.connect(self._on_file_done)
        self.signals.all_done.connect(self._on_all_done)
        self.signals.analysis_done.connect(self._on_analysis_done)

        self._load_settings()
        self._build()
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+O"), self, self._browse)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._run)
        QShortcut(QKeySequence("Escape"), self, self._clear)

    def _load_settings(self):
        s = self.shell.settings
        saved_preset = s.value("compress/preset", "standard")
        if saved_preset in PRESETS:
            self.preset_key = saved_preset
        self.linearize = s.value("compress/linearize", "false") == "true"
        self.use_gs = s.value("compress/use_gs", "false") == "true"
        self.replace_original = s.value("compress/replace_original", "false") == "true"
        self._replace_warned = s.value("compress/replace_warned", "false") == "true"
        self.backup_enabled = s.value("compress/backup_enabled", "true") == "true"
        self.naming_template = s.value("compress/naming_template", "{name}_compressed")
        self._sort_key = s.value("compress/sort_key", "none")

    def _save_settings(self):
        s = self.shell.settings
        s.setValue("compress/preset", self.preset_key)
        s.setValue("compress/linearize", "true" if self.linearize else "false")
        s.setValue("compress/use_gs", "true" if self.use_gs else "false")
        s.setValue("compress/replace_original", "true" if self.replace_original else "false")
        s.setValue("compress/replace_warned", "true" if self._replace_warned else "false")
        s.setValue("compress/backup_enabled", "true" if self.backup_enabled else "false")
        s.setValue("compress/naming_template", self.naming_template)
        s.setValue("compress/sort_key", self._sort_key)

    # ── Build ────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 20, 32, 24)
        root.setSpacing(0)

        # ── File area ──
        self.drop_zone = DropZone(self.shell.theme)
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

        # ── Summary ──
        root.addSpacing(8)
        self.summary_lbl = QLabel("")
        self.summary_lbl.setFont(QFont(FONT, 10))
        root.addWidget(self.summary_lbl)

        # ── Preset cards ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(10)

        q_hdr = QHBoxLayout()
        self.q_title = QLabel("QUALITY")
        self.q_title.setFont(QFont(FONT, 9, QFont.Bold))
        q_hdr.addWidget(self.q_title)
        q_hdr.addStretch()
        self.q_detail = QLabel("")
        self.q_detail.setFont(QFont(FONT, 10))
        q_hdr.addWidget(self.q_detail)
        root.addLayout(q_hdr)
        root.addSpacing(6)

        self.preset_cards = {}
        self.presets_layout = QVBoxLayout()
        self.presets_layout.setSpacing(4)
        for key in PRESET_ORDER:
            card = PresetCard(key, PRESETS[key], self.shell.theme)
            card.clicked.connect(self._on_preset)
            if key == self.preset_key:
                card.set_selected(True)
            self.preset_cards[key] = card
            self.presets_layout.addWidget(card)
        root.addLayout(self.presets_layout)
        self._update_q_detail()

        # ── Output ──
        root.addSpacing(10)
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

        # ── Naming template ──
        root.addSpacing(6)
        name_row = QHBoxLayout()
        name_lbl = QLabel("Naming:")
        name_lbl.setFont(QFont(FONT, 9))
        name_row.addWidget(name_lbl)
        self.naming_input = QLineEdit(self.naming_template)
        self.naming_input.setFont(QFont(FONT, 9))
        self.naming_input.setPlaceholderText("{name}_compressed")
        self.naming_input.setToolTip(
            "Output filename template. Variables:\n"
            "  {name} \u2014 original filename without extension\n"
            "  {preset} \u2014 preset name (e.g. standard)\n"
            "  {dpi} \u2014 target DPI\n"
            "Example: {name}_{preset}_{dpi}dpi"
        )
        self.naming_input.textChanged.connect(self._on_naming_changed)
        name_row.addWidget(self.naming_input, 1)
        root.addLayout(name_row)

        # ── Option checkboxes ──
        root.addSpacing(4)

        self.chk_linearize = QCheckBox("Web-optimized (linearized)")
        self.chk_linearize.setChecked(self.linearize)
        self.chk_linearize.toggled.connect(self._on_linearize_toggled)
        root.addWidget(self.chk_linearize)

        self.chk_gs = QCheckBox("Full optimization (requires Ghostscript)")
        self._gs_path = find_ghostscript()
        if self._gs_path:
            self.chk_gs.setChecked(self.use_gs)
        else:
            self.chk_gs.setChecked(False)
            self.chk_gs.setEnabled(False)
            self.chk_gs.setToolTip("Ghostscript not found \u2014 install from ghostscript.com")
            self.use_gs = False
        self.chk_gs.toggled.connect(self._on_gs_toggled)
        root.addWidget(self.chk_gs)

        self.chk_replace = QCheckBox("Replace original files")
        self.chk_replace.setChecked(self.replace_original)
        self.chk_replace.toggled.connect(self._on_replace_toggled)
        root.addWidget(self.chk_replace)

        self.chk_backup = QCheckBox("Create backup when replacing originals")
        self.chk_backup.setChecked(self.backup_enabled)
        self.chk_backup.setToolTip("Saves a .backup copy before overwriting the original")
        self.chk_backup.toggled.connect(self._on_backup_toggled)
        self.chk_backup.setVisible(self.replace_original)
        root.addWidget(self.chk_backup)

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
        self.btn_recent.clicked.connect(self._show_recent_menu)
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
        self.btn_sort.setToolTip("Sort file list")
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

        self.btn_go = QPushButton("Compress")
        self.btn_go.setObjectName("primary")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setEnabled(False)
        self.btn_go.clicked.connect(self._run)
        bar.addWidget(self.btn_go)
        root.addLayout(bar)

        # ── Status ──
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

    # ── BasePage interface ──────────────────────────────────────

    def is_busy(self) -> bool:
        return self.running

    def handle_drop(self, paths: list[str]):
        if not self.running:
            self._add_files(paths)

    # ── Theme ────────────────────────────────────────────────────

    def apply_theme(self, theme: Theme):
        t = theme
        self.summary_lbl.setStyleSheet(f"color: {t.text2};")
        self.q_title.setStyleSheet(f"color: {t.text2};")
        self.q_detail.setStyleSheet(f"color: {t.text3};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")

        self.drop_zone.apply_theme(t)
        self.list_widget.setStyleSheet(f"background: {t.surface};")

        for card in self.preset_cards.values():
            card.apply_theme(t)

        for row in self.rows:
            row.apply_theme(t)

    # ── View switching ───────────────────────────────────────────

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

        compressible = [r for r in self.rows if not r.analysis.is_encrypted]
        encrypted = n - len(compressible)

        if not compressible:
            self.summary_lbl.setText(
                f"{n} file{'s' if n != 1 else ''}  \u00b7  all password-protected"
            )
            return

        total = sum(r.analysis.file_size for r in compressible)
        preset = PRESETS[self.preset_key]
        est = sum(r.analysis.estimate_output(preset) for r in compressible)
        pct = (1 - est / total) * 100 if total > 0 else 0
        total_img = sum(r.analysis.image_count for r in compressible)

        parts = [f"{n} file{'s' if n != 1 else ''}"]
        if encrypted:
            parts.append(f"{encrypted} locked")
        parts.append(f"{fmt_size(total)}  \u2192  ~{fmt_size(est)}")
        parts.append(f"~{pct:.0f}% est. saving")
        parts.append(f"{total_img} images")
        self.summary_lbl.setText("  \u00b7  ".join(parts))

    # ── Files ────────────────────────────────────────────────────

    def _browse(self):
        if self.running:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs", "", "PDF files (*.pdf);;All files (*)")
        if files:
            self._add_files(files)

    def _add_files(self, paths):
        existing = {r.filepath for r in self.rows}
        new = [p for p in paths
               if p.lower().endswith(".pdf") and os.path.isfile(p) and p not in existing]
        if not new:
            return

        self.btn_add.setEnabled(False)
        self.btn_add.setText("Analyzing\u2026")

        def _analyze():
            results = []
            for p in new:
                results.append((p, analyze_pdf(p)))
            self.signals.analysis_done.emit(results)

        threading.Thread(target=_analyze, daemon=True).start()

    def _on_analysis_done(self, results):
        self.btn_add.setEnabled(True)
        self.btn_add.setText("+ Add")

        if not results:
            return
        if not self.rows:
            self._show_list()

        new_paths = []
        for path, analysis in results:
            if any(r.filepath == path for r in self.rows):
                continue
            row = FileRow(path, analysis, self.shell.theme)
            row.update_estimate(self.preset_key)
            row.remove_clicked.connect(self._remove_row)
            row.info_btn.clicked.connect(
                lambda checked, r=row: SpaceAuditDialog(
                    r.filepath, r.analysis, self.shell.theme, self.window()
                ).exec()
            )
            idx = self.list_layout.count() - 1
            self.list_layout.insertWidget(idx, row)
            self.rows.append(row)
            new_paths.append(path)

        if new_paths:
            self._save_recent_files(new_paths)

        self._update_summary()
        self.btn_go.setEnabled(bool(self.rows))
        self.result_lbl.setText("")
        self.btn_open.setVisible(False)

    def _load_recent_files(self):
        raw = self.shell.settings.value("compress/recent_files", [])
        if isinstance(raw, str):
            raw = [raw] if raw else []
        return [f for f in raw if isinstance(f, str)]

    def _save_recent_files(self, new_paths):
        recent = self._load_recent_files()
        for p in new_paths:
            if p in recent:
                recent.remove(p)
            recent.insert(0, p)
        recent = recent[:20]
        self.shell.settings.setValue("compress/recent_files", recent)

    def _show_recent_menu(self):
        recent = self._load_recent_files()
        if not recent:
            return
        menu = QMenu(self)
        for path in recent:
            name = os.path.basename(path)
            action = menu.addAction(name)
            action.setToolTip(path)
            action.triggered.connect(lambda checked, p=path: self._add_recent_file(p))
        menu.exec(self.btn_recent.mapToGlobal(
            self.btn_recent.rect().bottomLeft()))

    def _add_recent_file(self, path):
        if os.path.isfile(path):
            self._add_files([path])

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

    # ── Output ───────────────────────────────────────────────────

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder")
        if d:
            self.out_dir = d
            self.out_lbl.setText(d if len(d) < 38 else "\u2026" + d[-35:])

    def _reset_out(self):
        self.out_dir = None
        self.out_lbl.setText("Same folder as input")

    def _on_linearize_toggled(self, checked):
        self.linearize = checked
        self._save_settings()

    def _on_gs_toggled(self, checked):
        self.use_gs = checked
        self._save_settings()

    def _on_replace_toggled(self, checked):
        if checked and not self._replace_warned:
            msg = QMessageBox(self)
            msg.setWindowTitle("Warning")
            msg.setText(
                "This will overwrite your original PDF files with the "
                "compressed versions.\n\n"
                "A .backup copy will be created if the backup option is enabled.\n\n"
                "Are you sure you want to enable this?"
            )
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg.setDefaultButton(QMessageBox.No)
            if msg.exec() != QMessageBox.Yes:
                self.chk_replace.setChecked(False)
                return
            self._replace_warned = True
        self.replace_original = checked
        self.chk_backup.setVisible(checked)
        self._save_settings()

    def _on_backup_toggled(self, checked: bool):
        self.backup_enabled = checked
        self._save_settings()

    def _on_naming_changed(self, text: str):
        raw = text.strip() or "{name}_compressed"
        # Sanitize: only allow known format variables, reject path separators
        import re
        sanitized = raw.replace("/", "").replace("\\", "").replace("..", "")
        # Only allow {name}, {preset}, {dpi} — strip any other braces patterns
        allowed = re.sub(r'\{(?!name\}|preset\}|dpi\})[^}]*\}', '', sanitized)
        self.naming_template = allowed or "{name}_compressed"
        self._save_settings()

    def _show_sort_menu(self):
        menu = QMenu(self)
        for key, label in [
            ("name", "Sort by name"),
            ("size", "Sort by size (largest first)"),
            ("pages", "Sort by page count"),
        ]:
            action = menu.addAction(label)
            action.triggered.connect(lambda checked, k=key: self._sort_files(k))
        menu.exec(self.btn_sort.mapToGlobal(
            self.btn_sort.rect().bottomLeft()))

    def _sort_files(self, key: str):
        if not self.rows or self.running:
            return
        self._sort_key = key

        if key == "name":
            self.rows.sort(key=lambda r: os.path.basename(r.filepath).lower())
        elif key == "size":
            self.rows.sort(key=lambda r: r.analysis.file_size, reverse=True)
        elif key == "pages":
            self.rows.sort(key=lambda r: r.analysis.page_count, reverse=True)

        for i, row in enumerate(self.rows):
            self.list_layout.removeWidget(row)
        for i, row in enumerate(self.rows):
            self.list_layout.insertWidget(i, row)
        self._save_settings()

    def _build_output_name(self, filepath: str) -> str:
        name, ext = os.path.splitext(os.path.basename(filepath))
        preset = PRESETS[self.preset_key]
        template = self.naming_template or "{name}_compressed"

        try:
            output_name = template.format(
                name=name,
                preset=self.preset_key,
                dpi=preset.target_dpi,
            )
        except (KeyError, IndexError):
            output_name = f"{name}_compressed"

        return output_name + ext

    def _open_folder(self):
        if not self.rows:
            return
        folder = self.out_dir or os.path.dirname(self.rows[0].filepath)
        folder = os.path.realpath(os.path.abspath(folder))
        if not os.path.isdir(folder):
            log.warning("Attempted to open non-directory path: %s", folder)
            return
        # Reject paths with null bytes or suspicious patterns
        if "\x00" in folder or ".." in os.path.basename(folder):
            log.warning("Rejected suspicious folder path: %s", folder)
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # ── Presets ──────────────────────────────────────────────────

    def _on_preset(self, key):
        self.preset_key = key
        for k, card in self.preset_cards.items():
            card.set_selected(k == key)
        self._update_q_detail()
        for row in self.rows:
            row.update_estimate(key)
        self._update_summary()
        self._save_settings()

    def _update_q_detail(self):
        p = PRESETS[self.preset_key]
        meta = "  \u00b7  strips metadata" if p.strip_metadata else ""
        self.q_detail.setText(f"{p.target_dpi} DPI  \u00b7  JPEG {p.jpeg_quality}%{meta}")

    # ── Compression ──────────────────────────────────────────────

    def _run(self):
        if self.running or not self.rows:
            return

        # ── Warn about invalid PDFs ──
        invalid = [r for r in self.rows if not r.analysis.is_valid_pdf]
        if invalid:
            names = ", ".join(os.path.basename(r.filepath) for r in invalid[:3])
            if len(invalid) > 3:
                names += f" (+{len(invalid) - 3} more)"
            msg = QMessageBox(self)
            msg.setWindowTitle("Invalid Files")
            msg.setText(
                f"The following files are not valid PDFs and will be skipped:\n\n{names}"
            )
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec()

        # ── Warn about PDF/A + metadata stripping ──
        preset = PRESETS[self.preset_key]
        if preset.strip_metadata:
            pdfa_rows = [r for r in self.rows if r.analysis.pdfa_conformance]
            if pdfa_rows:
                names = ", ".join(
                    f"{os.path.basename(r.filepath)} ({r.analysis.pdfa_conformance})"
                    for r in pdfa_rows[:3]
                )
                msg = QMessageBox(self)
                msg.setWindowTitle("PDF/A Warning")
                msg.setText(
                    f"The selected preset strips metadata, which will break "
                    f"PDF/A compliance on:\n\n{names}\n\n"
                    "Continue anyway?"
                )
                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                msg.setDefaultButton(QMessageBox.Yes)
                if msg.exec() != QMessageBox.Yes:
                    return

        # ── Check for existing output files ──
        existing_outputs = []
        if not self.replace_original:
            for row in self.rows:
                if row.analysis.is_encrypted and row.password is None:
                    continue
                if not row.analysis.is_valid_pdf:
                    continue
                if self.out_dir:
                    out_name = self._build_output_name(row.filepath)
                    out = os.path.join(self.out_dir, out_name)
                else:
                    out_name = self._build_output_name(row.filepath)
                    out = os.path.join(os.path.dirname(row.filepath), out_name)
                if os.path.exists(out):
                    existing_outputs.append(out)

        if existing_outputs:
            dlg = OverwriteDialog(existing_outputs, self.shell.theme, self.window())
            dlg.exec()
            if dlg.result_action != OverwriteDialog.OVERWRITE:
                return

        # ── Prompt for passwords on encrypted files ──
        for row in self.rows:
            if row.analysis.is_encrypted and row.password is None:
                dlg = PasswordDialog(
                    os.path.basename(row.filepath), self.shell.theme, self.window())
                if dlg.exec() == QDialog.Accepted and dlg.password:
                    row.password = dlg.password
                    self._reanalyze_with_password(row)

        self.running = True
        self._results = []
        self._cancel_event = threading.Event()
        self.btn_go.setText("Cancel")
        self.btn_go.setEnabled(True)
        self.btn_go.setStyleSheet("")
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

        for card in self.preset_cards.values():
            card.setEnabled(False)

        pk = self.preset_key
        use_gs = self.use_gs
        linearize = self.linearize
        replace_orig = self.replace_original
        backup = self.backup_enabled and replace_orig
        naming = self.naming_template
        row_snapshot = [
            (r.filepath, r.password, r.analysis.is_encrypted, r.analysis.is_valid_pdf)
            for r in self.rows
        ]
        threading.Thread(
            target=self._worker,
            args=(pk, row_snapshot, use_gs, linearize, replace_orig, backup, naming),
            daemon=True,
        ).start()

    def _reanalyze_with_password(self, row):
        try:
            row.analysis = analyze_pdf(row.filepath, password=row.password)
            row.update_estimate(self.preset_key)
        except Exception as e:
            log.warning("Re-analysis with password failed: %s", e)

    def _cancel(self):
        if self._cancel_event:
            self._cancel_event.set()
        self.btn_go.setEnabled(False)
        self.btn_go.setText("Cancelling\u2026")

    def _worker(self, preset_key, row_snapshot, use_gs, linearize,
                replace_orig, backup_enabled, naming_template):
        t0 = time.time()
        cancel = self._cancel_event
        preset = PRESETS[preset_key]

        for i, (filepath, password, is_encrypted, is_valid) in enumerate(row_snapshot):
            if cancel and cancel.is_set():
                break

            if not is_valid:
                self.signals.file_done.emit(
                    i, InvalidPDFError("Not a valid PDF file"))
                continue

            if is_encrypted and password is None:
                self.signals.file_done.emit(
                    i, EncryptedPDFError("Password-protected \u2014 skipped"))
                continue

            if replace_orig:
                out = filepath
            elif self.out_dir:
                name, ext = os.path.splitext(os.path.basename(filepath))
                try:
                    out_name = (naming_template or "{name}_compressed").format(
                        name=name, preset=preset_key, dpi=preset.target_dpi,
                    ) + ext
                except (KeyError, IndexError):
                    out_name = f"{name}_compressed{ext}"
                out = os.path.join(self.out_dir, out_name)
            else:
                name, ext = os.path.splitext(os.path.basename(filepath))
                try:
                    out_name = (naming_template or "{name}_compressed").format(
                        name=name, preset=preset_key, dpi=preset.target_dpi,
                    ) + ext
                except (KeyError, IndexError):
                    out_name = f"{name}_compressed{ext}"
                out = os.path.join(os.path.dirname(filepath), out_name)

            self.signals.progress.emit(i, 0, 0, "Starting...")

            def cb(cur, total, status, _i=i):
                self.signals.progress.emit(_i, cur, total, status)

            try:
                result = compress_pdf(
                    filepath, out, preset_key=preset_key,
                    on_progress=cb, cancel=cancel, password=password,
                    use_ghostscript=use_gs, linearize=linearize,
                    backup_on_overwrite=backup_enabled,
                )
                self.signals.file_done.emit(i, result)
            except CancelledError:
                break
            except Exception as e:
                log.error("Compression failed for %s: %s", filepath, e)
                self.signals.file_done.emit(i, e)

        elapsed = time.time() - t0
        self.signals.all_done.emit(elapsed)

    def _on_progress(self, fi, cur, total, status):
        row = self.rows[fi]
        if cur == 0 and total == 0:
            row.set_working()
        else:
            row.set_progress(cur, total, status)
        n = len(self.rows)
        pct = (fi / n) * 100
        if total > 0:
            pct += (cur / total) * (100 / n)
        self.progress.setValue(int(min(100, pct)))

    def _on_file_done(self, fi, result):
        row = self.rows[fi]
        if isinstance(result, EncryptedPDFError):
            row.set_error(str(result))
            self._results.append(result)
        elif isinstance(result, Exception):
            row.set_error(str(result))
            self._results.append(result)
        else:
            row.set_done(result)
            self._results.append(result)

    def _on_all_done(self, elapsed):
        was_cancelled = self._cancel_event is not None and self._cancel_event.is_set()
        self.running = False
        self._cancel_event = None
        self.progress.setVisible(False)

        try:
            self.btn_go.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_go.clicked.connect(self._run)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("Compress")
        self.btn_go.setStyleSheet("")

        self.btn_add.setEnabled(True)
        self.btn_clear.setEnabled(True)
        for card in self.preset_cards.values():
            card.setEnabled(True)

        t = self.shell.theme
        results = [r for r in self._results if isinstance(r, Result)]
        n_ok = sum(1 for r in results if not r.skipped)
        n_skip = sum(1 for r in results if r.skipped)
        n_err = sum(1 for r in self._results if isinstance(r, Exception))
        total_saved = sum(r.saved_bytes for r in results)
        total_orig = sum(r.original_size for r in results)

        parts = []
        if was_cancelled:
            parts.append("Cancelled")
        if n_ok:   parts.append(f"{n_ok} compressed")
        if n_skip: parts.append(f"{n_skip} already optimized")
        if n_err:  parts.append(f"{n_err} failed")
        if total_saved > 0 and total_orig > 0:
            parts.append(f"saved {fmt_size(total_saved)} ({total_saved/total_orig*100:.0f}%)")
        parts.append(f"{elapsed:.1f}s")

        color = t.amber if was_cancelled else (t.green if n_ok else (t.amber if n_skip else t.red))
        self.result_lbl.setStyleSheet(f"color: {color};")
        self.result_lbl.setText("  \u00b7  ".join(parts))
        self.summary_lbl.setText("  \u00b7  ".join(parts))
        if n_ok or n_skip:
            self.btn_open.setVisible(True)

        if len(results) >= 2 and not was_cancelled:
            QTimer.singleShot(300, lambda: SummaryDialog(
                results, elapsed, self.shell.theme, self.window()
            ).exec())

        # System tray notification if window is minimized
        shell = self.shell
        if shell.isMinimized() and shell.tray_icon.isVisible():
            from PySide6.QtWidgets import QSystemTrayIcon
            tray_msg = "  \u00b7  ".join(parts)
            shell.tray_icon.showMessage(
                "PDF Toolkit", tray_msg,
                QSystemTrayIcon.MessageIcon.Information, 5000
            )

        self._save_settings()
