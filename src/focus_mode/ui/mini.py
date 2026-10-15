"""Small always-on-top HUD shown while the main window is in the background.

Sci-fi panel with a knight medallion on the left, a glowing timer and
large focused-task title in the middle, and a bright right-aligned
session caption on the right. Theme-aware, draggable, and double-clickable.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QWidget

from focus_mode import paths
from focus_mode.ui.theme import theme


# -- Layout --------------------------------------------------------------------

W, H = 520, 140
SCALE = 0.56

AV_C = QPointF(74, H / 2)
AV_R = 50.0
RING_R = 60.0

LEFT = 172
DIV_X = W - 150

DONE_SECONDS = 20
FONT_FAMILIES = [
    "Orbitron",
    "Exo 2",
    "Rajdhani",
    "Segoe UI",
    "Inter",
    "Noto Sans",
]


def _font(
    px: float,
    weight: QFont.Weight = QFont.Weight.Normal,
    spacing: float = 0.0,
) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(max(1, int(px)))
    f.setWeight(weight)

    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)

    return f


def _width(font: QFont, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text)


class MiniTimer(QWidget):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(round(W * SCALE), round(H * SCALE))
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        self._time = "00:00"
        self._caption = "FOCUS SESSION"
        self._progress = 0.0
        self._task = ""
        self._done: tuple[str, str] | None = None
        self._drag: QPoint | None = None

        self._source = QPixmap(str(paths.asset("knight.png")))
        self._head: QPixmap | None = None

        self._expire = QTimer(self)
        self._expire.setSingleShot(True)
        self._expire.timeout.connect(self.clear_done)

        self.setToolTip("Drag to move. Double-click to open Focus Mode.")
        theme.changed.connect(self.update)

    # -- State -----------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self._done is not None

    def update_state(
        self,
        time_text: str,
        caption: str,
        progress: float,
        task: str = "",
    ) -> None:
        self._time = time_text
        self._caption = caption
        self._progress = progress

        task = " ".join(task.split())

        if task != self._task:
            self._task = task
            self.setToolTip(
                (f"Focusing on: {task}\n" if task else "")
                + "Drag to move. Double-click to open Focus Mode."
            )

        self.update()

    def show_done(
        self,
        title: str,
        detail: str,
        expire: bool = False,
    ) -> None:
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

    # -- Painting helpers -------------------------------------------------------

    def _head_pixmap(self) -> QPixmap:
        dpr = self.devicePixelRatioF() * SCALE

        if (
            self._head is not None
            and abs(self._head.devicePixelRatio() - dpr) < 1e-6
        ):
            return self._head

        size = max(1, int(AV_R * 2 * dpr))
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)

        if not self._source.isNull():
            crop = self._source.copy(
                0,
                0,
                self._source.width(),
                max(1, int(self._source.height() * 0.42)),
            )

            pix = crop.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )

            p = QPainter(out)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(
                0,
                0,
                pix,
                (pix.width() - size) // 2,
                max(0, (pix.height() - size) // 5),
                size,
                size,
            )
            p.end()

        out.setDevicePixelRatio(dpr)
        self._head = out
        return out

    @staticmethod
    def _glow_stroke(
        p: QPainter,
        path: QPainterPath,
        color: QColor,
        width: float,
        strength: float = 1.0,
    ) -> None:
        """Draw a neon edge with a soft outer glow."""

        p.setBrush(Qt.BrushStyle.NoBrush)

        for w, a in (
            (width + 7, 0.05),
            (width + 4.5, 0.09),
            (width + 2, 0.16),
        ):
            c = QColor(color)
            c.setAlphaF(a * strength)
            p.setPen(
                QPen(
                    c,
                    w,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            p.drawPath(path)

        p.setPen(
            QPen(
                color,
                width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawPath(path)

    @staticmethod
    def _glow_text(
        p: QPainter,
        x: float,
        base: float,
        text: str,
        font: QFont,
        color: QColor,
        glow: bool,
        glow_color: QColor | None = None,
    ) -> None:
        path = QPainterPath()
        path.addText(x, base, font, text)

        if glow:
            for w, a in ((7, 0.07), (4, 0.12)):
                c = QColor(glow_color or color)
                c.setAlphaF(a)
                p.setPen(
                    QPen(
                        c,
                        w,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    )
                )
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)

    # -- Main painting ----------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.scale(SCALE, SCALE)

        accent = theme.c("accent")
        accent_hi = theme.c("accent_hi")
        dark = theme.c("bg").darker(150)
        muted = theme.c("muted")
        text = theme.c("text")

        done = self._done is not None

        self._paint_frame(p, accent, dark, done)
        self._paint_avatar(p, accent, dark, done)

        if done:
            title, detail = self._done

            self._paint_caption(p, accent, self._caption)

            font = _font(32, QFont.Weight.Black, 0.8)
            room = DIV_X - LEFT - 10

            while _width(font, title) > room and font.pixelSize() > 14:
                font.setPixelSize(font.pixelSize() - 1)

            self._glow_text(p, LEFT, 77, title, font, accent, True)

            self._paint_task_row(
                p, accent, accent_hi, detail, dim=False
            )
            self._paint_bar(p, accent, accent_hi, 1.0, "100%")
            return

        self._paint_caption(p, accent, self._caption)
        self._paint_time(p, accent, text)
        self._paint_task_row(
            p, accent, accent_hi, self._task, dim=not self._task
        )
        self._paint_bar(
            p,
            accent,
            accent_hi,
            self._progress,
            f"{round(max(0.0, min(1.0, self._progress)) * 100)}%",
        )

    def _paint_frame(
        self,
        p: QPainter,
        accent: QColor,
        dark: QColor,
        done: bool,
    ) -> None:
        edge = QColor(accent)
        edge.setAlphaF(0.95 if done else 0.7)

        # Arrow-shaped plate behind the medallion.
        plate = QPolygonF([
            QPointF(6, H / 2),
            QPointF(38, 24),
            QPointF(124, 24),
            QPointF(124, H - 24),
            QPointF(38, H - 24),
        ])

        pp = QPainterPath()
        pp.addPolygon(plate)
        pp.closeSubpath()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dark.lighter(125))
        p.drawPath(pp)

        lp = QPainterPath(QPointF(38, H - 24))
        lp.lineTo(6, H / 2)
        lp.lineTo(38, 24)
        self._glow_stroke(p, lp, edge, 2.0)

        # Main body.
        body = QPainterPath()
        body.moveTo(100, 16)
        body.lineTo(W - 18, 16)
        body.lineTo(W - 6, 34)
        body.lineTo(W - 6, H - 34)
        body.lineTo(W - 26, H - 12)
        body.lineTo(120, H - 12)
        body.lineTo(100, H - 16)
        body.closeSubpath()

        grad = QLinearGradient(0, 0, W, H)
        grad.setColorAt(0.0, dark.lighter(135))
        grad.setColorAt(0.55, dark)
        grad.setColorAt(1.0, dark.lighter(120))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(body)

        self._glow_stroke(
            p,
            body,
            theme.alpha("accent", 0.45 if not done else 0.85),
            1.2,
            0.8,
        )

        # Inner hairline frame.
        inner = QPainterPath()
        inner.moveTo(124, 22)
        inner.lineTo(W - 22, 22)
        inner.lineTo(W - 13, 33)
        inner.lineTo(W - 13, H - 38)
        inner.lineTo(W - 31, H - 19)
        inner.lineTo(124, H - 19)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.alpha("accent", 0.14), 1))
        p.drawPath(inner)

        # Decorative slanted stripes.
        p.setPen(Qt.PenStyle.NoPen)

        for i, a in enumerate((1.0, 0.75, 0.4, 0.16)):
            x = W - 96 + i * 13
            stripe = QPolygonF([
                QPointF(x + 5, 20),
                QPointF(x + 15, 20),
                QPointF(x + 10, 28),
                QPointF(x, 28),
            ])
            p.setBrush(theme.alpha("accent_hi", a))
            p.drawPolygon(stripe)

    def _paint_avatar(
        self,
        p: QPainter,
        accent: QColor,
        dark: QColor,
        done: bool,
    ) -> None:
        p.setPen(QPen(theme.alpha("accent", 0.35), 1))
        p.setBrush(dark.darker(130))
        p.drawEllipse(AV_C, RING_R + 6, RING_R + 6)

        circle = QPainterPath()
        circle.addEllipse(AV_C, AV_R, AV_R)
        p.fillPath(circle, theme.c("panel2"))

        p.save()
        p.setClipPath(circle)
        p.drawPixmap(
            QPointF(AV_C.x() - AV_R, AV_C.y() - AV_R),
            self._head_pixmap(),
        )
        p.restore()

        p.setPen(QPen(theme.alpha("accent", 0.5), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(AV_C, AV_R + 1.5, AV_R + 1.5)

        rect = QRectF(
            AV_C.x() - RING_R,
            AV_C.y() - RING_R,
            RING_R * 2,
            RING_R * 2,
        )

        arcs = (
            ((0, 358),)
            if done
            else ((70, 62), (152, 20), (190, 80), (292, 56))
        )

        for start, span in arcs:
            path = QPainterPath()
            path.arcMoveTo(rect, start)
            path.arcTo(rect, start, span)
            self._glow_stroke(
                p, path, accent, 3.2, 1.0 if done else 0.8
            )

        p.setPen(QPen(theme.alpha("accent", 0.22), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(AV_C, RING_R - 5, RING_R - 5)

    def _paint_caption(
        self,
        p: QPainter,
        accent: QColor,
        caption: str,
    ) -> None:
        """Bright, right-aligned session caption in the former motto column."""

        x0 = DIV_X + 10
        x1 = W - 24
        width = x1 - x0

        # Bright theme accent with a subtle glow.
        color = QColor(accent)
        color.setAlpha(255)

        font = _font(16, QFont.Weight.Bold, 1.0)
        shown = caption.upper().strip() or "FOCUS SESSION"

        # Keep long captions within the right-hand column.
        while _width(font, shown) > width and font.pixelSize() > 8:
            font.setPixelSize(font.pixelSize() - 1)

        shown = QFontMetricsF(font).elidedText(
            shown,
            Qt.TextElideMode.ElideRight,
            width,
        )

        # Small accent rule above the caption.
        p.setPen(QPen(theme.alpha("accent", 0.55), 1))
        p.drawLine(
            QPointF(x0 + 5, 43),
            QPointF(x1, 43),
        )

        self._glow_text(
            p,
            x1 - _width(font, shown),
            68,
            shown,
            font,
            color,
            True,
            accent,
        )

    def _paint_time(
        self,
        p: QPainter,
        accent: QColor,
        text: QColor,
    ) -> None:
        head, sep, tail = self._time.rpartition(":")
        room = DIV_X - LEFT - 14

        # Slightly smaller timer.
        font = _font(46, QFont.Weight.Black, 0.5)

        while _width(font, self._time) > room and font.pixelSize() > 16:
            font.setPixelSize(font.pixelSize() - 1)

        x = float(LEFT)

        if sep:
            self._glow_text(p, x, 76, head, font, text, False)
            x += _width(font, head)

            self._glow_text(p, x, 76, ":", font, accent, True)
            x += _width(font, ":")

        whitish = QColor(accent)
        whitish = QColor(
            *(
                round(c + (255 - c) * 0.55)
                for c in (
                    whitish.red(),
                    whitish.green(),
                    whitish.blue(),
                )
            )
        )

        self._glow_text(
            p, x, 76, tail, font, whitish, True, accent
        )

    def _paint_task_row(
        self,
        p: QPainter,
        accent: QColor,
        accent_hi: QColor,
        label: str,
        dim: bool,
    ) -> None:
        dot = QPointF(LEFT + 6, 99)
        color = theme.alpha("accent", 0.35) if dim else accent

        for r, a in ((9, 0.07), (7.5, 0.12)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.alpha("accent", a if not dim else 0))
            p.drawEllipse(dot, r, r)

        p.setBrush(color)
        p.drawEllipse(dot, 5.5, 5.5)

        # Larger focused-task title.
        font = _font(20, QFont.Weight.DemiBold, 0.2)
        shown = label or "No task selected"
        room = DIV_X - (LEFT + 22) - 10

        while _width(font, shown) > room and font.pixelSize() > 11:
            font.setPixelSize(font.pixelSize() - 1)

        shown = QFontMetricsF(font).elidedText(
            shown,
            Qt.TextElideMode.ElideRight,
            room,
        )

        mint = QColor(accent_hi)
        mint = QColor.fromHslF(
            mint.hslHueF(),
            min(1.0, mint.hslSaturationF()),
            min(0.9, mint.lightnessF() + 0.18),
        )

        p.setFont(font)
        p.setPen(
            theme.alpha("muted", 0.8) if dim else mint
        )
        p.drawText(QPointF(LEFT + 22, 104), shown)

    def _paint_bar(
        self,
        p: QPainter,
        accent: QColor,
        accent_hi: QColor,
        progress: float,
        label: str,
    ) -> None:
        x0, x1, y, h = LEFT - 2, W - 70, 115.0, 7.0
        track = QRectF(x0, y, x1 - x0, h)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.alpha("muted", 0.16))
        p.drawRoundedRect(track, h / 2, h / 2)

        frac = max(0.0, min(1.0, progress))

        if frac > 0:
            fill = QRectF(x0, y, max(h, track.width() * frac), h)

            p.setBrush(theme.alpha("accent", 0.14))
            p.drawRoundedRect(fill.adjusted(-2, -3, 2, 3), h, h)

            g = QLinearGradient(fill.left(), 0, fill.right(), 0)
            g.setColorAt(0.0, theme.c("accent_lo"))
            g.setColorAt(1.0, accent_hi)

            p.setBrush(g)
            p.drawRoundedRect(fill, h / 2, h / 2)

            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(fill, h / 2, h / 2)
            p.setClipPath(clip)
            p.setPen(QPen(QColor(0, 0, 0, 38), 1.4))

            sx = fill.left() - h
            while sx < fill.right():
                p.drawLine(
                    QPointF(sx, fill.bottom()),
                    QPointF(sx + h, fill.top()),
                )
                sx += 6

            p.restore()

        font = _font(20, QFont.Weight.Bold, 0.8)
        p.setFont(font)
        pc = QColor(accent)
        pc = QColor(
            *(
                round(c + (255 - c) * 0.65)
                for c in (pc.red(), pc.green(), pc.blue())
            )
        )
        pc.setAlpha(235)
        p.setPen(pc)

        p.drawText(
            QRectF(x1 + 8, y - 6, W - 6 - x1 - 8, h + 12),
            Qt.AlignmentFlag.AlignVCenter
            | Qt.AlignmentFlag.AlignRight,
            label,
        )

    # -- Dragging ---------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        handle = self.windowHandle()

        # Prefer compositor-managed movement where available.
        if handle is not None and handle.startSystemMove():
            return

        self._drag = (
            event.globalPosition().toPoint()
            - self.frameGeometry().topLeft()
        )

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(
                event.globalPosition().toPoint() - self._drag
            )

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, _event) -> None:
        self.clicked.emit()