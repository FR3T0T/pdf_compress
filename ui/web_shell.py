"""Web-based main window -- QWebEngineView + QWebChannel.

Replaces the old Qt widget-based shell.py with a single QWebEngineView
that hosts the HTML/CSS/JS frontend.  Communication between Python and
JavaScript happens through the ``Bridge`` object registered on a
``QWebChannel``.
"""

import json
import logging
import os

from PySide6.QtCore import QSettings, QTimer, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon

from .bridge import Bridge
from .net_guard import install_offline_guard
from .theme import DARK, LIGHT, Theme

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Paths
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)

# The frontend is a React + Vite + TypeScript app (web-react/). Its built
# bundle is committed to web-react/dist/, so end users need no Node toolchain;
# developers who change web-react/src/ must rebuild (`npm run build`).
_WEB_REACT_DIR = os.path.join(_PROJECT_ROOT, "web-react", "dist")
_WEB_REACT_INDEX_HTML = os.path.join(_WEB_REACT_DIR, "index.html")


def _resolve_index_html() -> str:
    """Return the path to the built React frontend (web-react/dist/index.html).

    The bundle is committed, so this normally always exists. If it's missing
    the checkout is incomplete or the build hasn't been run -- raise a clear
    error rather than launching an empty window.
    """
    if os.path.isfile(_WEB_REACT_INDEX_HTML):
        return _WEB_REACT_INDEX_HTML
    raise FileNotFoundError(
        f"Frontend build not found at {_WEB_REACT_INDEX_HTML}. "
        "Run `npm run build` in web-react/ to generate it."
    )


