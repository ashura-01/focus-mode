"""Reusable widgets: cards, toggle, stepper, tabs, check box, chips, stat rows, toast."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from focus_mode.ui import icons
from focus_mode.ui.theme import PRIORITY_COLORS, theme


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if role:
        lab.setProperty("role", role)
    lab.setWordWrap(wrap)
    return lab


class ElideLabel(QLabel):
    """Single-line label that shrinks (with an ellipsis) instead of forcing its parent wider."""

    def __init__(self, text: str = "", role: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if role:
            self.setProperty("role", role)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, super().sizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        if shown != self.text():
            self.setText(shown)


def caps(text: str, letter_spacing: float = 1.4, pixel: int = 12, weight: int = 600) -> QLabel:
    """Small uppercase section label with tracking, as in the mockups."""
    lab = QLabel(text.upper())
    font = QFont(lab.font())
    font.setPixelSize(pixel)
    font.setWeight(QFont.Weight(weight))
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    lab.setFont(font)
    lab.setProperty("role", "muted")
    return lab


def button(text: str, variant: str = "", icon_name: str | None = None) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if variant:
        btn.setProperty("variant", variant)
    if icon_name:
        tint = "#ffffff" if variant == "primary" else theme.c("text")
        btn.setIcon(icons.icon(icon_name, tint, 18))
        btn.setIconSize(QSize(18, 18))
        btn.setProperty("icon_name", icon_name)
    return btn


def restyle(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")


class IconLabel(QLabel):
    """An icon that follows the theme (colour given as a theme key or #hex)."""

    def __init__(self, name: str, color: str = "muted", size: int = 22, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name, self._color, self._size = name, color, size
        self.setFixedSize(size + 2, size + 2)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.changed.connect(self._refresh)
        self._refresh()

    def set_icon(self, name: str, color: str | None = None) -> None:
        self._name = name
        if color:
            self._color = color
        self._refresh()

    def _refresh(self) -> None:
        col = QColor(self._color) if self._color.startswith("#") else theme.c(self._color)
        self.setPixmap(icons.pixmap(self._name, col, self._size))


class Toggle(QAbstractButton):
    """Animated on/off switch."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _get(self) -> float:
        return self._pos

    def _set(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, _get, _set)

    def set_checked_silent(self, value: bool) -> None:
        self.blockSignals(True)
        self.setChecked(value)
        self.blockSignals(False)
        self._pos = 1.0 if value else 0.0
        self.update()

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setDuration(theme.ms(160))
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = theme.c("border"), theme.c("accent")
        t = self._pos
        track = QColor(
            round(off.red() + (on.red() - off.red()) * t),
            round(off.green() + (on.green() - off.green()) * t),
            round(off.blue() + (on.blue() - off.blue()) * t),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 13, 13)
        p.setBrush(QColor("#ffffff"))
        x = 3 + (self.width() - 26) * t
        p.drawEllipse(QRectF(x, 3, 20, 20))


class Stepper(QWidget):
    """[-]  25 min  [+] with clamping."""

    valueChanged = Signal(int)

    def __init__(self, value: int, lo: int, hi: int, suffix: str = "", step: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lo, self._hi, self._step, self._suffix = lo, hi, step, suffix
        self._value = max(lo, min(hi, value))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._minus, self._plus = self._btn("−"), self._btn("+")
        self._text = QLabel()
        self._text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text.setMinimumWidth(78)
        lay.addWidget(self._minus)
        lay.addWidget(self._text)
        lay.addWidget(self._plus)
        self._minus.clicked.connect(lambda: self.setValue(self._value - self._step))
        self._plus.clicked.connect(lambda: self.setValue(self._value + self._step))
        self._refresh()

    def _btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(30, 30)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet("QPushButton{padding:0;border-radius:9px;font-size:16px;}")
        b.setAutoRepeat(True)
        b.setAutoRepeatInterval(90)
        return b

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        value = max(self._lo, min(self._hi, value))
        if value != self._value:
            self._value = value
            self._refresh()
            self.valueChanged.emit(value)

    def _refresh(self) -> None:
        self._text.setText(f"{self._value}{self._suffix}")


class Tabs(QWidget):
    """Underlined tab strip like the Tasks mockup. Emits the selected key."""

    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], current: str | None = None, parent: QWidget | None = None, height: int = 46) -> None:
        super().__init__(parent)
        self._h = height
        self._items = items
        self._current = current or items[0][0]
        self._rects: list[QRectF] = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(height)
        self._ul = 0.0  # animated underline x
        self._anim = QPropertyAnimation(self, b"underline", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_ul(self) -> float:
        return self._ul

    def _set_ul(self, v: float) -> None:
        self._ul = v
        self.update()

    underline = Property(float, _get_ul, _set_ul)

    def _layout(self) -> list[QRectF]:
        fm = QFontMetrics(self.font())
        rects, x = [], 0.0
        for _key, text in self._items:
            w = fm.horizontalAdvance(text) + 36
            rects.append(QRectF(x, 0, w, self.height()))
            x += w
        return rects

    def sizeHint(self) -> QSize:
        rects = self._layout()
        return QSize(int(rects[-1].right()) + 8, self._h)

    def current(self) -> str:
        return self._current

    def set_current(self, key: str) -> None:
        """Select a tab without emitting `changed`."""
        if any(k == key for k, _t in self._items):
            self._current = key
            self.update()

    def _index(self) -> int:
        return next(i for i, (k, _t) in enumerate(self._items) if k == self._current)

    def mousePressEvent(self, event) -> None:
        for (key, _t), rect in zip(self._items, self._layout()):
            if rect.contains(event.position()) and key != self._current:
                old = self._layout()[self._index()].x()
                self._current = key
                self._anim.stop()
                self._anim.setDuration(theme.ms(200))
                self._anim.setStartValue(old)
                self._anim.setEndValue(rect.x())
                self._anim.start()
                self.changed.emit(key)
                self.update()
                return

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("panel"))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), self.height() / 2, self.height() / 2)
        rects = self._layout()
        idx = self._index()
        for i, ((_k, text), rect) in enumerate(zip(self._items, rects)):
            p.setPen(theme.c("text") if i == idx else theme.c("muted"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        cur = rects[idx]
        x = cur.x() if self._anim.state() != QPropertyAnimation.State.Running else self._ul
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("accent"))
        p.drawRoundedRect(QRectF(x + 18, self.height() - 6, cur.width() - 36, 3), 1.5, 1.5)


class RoundCheck(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(2, 2, 18, 18)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.c("accent"))
            p.drawRoundedRect(rect, 6, 6)
            p.drawPixmap(5, 5, icons.pixmap("check", "#ffffff", 12, stroke=3))
        else:
            p.setPen(QPen(theme.c("muted"), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 6, 6)


class PriorityChip(QLabel):
    def __init__(self, priority: str = "medium", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(62, 22)
        self.set_priority(priority)

    def set_priority(self, priority: str) -> None:
        col = QColor(PRIORITY_COLORS.get(priority, PRIORITY_COLORS["medium"]))
        self.setText(priority.capitalize())
        self.setStyleSheet(
            f"background: rgba({col.red()},{col.green()},{col.blue()},0.15);"
            f"color: {col.name()}; border-radius: 7px; font-size: 12px;"
        )


class StatRow(Card):
    """Titled card with three icon/value/caption columns (Today's progress, Focus summary)."""

    def __init__(self, title: str, cells: list[tuple[str, str, str]], parent: QWidget | None = None, compact: bool = False) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*((20, 12, 20, 12) if compact else (26, 20, 26, 20)))
        outer.setSpacing(8 if compact else 12)
        if title:
            outer.addWidget(caps(title, pixel=12))
        row = QHBoxLayout()
        row.setSpacing(0)
        self.values: list[QLabel] = []
        for i, (icon_name, color, caption) in enumerate(cells):
            if i:
                div = QFrame()
                div.setObjectName("Divider")
                row.addWidget(div)
            cell = QVBoxLayout()
            cell.setSpacing(2 if compact else 4)
            cell.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            ic = IconLabel(icon_name, color, 20 if compact else 26)
            val = label("0", "value")
            if compact:
                val.setStyleSheet("font-size: 22px;")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap = label(caption, "muted")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setWordWrap(True)  # lets narrow windows wrap "Focus Time Planned" instead of forcing width
            cell.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
            cell.addWidget(val)
            cell.addWidget(cap)
            wrapper = QWidget()
            wrapper.setLayout(cell)
            wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(wrapper, 1)
            self.values.append(val)
        outer.addLayout(row)

    def set_values(self, *texts: str) -> None:
        for lab, text in zip(self.values, texts):
            lab.setText(text)


class QuoteBar(QFrame):
    def __init__(self, text: str, parent: QWidget | None = None, compact: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*((20, 6, 20, 6) if compact else (24, 14, 24, 14)))
        self._open, self._close = label("“", "accent"), label("”", "accent")
        for lab in (self._open, self._close):
            lab.setStyleSheet("font-size: 30px;")
        self.text = label(text, "muted")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setWordWrap(True)
        lay.addWidget(self._open)
        lay.addWidget(self.text, 1)
        lay.addWidget(self._close)


class Toast(QLabel):
    """Small transient message pinned to the bottom of its parent."""

    COLORS = {"info": "#8c95a8", "ok": "#34c58a", "error": "#ff6b70"}

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMaximumWidth(520)
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)
        self.hide()

    def show_message(self, kind: str, text: str) -> None:
        col = self.COLORS.get(kind, self.COLORS["info"])
        self.setStyleSheet(
            f"background: {theme.c('panel2').name()}; color: {theme.c('text').name()};"
            f"border: 1px solid {col}; border-radius: 12px; padding: 12px 18px;"
        )
        self.setText(text)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._hide.start(5500 if kind == "error" else 3200)

    def reposition(self) -> None:
        par = self.parentWidget()
        if par:
            w = min(self.maximumWidth(), par.width() - 40)
            self.setFixedWidth(max(200, w))
            self.adjustSize()
            self.move((par.width() - self.width()) // 2, par.height() - self.height() - 28)

    def _fade_out(self) -> None:
        self.hide()


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


class NoteBox(QLineEdit):
    """One-line note for the session in progress; saved with it when it is logged.

    Text you type looks normal. Text put there for you (the task you are working on, or a
    note the AI set) is shown in bold whitish-red; typing over it replaces it.
    """

    PLACEHOLDER = "Session note — what are you working on? (saved with this session)"

    def __init__(self, svc, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = svc
        self._typed = ""  # the last note the person typed themselves
        self._auto = False  # showing "Working on: …" (never saved as a note)
        self.setPlaceholderText(self.PLACEHOLDER)
        self.setMaxLength(300)
        self.setClearButtonEnabled(False)
        self.textEdited.connect(self._edited)
        self.editingFinished.connect(self._sync)
        svc.session_note_cleared.connect(self._sync)
        svc.pending_note_changed.connect(self._sync)
        svc.active_task_changed.connect(self._sync)
        svc.tasks_changed.connect(self._sync)
        theme.changed.connect(self._style)
        self._sync()

    def _style(self) -> None:
        coloured = bool(self.text()) and (self._auto or self.svc.session_note != self._typed)
        self.setStyleSheet("color: #ffb3b3; font-weight: 700;" if coloured else "")

    def _edited(self, text: str) -> None:
        self._auto = False
        self._typed = self.svc.session_note = text.strip()
        self._style()

    def _task_text(self) -> str:
        sv = self.svc
        task = sv.db.get_task(sv.active_task_id) if sv.active_task_id else None
        return f"Working on: {task.title}" if task else ""

    def _sync(self) -> None:
        note = self.svc.session_note
        if note:
            self._auto = False
            text = note
        else:
            self._typed = ""
            text = self._task_text()
            self._auto = bool(text)
        if self.text() != text:
            self.setText(text)
        self._style()

    def focusInEvent(self, e) -> None:
        super().focusInEvent(e)
        if self._auto:  # first keystroke replaces the suggestion
            QTimer.singleShot(0, self.selectAll)
