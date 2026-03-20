"""Dialog windows — About, Password, Overwrite, SpaceAudit, Summary."""

import os
import sys
import subprocess

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QWidget, QMessageBox,
)

from engine import PDFAnalysis, Result, fmt_size
from .theme import Theme, FONT

VERSION = "4.0.0"


# ═══════════════════════════════════════════════════════════════════
#  About dialog
# ═══════════════════════════════════════════════════════════════════

class AboutDialog(QDialog):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About PDF Toolkit")
        self.setFixedSize(400, 360 if sys.platform == "win32" else 300)
        t = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 32, 36, 28)
        layout.setSpacing(0)

        title = QLabel("PDF Toolkit")
        title.setFont(QFont(FONT, 18, QFont.Bold))
        title.setStyleSheet(f"color: {t.text};")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(6)
        ver = QLabel(f"Version {VERSION}")
        ver.setFont(QFont(FONT, 11))
        ver.setStyleSheet(f"color: {t.text3};")
        ver.setAlignment(Qt.AlignCenter)
        layout.addWidget(ver)

        layout.addSpacing(24)
        desc = QLabel(
            "Your complete PDF toolkit — fully offline.\n"
            "Compress, merge, split, redact, protect,\n"
            "and manage pages with 20 professional tools.\n"
            "DPI-aware image recompression, AES-256 encryption,\n"
            "enhanced .epdf encryption (ChaCha20, Camellia,\n"
            "Argon2), and PDF/A compliance.\n\n"
            "No files leave your machine.\n"
            "No account required. No tracking."
        )
        desc.setFont(QFont(FONT, 10))
        desc.setStyleSheet(f"color: {t.text2};")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Windows context menu registration
        if sys.platform == "win32":
            layout.addSpacing(16)
            self.ctx_btn = QPushButton("Register Windows context menu")
            self.ctx_btn.setFont(QFont(FONT, 10))
            self.ctx_btn.setCursor(Qt.PointingHandCursor)
            self.ctx_btn.clicked.connect(self._register_context_menu)
            self.ctx_btn.setStyleSheet(
                f"QPushButton {{ background: {t.surface2}; color: {t.text}; "
                f"border: 1.5px solid {t.border}; border-radius: 8px; padding: 10px 20px; }}"
                f"QPushButton:hover {{ background: {t.border}; }}"
            )
            layout.addWidget(self.ctx_btn)

        layout.addStretch()

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {t.border};")
        layout.addWidget(sep)

        layout.addSpacing(14)
        footer = QLabel("MIT License  \u00b7  Frederik \u00a9 2026")
        footer.setFont(QFont(FONT, 9))
        footer.setStyleSheet(f"color: {t.text3};")
        footer.setAlignment(Qt.AlignCenter)
        layout.addWidget(footer)

    def _register_context_menu(self):
        """Register a Windows Explorer context menu entry for .pdf files."""
        script_path = os.path.abspath(sys.argv[0])
        python_path = sys.executable

        reg_key = (
            r"HKCU\Software\Classes\SystemFileAssociations\.pdf\shell"
            r"\CompressWithPDFCompress"
        )
        command_value = f'"{python_path}" "{script_path}" "%1"'

        try:
            subprocess.run(
                ["reg", "add", reg_key, "/ve",
                 "/d", "Compress with PDF Toolkit", "/f"],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["reg", "add", f"{reg_key}\\command", "/ve",
                 "/d", command_value, "/f"],
                check=True, capture_output=True, text=True,
            )
            QMessageBox.information(
                self, "Success",
                "Context menu registered successfully.\n"
                "Right-click any .pdf file to see\n"
                "\"Compress with PDF Toolkit\"."
            )
        except subprocess.CalledProcessError as e:
            QMessageBox.warning(
                self, "Failed",
                f"Could not register context menu.\n{e.stderr or str(e)}"
            )


# ═══════════════════════════════════════════════════════════════════
#  Password dialog
# ═══════════════════════════════════════════════════════════════════

