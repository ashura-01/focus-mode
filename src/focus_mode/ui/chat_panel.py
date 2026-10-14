"""Collapsible AI chat sidebar (right side of the window)."""

from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPainterPath, QPen, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from focus_mode.agent import Agent
from focus_mode.services import Services
from focus_mode import paths
from focus_mode.ui import icons
from focus_mode.ui.theme import theme
from focus_mode.ui.widgets import IconLabel, button, label

WIDTH = 380
INPUT_MIN, INPUT_MAX = 46, 150
GUTTER_LEFT, GUTTER_RIGHT = 10, 16
SUGGESTIONS = [
    "Show how my week went",
    "Set focus to 50 minutes and start",
    "Add a task: revise notes tomorrow 9-10am",
    "Make a PDF report of the last 30 days",
]


class ChatInput(QPlainTextEdit):
    submitted = Signal()
    width_changed = Signal()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        # Wrapping depends on the viewport width, not just the number of paragraphs.
        if e.oldSize().width() != e.size().width():
            self.width_changed.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(e)


class KnightAvatar(QLabel):
    """Small circular crop of the knight portrait used beside assistant messages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = QPixmap(str(paths.asset("knight.png")))
        self.setFixedSize(30, 30)
        self.setToolTip("Knight AI")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        circle = QPainterPath()
        circle.addEllipse(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setClipPath(circle)
        painter.fillPath(circle, theme.c("panel2"))
        if not self._source.isNull():
            # Use the upper portrait area so the circular icon reads as a helmet/head.
            source = self._source.copy(0, 0, self._source.width(), max(1, int(self._source.height() * 0.42)))
            pix = source.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
            x = (pix.width() - self.width()) // 2
            y = max(0, (pix.height() - self.height()) // 5)
            painter.drawPixmap(0, 0, pix, x, y, self.width(), self.height())
        painter.setClipping(False)
        painter.setPen(theme.c("border"))
        painter.drawEllipse(0.5, 0.5, self.width() - 1, self.height() - 1)


def _make_narrow_tables_readable(markdown: str) -> str:
    """Convert Markdown tables to stacked field/value rows for the narrow chat sidebar.

    QText labels cannot make wide Markdown tables responsive; turning each record into
    a short vertical block avoids horizontal clipping without changing the model output.
    """
    lines = markdown.splitlines()
    out: list[str] = []
    i = 0

    def cells(line: str) -> list[str]:
        return [part.strip() for part in line.strip().strip("|").split("|")]

    while i < len(lines):
        if (i + 1 < len(lines) and "|" in lines[i]
                and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", lines[i + 1])):
            headers = cells(lines[i])
            i += 2
            row_no = 0
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                values = cells(lines[i])
                if len(values) == len(headers):
                    parts = []
                    for header, value in zip(headers, values):
                        if value:
                            parts.append(f"**{header}:** {value}" if header else value)
                    if parts:
                        out.append("\n".join(parts))
                        out.append("")
                i += 1
                row_no += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip()


class Bubble(QFrame):
    def __init__(self, role: str, text: str) -> None:
        super().__init__()
        self.role = role
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.MarkdownText)
        # Markdown tables have a large intrinsic width and QLabel clips their cells in
        # this narrow sidebar. Stack each table row into labelled fields before rendering.
        display_text = _make_narrow_tables_readable(text)
        self.label.setText(display_text)
        self.label.setWordWrap(False)
        natural = self.label.sizeHint().width()
        self.label.setWordWrap(True)
        limit = max(120, WIDTH - 90)
        width = max(40, min(natural + 2, limit))
        self.label.setFixedWidth(width)
        self.label.setMinimumHeight(0)
        self.label.setFixedHeight(max(1, self.label.heightForWidth(width)))
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.label)
        theme.changed.connect(self._style)
        self._style()

    def _style(self) -> None:
        if self.role == "user":
            bg = theme.alpha("accent", 0.22).name(theme.alpha("accent", 0.22).NameFormat.HexArgb)
            self.setStyleSheet(f"Bubble {{ background: {bg}; border-radius: 14px; }}")
        else:
            self.setStyleSheet(f"Bubble {{ background: {theme.c('panel2').name()}; border-radius: 14px; }}")


class StepRow(QWidget):
    """One reasoning step: a theme-coloured bullet joined to its neighbours by a thin line."""

    DOT, LEFT = 4, 8

    def __init__(self, text: str, linked_above: bool) -> None:
        super().__init__()
        self._above, self._below = linked_above, False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(self.LEFT * 2 + 6, 3, 4, 3)
        self.detail = QLabel(text)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.detail)
        theme.changed.connect(self._style)
        self._style()

    def _style(self) -> None:
        self.detail.setStyleSheet(
            "QLabel { color: %s; font-size: 12px; background: transparent; border: 0; }" % theme.c("muted").name()
        )
        self.update()

    def set_linked_below(self, on: bool) -> None:
        self._below = on
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = 3 + self.detail.fontMetrics().height() / 2 + 1  # centre of the first text line
        if self._above:
            p.setPen(QPen(theme.alpha("accent", 0.45), 1.5))
            p.drawLine(QPointF(self.LEFT, 0), QPointF(self.LEFT, cy))
        if self._below:
            p.setPen(QPen(theme.alpha("accent", 0.45), 1.5))
            p.drawLine(QPointF(self.LEFT, cy), QPointF(self.LEFT, self.height()))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.c("accent"))
        p.drawEllipse(QPointF(self.LEFT, cy), self.DOT, self.DOT)


class ChatPanel(QWidget):
    open_settings = Signal()
    collapse_requested = Signal()

    def __init__(self, svc: Services, agent: Agent) -> None:
        super().__init__()
        self.svc, self.agent = svc, agent
        self._open = False
        self._bottom_queued = False
        self._step_count = 0
        self._last_step: StepRow | None = None
        self.setMaximumWidth(0)
        self.setMinimumWidth(0)
        self._anim = QPropertyAnimation(self, b"maximumWidth", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(GUTTER_LEFT, 0, GUTTER_RIGHT, 0)
        self.card = QFrame()
        self.card.setObjectName("Card")
        self.card.setFixedWidth(WIDTH)
        outer.addWidget(self.card)

        col = QVBoxLayout(self.card)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # header
        head = QHBoxLayout()
        head.setContentsMargins(18, 14, 10, 10)
        head.addWidget(IconLabel("sparkles", "accent", 20))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label("Knight AI", "h2"))
        self.status = label("", "small")
        titles.addWidget(self.status)
        head.addLayout(titles, 1)
        self.clear_btn = self._flat("trash", "Clear conversation")
        self.close_btn = self._flat("skip", "Collapse (Ctrl+J)")
        head.addWidget(self.clear_btn)
        head.addWidget(self.close_btn)
        col.addLayout(head)

        # messages
        self.host = QWidget()
        self.box = QVBoxLayout(self.host)
        self.box.setContentsMargins(14, 6, 14, 6)
        self.box.setSpacing(8)
        self.box.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.host)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        col.addWidget(self.scroll, 1)

        self.thinking = label("Thinking…", "small")
        self.thinking.setContentsMargins(20, 0, 0, 4)
        self.thinking.hide()
        col.addWidget(self.thinking)

        # set-up hint and suggestions
        self.setup = QFrame()
        self.setup.setObjectName("Inset")
        sl = QVBoxLayout(self.setup)
        sl.setContentsMargins(14, 12, 14, 12)
        sl.addWidget(label("Add your OpenRouter base URL, API key and a tool-calling model to chat.", "muted", wrap=True))
        go = button("Open Settings", "ghost")
        go.clicked.connect(self.open_settings)
        sl.addWidget(go)
        col.addWidget(self.setup)
        self.chips_host = QWidget()
        cl = QVBoxLayout(self.chips_host)
        cl.setContentsMargins(14, 0, 14, 8)
        cl.setSpacing(6)
        for text in SUGGESTIONS:
            chip = QPushButton(text)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setProperty("variant", "ghost")
            chip.setStyleSheet("text-align: left; padding: 8px 12px; border-radius: 10px;")
            chip.clicked.connect(lambda _=False, t=text: self._send(t))
            cl.addWidget(chip)
        col.addWidget(self.chips_host)

        # input
        row = QHBoxLayout()
        row.setContentsMargins(14, 4, 14, 14)
        row.setSpacing(8)
        self.input = ChatInput()
        self.input.setPlaceholderText("Ask or tell me to do something…")
        self.input.setFixedHeight(INPUT_MIN)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.input.setSizePolicy(self.input.sizePolicy().horizontalPolicy(), self.input.sizePolicy().verticalPolicy())
        self.send_btn = QPushButton()
        self.send_btn.setProperty("variant", "primary")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setFixedSize(46, 46)
        self.send_btn.setStyleSheet("padding: 0; border-radius: 14px;")
        self.send_btn.setIconSize(QSize(20, 20))
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        col.addLayout(row)

        self.send_btn.clicked.connect(self._on_send_clicked)
        self.input.submitted.connect(self._on_send_clicked)
        self.input.textChanged.connect(self._schedule_grow_input)
        self.input.width_changed.connect(self._schedule_grow_input)
        self.clear_btn.clicked.connect(self._clear)
        self.close_btn.clicked.connect(self.collapse_requested)
        agent.reply.connect(self._on_reply)
        agent.step.connect(self._add_step)
        agent.failed.connect(self._on_error)
        agent.busy_changed.connect(self._on_busy)
        svc.report_ready.connect(self._add_report)
        svc.settings.changed.connect(lambda *_: self._refresh_state())
        theme.changed.connect(self._refresh_icons)

        for role, text in svc.db.recent_chat(30):
            self._add_bubble(role, text)
        self._refresh_icons()
        self._refresh_state()

    # -- building blocks ---------------------------------------------------------
    def _flat(self, icon_name: str, tip: str) -> QPushButton:
        b = QPushButton()
        b.setFlat(True)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFixedSize(34, 34)
        b.setProperty("icon_name", icon_name)
        b.setStyleSheet("QPushButton{border:0;background:transparent;padding:0;border-radius:9px}QPushButton:hover{background:%s}" % theme.c("panel2").name())
        return b

    def _refresh_icons(self) -> None:
        for b in (self.clear_btn, self.close_btn):
            b.setIcon(icons.icon(b.property("icon_name"), theme.c("muted"), 18))
            b.setIconSize(QSize(18, 18))
            b.setStyleSheet("QPushButton{border:0;background:transparent;padding:0;border-radius:9px}QPushButton:hover{background:%s}" % theme.c("panel2").name())
        self._on_busy(self.agent.busy)

    def _refresh_state(self) -> None:
        client = self.svc.ai_client()
        ok = client.configured
        self.status.setText(client.model if ok else "not set up")
        self.setup.setVisible(not ok)
        self.input.setEnabled(ok)
        self.send_btn.setEnabled(ok)
        self.chips_host.setVisible(ok and self.box.count() <= 1)

    def _push(self, widget: QWidget, align: Qt.AlignmentFlag, is_step: bool = False) -> None:
        if not is_step:
            self._last_step = None  # a normal message ends the current bullet list
        self.box.insertWidget(self.box.count() - 1, widget, 0, align)
        self.chips_host.hide()
        self._queue_bottom()

    def _to_bottom(self) -> None:
        self._bottom_queued = False
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _queue_bottom(self) -> None:
        """Many agent steps can arrive in a burst: scroll once, after they've all been laid out."""
        if not self._bottom_queued:
            self._bottom_queued = True
            QTimer.singleShot(0, self._to_bottom)

    def _add_bubble(self, role: str, text: str) -> None:
        bubble = Bubble(role, text)
        if role == "assistant":
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(7)
            row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            row.addWidget(KnightAvatar(), 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(bubble, 0, Qt.AlignmentFlag.AlignTop)
            row.addStretch(1)
            self._push(line, Qt.AlignmentFlag.AlignLeft)
        else:
            self._push(bubble, Qt.AlignmentFlag.AlignRight)

    def _add_step(self, text: str) -> None:
        """Render agent/tool progress as a linked bullet list (no extra model calls)."""
        self._step_count += 1
        row = StepRow(text, linked_above=self._last_step is not None)
        if self._last_step is not None:
            self._last_step.set_linked_below(True)
        self._last_step = row
        self._push(row, Qt.AlignmentFlag.AlignLeft, is_step=True)

    def _add_report(self, path: str) -> None:
        box = QFrame()
        box.setObjectName("Inset")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(label("PDF report ready", "h2"))
        lay.addWidget(label(Path(path).name, "small", wrap=True))
        row = QHBoxLayout()
        open_btn = button("Open", "primary", "zap")
        open_btn.setStyleSheet("padding: 7px 14px; border-radius: 10px;")
        folder_btn = button("Show folder", "ghost")
        folder_btn.setStyleSheet("padding: 7px 14px; border-radius: 10px;")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent))))
        row.addWidget(open_btn)
        row.addWidget(folder_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self._push(box, Qt.AlignmentFlag.AlignLeft)

    # -- sending ---------------------------------------------------------------------
    def _on_send_clicked(self) -> None:
        if self.agent.busy:
            self.agent.cancel()
            self._add_step("Stopped.")
            return
        self._send(self.input.toPlainText())

    def _send(self, text: str) -> None:
        text = text.strip()
        if not text or self.agent.busy:
            return
        if self.agent.send(text):
            self._step_count = 0
            self._add_bubble("user", text)
            self.input.clear()

    def _on_reply(self, text: str) -> None:
        self._add_bubble("assistant", text)

    def _on_error(self, text: str) -> None:
        lab = label(text, "bad", wrap=True)
        lab.setContentsMargins(6, 0, 6, 0)
        self._push(lab, Qt.AlignmentFlag.AlignLeft)

    def _on_busy(self, busy: bool) -> None:
        self.thinking.setVisible(busy)
        self.send_btn.setIcon(icons.icon("pause" if busy else "skip", "#ffffff", 20))
        self.send_btn.setToolTip("Stop" if busy else "Send (Enter)")
        self.clear_btn.setEnabled(not busy)
        if busy:
            self._queue_bottom()

    def _clear(self) -> None:
        self.agent.clear()
        while self.box.count() > 1:
            item = self.box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._refresh_state()

    def _schedule_grow_input(self, *_args) -> None:
        # Run after Qt has completed the current text/resize layout pass. This avoids
        # measuring the old viewport width while the frameless window is being resized.
        QTimer.singleShot(0, self._grow_input)

    def _grow_input(self) -> None:
        if not hasattr(self, "input"):
            return
        edit = self.input
        # QPlainTextEdit's own document layout reports its height in *lines*, not pixels, so
        # measure the wrapped text with a throw-away rich-text document of the same width/font.
        probe = QTextDocument()
        probe.setDefaultFont(edit.font())
        probe.setDocumentMargin(edit.document().documentMargin())
        probe.setDefaultTextOption(edit.document().defaultTextOption())
        probe.setTextWidth(max(1, edit.viewport().width()))
        probe.setPlainText(edit.toPlainText() or " ")
        chrome = max(0, edit.height() - edit.viewport().height())  # border + padding
        target = int(round(probe.size().height())) + chrome
        target = max(INPUT_MIN, min(INPUT_MAX, target))
        if edit.height() != target:
            edit.setFixedHeight(target)
        edit.ensureCursorVisible()

    # -- open / close -------------------------------------------------------------------
    def is_animating(self) -> bool:
        return self._anim.state() == QPropertyAnimation.State.Running

    @property
    def is_open(self) -> bool:
        return self._open

    def full_width(self) -> int:
        return WIDTH + GUTTER_LEFT + GUTTER_RIGHT

    def set_open(self, value: bool, animate: bool = True) -> None:
        if value == self._open:
            return
        self._open = value
        self._anim.stop()
        target = self.full_width() if value else 0
        if animate and theme.animations:
            self._anim.setDuration(theme.ms(220))
            self._anim.setStartValue(self.maximumWidth())
            self._anim.setEndValue(target)
            self._anim.start()
        else:  # no animation: one layout pass instead of ~13 while the window may be resizing
            self.setMaximumWidth(target)
        if value:
            self._refresh_state()
            self.input.setFocus()