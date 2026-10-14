"""Timer logic: a Pomodoro engine and a stopwatch. No widgets in here.

Time is measured with time.monotonic() deadlines rather than counting QTimer
ticks, so a busy UI thread can't make the clock drift.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from focus_mode.config import Settings

MIN_LOGGED_SECONDS = 60  # a skipped/reset focus session shorter than this isn't recorded


class PomodoroEngine(QObject):
    tick = Signal()  # fires while running, for repainting
    state_changed = Signal()  # start / pause / reset / phase change
    phase_completed = Signal(str, str)  # finished phase, next phase
    session_logged = Signal(str, int, bool)  # kind, seconds, completed

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.phase = "focus"  # focus | short | long
        self.cycle = 1
        self.state = "idle"  # idle | running | paused
        self._elapsed = 0.0
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._on_timer)

    # -- derived values -----------------------------------------------------
    @property
    def cycles(self) -> int:
        return self.settings.get("timer", "cycles")

    @property
    def running(self) -> bool:
        return self.state == "running"

    def duration(self) -> float:
        key = {"focus": "focus_min", "short": "short_min", "long": "long_min"}[self.phase]
        return self.settings.get("timer", key) * 60.0

    def elapsed(self) -> float:
        extra = time.monotonic() - self._t0 if self.running else 0.0
        return self._elapsed + extra

    def remaining(self) -> float:
        return max(0.0, self.duration() - self.elapsed())

    def progress(self) -> float:
        total = self.duration()
        return min(1.0, self.elapsed() / total) if total else 0.0

    # -- controls -----------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self._t0 = time.monotonic()
        self.state = "running"
        self._last_second = None
        self._timer.start()
        self.state_changed.emit()

    def pause(self) -> None:
        if not self.running:
            return
        self._elapsed = self.elapsed()
        self.state = "paused"
        self._timer.stop()
        self.state_changed.emit()

    def toggle(self) -> None:
        self.pause() if self.running else self.start()

    def reset(self) -> None:
        self._log_partial()
        self._stop_clock()
        self.state_changed.emit()

    def skip(self) -> None:
        self._log_partial()
        self._stop_clock()
        self._advance()
        self.state_changed.emit()

    def reload(self) -> None:
        """Settings changed: let the UI re-read the (possibly new) duration."""
        self.cycle = min(self.cycle, self.cycles)
        self.state_changed.emit()

    # -- persistence --------------------------------------------------------
    def snapshot(self) -> dict:
        return {"phase": self.phase, "cycle": self.cycle, "elapsed": round(self.elapsed(), 1)}

    def restore(self, data: dict) -> bool:
        """Put back a saved session as *paused*, so nothing starts counting by surprise."""
        try:
            phase, cycle, elapsed = str(data["phase"]), int(data["cycle"]), float(data["elapsed"])
        except (KeyError, TypeError, ValueError):
            return False
        if phase not in ("focus", "short", "long"):
            return False
        self._timer.stop()
        self.phase = phase
        self.cycle = max(1, min(cycle, self.cycles))
        if not 0 <= elapsed < self.duration() - 1:
            elapsed = 0.0  # the saved time no longer fits (e.g. the focus length was shortened)
        self._elapsed = elapsed
        self.state = "paused" if elapsed >= 1 else "idle"
        self.state_changed.emit()
        return self.state == "paused"

    # -- internals ----------------------------------------------------------
    def _stop_clock(self) -> None:
        self._timer.stop()
        self._elapsed = 0.0
        self.state = "idle"

    def _log_partial(self) -> None:
        if self.phase == "focus" and self.elapsed() >= MIN_LOGGED_SECONDS:
            self.session_logged.emit("focus", int(self.elapsed()), False)

    def _advance(self) -> None:
        if self.phase == "focus":
            self.phase = "long" if self.cycle >= self.cycles else "short"
        else:
            self.cycle = 1 if self.phase == "long" else self.cycle + 1
            self.phase = "focus"

    def _on_timer(self) -> None:
        remaining = self.remaining()
        if remaining <= 0:
            self._complete()
            return
        # The clock shows whole seconds, so repaint once per second rather than five times.
        second = int(remaining)
        if second != getattr(self, "_last_second", None):
            self._last_second = second
            self.tick.emit()

    def _complete(self) -> None:
        finished = self.phase
        if finished == "focus":
            self.session_logged.emit("focus", int(self.duration()), True)
        self._stop_clock()
        self._advance()
        self.phase_completed.emit(finished, self.phase)
        self.state_changed.emit()
        if self.settings.get("timer", "auto_start"):
            self.start()


class StopwatchEngine(QObject):
    tick = Signal()
    state_changed = Signal()
    session_logged = Signal(str, int, bool)

    MIN_SAVE_SECONDS = 10

    def __init__(self) -> None:
        super().__init__()
        self.state = "idle"
        self.laps: list[int] = []  # cumulative ms at each lap
        self._elapsed = 0.0
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.tick)

    @property
    def running(self) -> bool:
        return self.state == "running"

    def elapsed_ms(self) -> int:
        extra = time.monotonic() - self._t0 if self.running else 0.0
        return int((self._elapsed + extra) * 1000)

    def start(self) -> None:
        if self.running:
            return
        self._t0 = time.monotonic()
        self.state = "running"
        self._timer.start()
        self.state_changed.emit()

    def pause(self) -> None:
        if not self.running:
            return
        self._elapsed += time.monotonic() - self._t0
        self.state = "paused"
        self._timer.stop()
        self.state_changed.emit()

    def toggle(self) -> None:
        self.pause() if self.running else self.start()

    def snapshot(self) -> dict:
        return {"elapsed_ms": self.elapsed_ms(), "laps": list(self.laps)}

    def restore(self, data: dict) -> bool:
        try:
            ms = int(data["elapsed_ms"])
            laps = [int(x) for x in data.get("laps", [])]
        except (KeyError, TypeError, ValueError):
            return False
        if ms < 1000:
            return False
        self._timer.stop()
        self._elapsed = ms / 1000.0
        self.laps = laps
        self.state = "paused"
        self.state_changed.emit()
        return True

    def lap(self) -> None:
        if self.state != "idle":
            self.laps.append(self.elapsed_ms())
            self.state_changed.emit()

    def reset(self) -> None:
        """Stop, save the time as a focus session if it was long enough, and clear."""
        seconds = self.elapsed_ms() // 1000
        if seconds >= self.MIN_SAVE_SECONDS:
            self.session_logged.emit("stopwatch", seconds, True)
        self._timer.stop()
        self._elapsed = 0.0
        self.state = "idle"
        self.laps.clear()
        self.state_changed.emit()
