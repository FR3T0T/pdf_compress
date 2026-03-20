"""Protect PDF — dual-mode encryption with batch support.

Modes:
  Standard PDF: AES-256 / AES-128 (opens in any PDF reader)
  Enhanced .epdf: ChaCha20-Poly1305 / AES-256-GCM / Camellia-256
                  with Argon2id / Argon2d key derivation
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
    QComboBox, QMenu, QMessageBox,
)

from engine import fmt_size
from pdf_ops import protect_pdf
from epdf_crypto import (
    epdf_encrypt, CIPHERS, KDFS,
)
from ..theme import Theme, FONT
from ..signals import Signals
from ..widgets import DropZone
from ..widgets_generic import GenericFileRow
from ..dialogs import OverwriteDialog, GenericSummaryDialog
from ..batch_helpers import (
    load_recent_files, save_recent_files, show_recent_menu,
    setup_standard_shortcuts, notify_tray_if_minimized,
)
from .base import BasePage

log = logging.getLogger(__name__)


class ProtectPage(BasePage):
    page_title = "Protect PDF"
    page_icon = "lock"
    page_key = "protect"

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
        self._mode = s.value("protect/mode", "standard")  # "standard" or "enhanced"
        self._std_enc = s.value("protect/std_enc", "AES-256")
        self._cipher = s.value("protect/cipher", "chacha20-poly1305")
        self._kdf = s.value("protect/kdf", "argon2id")
        self._naming = s.value("protect/naming", "{name}_protected")
        self._perm_print = s.value("protect/perm_print", "true") == "true"
        self._perm_copy = s.value("protect/perm_copy", "true") == "true"
        self._perm_edit = s.value("protect/perm_edit", "true") == "true"
        self._perm_annotate = s.value("protect/perm_annotate", "true") == "true"

    def _save_settings(self):
        s = self.shell.settings
        s.setValue("protect/mode", self._mode)
        s.setValue("protect/std_enc", self._std_enc)
        s.setValue("protect/cipher", self._cipher)
        s.setValue("protect/kdf", self._kdf)
        s.setValue("protect/naming", self._naming)
        s.setValue("protect/perm_print", "true" if self._perm_print else "false")
        s.setValue("protect/perm_copy", "true" if self._perm_copy else "false")
        s.setValue("protect/perm_edit", "true" if self._perm_edit else "false")
        s.setValue("protect/perm_annotate", "true" if self._perm_annotate else "false")

    # ── Build UI ────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 20, 32, 24)
        root.setSpacing(0)

        # ── Drop zone ──
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Drop PDFs to protect")
        self.drop_zone.hint_lbl.setText("add password protection and encryption")
        self.drop_zone.clicked.connect(self._browse)
        root.addWidget(self.drop_zone)

        # ── File list (scrollable) ──
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

        # ── Encryption Mode ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        mode_hdr = QHBoxLayout()
        self.mode_title = QLabel("ENCRYPTION MODE")
        self.mode_title.setFont(QFont(FONT, 9, QFont.Bold))
        mode_hdr.addWidget(self.mode_title)
        mode_hdr.addStretch()
        root.addLayout(mode_hdr)
        root.addSpacing(6)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Standard PDF (AES — opens in any reader)", "standard")
        self.mode_combo.addItem("Enhanced .epdf (advanced ciphers — this toolkit only)", "enhanced")
        idx = 0 if self._mode == "standard" else 1
        self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        root.addWidget(self.mode_combo)

        # ── Standard options ──
        root.addSpacing(8)
        self.std_frame = QFrame()
        std_layout = QVBoxLayout(self.std_frame)
        std_layout.setContentsMargins(0, 0, 0, 0)
        std_layout.setSpacing(4)

        enc_row = QHBoxLayout()
        enc_row.addWidget(QLabel("Encryption:"))
        self.enc_combo = QComboBox()
        self.enc_combo.addItems(["AES-256", "AES-128"])
        self.enc_combo.setCurrentText(self._std_enc)
        self.enc_combo.currentTextChanged.connect(self._on_std_enc_changed)
        enc_row.addWidget(self.enc_combo, 1)
        std_layout.addLayout(enc_row)

        std_layout.addSpacing(6)
        perm_lbl = QLabel("PERMISSIONS")
        perm_lbl.setFont(QFont(FONT, 8, QFont.Bold))
        std_layout.addWidget(perm_lbl)
        self._perm_section_lbl = perm_lbl

        self.chk_print = QCheckBox("Allow printing")
        self.chk_print.setChecked(self._perm_print)
        self.chk_print.toggled.connect(lambda v: setattr(self, '_perm_print', v) or self._save_settings())
        std_layout.addWidget(self.chk_print)

        self.chk_copy = QCheckBox("Allow copying text")
        self.chk_copy.setChecked(self._perm_copy)
        self.chk_copy.toggled.connect(lambda v: setattr(self, '_perm_copy', v) or self._save_settings())
        std_layout.addWidget(self.chk_copy)

        self.chk_edit = QCheckBox("Allow editing")
        self.chk_edit.setChecked(self._perm_edit)
        self.chk_edit.toggled.connect(lambda v: setattr(self, '_perm_edit', v) or self._save_settings())
        std_layout.addWidget(self.chk_edit)

        self.chk_annotate = QCheckBox("Allow annotations")
        self.chk_annotate.setChecked(self._perm_annotate)
        self.chk_annotate.toggled.connect(lambda v: setattr(self, '_perm_annotate', v) or self._save_settings())
        std_layout.addWidget(self.chk_annotate)

        root.addWidget(self.std_frame)

        # ── Enhanced options ──
        self.enh_frame = QFrame()
        enh_layout = QVBoxLayout(self.enh_frame)
        enh_layout.setContentsMargins(0, 0, 0, 0)
        enh_layout.setSpacing(4)

        cipher_row = QHBoxLayout()
        cipher_row.addWidget(QLabel("Cipher:"))
        self.cipher_combo = QComboBox()
        for key, label in CIPHERS.items():
            self.cipher_combo.addItem(label, key)
        # Set saved cipher
        for i in range(self.cipher_combo.count()):
            if self.cipher_combo.itemData(i) == self._cipher:
                self.cipher_combo.setCurrentIndex(i)
                break
        self.cipher_combo.currentIndexChanged.connect(self._on_cipher_changed)
        cipher_row.addWidget(self.cipher_combo, 1)
        enh_layout.addLayout(cipher_row)

        kdf_row = QHBoxLayout()
        kdf_row.addWidget(QLabel("Key derivation:"))
        self.kdf_combo = QComboBox()
        for key, label in KDFS.items():
            self.kdf_combo.addItem(label, key)
        for i in range(self.kdf_combo.count()):
            if self.kdf_combo.itemData(i) == self._kdf:
                self.kdf_combo.setCurrentIndex(i)
                break
        self.kdf_combo.currentIndexChanged.connect(self._on_kdf_changed)
        kdf_row.addWidget(self.kdf_combo, 1)
        enh_layout.addLayout(kdf_row)

        enh_note = QLabel(
            "Enhanced encryption creates .epdf files that can only be "
            "opened with this toolkit. Uses military-grade cryptography "
            "with memory-hard key derivation."
        )
        enh_note.setFont(QFont(FONT, 9))
        enh_note.setWordWrap(True)
        enh_layout.addWidget(enh_note)
        self._enh_note = enh_note

        root.addWidget(self.enh_frame)

        # ── Passwords ──
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        pw_lbl = QLabel("PASSWORDS")
        pw_lbl.setFont(QFont(FONT, 9, QFont.Bold))
        root.addWidget(pw_lbl)
        self._pw_section_lbl = pw_lbl
        root.addSpacing(6)

        row1 = QHBoxLayout()
        self.user_pw_lbl = QLabel("User password:")
        row1.addWidget(self.user_pw_lbl)
        self.user_pw = QLineEdit()
        self.user_pw.setEchoMode(QLineEdit.Password)
        self.user_pw.setPlaceholderText("Required to open the PDF")
        row1.addWidget(self.user_pw, 1)
        root.addLayout(row1)
        root.addSpacing(4)

        row2 = QHBoxLayout()
        self.owner_pw_lbl = QLabel("Owner password:")
        row2.addWidget(self.owner_pw_lbl)
        self.owner_pw = QLineEdit()
        self.owner_pw.setEchoMode(QLineEdit.Password)
        self.owner_pw.setPlaceholderText("Required to change permissions")
        row2.addWidget(self.owner_pw, 1)
        root.addLayout(row2)
        self._owner_row = row2
        root.addSpacing(4)

        self.chk_show_pw = QCheckBox("Show passwords")
        self.chk_show_pw.toggled.connect(self._toggle_pw_visibility)
        root.addWidget(self.chk_show_pw)

        # Password strength indicator
        root.addSpacing(4)
        self.strength_lbl = QLabel("")
        self.strength_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.strength_lbl)
        self.user_pw.textChanged.connect(self._update_strength)
        self.owner_pw.textChanged.connect(self._update_strength)

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
        self.naming_input.setPlaceholderText("{name}_protected")
        self.naming_input.setToolTip(
            "Output filename template. Variables:\n"
            "  {name} \u2014 original filename without extension\n"
            "  {cipher} \u2014 cipher name\n"
            "  {mode} \u2014 standard or enhanced"
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

        self.btn_go = QPushButton("Protect")
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

        # Set initial mode visibility
        self._update_mode_visibility()

    def _sep(self):
        s = QFrame()
        s.setObjectName("separator")
        s.setFrameShape(QFrame.HLine)
        return s

    # ── Mode switching ──────────────────────────────────────────────

    def _on_mode_changed(self, index):
        self._mode = self.mode_combo.itemData(index)
        self._update_mode_visibility()
        self._save_settings()

    def _update_mode_visibility(self):
        is_std = self._mode == "standard"
        self.std_frame.setVisible(is_std)
        self.enh_frame.setVisible(not is_std)

        # In enhanced mode, hide owner password (single password)
        self.owner_pw_lbl.setVisible(is_std)
        self.owner_pw.setVisible(is_std)

        if is_std:
            self.user_pw.setPlaceholderText("Required to open the PDF")
        else:
            self.user_pw.setPlaceholderText("Encryption password")

    def _on_std_enc_changed(self, text):
        self._std_enc = text
        self._save_settings()

    def _on_cipher_changed(self, index):
        self._cipher = self.cipher_combo.itemData(index)
        self._save_settings()

    def _on_kdf_changed(self, index):
        self._kdf = self.kdf_combo.itemData(index)
        self._save_settings()

    # ── Password ────────────────────────────────────────────────────

    def _update_strength(self):
        pw = self.user_pw.text() or self.owner_pw.text()
        t = self.shell.theme
        if not pw:
            self.strength_lbl.setText("")
            return
        score = 0
        if len(pw) >= 8: score += 1
        if len(pw) >= 12: score += 1
        if any(c.isupper() for c in pw) and any(c.islower() for c in pw): score += 1
        if any(c.isdigit() for c in pw): score += 1
        if any(not c.isalnum() for c in pw): score += 1

        if score <= 1:
            self.strength_lbl.setText(
                "Weak password \u2014 consider using 12+ chars with mixed case, numbers, and symbols")
            self.strength_lbl.setStyleSheet(f"color: {t.red};")
        elif score <= 3:
            self.strength_lbl.setText("Moderate password strength")
            self.strength_lbl.setStyleSheet(f"color: {t.amber};")
        else:
            self.strength_lbl.setText("Strong password")
            self.strength_lbl.setStyleSheet(f"color: {t.green};")

    def _toggle_pw_visibility(self, show):
        mode = QLineEdit.Normal if show else QLineEdit.Password
        self.user_pw.setEchoMode(mode)
        self.owner_pw.setEchoMode(mode)

    # ── File management ─────────────────────────────────────────────

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
               if p.lower().endswith(".pdf") and os.path.isfile(p)
               and p not in existing]
        if not new:
            return

        if not self.rows:
            self._show_list()

        new_paths = []
        for path in new:
            row = GenericFileRow(path, self.shell.theme)
            row.remove_clicked.connect(self._remove_row)
            idx = self.list_layout.count() - 1
            self.list_layout.insertWidget(idx, row)
            self.rows.append(row)
            new_paths.append(path)

        if new_paths:
            save_recent_files(self.shell.settings, "protect", new_paths)

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
        total = sum(os.path.getsize(r.filepath) for r in self.rows
                    if os.path.isfile(r.filepath))
        mode_label = "Standard PDF" if self._mode == "standard" else "Enhanced .epdf"
        self.summary_lbl.setText(
            f"{n} file{'s' if n != 1 else ''}  \u00b7  "
            f"{fmt_size(total)}  \u00b7  {mode_label}"
        )

    def _show_recent(self):
        show_recent_menu(self, self.btn_recent, self.shell.settings,
                         "protect", self._add_recent_file)

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
        raw = text.strip() or "{name}_protected"
        sanitized = raw.replace("/", "").replace("\\", "").replace("..", "")
        allowed = re.sub(r'\{(?!name\}|cipher\}|mode\})[^}]*\}', '', sanitized)
        self._naming = allowed or "{name}_protected"
        self._save_settings()

    def _build_output_path(self, filepath: str) -> str:
        name, _ext = os.path.splitext(os.path.basename(filepath))
        template = self._naming or "{name}_protected"

        if self._mode == "standard":
            cipher_label = self._std_enc.lower()
            ext = ".pdf"
        else:
            cipher_label = self._cipher.split("-")[0]  # e.g. "chacha20"
            ext = ".epdf"

        try:
            output_name = template.format(
                name=name, cipher=cipher_label, mode=self._mode)
        except (KeyError, IndexError):
            output_name = f"{name}_protected"

        folder = self.out_dir or os.path.dirname(filepath)
        return os.path.join(folder, output_name + ext)

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

        user_pw = self.user_pw.text()
        owner_pw = self.owner_pw.text()
        if not user_pw and not owner_pw:
            QMessageBox.warning(self, "Password needed",
                                "Enter at least one password.")
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

        # Snapshot state
        snapshot = [(r.filepath,) for r in self.rows]
        mode = self._mode
        std_enc = self._std_enc
        cipher = self._cipher
        kdf = self._kdf
        perms = {
            "print": self._perm_print,
            "copy": self._perm_copy,
            "edit": self._perm_edit,
            "annotate": self._perm_annotate,
        }

        threading.Thread(
            target=self._worker,
            args=(snapshot, user_pw, owner_pw, mode, std_enc,
                  cipher, kdf, perms),
            daemon=True,
        ).start()

    def _cancel(self):
        if self._cancel_event:
            self._cancel_event.set()
        self.btn_go.setEnabled(False)
        self.btn_go.setText("Cancelling\u2026")

    def _worker(self, snapshot, user_pw, owner_pw, mode, std_enc,
                cipher, kdf, perms):
        t0 = time.time()
        cancel = self._cancel_event

        for i, (filepath,) in enumerate(snapshot):
            if cancel and cancel.is_set():
                break

            self.signals.progress.emit(i, 0, 0, "Starting...")
            output = self._build_output_path(filepath)

            try:
                if mode == "standard":
                    protect_pdf(filepath, output,
                                user_pw or "", owner_pw or "",
                                perms, std_enc)
                    detail = std_enc
                else:
                    epdf_encrypt(filepath, output, user_pw,
                                 cipher=cipher, kdf=kdf)
                    detail = f"{cipher} / {kdf}"

                result = {
                    "file": os.path.basename(filepath),
                    "status": "OK",
                    "details": detail,
                    "output": output,
                }
                self.signals.file_done.emit(i, result)
            except Exception as e:
                log.error("Protection failed for %s: %s", filepath, e)
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
            self.rows[fi].set_working("Encrypting\u2026")
        self.progress.setValue(fi)

    def _on_file_done(self, fi, result):
        if fi >= len(self.rows):
            return
        row = self.rows[fi]
        if isinstance(result, Exception):
            row.set_error(str(result))
        else:
            out_name = os.path.basename(result.get("output", ""))
            row.set_done(f"Protected \u2192 {out_name}")
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
        self.btn_go.setText("Protect")
        self.btn_add.setEnabled(True)
        self.btn_clear.setEnabled(True)

        t = self.shell.theme
        n_ok = sum(1 for r in self._results if r.get("status") != "Error")
        n_err = sum(1 for r in self._results if r.get("status") == "Error")

        parts = []
        if was_cancelled:
            parts.append("Cancelled")
        if n_ok:
            parts.append(f"{n_ok} protected")
        if n_err:
            parts.append(f"{n_err} failed")
        parts.append(f"{elapsed:.1f}s")

        color = t.amber if was_cancelled else (t.green if n_ok else t.red)
        self.result_lbl.setStyleSheet(f"color: {color};")
        self.result_lbl.setText("  \u00b7  ".join(parts))
        self.summary_lbl.setText("  \u00b7  ".join(parts))

        if n_ok:
            self.btn_open.setVisible(True)

        # Show summary dialog for 2+ files
        if len(self._results) >= 2 and not was_cancelled:
            columns = [
                ("file", "File"),
                ("status", "Status"),
                ("details", "Encryption"),
            ]
            QTimer.singleShot(300, lambda: GenericSummaryDialog(
                "Protection Complete", self._results, columns,
                elapsed, self.shell.theme, self.window()
            ).exec())

        # System tray notification
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
        self.mode_title.setStyleSheet(f"color: {t.text2};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self._pw_section_lbl.setStyleSheet(f"color: {t.text2};")
        self._perm_section_lbl.setStyleSheet(f"color: {t.text2};")
        self._enh_note.setStyleSheet(f"color: {t.text3};")
        self.drop_zone.apply_theme(t)
        self.list_widget.setStyleSheet(f"background: {t.surface};")
        for row in self.rows:
            row.apply_theme(t)
