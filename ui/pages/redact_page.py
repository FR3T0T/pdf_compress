"""Redact PDF — permanently remove sensitive text from PDFs."""

import os
import threading
import logging

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QFileDialog, QProgressBar, QLineEdit, QCheckBox, QTextEdit,
    QSpinBox, QComboBox, QMessageBox,
)

from engine import fmt_size
from pdf_ops import redact_text, RedactResult
from ..theme import Theme, FONT
from ..widgets import DropZone
from .base import BasePage

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)


class RedactPage(BasePage):
    page_title = "Redact PDF"
    page_icon = "redact"
    page_key = "redact"

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.running = False
        self._input_path = None

        self.signals = _Signals()
        self.signals.done.connect(self._on_done)
        self.signals.error.connect(self._on_error)
        self.signals.progress.connect(self._on_progress)

        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(0)

        # Drop zone
        self.drop_zone = DropZone(self.shell.theme)
        self.drop_zone.title_lbl.setText("Select a PDF to redact")
        self.drop_zone.hint_lbl.setText("permanently remove sensitive text content")
        self.drop_zone.clicked.connect(self._browse)
        root.addWidget(self.drop_zone)

        # File info
        self.file_frame = QFrame()
        fl = QHBoxLayout(self.file_frame)
        fl.setContentsMargins(0, 8, 0, 8)
        self.file_lbl = QLabel("")
        self.file_lbl.setFont(QFont(FONT, 10, QFont.DemiBold))
        fl.addWidget(self.file_lbl, 1)
        btn_change = QPushButton("Change")
        btn_change.setObjectName("ghost")
        btn_change.setFont(QFont(FONT, 9))
        btn_change.setCursor(Qt.PointingHandCursor)
        btn_change.clicked.connect(self._browse)
        fl.addWidget(btn_change)
        self.file_frame.setVisible(False)
        root.addWidget(self.file_frame)

        # Search terms
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        terms_lbl = QLabel("TEXT TO REDACT")
        terms_lbl.setFont(QFont(FONT, 8, QFont.Bold))
        root.addWidget(terms_lbl)
        self._terms_section_lbl = terms_lbl
        root.addSpacing(4)

        terms_hint = QLabel("Enter each term on a separate line. All occurrences will be permanently removed.")
        terms_hint.setFont(QFont(FONT, 9))
        terms_hint.setWordWrap(True)
        root.addWidget(terms_hint)
        self._terms_hint_lbl = terms_hint
        root.addSpacing(6)

        self.terms_input = QTextEdit()
        self.terms_input.setFont(QFont(FONT, 10))
        self.terms_input.setPlaceholderText(
            "e.g.\nJohn Doe\n555-123-4567\nSSN: 123-45-6789"
        )
        self.terms_input.setFixedHeight(120)
        root.addWidget(self.terms_input)

        # Options
        root.addSpacing(8)
        root.addWidget(self._sep())
        root.addSpacing(8)

        opts_lbl = QLabel("OPTIONS")
        opts_lbl.setFont(QFont(FONT, 8, QFont.Bold))
        root.addWidget(opts_lbl)
        self._opts_section_lbl = opts_lbl
        root.addSpacing(4)

        self.chk_case = QCheckBox("Case-sensitive matching")
        root.addWidget(self.chk_case)

        # Warning
        root.addSpacing(8)
        self.warn_frame = QFrame()
        warn_layout = QHBoxLayout(self.warn_frame)
        warn_layout.setContentsMargins(12, 10, 12, 10)
        self.warn_lbl = QLabel(
            "Redaction is permanent and irreversible. "
            "The redacted text is destroyed — it cannot be recovered. "
            "Always work on a copy of your file."
        )
        self.warn_lbl.setFont(QFont(FONT, 9))
        self.warn_lbl.setWordWrap(True)
        warn_layout.addWidget(self.warn_lbl)
        root.addWidget(self.warn_frame)

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

        # Progress + result
        root.addSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        self.result_lbl = QLabel("")
        self.result_lbl.setFont(QFont(FONT, 9))
        root.addWidget(self.result_lbl)

        # Action bar
        root.addStretch()
        root.addWidget(self._sep())
        root.addSpacing(12)

        bar = QHBoxLayout()
        bar.addStretch()
        self.btn_redact = QPushButton("Redact")
        self.btn_redact.setObjectName("primary")
        self.btn_redact.setCursor(Qt.PointingHandCursor)
        self.btn_redact.clicked.connect(self._run)
        bar.addWidget(self.btn_redact)
        root.addLayout(bar)

    def _sep(self):
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        return sep

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select PDF", "", "PDF files (*.pdf)")
        if path:
            self._load_file(path)

    def _load_file(self, path):
        self._input_path = path
        name = os.path.basename(path)
        self.file_lbl.setText(name if len(name) < 45 else name[:42] + "\u2026")
        self.file_lbl.setToolTip(path)
        self.drop_zone.setVisible(False)
        self.file_frame.setVisible(True)
        self.result_lbl.setText("")
        base, ext = os.path.splitext(path)
        self._output_path = base + "_redacted" + ext
        self.out_lbl.setText(os.path.basename(self._output_path))

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save redacted PDF", self._output_path or "", "PDF files (*.pdf)")
        if path:
            self._output_path = path
            self.out_lbl.setText(os.path.basename(path))

    def _run(self):
        if self.running or not self._input_path:
            return

        raw_text = self.terms_input.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "No terms", "Enter at least one text term to redact.")
            return

        terms = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not terms:
            QMessageBox.warning(self, "No terms", "Enter at least one text term to redact.")
            return

        # Confirm destructive action
        msg = QMessageBox(self)
        msg.setWindowTitle("Confirm Redaction")
        msg.setText(
            f"This will permanently remove {len(terms)} term(s) from the PDF.\n\n"
            "This action is irreversible. The original text will be destroyed.\n\n"
            "Continue?"
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        if msg.exec() != QMessageBox.Yes:
            return

        self.running = True
        self.progress.setVisible(True)
        self.progress.setMaximum(0)
        self.btn_redact.setEnabled(False)
        self.result_lbl.setText("")

        input_path = self._input_path
        output_path = self._output_path
        case_sensitive = self.chk_case.isChecked()
        signals = self.signals

        def _worker():
            try:
                def _progress(cur, total):
                    signals.progress.emit(cur, total)

                result = redact_text(
                    input_path, output_path,
                    search_terms=terms,
                    case_sensitive=case_sensitive,
                    on_progress=_progress,
                )
                signals.done.emit(result)
            except Exception as e:
                signals.error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_progress(self, cur, total):
        if total > 0:
            self.progress.setMaximum(total)
            self.progress.setValue(cur)

    def _on_done(self, result):
        self.running = False
        self.progress.setVisible(False)
        self.btn_redact.setEnabled(True)
        t = self.shell.theme

        if result.redaction_count == 0:
            self.result_lbl.setText("No matches found — no changes made")
            self.result_lbl.setStyleSheet(f"color: {t.amber};")
        else:
            self.result_lbl.setText(
                f"Redacted {result.redaction_count} occurrence(s) "
                f"across {result.pages_affected} page(s) "
                f"\u2192 {os.path.basename(result.output_path)}"
            )
            self.result_lbl.setStyleSheet(f"color: {t.green};")

    def _on_error(self, msg):
        self.running = False
        self.progress.setVisible(False)
        self.btn_redact.setEnabled(True)
        t = self.shell.theme
        self.result_lbl.setText(f"Error: {msg[:80]}")
        self.result_lbl.setStyleSheet(f"color: {t.red};")

    def is_busy(self):
        return self.running

    def handle_drop(self, paths):
        if paths:
            self._load_file(paths[0])

    def apply_theme(self, theme):
        t = theme
        self.file_lbl.setStyleSheet(f"color: {t.text};")
        self.out_title.setStyleSheet(f"color: {t.text2};")
        self.out_lbl.setStyleSheet(f"color: {t.text3};")
        self.result_lbl.setStyleSheet(f"color: {t.green};")
        self._terms_section_lbl.setStyleSheet(f"color: {t.text2};")
        self._terms_hint_lbl.setStyleSheet(f"color: {t.text3};")
        self._opts_section_lbl.setStyleSheet(f"color: {t.text2};")
        self.drop_zone.apply_theme(t)
        # Warning frame styling
        self.warn_frame.setStyleSheet(
            f"QFrame {{ background: {t.amber}22; border: 1px solid {t.amber}44; "
            f"border-radius: 8px; }}"
        )
        self.warn_lbl.setStyleSheet(f"color: {t.amber}; border: none; background: transparent;")
