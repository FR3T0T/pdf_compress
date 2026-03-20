"""Base class for all tool pages."""

from PySide6.QtWidgets import QWidget

from ..theme import Theme


class BasePage(QWidget):
    """Abstract base for tool pages in the stacked widget."""

    # Subclasses should set these
    page_title = "Untitled"
    page_icon = "?"
    page_key = ""
    accepted_extensions: list[str] = [".pdf"]

    def apply_theme(self, theme: Theme):
        """Re-apply theme colors. Must be implemented by subclasses."""
        pass

    def on_activated(self):
        """Called when this page becomes the active/visible page."""
        pass

    def on_deactivated(self):
        """Called when the user navigates away from this page."""
        pass

    def is_busy(self) -> bool:
        """Return True if the page is running an operation (prevent switching)."""
        return False

    def accepts_drops(self) -> bool:
        """Return True if this page accepts drag-and-drop files."""
        return True

    def handle_drop(self, paths: list[str]):
        """Handle dropped file paths. Override in subclass."""
        pass
