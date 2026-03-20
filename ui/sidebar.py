"""Sidebar navigation widget — collapsible with icon + text labels."""

from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QWidget,
)

from .theme import Theme, FONT
from .icons import draw_icon

EXPANDED_WIDTH = 230
COLLAPSED_WIDTH = 58


class SidebarButton(QPushButton):
    """A single sidebar navigation button with icon + text."""

    def __init__(self, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._label_text = label
        self._active = False
        self._theme = None
        self._collapsed = False

        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(42)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 12, 0)
        layout.setSpacing(12)

        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(20, 20)
        self.icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.icon_lbl)

        self.text_lbl = QLabel(label)
        self.text_lbl.setFont(QFont(FONT, 10))
        self.text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.text_lbl, 1)

    def set_active(self, active: bool, theme: Theme):
        self._active = active
        self._theme = theme
        if active:
            self.setObjectName("sidebarBtnActive")
        else:
            self.setObjectName("sidebarBtn")
        self.style().unpolish(self)
        self.style().polish(self)
        self._update_colors(theme)

    def _update_colors(self, theme: Theme):
        color = QColor(theme.accent) if self._active else QColor(theme.text3)
        pm = draw_icon(self._icon_name, 20, color)
        self.icon_lbl.setPixmap(pm)
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")
        text_color = theme.accent if self._active else theme.text2
        font_weight = "600" if self._active else "normal"
        self.text_lbl.setStyleSheet(
            f"color: {text_color}; background: transparent; border: none; "
            f"font-weight: {font_weight};"
        )

    def set_collapsed(self, collapsed: bool):
        self._collapsed = collapsed
        self.text_lbl.setVisible(not collapsed)
        if collapsed:
            self.layout().setContentsMargins(17, 0, 0, 0)
        else:
            self.layout().setContentsMargins(14, 0, 12, 0)

    @property
    def active(self) -> bool:
        return self._active


