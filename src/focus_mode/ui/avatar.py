"""The knight and his speech bubble, plus the header that hosts them."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QSizePolicy, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from focus_mode import paths
from focus_mode.services import Services
from focus_mode.ui.theme import theme
from focus_mode.ui.widgets import caps, label

TAIL = 14
VISIBLE_FRACTION = 0.8  # share of the knight's height shown in the header (0.5 = waist up)
BOB_PIXELS = 6.0  # total up/down travel of the "breathing" motion
BREATH_SCALE = 0.012  # how much the knight swells on each breath (1.2%)


class KnightWidget(QWidget):
    """The portrait, faded into the background and gently 'breathing'."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._src = QPixmap(str(paths.asset("knight.png")))
        self._bob = 0.0
        self._phase = 0.0
        self._cache: QPixmap | None = None
        self._cache_key: tuple | None = None
        # Breathing runs at ~25 fps (smooth enough, much lighter than 60) and only while the knight
        # is actually on screen in an active window, so an idle or minimised app costs no CPU.
        self._t0 = time.monotonic()
        self._clock = QTimer(self)
        self._clock.setInterval(40)
        self._clock.timeout.connect(self._tick)
        theme.changed.connect(self.sync_animation)
        QGuiApplication.instance().applicationStateChanged.connect(self.sync_animation)
        self.sync_animation()

    def width_for_height(self, height: int, fraction: float = 0.8) -> int:
        """Width at which `fraction` of the knight's height fits in `height` pixels."""
        return round(height / fraction * self._src.width() / self._src.height())

    def sync_animation(self, *_args) -> None:
        active = QGuiApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        if theme.animations and self.isVisible() and active:
            if not self._clock.isActive():
                self._clock.start()
        else:
            self._clock.stop()
            if not theme.animations and self._bob:
                self._bob = self._phase = 0.0
                self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.sync_animation()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._clock.stop()

    def _tick(self) -> None:
        self._on_value(((time.monotonic() - self._t0) / 5.2) % 1.0)

    def _on_value(self, v: float) -> None:
        # Eased with a cosine so it slows at both ends like a real breath.
        self._phase = (1 - math.cos(2 * math.pi * v)) / 2  # 0..1
        self._bob = BOB_PIXELS * self._phase
        self.update()

    def _render(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        w, h = int(self.width() * dpr), int(self.height() * dpr)
        if w < 2 or h < 2:  # mid-resize the widget can be momentarily empty; painting on a null pixmap crashes Qt
            return QPixmap()
        scaled = self._src.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(w, h)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.drawPixmap(0, 0, scaled)
        # The art is already transparent; only fade the cut-off at the bottom edge.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(0, 0, 0, 255))
        grad.setColorAt(0.84, QColor(0, 0, 0, 255))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(out.rect(), grad)
        edge = QLinearGradient(0, 0, w, 0)  # the source art is cropped at its right edge
        edge.setColorAt(0.0, QColor(0, 0, 0, 255))
        edge.setColorAt(0.9, QColor(0, 0, 0, 255))
        edge.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(out.rect(), edge)
        painter.end()
        out.setDevicePixelRatio(dpr)
        self._cache, self._cache_key = out, key
        return out

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # Qt snaps a pure translation to whole pixels, which is what made the old motion "tick".
        # Adding a slight breathing scale (about the knight's base) makes Qt draw the pixmap
        # through its filtered path, so every frame lands at a true sub-pixel position.
        breathe = 1.0 + BREATH_SCALE * self._phase
        p.translate(self.width() / 2, self.height())
        p.scale(breathe, breathe)
        p.translate(-self.width() / 2, -self.height() + self._bob - BOB_PIXELS)
        pix = self._render()
        if not pix.isNull():
            p.drawPixmap(0, 0, pix)


class SpeechBubble(QWidget):
    def __init__(self, width: int = 250, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_w = width
        self._w = width
        self._scale = 1.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 16, 22 + TAIL, 14)
        lay.setSpacing(2)
        self.head = QLabel()
        self.head.setWordWrap(True)
        self.head.setMinimumWidth(0)
        self.head.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.body = label("", None, wrap=True)
        self.body.setWordWrap(True)
        self.body.setMinimumWidth(0)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.mark = QLabel("†")
        for lab in (self.head, self.body, self.mark):
            lay.addWidget(lab)
        self.setMinimumWidth(120)
        self.setMaximumWidth(width)
        self.setFixedWidth(width)
        theme.changed.connect(self._recolor)
        self._recolor()

    def _recolor(self) -> None:
        accent = theme.c("accent").name()
        big, small = round(22 * self._scale), round(19 * self._scale)
        self.head.setStyleSheet(f"font-size: {big}px; font-weight: 600; color: {accent};")
        self.body.setStyleSheet(f"font-size: {small}px;")
        self.mark.setStyleSheet(f"font-size: {big}px; color: {accent};")
        self.update()

    def set_scale(self, scale: float) -> None:
        """Shrink the bubble (text and width) together when the window gets narrow."""
        scale = round(max(0.6, min(1.0, scale)), 2)
        if scale == self._scale:
            return
        self._scale = scale
        self._w = round(self._base_w * scale)
        self.setFixedWidth(self._w)
        self._recolor()
        self._sync_height()

    def _sync_height(self) -> None:
        # Use the layout's real height-for-width calculation so wrapped custom
        # messages resize the bubble instead of being clipped during window resizes.
        layout = self.layout()
        # Activate after text/width changes so QLabel word-wrapping is reflected.
        layout.activate()
        width = max(120, self.width())
        height = layout.totalHeightForWidth(width)
        if height <= 0:
            height = layout.sizeHint().height()
        self.setMinimumHeight(max(64, height))
        self.setMaximumHeight(16777215)
        self.setFixedHeight(max(64, height))
        self.updateGeometry()

    def _apply(self, head: str, body: str) -> None:
        self.head.setText(head)
        self.body.setText(body)
        self._sync_height()
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "place"):
            parent.place()

    def set_message(self, head: str, body: str, animate: bool = True) -> None:
        # No fade: graphics effects on this widget crashed Qt while the window was being resized.
        self._apply(head, body)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(0, 0, self.width() - TAIL, self.height())
        path = QPainterPath()
        path.addRoundedRect(body, 16, 16)
        ty = min(60.0, self.height() * 0.3)
        tail = QPainterPath()
        tail.moveTo(QPointF(body.right() - 1, ty - 12))
        tail.lineTo(QPointF(self.width(), ty))
        tail.lineTo(QPointF(body.right() - 1, ty + 12))
        tail.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("panel2"))
        p.drawPath(path.united(tail))


