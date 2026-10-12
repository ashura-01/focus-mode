"""Colours and the application stylesheet. One global `theme` object.

Custom-painted widgets read colours from `theme.c(...)`; everything else is
styled through the QSS built in `stylesheet()`. Changing accent or base style
emits `theme.changed` and the app re-applies both.
"""

from __future__ import annotations

from string import Template

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette

BASES: dict[str, dict[str, str]] = {
    "charcoal": dict(bg="#12151c", panel="#1a1e28", panel2="#222735", border="#2a3040", text="#f2f4f8", muted="#8c95a8"),
    "amoled": dict(bg="#000000", panel="#0d0e12", panel2="#15171d", border="#1f222b", text="#f5f6f8", muted="#8a8f9c"),
    "midnight": dict(bg="#0a0f1f", panel="#111a30", panel2="#18233f", border="#233256", text="#eef2fb", muted="#8593b3"),
}
BASE_LABELS = {"charcoal": "Charcoal", "amoled": "AMOLED", "midnight": "Midnight"}

ACCENTS: list[tuple[str, str]] = [
    ("Crimson", "#e0474f"),
    ("Ember", "#f07a3a"),
    ("Gold", "#e3b341"),
    ("Emerald", "#34c58a"),
    ("Azure", "#4d8dff"),
    ("Violet", "#8f6cf2"),
    ("Rose", "#e85a9d"),
]

PRIORITY_COLORS = {"high": "#ff5d63", "medium": "#9aa3b5", "low": "#5b92f5"}
GOOD = "#34c58a"
BLUE = "#6c83e0"
ORANGE = "#f08a3a"


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


