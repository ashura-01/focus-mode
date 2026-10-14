"""Small always-on-top widget shown while the main window is in the background.

The knight's head sits beside a speech bubble that shows the time. When a session or break
ends the bubble says so. Drag it anywhere; double-click it to open Focus Mode.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from focus_mode import paths
from focus_mode.ui.theme import theme

HEAD = 60  # diameter of the knight's head
TAIL = 10
DONE_SECONDS = 20  # how long "time is up" stays up if the timer keeps running afterwards


class MiniTimer(QWidget):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(256, 80)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._time, self._caption, self._progress = "00:00", "FOCUS", 0.0
        self._done: tuple[str, str] | None = None
        self._drag: QPoint | None = None
        self._source = QPixmap(str(paths.asset("knight.png")))
        self._head: QPixmap | None = None
        self._expire = QTimer(self)
        self._expire.setSingleShot(True)
        self._expire.timeout.connect(self.clear_done)
        self.setToolTip("Drag to move. Double-click to open Focus Mode.")
        theme.changed.connect(self.update)

    # -- state ------------------------------------------------------------------
    @property
    def done(self) -> bool:
        return self._done is not None

    def update_state(self, time_text: str, caption: str, progress: float) -> None:
        self._time, self._caption, self._progress = time_text, caption, progress
        self.update()

    def show_done(self, title: str, detail: str, expire: bool = False) -> None:
        self._done = (title, detail)
        self._expire.stop()
        if expire:
            self._expire.start(DONE_SECONDS * 1000)
        self.update()

    def clear_done(self) -> None:
        self._expire.stop()
        if self._done is not None:
            self._done = None
            self.update()

    # -- painting ---------------------------------------------------------------
    def _head_pixmap(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        if self._head is not None and self._head.devicePixelRatio() == dpr:
            return self._head
        size = int(HEAD * dpr)
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)
        if not self._source.isNull():
            crop = self._source.copy(0, 0, self._source.width(), max(1, int(self._source.height() * 0.42)))
            pix = crop.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            p = QPainter(out)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(0, 0, pix, (pix.width() - size) // 2, max(0, (pix.height() - size) // 5), size, size)
            p.end()
        out.setDevicePixelRatio(dpr)
        self._head = out
        return out

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        h = self.height()

        # knight head
        head = QRectF(2, (h - HEAD) / 2, HEAD, HEAD)
        circle = QPainterPath()
        circle.addEllipse(head)
        p.fillPath(circle, theme.c("panel2"))
        p.save()
        p.setClipPath(circle)
        p.drawPixmap(head.topLeft(), self._head_pixmap())
        p.restore()
        p.setPen(QPen(theme.c("accent") if self._done else theme.c("border"), 2 if self._done else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(head)

        # speech bubble (tail points at the knight)
        left = head.right() + TAIL + 4
        body = QRectF(left, 4, self.width() - left - 3, h - 8)
        path = QPainterPath()
        path.addRoundedRect(body, 16, 16)
        ty = body.center().y()
        tail = QPainterPath()
        tail.moveTo(QPointF(body.left() + 1, ty - 9))
        tail.lineTo(QPointF(body.left() - TAIL, ty))
        tail.lineTo(QPointF(body.left() + 1, ty + 9))
        tail.closeSubpath()
        shape = path.united(tail)
        p.setPen(QPen(theme.alpha("accent", 0.85 if self._done else 0.55), 1.5))
        p.setBrush(theme.c("panel"))
        p.drawPath(shape)

        font = QFont(self.font())
        if self._done:
            title, detail = self._done
            font.setPixelSize(21)
            font.setWeight(QFont.Weight.Bold)
            p.setFont(font)
            p.setPen(theme.c("accent"))
            p.drawText(QRectF(body.left(), body.top() + 8, body.width(), 28), Qt.AlignmentFlag.AlignCenter, title)
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Normal)
            p.setFont(font)
            p.setPen(theme.c("text"))
            p.drawText(QRectF(body.left() + 6, body.top() + 38, body.width() - 12, 20), Qt.AlignmentFlag.AlignCenter, detail)
            return

        font.setPixelSize(28)
        font.setWeight(QFont.Weight.Medium)
        p.setFont(font)
        p.setPen(theme.c("text"))
        p.drawText(QRectF(body.left(), body.top() + 6, body.width(), 34), Qt.AlignmentFlag.AlignCenter, self._time)
        font.setPixelSize(10)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        p.setFont(font)
        p.setPen(theme.c("muted"))
        p.drawText(QRectF(body.left(), body.top() + 40, body.width(), 14), Qt.AlignmentFlag.AlignCenter, self._caption)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("accent"))
        p.drawRoundedRect(QRectF(body.left() + 18, body.bottom() - 9, (body.width() - 36) * max(0.0, min(1.0, self._progress)), 3), 1.5, 1.5)

    # -- dragging ---------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Wayland compositors ignore QWidget.move() for top-level windows; ask the compositor
        # to do the move instead. On X11 (and as a fallback) move the window ourselves.
        handle = self.windowHandle()
        if handle is not None and handle.startSystemMove():
            return
        self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, _event) -> None:
        self.clicked.emit()
