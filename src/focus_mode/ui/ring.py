"""The big circular timer and the vertical heat meter beside it."""

from __future__ import annotations

import math

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from focus_mode.ui import icons
from focus_mode.ui.theme import theme
from focus_mode.ui.widgets import button

TRACK_START = 240.0  # degrees, Qt convention (0 = 3 o'clock, counter-clockwise positive)
TRACK_SPAN = 300.0  # clockwise sweep, leaving a 60 degree gap at the bottom


class TimerRing(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._top, self._time, self._bottom = "", "00:00", ""
        self._time_pixel_scale = 0.15
        self.setMinimumSize(200, 200)
        self.setMaximumHeight(440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.skip_button: QPushButton = button("Skip Session", "pill", "skip")
        self.skip_button.setParent(self)
        theme.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(380, 360)  # modest hint: the page uses spare height, but never needs extra to show the buttons

    def _get(self) -> float:
        return self._progress

    def _set(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, value))
        self.update()

    progress = Property(float, _get, _set)

    def set_progress(self, value: float, animate: bool = False) -> None:
        value = max(0.0, min(1.0, value))
        if animate and theme.animations and abs(value - self._progress) > 0.05:
            self._anim.stop()
            self._anim.setDuration(450)
            self._anim.setStartValue(self._progress)
            self._anim.setEndValue(value)
            self._anim.start()
        elif self._anim.state() != QPropertyAnimation.State.Running:
            self._set(value)

    def set_texts(self, top: str, time_text: str, bottom: str, time_scale: float = 0.15) -> None:
        self._top, self._time, self._bottom = top, time_text, bottom
        self._time_pixel_scale = time_scale
        self.update()

    # -- geometry -----------------------------------------------------------
    def _geom(self) -> tuple[QPointF, float, float]:
        s = min(self.width(), self.height())
        return QPointF(self.width() / 2, self.height() / 2), s * 0.40, s

    def resizeEvent(self, _event) -> None:
        c, r, s = self._geom()
        self.skip_button.adjustSize()
        y = c.y() + s * 0.255
        knob = max(15.0, s * 0.036)
        if r * 0.5 - knob < self.skip_button.width() / 2 + 6:  # small dial: drop the pill below the start knob
            y = min(c.y() + r * 0.866 + knob + 2, self.height() - self.skip_button.height())
        self.skip_button.move(int(c.x() - self.skip_button.width() / 2), int(y))

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, r, s = self._geom()
        width = max(6.0, s * 0.013)

        # tick marks around the dial, fading out through the gap at the bottom
        for i in range(72):
            ang = math.radians(90 - i * 5)
            in_gap = abs(((i * 5) % 360) - 180) < 31
            long = i % 6 == 0
            r1, r2 = r * 1.12, r * (1.19 if long else 1.16)
            col = theme.alpha("muted", 0.10 if in_gap else (0.55 if long else 0.34))
            p.setPen(QPen(col, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(
                QPointF(c.x() + r1 * math.sin(math.radians(i * 5)), c.y() - r1 * math.cos(math.radians(i * 5))),
                QPointF(c.x() + r2 * math.sin(math.radians(i * 5)), c.y() - r2 * math.cos(math.radians(i * 5))),
            )
            del ang

        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # track
        p.setPen(QPen(theme.alpha("border", 0.9), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, int(TRACK_START * 16), int(-TRACK_SPAN * 16))

        # progress (soft glow underneath, crisp arc on top)
        if self._progress > 0.002:
            span = -TRACK_SPAN * self._progress
            p.setPen(QPen(theme.alpha("accent", 0.22), width * 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, int(TRACK_START * 16), int(span * 16))
            p.setPen(QPen(theme.c("accent"), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, int(TRACK_START * 16), int(span * 16))

        # start dot and the knob at the leading edge
        a0 = math.radians(TRACK_START)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("accent"))
        p.drawEllipse(QPointF(c.x() + (r - s * 0.045) * math.cos(a0), c.y() - (r - s * 0.045) * math.sin(a0)), 3.2, 3.2)

        ang = math.radians(TRACK_START - TRACK_SPAN * self._progress)
        kx, ky, kr = c.x() + r * math.cos(ang), c.y() - r * math.sin(ang), max(15.0, s * 0.036)
        p.setBrush(theme.c("panel2"))
        p.setPen(QPen(theme.alpha("accent", 0.55), 1.5))
        p.drawEllipse(QPointF(kx, ky), kr, kr)
        ic = int(kr * 1.1)
        p.drawPixmap(int(kx - ic / 2), int(ky - ic / 2), icons.pixmap("skip", theme.c("text"), ic, stroke=2.4))

        # texts
        def draw(text: str, px: float, weight: QFont.Weight, y: float, color: QColor, spacing: float = 0.0) -> None:
            font = QFont(self.font())
            font.setPixelSize(max(9, int(px)))
            font.setWeight(weight)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
            p.setFont(font)
            p.setPen(color)
            box = QRectF(c.x() - r, y - px, 2 * r, 2 * px)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

        draw(self._top, max(11, s * 0.027), QFont.Weight.Medium, c.y() - s * 0.135, theme.c("muted"), 1.6)
        draw(self._time, s * self._time_pixel_scale, QFont.Weight.Normal, c.y() + s * 0.005, theme.c("text"), -1.0)
        draw(self._bottom, max(11, s * 0.027), QFont.Weight.Medium, c.y() + s * 0.135, theme.c("muted"), 1.6)


class HeatMeter(QWidget):
    """Vertical bar meter that fills up as the session progresses."""

    BARS = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self.setFixedWidth(48)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180)
        theme.changed.connect(self.update)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        top, bottom = 8.0, self.height() - 52.0
        step = (bottom - top) / self.BARS
        lit = round(self._value * self.BARS)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.BARS):  # i = 0 is the top bar
            filled = (self.BARS - i) <= lit
            p.setBrush(theme.c("accent") if filled else theme.alpha("muted", 0.28))
            y = top + i * step
            p.drawRoundedRect(QRectF((self.width() - 32) / 2, y, 32, max(3.0, step * 0.34)), 2, 2)
        color = theme.c("accent") if self._value > 0 else theme.alpha("muted", 0.6)
        p.drawPixmap(int((self.width() - 30) / 2), self.height() - 40, icons.pixmap("flame", color, 30))
