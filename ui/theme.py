"""Theme system — colors, fonts, and QSS stylesheet generation.

Provides a modern, glassmorphism-inspired design system with smooth
hover states, subtle shadows, and refined typography.
"""

import sys


class Theme:
    def __init__(self, name, bg, surface, surface2, surface3, border, border2,
                 accent, accent_h, accent_m, accent_soft,
                 text, text2, text3, green, red, amber,
                 bar_bg, bar_fg, card_bg, card_border, card_sel,
                 sidebar_bg, sidebar_active_bg, shadow, glow):
        self.name = name
        self.bg = bg
        self.surface = surface
        self.surface2 = surface2
        self.surface3 = surface3
        self.border = border
        self.border2 = border2
        self.accent = accent
        self.accent_h = accent_h
        self.accent_m = accent_m
        self.accent_soft = accent_soft
        self.text = text
        self.text2 = text2
        self.text3 = text3
        self.green = green
        self.red = red
        self.amber = amber
        self.bar_bg = bar_bg
        self.bar_fg = bar_fg
        self.card_bg = card_bg
        self.card_border = card_border
        self.card_sel = card_sel
        self.sidebar_bg = sidebar_bg
        self.sidebar_active_bg = sidebar_active_bg
        self.shadow = shadow
        self.glow = glow


LIGHT = Theme(
    name="light",
    bg="#f4f5f7",
    surface="#ffffff",
    surface2="#f0f1f3",
    surface3="#e8eaed",
    border="#e0e3e8",
    border2="#d0d4db",
    accent="#6366f1",
    accent_h="#4f46e5",
    accent_m="#a5b4fc",
    accent_soft="#eef2ff",
    text="#0f172a",
    text2="#475569",
    text3="#94a3b8",
    green="#059669",
    red="#dc2626",
    amber="#d97706",
    bar_bg="#e2e8f0",
    bar_fg="#6366f1",
    card_bg="#ffffff",
    card_border="#e2e5ea",
    card_sel="#eef2ff",
    sidebar_bg="#fafbfc",
    sidebar_active_bg="#eef2ff",
    shadow="rgba(0,0,0,0.06)",
    glow="rgba(99,102,241,0.15)",
)

DARK = Theme(
    name="dark",
    bg="#0c0c0f",
    surface="#16161b",
    surface2="#1e1e24",
    surface3="#26262e",
    border="#2a2a33",
    border2="#3a3a45",
    accent="#818cf8",
    accent_h="#a5b4fc",
    accent_m="#4338ca",
    accent_soft="#1e1b4b",
    text="#f1f5f9",
    text2="#94a3b8",
    text3="#64748b",
    green="#34d399",
    red="#f87171",
    amber="#fbbf24",
    bar_bg="#2a2a33",
    bar_fg="#818cf8",
    card_bg="#16161b",
    card_border="#2a2a33",
    card_sel="#1e1b4b",
    sidebar_bg="#111114",
    sidebar_active_bg="#1e1b4b",
    shadow="rgba(0,0,0,0.3)",
    glow="rgba(129,140,248,0.12)",
)

FONT = (
    "Segoe UI" if sys.platform == "win32"
    else ".AppleSystemUIFont" if sys.platform == "darwin"
    else "Cantarell"
)