def _find_qwebchannel_js() -> str:
    """Locate the ``qwebchannel.js`` shipped with PySide6.

    The file lives inside the PySide6 package directory.  We try several
    known relative paths so this works across platforms and PySide6
    versions.
    """
    import PySide6
    pyside_dir = os.path.dirname(PySide6.__file__)

    candidates = [
        os.path.join(pyside_dir, "resources", "qwebchannel.js"),
        os.path.join(pyside_dir, "Qt", "lib", "QtWebEngine.framework",
                     "Resources", "qtwebchannel", "qwebchannel.js"),
        os.path.join(pyside_dir, "Qt", "resources", "qwebchannel.js"),
        os.path.join(pyside_dir, "qwebchannel.js"),
        # Fallback: search recursively (slow, last resort)
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path

    # Recursive fallback
    for root, _dirs, files in os.walk(pyside_dir):
        if "qwebchannel.js" in files:
            return os.path.join(root, "qwebchannel.js")

    raise FileNotFoundError(
        "Could not locate qwebchannel.js in the PySide6 installation.  "
        "Ensure PySide6-WebEngine is installed."
    )


# ---------------------------------------------------------------------------
#  Theme -> CSS custom-property dict
# ---------------------------------------------------------------------------

def _theme_to_css_vars(theme: Theme) -> dict[str, str]:
    """Convert a ``Theme`` instance into a dict of CSS custom properties.

    The keys match those defined in ``web/css/variables.css`` so the JS
    side can simply iterate and call
    ``document.documentElement.style.setProperty(key, value)``.
    """
    return {
        "--theme-name":           theme.name,
        "--color-bg":             theme.bg,
        "--color-surface":        theme.surface,
        "--color-surface-2":      theme.surface2,
        "--color-surface-3":      theme.surface3,
        "--color-border":         theme.border,
        "--color-border-2":       theme.border2,
        "--color-accent":         theme.accent,
        "--color-accent-hover":   theme.accent_h,
        "--color-accent-muted":   theme.accent_m,
        "--color-accent-soft":    theme.accent_soft,
        "--color-text":           theme.text,
        "--color-text-2":         theme.text2,
        "--color-text-3":         theme.text3,
        "--color-green":          theme.green,
        "--color-red":            theme.red,
        "--color-amber":          theme.amber,
        "--color-bar-bg":         theme.bar_bg,
        "--color-bar-fg":         theme.bar_fg,
        "--color-card-bg":        theme.card_bg,
        "--color-card-border":    theme.card_border,
        "--color-card-selected":  theme.card_sel,
        "--color-sidebar-bg":     theme.sidebar_bg,
        "--color-sidebar-active": theme.sidebar_active_bg,
        "--color-shadow":         theme.shadow,
        "--color-glow":           theme.glow,
    }


# ---------------------------------------------------------------------------
#  Custom QWebEngineView -- drag & drop interception
# ---------------------------------------------------------------------------

class CustomWebView(QWebEngineView):
    """QWebEngineView subclass that intercepts drag-and-drop for files.

    Dropped file paths are forwarded to the ``Bridge.filesDropped`` signal
    so the JavaScript frontend can react without needing native HTML5
    drag-and-drop support for local files (which Chromium restricts).
    """

    #: Reference to the Bridge so we can emit ``filesDropped``.
    bridge: Bridge | None = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    # -- Drag enter --------------------------------------------------------

    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    # -- Drop --------------------------------------------------------------

    def dropEvent(self, event):  # noqa: N802
        mime = event.mimeData()
        if not mime.hasUrls():
            super().dropEvent(event)
            return

        paths: list[str] = []
        for url in mime.urls():
            local = url.toLocalFile()
            if not local:
                continue
            if os.path.isfile(local):
                paths.append(os.path.normpath(local))
            elif os.path.isdir(local):
                for root, _dirs, files in os.walk(local):
                    for fname in sorted(files):
                        paths.append(os.path.normpath(
                            os.path.join(root, fname)))

        if paths and self.bridge is not None:
            self.bridge.filesDropped.emit(json.dumps(paths))
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


# ---------------------------------------------------------------------------
#  Main window
# ---------------------------------------------------------------------------

class WebMainWindow(QMainWindow):
    """Application shell backed by a single ``QWebEngineView``.

    All UI rendering is handled by the HTML/CSS/JS frontend.  Python
    provides backend services (PDF operations, settings, file I/O)
    through the ``Bridge`` object exposed over ``QWebChannel``.
    """

    def __init__(self, initial_files: list[str] | None = None):
        super().__init__()
        self.setWindowTitle("PDF Toolkit")
        self.resize(1080, 760)
        self.setMinimumSize(720, 560)

        # -- Persistent settings -------------------------------------------
        self.settings = QSettings("PDFCompress", "PDFCompress")

        saved_theme = self.settings.value("theme", "light")
        self._theme: Theme = LIGHT if saved_theme == "light" else DARK

        self._initial_files: list[str] | None = initial_files

        # -- System tray icon ----------------------------------------------
        self._setup_tray_icon()

        # -- Bridge --------------------------------------------------------
        self._bridge = Bridge()
        self._bridge.themeToggleRequested.connect(self._toggle_theme)

        # Provide the qwebchannel.js path to the bridge so JS can query it
        try:
            self._qwebchannel_js_path = _find_qwebchannel_js()
            log.info("qwebchannel.js located at %s", self._qwebchannel_js_path)
        except FileNotFoundError:
            log.warning("qwebchannel.js not found; WebChannel may not work")
            self._qwebchannel_js_path = ""

        self._bridge.set_qwebchannel_js_path(self._qwebchannel_js_path)

        # -- QWebChannel ---------------------------------------------------
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)

        # -- QWebEngineView ------------------------------------------------
        self._view = CustomWebView(self)
        self._view.bridge = self._bridge

        page = self._view.page()
        page.setWebChannel(self._channel)

        # -- Hard offline enforcement --------------------------------------
        # Block every non-local network request at the engine level so the
        # app provably cannot phone home, even if a future change or
        # dependency tried to. Reference is held so Qt doesn't GC it.
        self._offline_guard = install_offline_guard(page.profile())

        # Enable local file access and other useful settings
        ws = page.settings()
        ws.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        ws.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        ws.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        ws.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        # Defense-in-depth: deny JS access to the clipboard and block any
        # attempt to open external windows.
        try:
            ws.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
            ws.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        except Exception:
            pass

        self.setCentralWidget(self._view)

        # -- Load the frontend HTML ----------------------------------------
        self._index_html_path = _resolve_index_html()
        log.info("Loading frontend from %s", self._index_html_path)
        page.loadFinished.connect(self._on_page_loaded)
        self._view.setUrl(QUrl.fromLocalFile(self._index_html_path))

        # -- Keyboard shortcuts --------------------------------------------
        self._setup_shortcuts()

    # -- Tray icon ---------------------------------------------------------

    def _setup_tray_icon(self):
        self._tray_icon = QSystemTrayIcon(self)
        self._tray_icon.setToolTip("PDF Toolkit")
        app_icon = QApplication.instance().windowIcon()
        if not app_icon.isNull():
            self._tray_icon.setIcon(app_icon)
        else:
            self._tray_icon.setIcon(
                self.style().standardIcon(
                    self.style().StandardPixmap.SP_FileDialogDetailedView
                )
            )
        self._tray_icon.setVisible(True)

    # -- Page lifecycle ----------------------------------------------------

    def _on_page_loaded(self, ok: bool):
        """Called when the web page finishes loading."""
        if not ok:
            log.error("Failed to load frontend HTML from %s", self._index_html_path)
            return

        log.info("Frontend loaded successfully")

        # Push the initial theme to JavaScript
        css_vars = _theme_to_css_vars(self._theme)
        self._bridge.themeChanged.emit(json.dumps(css_vars))

        # Forward initial files (if any) after a short delay to let JS boot
        if self._initial_files:
            QTimer.singleShot(
                150,
                lambda: self._bridge.filesDropped.emit(
                    json.dumps(self._initial_files)
                ),
            )

    # -- Theme toggling ----------------------------------------------------

    def _toggle_theme(self):
        """Toggle between light and dark themes."""
        self._theme = DARK if self._theme.name == "light" else LIGHT
        css_vars = _theme_to_css_vars(self._theme)
        self._bridge.themeChanged.emit(json.dumps(css_vars))
        self.settings.setValue("theme", self._theme.name)
        log.debug("Theme switched to %s", self._theme.name)

    # -- Navigation helpers ------------------------------------------------

    def _navigate_home(self):
        """Tell the JS router to navigate to the home page.

        Vanilla exposes a global ``Router``; the React frontend uses plain
        hash routing with no global to call, so fall back to setting the
        hash directly -- its RouterProvider listens for hashchange either way.
        """
        self._view.page().runJavaScript(
            "if (typeof Router !== 'undefined') { Router.navigate('home'); }"
            "else { window.location.hash = '#/home'; }"
        )

    # -- Keyboard shortcuts ------------------------------------------------

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+T"), self, self._toggle_theme)
        QShortcut(QKeySequence("Ctrl+Home"), self, self._navigate_home)

    # -- Window close ------------------------------------------------------

    def closeEvent(self, event):  # noqa: N802
        self.settings.setValue("theme", self._theme.name)
        super().closeEvent(event)