class AvatarHeader(QWidget):
    """Title + subtitle on the left, speech bubble and knight on the right."""

    def __init__(self, services: Services, subtitle: str = "Stay disciplined. Get results.", compact: bool = False, parent: QWidget | None = None, base_height: int | None = None) -> None:
        super().__init__(parent)
        self.compact = compact
        self._base_height = base_height or (260 if compact else 370)
        self._scale = 1.0
        self._height_scale = 1.0
        self.setFixedHeight(self._base_height)
        self.knight = KnightWidget(self)
        self.title_box = QWidget(self)
        col = QVBoxLayout(self.title_box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        title = caps("Focus Mode", letter_spacing=1.2, pixel=17, weight=500)
        title.setProperty("role", "")  # caps() greys the text; the page title stays white
        col.addWidget(title)
        sub = label(subtitle, "muted")
        col.addWidget(sub)
        self.bubble = SpeechBubble(220 if compact else 250, self)
        self.clock = label("", "muted")
        self.clock.setParent(self)
        self.title_box.adjustSize()
        self.bubble.set_message(*services.current_message, animate=False)
        services.message_changed.connect(self.bubble.set_message)
        self.bubble.raise_()

    def set_clock(self, text: str) -> None:
        self.clock.setText(text)
        self.clock.adjustSize()
        self.place()

    def place(self) -> None:
        w, h = self.width(), self.height()
        kw = self.knight.width_for_height(h, VISIBLE_FRACTION)
        self.knight.setGeometry(w - kw, 0, kw, h)
        self.title_box.move(0, 4)
        self.title_box.adjustSize()

        # Reserve room for the title on narrow windows; never let the bubble
        # extend beyond the header's right edge.
        gap = 12
        available = max(120, w - min(self.title_box.width() + gap, w // 2) - gap)
        desired = min(round(self.bubble._base_w * self._scale), available)
        desired = max(120, desired)
        if self.bubble.width() != desired:
            self.bubble.setFixedWidth(desired)
            self.bubble._w = desired
            self.bubble._sync_height()

        top = round((24 if self.compact else 70) * self._scale)
        bx = max(0, min(w - self.bubble.width(), w - kw - self.bubble.width() + 6))
        if bx < self.title_box.width() + 8:
            top = max(top, self.title_box.height() + 10)

        # Keep the whole bubble visible vertically whenever the header permits it.
        top = min(top, max(0, h - self.bubble.height()))
        self.bubble.move(bx, top)
        self.bubble.setVisible(True)
        self.bubble.raise_()
        self.clock.move(w - self.clock.width() - 4, 8)
        self.clock.raise_()

    def set_height_scale(self, scale: float) -> None:
        """Window height also limits the header, so short windows keep room for the timer."""
        self._height_scale = max(0.6, min(1.0, scale))
        self._apply_scale()

    def _apply_scale(self) -> None:
        # Scale the whole header with the available width/height so small windows aren't swamped by it.
        if getattr(self, "_applying", False):  # setFixedHeight below re-enters via resizeEvent
            return
        self._applying = True
        try:
            scale = max(0.6, min(1.0, self.width() / 720, self._height_scale))
            # Snap to 5% steps so a window drag doesn't rebuild the bubble on every pixel.
            scale = round(scale * 20) / 20
            if abs(scale - self._scale) > 0.001:
                self._scale = scale
                self.setFixedHeight(round(self._base_height * scale))
                self.bubble.set_scale(scale)
            self.place()
        finally:
            self._applying = False

    def resizeEvent(self, _event) -> None:
        self._apply_scale()
