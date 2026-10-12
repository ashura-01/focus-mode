"""Small always-on-top timer shown while the main window is in the background."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from focus_mode.ui.theme import theme


class MiniTimer(QWidget):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(170, 64)
        self._time, self._caption, self._progress = "00:00", "FOCUS", 0.0
        self._drag: QPoint | None = None
        self.setToolTip("Double-click to open Focus Mode. Drag to move.")

    def update_state(self, time_text: str, caption: str, progress: float) -> None:
        self._time, self._caption, self._progress = time_text, caption, progress
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.setPen(QPen(theme.alpha("accent", 0.6), 1.5))
        p.setBrush(theme.c("panel"))
        p.drawRoundedRect(rect, 16, 16)
        # progress underline
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("accent"))
        p.drawRoundedRect(QRectF(16, self.height() - 12, (self.width() - 32) * self._progress, 3), 1.5, 1.5)
        font = QFont(self.font())
        font.setPixelSize(26)
        font.setWeight(QFont.Weight.Medium)
        p.setFont(font)
        p.setPen(theme.c("text"))
        p.drawText(QRectF(0, 6, self.width(), 34), Qt.AlignmentFlag.AlignCenter, self._time)
        font.setPixelSize(10)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        p.setFont(font)
        p.setPen(theme.c("muted"))
        p.drawText(QRectF(0, 38, self.width(), 14), Qt.AlignmentFlag.AlignCenter, self._caption)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, _event) -> None:
        self.clicked.emit()