def build_stylesheet(t: Theme) -> str:
    # Build a gradient hint for accent buttons
    accent_gradient = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t.accent}, stop:1 {t.accent_h})"
    accent_hover_gradient = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t.accent_h}, stop:1 {t.accent})"

    return f"""
    /* ─── Base ───────────────────────────────────────────────── */
    QMainWindow, QWidget#central {{
        background: {t.bg};
    }}
    QLabel {{
        color: {t.text};
        background: transparent;
        font-family: "{FONT}";
    }}

    /* ─── Separators ─────────────────────────────────────────── */
    QFrame#separator {{
        background: {t.border};
        max-height: 1px; min-height: 1px;
    }}

    /* ─── File list frame ────────────────────────────────────── */
    QFrame#fileListFrame {{
        background: {t.surface};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}

    /* ─── Scroll areas ───────────────────────────────────────── */
    QScrollArea {{
        background: {t.surface};
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 7px;
        margin: 4px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {t.border2};
        min-height: 40px;
        border-radius: 3px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t.text3};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none; height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 7px;
        margin: 0 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t.border2};
        min-width: 40px;
        border-radius: 3px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {t.text3};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none; width: 0;
    }}

    /* ─── Buttons ────────────────────────────────────────────── */
    QPushButton {{
        font-family: "{FONT}"; font-size: 12px; font-weight: 500;
        border: 1px solid {t.border}; border-radius: 8px;
        padding: 8px 20px;
        color: {t.text}; background: {t.surface};
    }}
    QPushButton:hover {{
        background: {t.surface2};
        border-color: {t.border2};
    }}
    QPushButton:pressed {{
        background: {t.surface3};
    }}
    QPushButton:disabled {{
        color: {t.text3}; background: {t.surface2};
        border-color: {t.border};
    }}

    /* Primary CTA button — gradient accent */
    QPushButton#primary {{
        background: {accent_gradient};
        color: #ffffff;
        font-weight: 600; font-size: 13px;
        padding: 11px 32px;
        border-radius: 10px;
        border: none;
    }}
    QPushButton#primary:hover {{
        background: {accent_hover_gradient};
    }}
    QPushButton#primary:pressed {{
        background: {t.accent_h};
    }}
    QPushButton#primary:disabled {{
        background: {t.accent_m};
        color: {t.text3};
    }}

    /* Ghost / text-only button */
    QPushButton#ghost {{
        background: transparent; color: {t.text2};
        padding: 8px 14px; border: none;
    }}
    QPushButton#ghost:hover {{
        color: {t.accent};
        background: {t.accent_soft};
    }}

    /* Remove / close button */
    QPushButton#removeBtn {{
        background: transparent; color: {t.text3};
        font-size: 14px; padding: 3px 7px; border-radius: 6px;
        border: none;
    }}
    QPushButton#removeBtn:hover {{
        color: {t.red};
        background: {"#fef2f2" if t.name == "light" else "#2a1515"};
    }}

    QPushButton#themeBtn {{
        background: transparent; color: {t.text3};
        font-size: 14px; padding: 6px 10px; border-radius: 8px;
        border: none;
    }}
    QPushButton#themeBtn:hover {{ background: {t.surface2}; color: {t.text2}; }}

    /* ─── Progress bars ──────────────────────────────────────── */
    QProgressBar {{
        background: {t.border}; border: none; border-radius: 3px;
        max-height: 5px; min-height: 5px;
    }}
    QProgressBar::chunk {{
        background: {accent_gradient};
        border-radius: 3px;
    }}

    /* ─── Checkboxes ─────────────────────────────────────────── */
    QCheckBox {{
        color: {t.text2}; font-family: "{FONT}"; font-size: 11px;
        spacing: 10px;
    }}
    QCheckBox::indicator {{
        width: 18px; height: 18px; border-radius: 5px;
        border: 1.5px solid {t.border2}; background: {t.surface};
    }}
    QCheckBox::indicator:hover {{
        border-color: {t.accent_m};
        background: {t.accent_soft};
    }}
    QCheckBox::indicator:checked {{
        background: {t.accent};
        border-color: {t.accent};
    }}

    /* ─── Dialogs ────────────────────────────────────────────── */
    QDialog {{
        background: {t.bg};
    }}

    /* ─── Tables ─────────────────────────────────────────────── */
    QTableWidget {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; border-radius: 8px;
        gridline-color: {t.border};
        font-family: "{FONT}"; font-size: 11px;
        selection-background-color: {t.accent_soft};
        selection-color: {t.text};
    }}
    QTableWidget::item {{
        padding: 6px 12px;
        border-bottom: 1px solid {t.border};
    }}
    QTableWidget::item:hover {{
        background: {t.surface2};
    }}
    QHeaderView::section {{
        background: {t.surface2}; color: {t.text2};
        border: none; border-bottom: 2px solid {t.border};
        padding: 10px 12px; font-weight: 600; font-size: 11px;
    }}

    /* ─── Sidebar ────────────────────────────────────────────── */
    QFrame#sidebar {{
        background: {t.sidebar_bg};
        border-right: 1px solid {t.border};
    }}
    QPushButton#sidebarBtn {{
        background: transparent; color: {t.text2};
        text-align: left; padding: 9px 12px;
        border: none; border-radius: 10px;
        margin: 1px 6px;
    }}
    QPushButton#sidebarBtn:hover {{
        background: {t.surface2}; color: {t.text};
    }}
    QPushButton#sidebarBtnActive {{
        background: {t.sidebar_active_bg}; color: {t.accent};
        text-align: left; padding: 9px 12px;
        border: none; border-radius: 10px;
        margin: 1px 6px;
        font-weight: 600;
    }}

    /* ─── Combo boxes ────────────────────────────────────────── */
    QComboBox {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; border-radius: 8px;
        padding: 7px 12px; font-family: "{FONT}"; font-size: 11px;
        min-height: 22px;
    }}
    QComboBox:hover {{ border-color: {t.accent_m}; }}
    QComboBox:focus {{ border-color: {t.accent}; }}
    QComboBox::drop-down {{
        border: none; width: 28px;
    }}
    QComboBox QAbstractItemView {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; border-radius: 6px;
        selection-background-color: {t.accent_soft};
        selection-color: {t.text};
        padding: 4px;
    }}
    QComboBox QAbstractItemView::item {{
        padding: 6px 10px;
        border-radius: 4px;
    }}

    /* ─── Spin boxes ─────────────────────────────────────────── */
    QSpinBox, QDoubleSpinBox {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; border-radius: 8px;
        padding: 6px 12px; font-family: "{FONT}"; font-size: 11px;
    }}
    QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {t.accent_m}; }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t.accent}; }}

    /* ─── Line edits ─────────────────────────────────────────── */
    QLineEdit {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; border-radius: 8px;
        padding: 7px 12px; font-family: "{FONT}"; font-size: 11px;
        min-height: 22px;
    }}
    QLineEdit:hover {{ border-color: {t.accent_m}; }}
    QLineEdit:focus {{ border-color: {t.accent}; }}

    /* ─── Text edits ─────────────────────────────────────────── */
    QTextEdit {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; border-radius: 8px;
        padding: 8px; font-family: "{FONT}"; font-size: 11px;
    }}
    QTextEdit:focus {{ border-color: {t.accent}; }}

    /* ─── Sliders ────────────────────────────────────────────── */
    QSlider::groove:horizontal {{
        background: {t.border}; height: 5px; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {t.accent}; width: 18px; height: 18px;
        margin: -7px 0; border-radius: 9px;
        border: 2px solid {t.surface};
    }}
    QSlider::handle:horizontal:hover {{
        background: {t.accent_h};
        border: 2px solid {t.accent_soft};
    }}
    QSlider::sub-page:horizontal {{
        background: {t.accent};
        border-radius: 2px;
    }}

    /* ─── Menus ──────────────────────────────────────────────── */
    QMenu {{
        background: {t.surface}; color: {t.text};
        border: 1px solid {t.border}; border-radius: 8px;
        padding: 6px 4px;
        font-family: "{FONT}"; font-size: 11px;
    }}
    QMenu::item {{
        padding: 8px 24px 8px 16px;
        border-radius: 6px;
        margin: 1px 4px;
    }}
    QMenu::item:selected {{
        background: {t.accent_soft};
        color: {t.accent};
    }}
    QMenu::separator {{
        height: 1px;
        background: {t.border};
        margin: 4px 12px;
    }}

    /* ─── Tooltips ───────────────────────────────────────────── */
    QToolTip {{
        background: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 6px;
        padding: 6px 10px;
        font-family: "{FONT}"; font-size: 10px;
    }}

    /* ─── Message boxes ──────────────────────────────────────── */
    QMessageBox {{
        background: {t.bg};
    }}
    QMessageBox QLabel {{
        color: {t.text};
        font-family: "{FONT}";
    }}
    """