class Theme(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.base = "charcoal"
        self.accent_hex = "#e0474f"
        self.animations = True

    def configure(self, base: str, accent: str, animations: bool) -> None:
        base = base if base in BASES else "charcoal"
        accent = accent if QColor(accent).isValid() else "#e0474f"
        dirty = (base, accent) != (self.base, self.accent_hex)
        self.base, self.accent_hex, self.animations = base, accent, animations
        if dirty:
            self.changed.emit()

    # -- colours ------------------------------------------------------------
    def c(self, name: str) -> QColor:
        if name == "accent":
            return QColor(self.accent_hex)
        if name == "accent_hi":
            return _mix(QColor(self.accent_hex), QColor("#ffffff"), 0.14)
        if name == "accent_lo":
            return _mix(QColor(self.accent_hex), QColor("#000000"), 0.2)
        return QColor(BASES[self.base][name])

    def alpha(self, name: str, a: float) -> QColor:
        """A theme colour name (or a #hex string) with the given opacity."""
        col = QColor(name) if name.startswith("#") else self.c(name)
        col.setAlphaF(max(0.0, min(1.0, a)))
        return col

    def ms(self, duration: int) -> int:
        """Animation length, collapsed to 0 when animations are off."""
        return duration if self.animations else 0

    # -- Qt styling ---------------------------------------------------------
    def palette(self) -> QPalette:
        pal = QPalette()
        pal.setColor(QPalette.ColorRole.Window, self.c("bg"))
        pal.setColor(QPalette.ColorRole.WindowText, self.c("text"))
        pal.setColor(QPalette.ColorRole.Base, self.c("panel2"))
        pal.setColor(QPalette.ColorRole.AlternateBase, self.c("panel"))
        pal.setColor(QPalette.ColorRole.Text, self.c("text"))
        pal.setColor(QPalette.ColorRole.Button, self.c("panel2"))
        pal.setColor(QPalette.ColorRole.ButtonText, self.c("text"))
        pal.setColor(QPalette.ColorRole.Highlight, self.c("accent"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        pal.setColor(QPalette.ColorRole.ToolTipBase, self.c("panel2"))
        pal.setColor(QPalette.ColorRole.ToolTipText, self.c("text"))
        pal.setColor(QPalette.ColorRole.PlaceholderText, self.c("muted"))
        pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, self.c("muted"))
        pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, self.c("muted"))
        return pal

    def stylesheet(self) -> str:
        b = BASES[self.base]
        accent = self.c("accent")
        soft = QColor(accent)
        soft.setAlphaF(0.16)
        tokens = dict(
            b,
            accent=accent.name(),
            accent_hi=self.c("accent_hi").name(),
            accent_lo=self.c("accent_lo").name(),
            accent_soft=f"rgba({accent.red()},{accent.green()},{accent.blue()},0.18)",
        )
        return Template(_QSS).safe_substitute(tokens)


_QSS = """
QMainWindow, QDialog { background: $bg; }
QWidget { color: $text; font-size: 14px; }
QLabel { background: transparent; }
QToolTip { background: $panel2; color: $text; border: 1px solid $border; padding: 6px 8px; }

#Sidebar { background: $panel; border-radius: 22px; }
#Card { background: $panel; border-radius: 18px; }
#Divider { background: $border; max-width: 1px; }
#TaskCard { background: $panel; border: 1px solid $border; border-radius: 14px; }
#TaskCard:hover { background: $panel2; border: 1px solid $accent; }
#Inset { background: $panel2; border-radius: 12px; }

QLabel[role="title"] { font-size: 22px; font-weight: 600; }
QLabel[role="section"] { font-size: 13px; font-weight: 700; color: $muted; }
QLabel[role="h2"] { font-size: 16px; font-weight: 600; }
QLabel[role="muted"] { color: $muted; }
QLabel[role="small"] { color: $muted; font-size: 12px; }
QLabel[role="value"] { font-size: 28px; font-weight: 500; }
QLabel[role="hint"] { color: $muted; font-size: 12px; }
QLabel[role="ok"] { color: #34c58a; }
QLabel[role="bad"] { color: #ff6b70; }
QLabel[role="accent"] { color: $accent; font-weight: 600; }

QPushButton { background: $panel2; border: 1px solid $border; border-radius: 12px; padding: 9px 18px; }
QPushButton:hover { background: $border; }
QPushButton:disabled { color: $muted; }
QPushButton[variant="primary"] { background: $accent; border: 0; color: white; font-weight: 600; padding: 11px 26px; border-radius: 14px; }
QPushButton[variant="primary"]:hover { background: $accent_hi; }
QPushButton[variant="primary"]:pressed { background: $accent_lo; }
QPushButton[variant="primary"]:disabled { background: $border; color: $muted; }
QPushButton[variant="ghost"] { background: transparent; border: 1px solid $border; }
QPushButton[variant="ghost"]:hover { background: $panel2; }
QPushButton[variant="pill"] { background: transparent; border: 1px solid $border; border-radius: 17px; padding: 7px 18px; color: $text; }
QPushButton[variant="pill"]:hover { background: $panel2; }
QPushButton[variant="seg"] { background: $panel2; border: 1px solid $border; border-radius: 10px; padding: 9px 12px; }
QPushButton[variant="seg"]:checked { background: $accent_soft; border: 1px solid $accent; }
QPushButton[variant="danger"] { background: transparent; border: 1px solid rgba(255,107,112,0.45); color: #ff6b70; }
QPushButton[variant="danger"]:hover { background: rgba(255,107,112,0.12); }
QPushButton[size="sm"], QPushButton[variant="primary"][size="sm"] { padding: 6px 14px; border-radius: 10px; }
QPushButton[variant="icon"] { padding: 0; border-radius: 12px; min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px; }

QLineEdit, QPlainTextEdit, QDateEdit, QTimeEdit, QComboBox {
    background: $panel2; border: 1px solid $border; border-radius: 10px; padding: 8px 10px;
    selection-background-color: $accent; selection-color: white;
}
QLineEdit:focus, QPlainTextEdit:focus, QDateEdit:focus, QTimeEdit:focus, QComboBox:focus { border: 1px solid $accent; }
QLineEdit:disabled, QDateEdit:disabled, QTimeEdit:disabled { color: $muted; }
QComboBox QAbstractItemView { background: $panel2; border: 1px solid $border; selection-background-color: $accent_soft; outline: 0; }
QCalendarWidget QWidget { background: $panel; color: $text; }
QCalendarWidget QAbstractItemView:enabled { selection-background-color: $accent; selection-color: white; color: $text; }
QCalendarWidget QAbstractItemView:disabled { color: $muted; }

QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 0; }

QMenu { background: $panel2; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 22px; border-radius: 6px; }
QMenu::item:selected { background: $accent_soft; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }

QMessageBox { background: $bg; }
QCheckBox { spacing: 8px; }
"""

theme = Theme()