class SidebarWidget(QFrame):
    """Left sidebar with collapsible tool navigation."""

    tool_changed = Signal(int)

    def __init__(self, theme: Theme, collapsed: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._theme = theme
        self._buttons: list[SidebarButton] = []
        self._active_index = 0
        self._collapsed = collapsed

        self.setFixedWidth(COLLAPSED_WIDTH if collapsed else EXPANDED_WIDTH)
        self.setMinimumWidth(COLLAPSED_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 10)
        layout.setSpacing(0)

        # Collapse toggle — using drawn menu icon
        self._toggle_btn = QPushButton()
        self._toggle_btn.setObjectName("sidebarBtn")
        self._toggle_btn.setFixedHeight(40)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)

        toggle_layout = QHBoxLayout(self._toggle_btn)
        toggle_layout.setContentsMargins(17, 0, 0, 0)
        toggle_layout.setSpacing(12)
        self._toggle_icon = QLabel()
        self._toggle_icon.setFixedSize(22, 22)
        self._toggle_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        toggle_layout.addWidget(self._toggle_icon)
        self._toggle_text = QLabel("PDF Toolkit")
        self._toggle_text.setFont(QFont(FONT, 11, QFont.Bold))
        self._toggle_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._toggle_text.setVisible(not collapsed)
        toggle_layout.addWidget(self._toggle_text, 1)

        self._toggle_btn.clicked.connect(self.toggle_collapse)
        layout.addWidget(self._toggle_btn)

        layout.addSpacing(6)

        # Home button
        self._home_btn = SidebarButton("home", "Home")
        self._home_btn.set_collapsed(collapsed)
        self._home_btn.clicked.connect(lambda: self._on_click(0))
        layout.addWidget(self._home_btn)

        # Separator
        layout.addSpacing(6)
        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        layout.addWidget(sep)
        layout.addSpacing(6)

        # Tool buttons (in scroll area for many tools)
        from PySide6.QtWidgets import QScrollArea
        self._tools_scroll = QScrollArea()
        self._tools_scroll.setWidgetResizable(True)
        self._tools_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tools_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tools_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._tools_container = QWidget()
        self._tools_container.setStyleSheet("background: transparent;")
        self._tool_layout = QVBoxLayout(self._tools_container)
        self._tool_layout.setSpacing(2)
        self._tool_layout.setContentsMargins(0, 0, 0, 0)
        self._tool_layout.addStretch()
        self._tools_scroll.setWidget(self._tools_container)
        layout.addWidget(self._tools_scroll, 1)

        layout.addStretch()

        # Bottom buttons (theme, about)
        self._bottom_layout = QVBoxLayout()
        self._bottom_layout.setSpacing(2)
        layout.addLayout(self._bottom_layout)

        # Set home as active initially
        self._buttons.append(self._home_btn)
        self._home_btn.set_active(True, theme)
        self._update_toggle_icon(theme)

    def _update_toggle_icon(self, theme: Theme):
        pm = draw_icon("menu", 22, QColor(theme.accent))
        self._toggle_icon.setPixmap(pm)
        self._toggle_icon.setStyleSheet("background: transparent; border: none;")
        self._toggle_text.setStyleSheet(
            f"color: {theme.text}; background: transparent; border: none; "
            f"font-weight: bold;"
        )

    def add_tool(self, icon_name: str, label: str) -> int:
        """Add a tool button. Returns its index."""
        idx = len(self._buttons)
        btn = SidebarButton(icon_name, label)
        btn.set_collapsed(self._collapsed)
        btn.clicked.connect(lambda checked, i=idx: self._on_click(i))
        self._buttons.append(btn)
        # Insert before the stretch
        insert_pos = self._tool_layout.count() - 1
        self._tool_layout.insertWidget(insert_pos, btn)
        btn.set_active(idx == self._active_index, self._theme)
        return idx

    def add_bottom_button(self, icon_name: str, label: str) -> SidebarButton:
        """Add a button to the bottom section."""
        btn = SidebarButton(icon_name, label)
        btn.set_collapsed(self._collapsed)
        btn.setObjectName("sidebarBtn")
        btn.set_active(False, self._theme)
        self._bottom_layout.addWidget(btn)
        return btn

    def _on_click(self, index: int):
        if index == self._active_index:
            return
        self._active_index = index
        for i, btn in enumerate(self._buttons):
            btn.set_active(i == index, self._theme)
        self.tool_changed.emit(index)

    def set_active(self, index: int):
        """Programmatically set the active tool."""
        if 0 <= index < len(self._buttons):
            self._active_index = index
            for i, btn in enumerate(self._buttons):
                btn.set_active(i == index, self._theme)

    def toggle_collapse(self):
        self._collapsed = not self._collapsed
        target = COLLAPSED_WIDTH if self._collapsed else EXPANDED_WIDTH

        anim = QPropertyAnimation(self, b"maximumWidth")
        anim.setDuration(180)
        anim.setStartValue(self.width())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim = anim  # prevent GC

        anim2 = QPropertyAnimation(self, b"minimumWidth")
        anim2.setDuration(180)
        anim2.setStartValue(self.width())
        anim2.setEndValue(target)
        anim2.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim2 = anim2

        for btn in self._buttons:
            btn.set_collapsed(self._collapsed)
        for i in range(self._bottom_layout.count()):
            item = self._bottom_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), SidebarButton):
                item.widget().set_collapsed(self._collapsed)
        self._toggle_text.setVisible(not self._collapsed)

        anim.start()
        anim2.start()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def apply_theme(self, theme: Theme):
        self._theme = theme
        for i, btn in enumerate(self._buttons):
            btn.set_active(i == self._active_index, theme)
        for i in range(self._bottom_layout.count()):
            item = self._bottom_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), SidebarButton):
                item.widget()._update_colors(theme)
        self._update_toggle_icon(theme)
