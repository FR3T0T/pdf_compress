"""Dashboard home page — grid of all available tools."""

from collections import OrderedDict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QGridLayout, QLineEdit, QFrame,
)

from ..theme import Theme, FONT
from ..widgets import ToolCard
from .base import BasePage


class HomePage(BasePage):
    page_title = "Home"
    page_icon = "home"

    tool_selected = Signal(str)  # emits tool key

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self._cards: list[ToolCard] = []
        self._category_labels: list[QLabel] = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 32, 36, 24)
        root.setSpacing(0)

        # ── Hero Header ──
        hdr = QVBoxLayout()
        hdr.setSpacing(6)

        self.title_lbl = QLabel("PDF Toolkit")
        self.title_lbl.setFont(QFont(FONT, 24, QFont.Bold))
        hdr.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel("Everything you need to work with PDFs — fully offline, no account required")
        self.subtitle_lbl.setFont(QFont(FONT, 11))
        self.subtitle_lbl.setWordWrap(True)
        hdr.addWidget(self.subtitle_lbl)

        root.addLayout(hdr)

        # ── Search bar ──
        root.addSpacing(20)
        self.search_frame = QFrame()
        self.search_frame.setObjectName("searchFrame")
        sf_layout = QHBoxLayout(self.search_frame)
        sf_layout.setContentsMargins(16, 0, 16, 0)

        self.search_icon = QLabel("\U0001f50d")
        self.search_icon.setFont(QFont(FONT, 12))
        sf_layout.addWidget(self.search_icon)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tools...")
        self.search.setFont(QFont(FONT, 12))
        self.search.setObjectName("searchInput")
        self.search.textChanged.connect(self._on_search)
        sf_layout.addWidget(self.search, 1)

        # Tool count badge
        self.count_lbl = QLabel("")
        self.count_lbl.setFont(QFont(FONT, 9, QFont.Bold))
        sf_layout.addWidget(self.count_lbl)

        root.addWidget(self.search_frame)

        root.addSpacing(24)

        # ── Scrollable tool grid ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)

        self.grid_widget = QWidget()
        self.grid_layout = QVBoxLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(0, 0, 8, 0)
        self.grid_layout.setSpacing(24)

        self.scroll.setWidget(self.grid_widget)
        root.addWidget(self.scroll, 1)

    def populate(self, tools, categories):
        """Populate the grid with tool cards. Called after all tools are registered."""

        # Group tools by category
        grouped = OrderedDict()
        for cat_key, cat_label in categories.items():
            grouped[cat_key] = {"label": cat_label, "tools": []}
        for tool in tools:
            if tool.category in grouped:
                grouped[tool.category]["tools"].append(tool)

        self._cards.clear()
        self._category_labels.clear()
        self._category_sections = []
        total_tools = 0

        for cat_key, cat_data in grouped.items():
            if not cat_data["tools"]:
                continue

            # Category section
            section_widget = QWidget()
            section_layout = QVBoxLayout(section_widget)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(12)

            # Category header with count
            cat_hdr = QHBoxLayout()
            cat_lbl = QLabel(cat_data["label"].upper())
            cat_lbl.setFont(QFont(FONT, 9, QFont.Bold))
            cat_lbl.setObjectName("categoryHeader")
            self._category_labels.append(cat_lbl)
            cat_hdr.addWidget(cat_lbl)

            cat_count = QLabel(f"{len(cat_data['tools'])} tools")
            cat_count.setFont(QFont(FONT, 9))
            cat_count.setObjectName("categoryCount")
            self._category_labels.append(cat_count)
            cat_hdr.addWidget(cat_count)
            cat_hdr.addStretch()

            section_layout.addLayout(cat_hdr)

            # Tool card grid — 3 columns
            grid = QGridLayout()
            grid.setSpacing(12)
            col = 0
            row = 0
            for tool in cat_data["tools"]:
                card = ToolCard(
                    tool.key, tool.title, tool.description,
                    tool.icon, self.shell.theme,
                )
                card.clicked.connect(self.tool_selected.emit)
                grid.addWidget(card, row, col)
                self._cards.append(card)
                total_tools += 1
                col += 1
                if col >= 3:
                    col = 0
                    row += 1

            section_layout.addLayout(grid)
            self.grid_layout.addWidget(section_widget)
            self._category_sections.append((section_widget, cat_data["tools"]))

        self.grid_layout.addStretch()
        self.count_lbl.setText(f"{total_tools} tools")

    def _on_search(self, text: str):
        text = text.lower().strip()
        for section_widget, tools in self._category_sections:
            any_visible = False
            section_layout = section_widget.layout()
            grid = section_layout.itemAt(1).layout()
            for i in range(grid.count()):
                item = grid.itemAt(i)
                if item and item.widget():
                    card = item.widget()
                    if isinstance(card, ToolCard):
                        match = (not text or
                                 text in card.title_lbl.text().lower() or
                                 text in card.desc_lbl.text().lower())
                        card.setVisible(match)
                        if match:
                            any_visible = True
            section_widget.setVisible(any_visible)

    def accepts_drops(self) -> bool:
        return False

    def apply_theme(self, theme: Theme):
        t = theme
        self.title_lbl.setStyleSheet(f"color: {t.text};")
        self.subtitle_lbl.setStyleSheet(f"color: {t.text3};")

        # Modern search bar styling
        self.search_frame.setStyleSheet(
            f"QFrame#searchFrame {{ background: {t.surface}; "
            f"border: 1px solid {t.border}; border-radius: 12px; "
            f"min-height: 44px; max-height: 44px; }}"
            f"QFrame#searchFrame:hover {{ border-color: {t.accent_m}; }}"
        )
        self.search.setStyleSheet(
            f"QLineEdit {{ background: transparent; color: {t.text}; "
            f"border: none; padding: 0; }}"
        )
        self.search_icon.setStyleSheet(f"color: {t.text3};")
        self.count_lbl.setStyleSheet(
            f"color: {t.accent}; background: {t.accent_soft}; "
            f"border-radius: 4px; padding: 2px 8px;"
        )
        self.grid_widget.setStyleSheet(f"background: transparent;")

        for lbl in self._category_labels:
            if lbl.objectName() == "categoryCount":
                lbl.setStyleSheet(f"color: {t.text3};")
            else:
                lbl.setStyleSheet(f"color: {t.text2};")
        for card in self._cards:
            card.apply_theme(t)
