"""Pomodoro timer page (the main screen)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from focus_mode import fmt
from focus_mode.messages import quote_of_the_day
from focus_mode.services import Services
from focus_mode.ui import icons
from focus_mode.ui.avatar import AvatarHeader
from focus_mode.ui.ring import HeatMeter, TimerRing
from focus_mode.ui.theme import BLUE, GOOD, ORANGE, theme
from focus_mode.ui.widgets import NoteBox, QuoteBar, StatRow, button, label

PHASE_NAMES = {"focus": "FOCUS SESSION", "short": "SHORT BREAK", "long": "LONG BREAK"}


class TimerPage(QWidget):
    def __init__(self, svc: Services) -> None:
        super().__init__()
        self.svc, self.pomo = svc, svc.pomo
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(8)

        self.header = AvatarHeader(svc, compact=False, base_height=300)
        root.addWidget(self.header)

        middle = QHBoxLayout()
        spacer = QWidget()
        spacer.setFixedWidth(48)
        self.ring = TimerRing()
        self.meter = HeatMeter()
        middle.addWidget(spacer)
        middle.addWidget(self.ring, 1)
        middle.addWidget(self.meter)
        root.addLayout(middle, 1)

        self.task_label = label("", "accent")
        self.task_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.task_label)
        self.note_box = NoteBox(svc)
        root.addWidget(self.note_box)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        controls.addStretch(1)
        self.reset_btn = button("Reset", "ghost", "reset")
        self.start_btn = button("Start", "primary", "play")
        self.start_btn.setMinimumWidth(150)
        controls.addWidget(self.reset_btn)
        controls.addWidget(self.start_btn)
        controls.addStretch(1)
        root.addLayout(controls)

        self.stats = StatRow(
            "Today's progress",
            [("target", BLUE, "Time Focused"), ("trend", GOOD, "Sessions"), ("flame", ORANGE, "Day Streak")],
        )
        root.addWidget(self.stats)
        self.quote = QuoteBar(quote_of_the_day())
        root.addWidget(self.quote)

        self.start_btn.clicked.connect(self.pomo.toggle)
        self.reset_btn.clicked.connect(self.pomo.reset)
        self.ring.skip_button.clicked.connect(self.pomo.skip)
        self.pomo.tick.connect(self.refresh)
        self.pomo.state_changed.connect(lambda: self.refresh(animate=True))
        svc.sessions_changed.connect(self.refresh_stats)
        svc.tasks_changed.connect(self.refresh_task)
        svc.active_task_changed.connect(self.refresh_task)

        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(15000)
        self._tick_clock()
        self.refresh()
        self.refresh_stats()
        self.refresh_task()

    def on_show(self) -> None:
        self.refresh_stats()
        self.refresh_task()

    def _tick_clock(self) -> None:
        self.header.set_clock(datetime.now().strftime("%I:%M %p").lstrip("0"))

    def refresh(self, animate: bool = False) -> None:
        p = self.pomo
        self.ring.set_texts(f"POMODORO {p.cycle}/{p.cycles}", fmt.mmss(p.remaining()), PHASE_NAMES[p.phase])
        self.ring.set_progress(p.progress(), animate=animate)
        self.meter.set_value(p.progress())
        if p.running:
            self.start_btn.setText("Pause")
            self.start_btn.setIcon(icons.icon("pause", "#ffffff", 18))
        else:
            self.start_btn.setText("Resume" if p.state == "paused" else "Start")
            self.start_btn.setIcon(icons.icon("play", "#ffffff", 18))
        self.start_btn.setIconSize(QSize(18, 18))
        self.reset_btn.setEnabled(p.state != "idle")

    def refresh_stats(self) -> None:
        s = self.svc.db.today_summary()
        self.stats.set_values(fmt.hm(s["seconds"]), str(s["sessions"]), str(s["streak"]))

    def refresh_task(self) -> None:
        task = self.svc.db.get_task(self.svc.active_task_id) if self.svc.active_task_id else None
        self.task_label.setText(f"Working on: {task.title}" if task else "")
        self.task_label.setVisible(task is not None)
