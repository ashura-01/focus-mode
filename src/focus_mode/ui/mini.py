"""Compact always-on-top HUD shown while the main window is in the background.

A small glass card: the knight sits inside a progress ring, the time ticks over
with a rolling-digit animation, and the focused task sits underneath. Animated
(pop-in / pop-out, smooth ring, breathing glow, sheen, ripples when a session
ends), theme-aware, draggable, double-click to open Focus Mode.

All motion is driven from here and respects the "Animations" setting.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
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
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from focus_mode import paths
from focus_mode.ui.theme import theme


# -- Layout (logical pixels) ----------------------------------------------------

M = 12  # transparent margin around the card, room for the soft shadow
CW, CH = 272, 67  # the card itself
RADIUS = 20.0

AV_CX = M + 33.0  # medallion centre
AV_CY = M + CH / 2
RING_R = 24.0
AV_R = 17.5

TEXT_X = M + 70.0
TEXT_R = M + CW - 16.0

# Jumping equaliser bars on the right.
BARS = 7
BAR_W, BAR_GAP = 4.0, 3.6
BARS_W = BARS * BAR_W + (BARS - 1) * BAR_GAP
BARS_X0 = TEXT_R - BARS_W
BARS_CY = M + 27.0
BARS_H = 34.0  # tallest bar
TEXT_ROOM = BARS_X0 - 14.0 - TEXT_X  # width left for the text column

DONE_SECONDS = 20
FONT_FAMILIES = ["Inter", "SF Pro Display", "Segoe UI", "Noto Sans", "Cantarell", "DejaVu Sans"]


def _font(px: float, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def _width(font: QFont, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text)


def _lighten(c: QColor, amount: float) -> QColor:
    return QColor(*(round(v + (255 - v) * amount) for v in (c.red(), c.green(), c.blue())))


def _alpha(c: QColor, a: float) -> QColor:
    out = QColor(c)
    out.setAlphaF(max(0.0, min(1.0, a)))
    return out


class MiniTimer(QWidget):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(CW + 2 * M, CH + 2 * M)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        # content
        self._time = "00:00"
        self._caption = "FOCUS SESSION"
        self._progress = 0.0
        self._task = ""
        self._done: tuple[str, str] | None = None
        self._drag: QPoint | None = None

        # animated values (0..1 unless noted)
        self._appear = 1.0  # pop-in / pop-out; overshoots a little on the way in
        self._exiting = False
        self._hover = 0.0
        self._shown_progress = 0.0  # eased copy of the real progress
        self._roll = 1.0  # digit roll-over
        self._roll_from = ""
        self._cap_fade = 1.0  # caption cross-fade
        self._cap_from = ""
        self._bump = 1.0  # springy scale when a session ends
        self._sheen = 0.0  # light sweep across the card (0 = off)
        self._done_t0 = 0.0
        self._phase = 0.0  # equaliser clock, advances faster when a session ends
        self._t = time.monotonic()
        self._anims: dict[str, QVariantAnimation] = {}

        self._source = QPixmap(str(paths.asset("knight.png")))
        self._head: QPixmap | None = None

        # Drives the "alive" motion (breathing glow, pulsing dot, colon blink, ripples). Only runs while shown.
        self._clock = QTimer(self)
        self._clock.setInterval(33)
        self._clock.timeout.connect(self._on_clock)

        self._expire = QTimer(self)
        self._expire.setSingleShot(True)
        self._expire.timeout.connect(self.clear_done)

        self.setToolTip("Drag to move. Double-click to open Focus Mode.")
        theme.changed.connect(self._on_theme)

    # -- Public state ------------------------------------------------------------

    @property
    def done(self) -> bool:
        return self._done is not None

    @property
    def shown(self) -> bool:
        """Visible and not on its way out."""
        return self.isVisible() and not self._exiting

    def update_state(self, time_text: str, caption: str, progress: float, task: str = "") -> None:
        progress = max(0.0, min(1.0, progress))

        if time_text != self._time:
            if self.isVisible() and theme.animations and len(time_text) == len(self._time):
                self._roll_from = self._time
                self._tween("_roll", 0.0, 1.0, 260, QEasingCurve.Type.OutCubic)
            self._time = time_text

        if caption != self._caption:
            if self.isVisible() and theme.animations:
                self._cap_from = self._caption
                self._tween("_cap_fade", 0.0, 1.0, 240, QEasingCurve.Type.OutCubic)
                self._tween("_sheen", 0.0, 1.0, 900, QEasingCurve.Type.InOutSine)
            self._caption = caption

        if progress != self._progress:
            self._progress = progress
            self._ease_progress()

        task = " ".join(task.split())
        if task != self._task:
            self._task = task
            self.setToolTip(
                (f"Focusing on: {task}\n" if task else "") + "Drag to move. Double-click to open Focus Mode."
            )

        self.update()

    def show_done(self, title: str, detail: str, expire: bool = False) -> None:
        self._expire.stop()
        first = self._done is None
        self._done = (title, detail)
        self._done_t0 = time.monotonic()

        if expire:
            self._expire.start(DONE_SECONDS * 1000)

        if first and self.isVisible() and theme.animations:
            self._tween("_bump", 0.0, 1.0, 700, QEasingCurve.Type.OutElastic)
            self._tween("_sheen", 0.0, 1.0, 1000, QEasingCurve.Type.InOutSine)
        self._ease_progress()
        self.update()

    def clear_done(self) -> None:
        self._expire.stop()
        if self._done is not None:
            self._done = None
            self._ease_progress()
            self.update()

    # -- Show / hide with motion ---------------------------------------------------

    def pop_in(self) -> None:
        """Show with a springy fade-and-rise (or just show, with animations off)."""
        self._exiting = False
        if not theme.animations:
            self._stop("_appear")
            self._appear = 1.0
            if not self.isVisible():
                self.show()
            self.update()
            return

        if not self.isVisible():
            self._appear = 0.0
            self._shown_progress = self._target_progress()
            self.show()
        self._tween("_appear", self._appear, 1.0, 460, QEasingCurve.Type.OutBack)

    def pop_out(self) -> None:
        """Fade and sink away, then hide."""
        if not self.isVisible() or self._exiting:
            return
        if not theme.animations:
            self.hide()
            return
        self._exiting = True
        self._tween("_appear", self._appear, 0.0, 220, QEasingCurve.Type.InCubic)

    def _appear_finished(self) -> None:
        if self._exiting:
            self._exiting = False
            self.hide()
        elif self.isVisible() and theme.animations:
            self._tween("_sheen", 0.0, 1.0, 900, QEasingCurve.Type.InOutSine)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._shown_progress = self._target_progress()
        self._sync_clock()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._exiting = False
        self._stop("_appear")
        self._clock.stop()

    # -- Animation plumbing ----------------------------------------------------------

    def _tween(self, attr: str, start: float, end: float, ms: int, curve: QEasingCurve.Type) -> None:
        if not theme.animations:
            self._stop(attr)
            setattr(self, attr, end if attr != "_sheen" else 0.0)
            if attr == "_appear":
                self._appear_finished()
            self.update()
            return

        anim = self._anims.get(attr)
        if anim is None:
            anim = QVariantAnimation(self)
            anim.valueChanged.connect(lambda v, a=attr: self._set(a, v))
            if attr == "_appear":
                anim.finished.connect(self._appear_finished)
            self._anims[attr] = anim

        anim.stop()
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setDuration(max(1, ms))
        anim.setEasingCurve(curve)
        anim.start()

    def _stop(self, attr: str) -> None:
        anim = self._anims.get(attr)
        if anim is not None:
            anim.stop()

    def _set(self, attr: str, value) -> None:
        setattr(self, attr, float(value))
        self.update()

    def _target_progress(self) -> float:
        return 1.0 if self._done is not None else self._progress

    def _ease_progress(self) -> None:
        target = self._target_progress()
        # A stopwatch wraps from 100% back to 0%: jump instead of sweeping backwards.
        if not self.isVisible() or not theme.animations or target < self._shown_progress - 0.5:
            self._stop("_shown_progress")
            self._shown_progress = target
            return
        if abs(target - self._shown_progress) > 1e-4:
            self._tween("_shown_progress", self._shown_progress, target, 600, QEasingCurve.Type.OutCubic)

    def _sync_clock(self) -> None:
        if self.isVisible() and theme.animations:
            if not self._clock.isActive():
                self._clock.start()
        else:
            self._clock.stop()

    def _on_clock(self) -> None:
        now = time.monotonic()
        speed = 1.9 if self._done is not None else 1.0
        self._phase += min(0.2, now - self._t) * speed
        self._t = now
        self.update()

    def _on_theme(self) -> None:
        self._head = None
        self._sync_clock()
        self.update()

    # -- Hover ------------------------------------------------------------------------

    def enterEvent(self, e) -> None:
        super().enterEvent(e)
        self._tween("_hover", self._hover, 1.0, 180, QEasingCurve.Type.OutCubic)

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        self._tween("_hover", self._hover, 0.0, 260, QEasingCurve.Type.OutCubic)

    # -- Painting helpers ---------------------------------------------------------------

    def _head_pixmap(self) -> QPixmap:
        dpr = self.devicePixelRatioF()

        if self._head is not None and abs(self._head.devicePixelRatio() - dpr) < 1e-6:
            return self._head

        size = max(1, int(AV_R * 2 * dpr))
        out = QPixmap(size, size)
        out.fill(Qt.GlobalColor.transparent)

        if not self._source.isNull():
            crop = self._source.copy(0, 0, self._source.width(), max(1, int(self._source.height() * 0.42)))
            pix = crop.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            p = QPainter(out)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(0, 0, pix, (pix.width() - size) // 2, max(0, (pix.height() - size) // 5), size, size)
            p.end()

        out.setDevicePixelRatio(dpr)
        self._head = out
        return out

    @staticmethod
    def _glow_stroke(p: QPainter, path: QPainterPath, color: QColor, width: float, strength: float = 1.0) -> None:
        """A crisp line with a soft halo."""
        p.setBrush(Qt.BrushStyle.NoBrush)

        for w, a in ((width + 4, 0.05), (width + 2, 0.10)):
            p.setPen(
                QPen(_alpha(color, a * strength), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            )
            p.drawPath(path)

        p.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(path)

    @staticmethod
    def _swap_text(
        p: QPainter,
        x: float,
        y: float,
        old: str,
        new: str,
        f: float,
        font: QFont,
        color: QColor,
        right: bool = False,
    ) -> None:
        """Draw `new`, or cross-fade from `old` with a small vertical slide while f < 1."""
        p.setFont(font)

        def one(text: str, alpha: float, dy: float) -> None:
            if alpha <= 0.01 or not text:
                return
            p.setPen(_alpha(color, color.alphaF() * alpha))
            px = x - _width(font, text) if right else x
            p.drawText(QPointF(px, y + dy), text)

        if f >= 1.0 or not old:
            one(new, 1.0, 0.0)
        else:
            one(old, 1.0 - f, -4.0 * f)
            one(new, f, 4.0 * (1.0 - f))

    # -- Main painting ---------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        anim = theme.animations
        t = self._t
        breath = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 3.4) if anim else 0.5

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Entrance / exit / bump transform around the card centre.
        card = QRectF(M, M, CW, CH)
        centre = card.center()
        appear = self._appear
        scale = (0.88 + 0.12 * appear) * (0.94 + 0.06 * self._bump) * (1.0 + 0.012 * self._hover)
        p.setOpacity(max(0.0, min(1.0, appear)))
        p.translate(centre.x(), centre.y() + (1.0 - appear) * 14.0 - 1.2 * self._hover)
        p.scale(scale, scale)
        p.translate(-centre.x(), -centre.y())

        accent = theme.c("accent")
        done = self._done is not None

        self._paint_card(p, card, accent, done, breath)
        self._paint_medallion(p, accent, done, breath, t)
        self._paint_bars(p, accent, done)
        self._paint_text(p, accent, done, breath, t)
        self._paint_sheen(p, card)

    def _paint_card(self, p: QPainter, card: QRectF, accent: QColor, done: bool, breath: float) -> None:
        # Soft layered shadow.
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(7):
            grow = i * 1.6
            p.setBrush(QColor(0, 0, 0, round(20 * (1 - i / 7))))
            p.drawRoundedRect(card.adjusted(-grow, -grow + 3, grow, grow + 3), RADIUS + grow, RADIUS + grow)

        body = QPainterPath()
        body.addRoundedRect(card, RADIUS, RADIUS)

        # Glass body: panel on top fading to the window colour; see-through when transparency is on.
        a = 0.80 if theme.transparent else 0.96
        top = _alpha(theme.solid("panel").lighter(112), a)
        bottom = _alpha(theme.solid("bg").darker(108), a)
        grad = QLinearGradient(card.topLeft(), card.bottomLeft())
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        p.setBrush(grad)
        p.drawPath(body)

        # Accent bloom behind the medallion.
        p.save()
        p.setClipPath(body)
        bloom = QRadialGradient(QPointF(AV_CX, AV_CY), 62)
        bloom.setColorAt(0.0, _alpha(accent, 0.10 + 0.03 * breath + (0.06 if done else 0.0)))
        bloom.setColorAt(1.0, _alpha(accent, 0.0))
        p.setBrush(bloom)
        p.drawRect(card)
        # Faint top highlight.
        hi = QLinearGradient(card.topLeft(), QPointF(card.left(), card.top() + 22))
        hi.setColorAt(0.0, QColor(255, 255, 255, 20))
        hi.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(hi)
        p.drawRect(QRectF(card.left(), card.top(), card.width(), 22))
        p.restore()

        # Hairline border that leans toward the accent.
        edge = QLinearGradient(card.topLeft(), card.bottomRight())
        strength = (0.9 if done else 0.5) + 0.25 * self._hover
        edge.setColorAt(0.0, _alpha(accent, min(1.0, strength + 0.1 * breath)))
        edge.setColorAt(0.55, _alpha(theme.c("border"), 0.9))
        edge.setColorAt(1.0, _alpha(accent, 0.25 + 0.2 * self._hover))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(edge, 1.1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS - 0.5, RADIUS - 0.5)

    def _paint_sheen(self, p: QPainter, card: QRectF) -> None:
        s = self._sheen
        if not (0.0 < s < 1.0):
            return
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(card, RADIUS, RADIUS)
        p.setClipPath(clip)

        x = card.left() - 70 + (card.width() + 140) * s
        band = QPolygonF([
            QPointF(x, card.top()),
            QPointF(x + 46, card.top()),
            QPointF(x + 22, card.bottom()),
            QPointF(x - 24, card.bottom()),
        ])
        g = QLinearGradient(x - 24, 0, x + 46, 0)
        g.setColorAt(0.0, QColor(255, 255, 255, 0))
        g.setColorAt(0.5, QColor(255, 255, 255, 46))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawPolygon(band)
        p.restore()

    def _paint_medallion(self, p: QPainter, accent: QColor, done: bool, breath: float, t: float) -> None:
        c = QPointF(AV_CX, AV_CY)
        anim = theme.animations

        # Ripples travelling outwards once a session has ended.
        if done and anim:
            for k in range(3):
                ph = ((t - self._done_t0) * 0.55 + k / 3) % 1.0
                r = RING_R + 2 + ph * 15
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_alpha(accent, 0.38 * (1 - ph) ** 1.5), 1.4))
                p.drawEllipse(c, r, r)

        # Ring track + progress.
        rect = QRectF(c.x() - RING_R, c.y() - RING_R, RING_R * 2, RING_R * 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_alpha(theme.c("muted"), 0.22), 3.0))
        p.drawEllipse(c, RING_R, RING_R)

        frac = max(0.0, min(1.0, self._shown_progress))
        glow = 0.3 + 0.1 * breath if anim else 0.3  # keep the halo faint so the ring stays crisp
        if done:
            glow = 0.45

        if frac > 0.002:
            path = QPainterPath()
            if frac >= 0.998:
                path.addEllipse(rect)
            else:
                path.arcMoveTo(rect, 90)
                path.arcTo(rect, 90, -360.0 * frac)
            self._glow_stroke(p, path, accent, 3.0, glow)

            if frac < 0.998:  # glowing head on the leading edge
                ang = 2 * math.pi * frac
                head = QPointF(c.x() + RING_R * math.sin(ang), c.y() - RING_R * math.cos(ang))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_lighten(accent, 0.6))
                p.drawEllipse(head, 2.0, 2.0)

        # Medallion.
        r = AV_R * (1.0 + 0.018 * breath if anim else 1.0)
        disc = QPainterPath()
        disc.addEllipse(c, r, r)
        p.fillPath(disc, theme.solid("panel2"))

        p.save()
        p.setClipPath(disc)
        p.drawPixmap(QPointF(c.x() - AV_R, c.y() - AV_R), self._head_pixmap())
        p.restore()

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_alpha(accent, 0.45), 1.0))
        p.drawEllipse(c, r + 0.5, r + 0.5)

        if done:
            self._paint_check(p, accent, QPointF(c.x() + 14.5, c.y() + 14.5))

    def _paint_check(self, p: QPainter, accent: QColor, pos: QPointF) -> None:
        s = 0.55 + 0.45 * max(0.0, min(1.3, self._bump))
        p.save()
        p.translate(pos)
        p.scale(s, s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.solid("bg"))
        p.drawEllipse(QPointF(0, 0), 8.6, 8.6)
        p.setBrush(accent)
        p.drawEllipse(QPointF(0, 0), 7.2, 7.2)
        tick = QPainterPath(QPointF(-3.4, 0.2))
        tick.lineTo(-1.0, 2.7)
        tick.lineTo(3.5, -2.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.c("on_accent"), 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(tick)
        p.restore()

    def _paint_bars(self, p: QPainter, accent: QColor, done: bool) -> None:
        """Mirrored bars that bounce up and down from a centre line, like a level meter."""
        anim = theme.animations
        amp = 1.0 if done else 0.78
        top = _lighten(accent, 0.45)
        low = theme.c("accent_lo")
        ph = self._phase

        for i in range(BARS):
            if anim:
                # Two out-of-step sines per bar give a lively but smooth, non-repeating-looking bounce.
                a = math.sin(ph * (3.1 + i * 0.47) + i * 1.7)
                b = math.sin(ph * (5.3 - i * 0.31) + i * 0.9)
                lvl = 0.5 + 0.5 * (0.62 * a + 0.38 * b)
            else:
                lvl = (0.35, 0.6, 0.85, 1.0, 0.8, 0.55, 0.4)[i % 7]
            h = 5.0 + (BARS_H - 5.0) * lvl * amp
            x = BARS_X0 + i * (BAR_W + BAR_GAP)
            rect = QRectF(x, BARS_CY - h / 2, BAR_W, h)

            p.setPen(Qt.PenStyle.NoPen)
            g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            g.setColorAt(0.0, top)
            g.setColorAt(0.5, accent)
            g.setColorAt(1.0, low)
            p.setBrush(g)
            p.drawRoundedRect(rect, BAR_W / 2, BAR_W / 2)

    def _paint_text(self, p: QPainter, accent: QColor, done: bool, breath: float, t: float) -> None:
        anim = theme.animations
        room = TEXT_ROOM
        text = theme.c("text")
        muted = theme.c("muted")

        # Row 1: status dot + caption (left), percentage (right).
        cap_font = _font(9, QFont.Weight.Bold, 1.1)
        pulse = (0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.6))) if (anim and not done) else 1.0
        dot = QPointF(TEXT_X + 3.0, M + 16.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(accent, 0.22 * pulse))
        p.drawEllipse(dot, 4.6 * (0.8 + 0.4 * pulse), 4.6 * (0.8 + 0.4 * pulse))
        p.setBrush(accent)
        p.drawEllipse(dot, 2.4, 2.4)

        cap_new = "DONE" if done else (self._caption.upper().strip() or "FOCUS SESSION")
        cap_old = self._cap_from.upper().strip()
        if done:
            cap_old = ""
        cap_font_fit = QFont(cap_font)
        while _width(cap_font_fit, cap_new) > room - 12 and cap_font_fit.pixelSize() > 7:
            cap_font_fit.setPixelSize(cap_font_fit.pixelSize() - 1)
        self._swap_text(p, TEXT_X + 12.0, M + 20.0, cap_old, cap_new, self._cap_fade, cap_font_fit, _lighten(accent, 0.25))

        if not done:
            pct = f"{round(max(0.0, min(1.0, self._progress)) * 100)}%"
            p.setFont(_font(10, QFont.Weight.DemiBold, 0.3))
            p.setPen(_alpha(muted, 0.95))
            p.drawText(QPointF(TEXT_R - _width(p.font(), pct), M + 58.5), pct)

        # Row 2: the time, or the "time's up" headline.
        if done:
            title, detail = self._done
            font = _font(20, QFont.Weight.Black, 0.2)
            while _width(font, title) > room and font.pixelSize() > 12:
                font.setPixelSize(font.pixelSize() - 1)
            p.setFont(font)
            p.setPen(_lighten(accent, 0.2))
            p.drawText(QPointF(TEXT_X, M + 43.0), title)
            sub = detail
        else:
            self._paint_time(p, accent, text, t)
            sub = self._task

        # Row 3: the focused task (or the finish detail).
        font = _font(11, QFont.Weight.Medium, 0.1)
        shown = sub or "No task selected"
        # The task row sits below the bars, so it only has to leave space for the percentage.
        row_room = (TEXT_R - TEXT_X) - (0 if done else 42)
        shown = QFontMetricsF(font).elidedText(shown, Qt.TextElideMode.ElideRight, row_room)
        p.setFont(font)
        p.setPen(_alpha(text, 0.78) if sub else _alpha(muted, 0.7))
        p.drawText(QPointF(TEXT_X, M + 58.5), shown)

    def _paint_time(self, p: QPainter, accent: QColor, text: QColor, t: float) -> None:
        anim = theme.animations
        room = TEXT_ROOM
        font = _font(25, QFont.Weight.Bold, 0.0)

        # Fixed-width digit cells keep the clock from jiggling and make per-digit rolling possible.
        def cells(f: QFont) -> tuple[float, float]:
            return max(_width(f, str(d)) for d in range(10)), _width(f, ":")

        digit_w, colon_w = cells(font)
        total = lambda s: sum(colon_w if ch == ":" else digit_w for ch in s)

        while total(self._time) > room and font.pixelSize() > 14:
            font.setPixelSize(font.pixelSize() - 1)
            digit_w, colon_w = cells(font)

        base_y = M + 43.0
        last_colon = self._time.rfind(":")
        blink = (0.4 + 0.6 * (0.5 + 0.5 * math.cos(t * 2 * math.pi))) if anim else 1.0

        old = self._roll_from if self._roll < 1.0 and len(self._roll_from) == len(self._time) else ""
        rise = 11.0

        p.setFont(font)
        x = TEXT_X
        for i, ch in enumerate(self._time):
            w = colon_w if ch == ":" else digit_w
            if ch == ":":
                col = _alpha(accent, blink)
            elif last_colon >= 0 and i > last_colon:
                col = _lighten(accent, 0.55)  # seconds pick up the accent
            else:
                col = text

            cx = x + (w - _width(font, ch)) / 2

            if old and old[i] != ch:
                r = self._roll
                p.setPen(_alpha(col, 1.0 - r))
                p.drawText(QPointF(cx, base_y - r * rise), old[i])
                p.setPen(_alpha(col, r))
                p.drawText(QPointF(cx, base_y + (1.0 - r) * rise), ch)
            else:
                p.setPen(col)
                p.drawText(QPointF(cx, base_y), ch)
            x += w

    # -- Dragging ------------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        handle = self.windowHandle()

        # Prefer compositor-managed movement where available.
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
