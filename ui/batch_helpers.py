"""Batch operation helpers — shared across upgraded pages.

Extracts common patterns (recent files, shortcuts, tray notifications)
to eliminate duplication between compress, protect, unlock, etc.
"""

import os

from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import QMenu

from .theme import FONT


# ═══════════════════════════════════════════════════════════════════
#  Recent files
# ═══════════════════════════════════════════════════════════════════

def load_recent_files(settings, prefix: str) -> list[str]:
    """Load recent file paths from QSettings.

    Args:
        settings: QSettings instance
        prefix:   Settings key prefix, e.g. "protect" → "protect/recent_files"
    """
    raw = settings.value(f"{prefix}/recent_files", [])
    if isinstance(raw, str):
        raw = [raw] if raw else []
    return [f for f in raw if isinstance(f, str)]


def save_recent_files(settings, prefix: str, new_paths: list[str],
                      max_entries: int = 20):
    """Save new paths to the recent files list in QSettings."""
    recent = load_recent_files(settings, prefix)
    for p in new_paths:
        if p in recent:
            recent.remove(p)
        recent.insert(0, p)
    recent = recent[:max_entries]
    settings.setValue(f"{prefix}/recent_files", recent)


def show_recent_menu(parent, button, settings, prefix: str, callback):
    """Show a popup menu with recent files.

    Args:
        parent:   Parent widget
        button:   Button to anchor the menu to
        settings: QSettings instance
        prefix:   Settings key prefix
        callback: Function to call with the selected path
    """
    recent = load_recent_files(settings, prefix)
    if not recent:
        return
    menu = QMenu(parent)
    for path in recent:
        name = os.path.basename(path)
        action = menu.addAction(name)
        action.setToolTip(path)
        action.triggered.connect(lambda checked, p=path: callback(p))
    menu.exec(button.mapToGlobal(button.rect().bottomLeft()))


# ═══════════════════════════════════════════════════════════════════
#  Keyboard shortcuts
# ═══════════════════════════════════════════════════════════════════

def setup_standard_shortcuts(page, browse_fn, run_fn, clear_fn):
    """Set up standard keyboard shortcuts on a page.

    Ctrl+O:      Browse / add files
    Ctrl+Return: Run the operation
    Escape:      Clear files
    """
    QShortcut(QKeySequence("Ctrl+O"), page, browse_fn)
    QShortcut(QKeySequence("Ctrl+Return"), page, run_fn)
    QShortcut(QKeySequence("Escape"), page, clear_fn)


# ═══════════════════════════════════════════════════════════════════
#  System tray notification
# ═══════════════════════════════════════════════════════════════════

def notify_tray_if_minimized(shell, message: str):
    """Send a system tray notification if the window is minimized."""
    if shell.isMinimized() and shell.tray_icon.isVisible():
        from PySide6.QtWidgets import QSystemTrayIcon
        shell.tray_icon.showMessage(
            "PDF Toolkit", message,
            QSystemTrayIcon.MessageIcon.Information, 5000
        )
