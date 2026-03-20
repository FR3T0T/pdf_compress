# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for PDF Toolkit.

Build with:
    pyinstaller pdf_toolkit.spec

Or use build.bat on Windows.

Output: dist/PDFToolkit/PDFToolkit.exe  (one-dir mode)
"""

import os
import sys

block_cipher = None

# ── Paths ────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.abspath(SPECPATH)
WEB_DIR = os.path.join(PROJECT_ROOT, "web")

# ── Data files (frontend assets) ─────────────────────────────────────
datas = [
    (WEB_DIR, "web"),  # HTML/CSS/JS frontend
]

# ── Hidden imports ───────────────────────────────────────────────────
# Modules that PyInstaller can't detect from static analysis.
hiddenimports = [
    # Qt / PySide6
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    # PDF
    "pikepdf",
    "pikepdf._core",
    # Images
    "PIL",
    "PIL.Image",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
    "PIL.WebPImagePlugin",
    "PIL.TiffImagePlugin",
    "PIL.BmpImagePlugin",
    "PIL.GifImagePlugin",
    # PDF rendering
    "fitz",  # PyMuPDF
    # Crypto
    "argon2",
    "argon2.low_level",
    "Crypto",
    "Crypto.Cipher",
    "Crypto.Cipher.AES",
    "cryptography",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.ciphers",
    "cryptography.hazmat.primitives.padding",
    # Optional
    "numpy",
    "docx",
    # App modules
    "engine",
    "pdf_ops",
    "epdf_crypto",
    "ui",
    "ui.web_shell",
    "ui.bridge",
    "ui.theme",
    "ui.dialogs",
    "ui.tool_registry",
]

# ── Excludes ─────────────────────────────────────────────────────────
excludes = [
    "tkinter",
    "unittest",
    "test",
    "tests",
    "pytest",
]

# ── Analysis ─────────────────────────────────────────────────────────
a = Analysis(
    ["app.py"],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── PYZ (Python bytecode archive) ────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ──────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # one-dir mode
    name="PDFToolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # GUI app, no console window
    disable_windowed_traceback=False,
)

# ── COLLECT (one-dir bundle) ─────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PDFToolkit",
)