class PasswordDialog(QDialog):
    """Prompt for a PDF password."""

    def __init__(self, filename: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Password required")
        self.setFixedSize(420, 200)
        t = theme
        self.password = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(14)

        title = QLabel("Password required")
        title.setFont(QFont(FONT, 14, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        short_name = filename if len(filename) < 50 else filename[:47] + "\u2026"
        desc = QLabel(f"{short_name} is password-protected.\nEnter the password to continue.")
        desc.setFont(QFont(FONT, 10))
        desc.setStyleSheet(f"color: {t.text2};")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.pw_input = QLineEdit()
        self.pw_input.setEchoMode(QLineEdit.Password)
        self.pw_input.setPlaceholderText("Password")
        self.pw_input.setFont(QFont(FONT, 11))
        self.pw_input.setFixedHeight(40)
        self.pw_input.setStyleSheet(
            f"QLineEdit {{ background: {t.surface}; color: {t.text}; "
            f"border: 1.5px solid {t.border}; border-radius: 8px; padding: 8px 12px; }}"
            f"QLineEdit:focus {{ border-color: {t.accent}; }}"
        )
        self.pw_input.returnPressed.connect(self._accept)
        layout.addWidget(self.pw_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        skip_btn = QPushButton("Skip")
        skip_btn.setObjectName("ghost")
        skip_btn.setCursor(Qt.PointingHandCursor)
        skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(skip_btn)

        ok_btn = QPushButton("Unlock")
        ok_btn.setObjectName("primary")
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.clicked.connect(self._accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

        QTimer.singleShot(100, self.pw_input.setFocus)

    def _accept(self):
        text = self.pw_input.text().strip()
        if text:
            self.password = text
            self.accept()


# ═══════════════════════════════════════════════════════════════════
#  Overwrite confirmation dialog
# ═══════════════════════════════════════════════════════════════════

class OverwriteDialog(QDialog):
    """Warn when output files already exist."""

    # Result codes
    OVERWRITE = 1
    SKIP = 2
    CANCEL = 0

    def __init__(self, filenames: list[str], theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Files already exist")
        self.setFixedWidth(460)
        t = theme
        self.result_action = self.CANCEL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(14)

        count = len(filenames)
        title = QLabel(
            f"{count} output file{'s' if count != 1 else ''} already "
            f"exist{'s' if count == 1 else ''}"
        )
        title.setFont(QFont(FONT, 14, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        names_text = "\n".join(
            os.path.basename(f) for f in filenames[:8]
        )
        if count > 8:
            names_text += f"\n\u2026 and {count - 8} more"
        names = QLabel(names_text)
        names.setFont(QFont(FONT, 10))
        names.setStyleSheet(f"color: {t.text2};")
        names.setWordWrap(True)
        layout.addWidget(names)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        overwrite_btn = QPushButton("Overwrite")
        overwrite_btn.setObjectName("primary")
        overwrite_btn.setCursor(Qt.PointingHandCursor)
        overwrite_btn.clicked.connect(self._overwrite)
        btn_row.addWidget(overwrite_btn)

        layout.addLayout(btn_row)

    def _overwrite(self):
        self.result_action = self.OVERWRITE
        self.accept()


# ═══════════════════════════════════════════════════════════════════
#  Space audit dialog
# ═══════════════════════════════════════════════════════════════════

class SpaceAuditDialog(QDialog):
    """Shows a breakdown of space usage inside a PDF."""

    def __init__(self, filepath: str, analysis: PDFAnalysis, theme: Theme, parent=None):
        super().__init__(parent)
        name = os.path.basename(filepath)
        self.setWindowTitle(f"Space Audit \u2014 {name}")
        self.setFixedSize(480, 320)
        t = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel(name if len(name) < 55 else name[:52] + "...")
        title.setFont(QFont(FONT, 14, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        total_lbl = QLabel(f"Total size: {fmt_size(analysis.file_size)}")
        total_lbl.setFont(QFont(FONT, 10))
        total_lbl.setStyleSheet(f"color: {t.text2};")
        layout.addWidget(total_lbl)

        # Build category data
        img_bytes = analysis.image_bytes
        fnt_bytes = analysis.font_bytes
        other_bytes = max(0, analysis.file_size - img_bytes - fnt_bytes)
        total = analysis.file_size or 1

        categories = [
            ("Images", img_bytes),
            ("Fonts (est.)", fnt_bytes),
            ("Other (structure, text, etc.)", other_bytes),
        ]

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Category", "Size", "Percentage", ""])
        table.setRowCount(len(categories))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setFixedHeight(130)

        bar_colors = [t.accent, t.green, t.text3]

        for i, (cat, size) in enumerate(categories):
            table.setItem(i, 0, QTableWidgetItem(cat))
            table.setItem(i, 1, QTableWidgetItem(fmt_size(size)))
            pct = (size / total * 100) if total > 0 else 0
            table.setItem(i, 2, QTableWidgetItem(f"{pct:.1f}%"))

            # Bar chart visualization
            bar_widget = QWidget()
            bar_layout = QHBoxLayout(bar_widget)
            bar_layout.setContentsMargins(4, 6, 4, 6)
            bar_frame = QFrame()
            bar_ratio = size / total if total > 0 else 0
            bar_width = max(2, int(bar_ratio * 120))
            bar_frame.setFixedSize(bar_width, 10)
            bar_frame.setStyleSheet(
                f"background: {bar_colors[i]}; border-radius: 4px;"
            )
            bar_layout.addWidget(bar_frame)
            bar_layout.addStretch()
            table.setCellWidget(i, 3, bar_widget)

            for col in [1, 2]:
                item = table.item(i, col)
                if item:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(table, 1)

        close = QPushButton("Close")
        close.setObjectName("primary")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedWidth(110)
        close.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close)
        layout.addLayout(btn_row)


# ═══════════════════════════════════════════════════════════════════
#  Batch summary dialog
# ═══════════════════════════════════════════════════════════════════

class SummaryDialog(QDialog):
    def __init__(self, results: list, elapsed: float, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Summary")
        self.setMinimumSize(560, 380)
        t = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        n_ok = sum(1 for r in results if isinstance(r, Result) and not r.skipped)
        n_skip = sum(1 for r in results if isinstance(r, Result) and r.skipped)
        total_orig = sum(r.original_size for r in results if isinstance(r, Result))
        total_comp = sum(r.compressed_size for r in results if isinstance(r, Result))
        total_saved = total_orig - total_comp

        header_parts = []
        if n_ok:
            header_parts.append(f"{n_ok} compressed")
        if n_skip:
            header_parts.append(f"{n_skip} already optimized")

        title = QLabel("  \u00b7  ".join(header_parts) if header_parts else "Done")
        title.setFont(QFont(FONT, 14, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        stats_parts = []
        if total_saved > 0 and total_orig > 0:
            pct = total_saved / total_orig * 100
            stats_parts.append(f"Saved {fmt_size(total_saved)} ({pct:.0f}%)")
        stats_parts.append(f"{elapsed:.1f} seconds")
        stats = QLabel("  \u00b7  ".join(stats_parts))
        stats.setFont(QFont(FONT, 11))
        stats.setStyleSheet(f"color: {t.text2};")
        layout.addWidget(stats)

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["File", "Original", "Compressed", "Saving"])
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSortIndicatorShown(True)
        alt_color = t.surface2 if t.name == "dark" else "#f8f9fb"
        table.setStyleSheet(
            table.styleSheet() +
            f"QTableWidget {{ alternate-background-color: {alt_color}; }}"
        )

        valid = [r for r in results if isinstance(r, Result)]
        table.setRowCount(len(valid))

        for i, r in enumerate(valid):
            name = os.path.basename(r.input_path)
            table.setItem(i, 0, QTableWidgetItem(name))

            item1 = QTableWidgetItem(fmt_size(r.original_size))
            item1.setData(Qt.UserRole, r.original_size)
            table.setItem(i, 1, item1)

            if r.skipped:
                item2 = QTableWidgetItem("\u2014")
                item2.setData(Qt.UserRole, r.original_size)
                table.setItem(i, 2, item2)
                item3 = QTableWidgetItem("already optimized")
                item3.setData(Qt.UserRole, 0)
                item3.setForeground(QColor(t.amber))
            else:
                item2 = QTableWidgetItem(fmt_size(r.compressed_size))
                item2.setData(Qt.UserRole, r.compressed_size)
                table.setItem(i, 2, item2)
                item3 = QTableWidgetItem(f"\u2212{r.saved_pct:.1f}%")
                item3.setData(Qt.UserRole, r.saved_bytes)
                item3.setForeground(QColor(t.green))
            table.setItem(i, 3, item3)

            for col in [1, 2, 3]:
                item = table.item(i, col)
                if item:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(table, 1)

        close = QPushButton("Close")
        close.setObjectName("primary")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedWidth(110)
        close.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close)
        layout.addLayout(btn_row)


# ═══════════════════════════════════════════════════════════════════
#  Generic batch summary dialog
# ═══════════════════════════════════════════════════════════════════

class GenericSummaryDialog(QDialog):
    """Configurable batch results summary — works for any operation.

    Usage:
        results = [
            {"file": "doc.pdf", "status": "OK", "details": "AES-256-GCM"},
            {"file": "report.pdf", "status": "Error", "details": "wrong password"},
        ]
        columns = [("file", "File"), ("status", "Status"), ("details", "Details")]
        GenericSummaryDialog("Protection Complete", results, columns, 3.2, theme, parent).exec()
    """

    def __init__(self, title_text: str, results: list[dict],
                 columns: list[tuple[str, str]], elapsed: float,
                 theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Summary")
        self.setMinimumSize(540, 360)
        t = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        # Title
        title = QLabel(title_text)
        title.setFont(QFont(FONT, 14, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        # Stats line
        n_ok = sum(1 for r in results if r.get("status") != "Error")
        n_err = sum(1 for r in results if r.get("status") == "Error")
        stats_parts = [f"{n_ok} succeeded"]
        if n_err:
            stats_parts.append(f"{n_err} failed")
        stats_parts.append(f"{elapsed:.1f}s")
        stats = QLabel("  \u00b7  ".join(stats_parts))
        stats.setFont(QFont(FONT, 11))
        stats.setStyleSheet(f"color: {t.text2};")
        layout.addWidget(stats)

        # Table
        table = QTableWidget()
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels([col[1] for col in columns])
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(columns)):
            table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSortIndicatorShown(True)
        alt_color = t.surface2 if t.name == "dark" else "#f8f9fb"
        table.setStyleSheet(
            table.styleSheet() +
            f"QTableWidget {{ alternate-background-color: {alt_color}; }}"
        )

        table.setRowCount(len(results))
        for i, row_data in enumerate(results):
            for j, (key, _header) in enumerate(columns):
                value = str(row_data.get(key, ""))
                item = QTableWidgetItem(value)
                if j > 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                # Color status column
                if key == "status":
                    if value == "Error":
                        item.setForeground(QColor(t.red))
                    else:
                        item.setForeground(QColor(t.green))
                table.setItem(i, j, item)

        layout.addWidget(table, 1)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("primary")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedWidth(110)
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


# ═══════════════════════════════════════════════════════════════════
#  EPDF info dialog
# ═══════════════════════════════════════════════════════════════════

class EPDFInfoDialog(QDialog):
    """Display .epdf file encryption metadata."""

    CIPHER_LABELS = {
        "chacha20-poly1305": "ChaCha20-Poly1305 (256-bit)",
        "aes-256-gcm":       "AES-256-GCM",
        "camellia-256-cbc":  "Camellia-256-CBC + HMAC",
    }
    KDF_LABELS = {
        "argon2id": "Argon2id",
        "argon2d":  "Argon2d",
    }

    def __init__(self, filepath: str, metadata: dict,
                 theme: Theme, parent=None):
        super().__init__(parent)
        name = os.path.basename(filepath)
        self.setWindowTitle(f"Encryption Details \u2014 {name}")
        self.setFixedSize(440, 320)
        t = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(10)

        title = QLabel("Enhanced Encryption Details")
        title.setFont(QFont(FONT, 14, QFont.DemiBold))
        title.setStyleSheet(f"color: {t.text};")
        layout.addWidget(title)

        layout.addSpacing(4)

        # Info rows
        rows = [
            ("Original file", metadata.get("original_filename", "unknown")),
            ("Cipher", self.CIPHER_LABELS.get(
                metadata.get("cipher", ""), metadata.get("cipher", "unknown"))),
            ("Key derivation", self.KDF_LABELS.get(
                metadata.get("kdf", ""), metadata.get("kdf", "unknown"))),
        ]

        kdf_params = metadata.get("kdf_params", {})
        if kdf_params:
            mem_mb = kdf_params.get("memory_cost", 0) / 1024
            rows.append(("KDF parameters",
                         f"t={kdf_params.get('time_cost', '?')}, "
                         f"m={mem_mb:.0f} MB, "
                         f"p={kdf_params.get('parallelism', '?')}"))

        original_size = metadata.get("original_size")
        if original_size:
            rows.append(("Original size", fmt_size(original_size)))

        created = metadata.get("created", "")
        if created:
            # Show just the date portion
            rows.append(("Created", created[:19].replace("T", " ")))

        for label, value in rows:
            row = QHBoxLayout()
            lbl = QLabel(label + ":")
            lbl.setFont(QFont(FONT, 10))
            lbl.setStyleSheet(f"color: {t.text2};")
            lbl.setFixedWidth(120)
            row.addWidget(lbl)

            val = QLabel(str(value))
            val.setFont(QFont(FONT, 10, QFont.DemiBold))
            val.setStyleSheet(f"color: {t.text};")
            val.setWordWrap(True)
            row.addWidget(val, 1)

            layout.addLayout(row)

        layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setObjectName("primary")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedWidth(110)
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
