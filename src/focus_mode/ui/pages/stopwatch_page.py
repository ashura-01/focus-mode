"""Stopwatch page: free-running timer with laps. Saved to your stats on Reset."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from focus_mode import fmt
from focus_mode.services import Services
from focus_mode.ui import icons
from focus_mode.ui.avatar import AvatarHeader
from focus_mode.ui.theme import theme
from focus_mode.ui.ring import TimerRing
from focus_mode.ui.widgets import Card, NoteBox, button, caps, label


class StopwatchPage(QWidget):
    def __init__(self, svc: Services) -> None:
        super().__init__()
        self.svc, self.watch = svc, svc.watch
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)
        root.addWidget(AvatarHeader(svc, "Count up. Keep going.", compact=True))

        self.body = body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        body.setSpacing(18)
        left = QVBoxLayout()
        self.ring = TimerRing()
        self.ring.skip_button.setText("Lap")
        self.ring.skip_button.setIcon(icons.icon("flag", theme.c("text"), 18))
        left.addWidget(self.ring, 1)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        controls.addStretch(1)
        self.reset_btn = button("Save && Reset", "ghost", "reset")
        self.start_btn = button("Start", "primary", "play")
        self.start_btn.setMinimumWidth(150)
        controls.addWidget(self.reset_btn)
        controls.addWidget(self.start_btn)
        controls.addStretch(1)
        left.addLayout(controls)
        self.note_box = NoteBox(svc)
        left.addWidget(self.note_box)
        body.addLayout(left, 1)

        self.laps_card = Card()
        self.laps_card.setFixedWidth(250)
        lay = QVBoxLayout(self.laps_card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.addWidget(caps("Laps"))
        self.laps_host = QWidget()
        self.laps_box = QVBoxLayout(self.laps_host)
        self.laps_box.setContentsMargins(0, 4, 0, 0)
        self.laps_box.setSpacing(8)
        self.laps_box.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.laps_host)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(scroll, 1)
        self.note = label("Time is saved to your stats when you press Save & Reset (10 s minimum).", "hint", wrap=True)
        lay.addWidget(self.note)
        body.addWidget(self.laps_card)
        root.addLayout(body, 1)
        self._stacked = False

        self.start_btn.clicked.connect(self.watch.toggle)
        self.reset_btn.clicked.connect(self.watch.reset)
        self.ring.skip_button.clicked.connect(self.watch.lap)
        self.watch.tick.connect(self.refresh)
        self.watch.state_changed.connect(self.refresh)
        self.refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Narrow window: laps go under the stopwatch instead of beside it.
        stacked = self.width() < 700
        if stacked == self._stacked:
            return
        self._stacked = stacked
        if stacked:
            self.body.setDirection(QBoxLayout.Direction.TopToBottom)
            self.laps_card.setMinimumWidth(0)
            self.laps_card.setMaximumWidth(16777215)
            self.laps_card.setMinimumHeight(190)
        else:
            self.body.setDirection(QBoxLayout.Direction.LeftToRight)
            self.laps_card.setMinimumHeight(0)
            self.laps_card.setFixedWidth(250)

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        w = self.watch
        ms = w.elapsed_ms()
        self.ring.set_texts("STOPWATCH", fmt.stopwatch(ms), "RUNNING" if w.running else ("PAUSED" if w.state == "paused" else "READY"), time_scale=0.115)
        self.ring.set_progress((ms % 60000) / 60000.0)
        if w.running:
            self.start_btn.setText("Pause")
            self.start_btn.setIcon(icons.icon("pause", "#ffffff", 18))
        else:
            self.start_btn.setText("Resume" if w.state == "paused" else "Start")
            self.start_btn.setIcon(icons.icon("play", "#ffffff", 18))
        self.start_btn.setIconSize(QSize(18, 18))
        self.reset_btn.setEnabled(w.state != "idle")
        self.ring.skip_button.setEnabled(w.state != "idle")
        self._sync_laps()

    def _sync_laps(self) -> None:
        count = self.laps_box.count() - 1  # last item is the stretch
        if count == len(self.watch.laps):
            return
        while self.laps_box.count() > 1:
            item = self.laps_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        prev = 0
        for i, total in enumerate(self.watch.laps, 1):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(label(f"Lap {i}", "muted"))
            h.addStretch(1)
            h.addWidget(label(f"+{fmt.stopwatch(total - prev)}", "small"))
            h.addWidget(label(fmt.stopwatch(total)))
            self.laps_box.insertWidget(0, row)  # newest first
            prev = total
