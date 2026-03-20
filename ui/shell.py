"""Main window shell — sidebar + dashboard + stacked tool pages.

.. deprecated:: 4.0.0
    This widget-based shell has been replaced by ``web_shell.py`` which uses
    QWebEngineView + QWebChannel for the UI.  This file is retained for
    reference only and is not imported by the application.
"""

import os
import logging

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QStackedWidget,
    QSystemTrayIcon,
)

from .theme import Theme, LIGHT, DARK, FONT, build_stylesheet
from .sidebar import SidebarWidget
from .dialogs import AboutDialog
from .pages.base import BasePage
from .pages.home_page import HomePage
from .tool_registry import get_tools, CATEGORIES

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Application shell: sidebar navigation + dashboard + stacked tool pages."""

    def __init__(self, initial_files=None):
        super().__init__()
        self.setWindowTitle("PDF Toolkit")
        self.resize(1080, 760)
        self.setMinimumSize(720, 560)
        self.setAcceptDrops(True)

        self.settings = QSettings("PDFCompress", "PDFCompress")

        saved_theme = self.settings.value("theme", "light")
        self.theme = LIGHT if saved_theme == "light" else DARK

        # System tray icon
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("PDF Toolkit")
        app_icon = QApplication.instance().windowIcon()
        if not app_icon.isNull():
            self.tray_icon.setIcon(app_icon)
        else:
            self.tray_icon.setIcon(self.style().standardIcon(
                self.style().StandardPixmap.SP_FileDialogDetailedView))
        self.tray_icon.setVisible(True)

        self._pages: list[BasePage] = []
        self._tool_key_to_index: dict[str, int] = {}
        self._build()
        self._apply_theme()
        self._setup_shortcuts()

        # Load initial files into compress page
        if initial_files:
            QTimer.singleShot(100, lambda: self._navigate_to_tool("compress"))
            QTimer.singleShot(200, lambda: self._tool_key_to_index.get("compress") is not None and
                              self._pages[self._tool_key_to_index["compress"]].handle_drop(initial_files))

    # ── Build ────────────────────────────────────────────────────

    def _build(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        collapsed = self.settings.value("sidebar_collapsed", "true") == "true"
        self.sidebar = SidebarWidget(self.theme, collapsed=collapsed)
        layout.addWidget(self.sidebar)

        # Stacked pages
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        # Home page at index 0
        self._home = HomePage(self)
        self._home.tool_selected.connect(self._navigate_to_tool)
        self.stack.addWidget(self._home)
        self._pages.append(self._home)

        # Register all tool pages from registry
        tools = get_tools()
        for tool_def in tools:
            page = tool_def.page_factory(self)
            idx = self.sidebar.add_tool(tool_def.icon, tool_def.title)
            self.stack.addWidget(page)
            self._pages.append(page)
            self._tool_key_to_index[tool_def.key] = idx

        # Populate dashboard with tool cards
        self._home.populate(tools, CATEGORIES)

        # Sidebar bottom buttons
        theme_icon = "moon" if self.theme.name == "light" else "sun"
        self._theme_btn = self.sidebar.add_bottom_button(theme_icon, "Theme")
        self._theme_btn.clicked.connect(self._toggle_theme)

        self._about_btn = self.sidebar.add_bottom_button("metadata", "About")
        self._about_btn.clicked.connect(self._show_about)

        # Sidebar navigation
        self.sidebar.tool_changed.connect(self._switch_page)

    # ── Navigation ────────────────────────────────────────────────

    def _navigate_to_tool(self, key: str):
        """Navigate to a tool page by its registry key."""
        idx = self._tool_key_to_index.get(key)
        if idx is not None:
            self._switch_page(idx)
            self.sidebar.set_active(idx)

    def navigate_home(self):
        """Go back to the dashboard."""
        self._switch_page(0)
        self.sidebar.set_active(0)

    def _switch_page(self, index: int):
        current_idx = self.stack.currentIndex()
        if index == current_idx:
            return
        current = self._pages[current_idx]
        if current.is_busy():
            self.sidebar.set_active(current_idx)
            return
        current.on_deactivated()
        self.stack.setCurrentIndex(index)
        self._pages[index].on_activated()

    # ── Theme ─────────────────────────────────────────────────────

    def _toggle_theme(self):
        self.theme = DARK if self.theme.name == "light" else LIGHT
        # Update theme button icon (moon for light, sun for dark)
        new_icon = "moon" if self.theme.name == "light" else "sun"
        self._theme_btn._icon_name = new_icon
        self._apply_theme()
        self.settings.setValue("theme", self.theme.name)

    def _apply_theme(self):
        t = self.theme
        QApplication.instance().setStyleSheet(build_stylesheet(t))
        self.sidebar.apply_theme(t)
        for page in self._pages:
            page.apply_theme(t)

    # ── About dialog ──────────────────────────────────────────────

    def _show_about(self):
        dlg = AboutDialog(self.theme, self)
        dlg.exec()

    # ── Keyboard shortcuts ────────────────────────────────────────

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+T"), self, self._toggle_theme)
        QShortcut(QKeySequence("Ctrl+,"), self, self._show_about)
        QShortcut(QKeySequence("Ctrl+Home"), self, self.navigate_home)

    # ── Drag and drop → route to active page ──────────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        page = self._pages[self.stack.currentIndex()]
        if page.is_busy() or not page.accepts_drops():
            return
        exts = getattr(page, 'accepted_extensions', ['.pdf'])
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if os.path.isfile(local):
                if any(local.lower().endswith(ext) for ext in exts):
                    paths.append(local)
            elif os.path.isdir(local):
                for root, _dirs, files in os.walk(local):
                    for f in sorted(files):
                        if any(f.lower().endswith(ext) for ext in exts):
                            paths.append(os.path.join(root, f))
        if paths:
            page.handle_drop(paths)

    # ── Close ─────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.settings.setValue("theme", self.theme.name)
        self.settings.setValue("sidebar_collapsed",
                               "true" if self.sidebar.collapsed else "false")
        super().closeEvent(event)
