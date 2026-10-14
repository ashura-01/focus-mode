"""Glue between the engines, the database, settings and the avatar.

Everything the pages share lives on one Services object, so pages never
reach into each other.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from focus_mode import ai as ai_mod
from focus_mode.alerts import Alerts
from focus_mode.config import Settings
from focus_mode.db import Database
from focus_mode import paths
from focus_mode.engine import PomodoroEngine, StopwatchEngine
from focus_mode.memory import Memory
from focus_mode.messages import DEFAULTS, Message, MessagePicker


AI_QUIET_AFTER_CHAT = 30.0  # seconds the avatar's AI lines stay off after the chat agent finishes


class AIJob(QObject):
    """Runs a blocking AI call on a daemon thread and reports back on the UI thread."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self._fn = fn

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            result = self._fn()
        except ai_mod.AIError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # never let a worker thread die silently
            self.failed.emit(f"Unexpected error: {exc}")
        else:
            self.done.emit(result)


class Services(QObject):
    sessions_changed = Signal()
    tasks_changed = Signal()
    active_task_changed = Signal()
    message_changed = Signal(str, str)  # headline, body
    toast = Signal(str, str)  # kind (info|error|ok), text
    session_note_cleared = Signal()
    pending_note_changed = Signal()
    report_ready = Signal(str)  # path of a freshly written PDF
    agent_clear_requested = Signal()

    def __init__(self, settings: Settings, db: Database) -> None:
        super().__init__()
        self.settings = settings
        self.db = db
        self.pomo = PomodoroEngine(settings)
        self.watch = StopwatchEngine()
        self.alerts = Alerts()
        self.picker = MessagePicker()
        self.active_task_id: int | None = None
        self.session_note = ""  # attached to the next session that gets logged
        self.memory = Memory(paths.data_dir() / "chroma", settings.get("memory", "mode"))
        self.context = "idle"
        self.current_message: Message = self.picker.next("idle")
        self._jobs: set[AIJob] = set()
        self._ai_fetched: set[str] = set()
        self._ai_retry_after = 0.0  # monotonic time before which failed AI-message fetches aren't retried
        self._hold_done = False
        self.chat_busy = False  # True while the chat agent is working
        self._chat_quiet_until = 0.0

        self.pomo.session_logged.connect(self._log_session)
        self.watch.session_logged.connect(self._log_session)
        self.pomo.phase_completed.connect(self._phase_completed)
        self.pomo.state_changed.connect(self._refresh_context)
        self.watch.state_changed.connect(self._refresh_context)
        settings.changed.connect(self._settings_changed)

        self._state_file = paths.data_dir() / "timer_state.json"
        self._last_saved = ""
        self.restored: str = ""  # human text if an unfinished session was put back at start-up
        self._restore_state()
        for sig in (self.pomo.state_changed, self.watch.state_changed, self.active_task_changed):
            sig.connect(self.save_state)
        self._saver = QTimer(self)  # while a timer runs, keep the file fresh in case of a crash
        self._saver.timeout.connect(self.save_state)
        self._saver.start(5000)

        self._rotator = QTimer(self)
        self._rotator.timeout.connect(self.rotate_message)
        self._apply_message_settings()

    # -- timer persistence --------------------------------------------------
    def save_state(self) -> None:
        data = {
            "pomo": self.pomo.snapshot(),
            "watch": self.watch.snapshot(),
            "task_id": self.active_task_id,
            "note": self.session_note,
        }
        text = json.dumps(data)
        # elapsed time changes every tick; only touch the disk when something is actually running/changed
        if text == self._last_saved:
            return
        self._last_saved = text
        try:
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self._state_file)
        except OSError:
            pass  # not being able to save must never break the timer

    def _restore_state(self) -> None:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        parts = []
        if isinstance(data.get("pomo"), dict) and self.pomo.restore(data["pomo"]):
            from focus_mode import fmt

            parts.append(f"{fmt.mmss(self.pomo.remaining())} left on your {self.pomo.phase} timer")
        if isinstance(data.get("watch"), dict) and self.watch.restore(data["watch"]):
            parts.append("your stopwatch")
        task_id = data.get("task_id")
        if isinstance(task_id, int) and self.db.get_task(task_id):
            self.active_task_id = task_id
        note = data.get("note")
        if isinstance(note, str):
            self.session_note = note[:500]
        if parts:
            self.restored = "Picked up where you left off: " + " and ".join(parts) + ". Press Start to continue."

    # -- AI -----------------------------------------------------------------
    def ai_client(self) -> ai_mod.AIClient:
        s = self.settings
        return ai_mod.AIClient(s.get("ai", "base_url"), s.get("ai", "api_key"), s.get("ai", "model"))

    def run_ai(self, fn: Callable[[ai_mod.AIClient], object], on_done, on_error=None) -> bool:
        """Run fn(client) off-thread. Returns False (with a toast) if AI isn't set up."""
        client = self.ai_client()
        if not client.configured:
            self.toast.emit("error", "AI isn't set up yet. Add your API details in Settings.")
            return False
        job = AIJob(lambda: fn(client))
        self._jobs.add(job)

        def finish() -> None:
            self._jobs.discard(job)

        job.done.connect(on_done)
        job.failed.connect(on_error or (lambda msg: self.toast.emit("error", msg)))
        job.done.connect(finish)
        job.failed.connect(finish)
        job.start()
        return True

    def run_bg(self, fn: Callable[[], object], on_done, on_error=None) -> None:
        """Run any blocking function off the UI thread (never touch SQLite or widgets inside fn)."""
        job = AIJob(fn)
        self._jobs.add(job)

        def finish() -> None:
            self._jobs.discard(job)

        job.done.connect(on_done)
        job.failed.connect(on_error or (lambda msg: self.toast.emit("error", msg)))
        job.done.connect(finish)
        job.failed.connect(finish)
        job.start()

    # -- avatar messages ----------------------------------------------------
    def set_chat_busy(self, busy: bool) -> None:
        """The chat agent has priority: while it works (and briefly after), the avatar neither
        fetches AI lines nor rotates, so the two never fight over the model or the UI."""
        if busy == self.chat_busy:
            return
        self.chat_busy = busy
        if busy:
            self._rotator.stop()
            return
        self._chat_quiet_until = time.monotonic() + AI_QUIET_AFTER_CHAT
        self._apply_message_settings()  # restarts the rotator; the AI fetch stays gated until the quiet time is over
        QTimer.singleShot(int((AI_QUIET_AFTER_CHAT + 0.5) * 1000), self._maybe_fetch_ai_messages)

    def _ai_paused(self) -> bool:
        return self.chat_busy or time.monotonic() < self._chat_quiet_until

    def _apply_message_settings(self) -> None:
        s = self.settings
        self.picker.custom = list(s.get("messages", "custom"))
        self.picker.use_ai = bool(s.get("messages", "use_ai"))
        if s.get("messages", "enabled"):
            self._rotator.start(int(s.get("messages", "interval_sec")) * 1000)
        else:
            self._rotator.stop()
        self._maybe_fetch_ai_messages()

    def _settings_changed(self, section: str, key: str) -> None:
        if section in ("messages", "ai", "*"):
            self._ai_fetched.clear()
            self._apply_message_settings()
            self.rotate_message()
        if section in ("timer", "*"):
            self.pomo.reload()
        if section in ("memory", "*"):
            self.memory.set_mode(self.settings.get("memory", "mode"))

    def _refresh_context(self) -> None:
        if self.pomo.running:
            ctx = "focus" if self.pomo.phase == "focus" else "break"
        elif self.watch.running:
            ctx = "focus"
        else:
            ctx = "idle"
        if ctx != self.context:
            self.context = ctx
            self._hold_done = False
            self.rotate_message()
            self._maybe_fetch_ai_messages()

    def rotate_message(self) -> None:
        if self._hold_done or self.chat_busy:
            return
        if not self.settings.get("messages", "enabled"):
            self.current_message = Message("FOCUS MODE", "Messages are turned off.")
        else:
            self.current_message = self.picker.next(self.context)
        self.message_changed.emit(*self.current_message)

    def _say_done(self) -> None:
        msg = self.picker.next("done")
        self.current_message = msg
        self._hold_done = True
        self.message_changed.emit(*msg)
        QTimer.singleShot(12000, self._release_done)

    def _release_done(self) -> None:
        self._hold_done = False
        self.rotate_message()

    def _maybe_fetch_ai_messages(self) -> None:
        if not (self.picker.use_ai and self.settings.get("messages", "enabled")):
            return
        if self._ai_paused() or self.context in self._ai_fetched or not self.ai_client().configured:
            return
        if time.monotonic() < self._ai_retry_after:
            return
        self._ai_fetched.add(self.context)
        context = self.context

        def done(lines: object) -> None:
            self.picker.ai[context] = list(lines)  # type: ignore[arg-type]

        def failed(msg: str) -> None:
            self._ai_fetched.discard(context)
            self._ai_retry_after = time.monotonic() + 300  # one toast, then leave it alone for 5 minutes
            self.toast.emit("error", f"AI messages: {msg} Using the built-in lines for now.")

        self.run_ai(lambda c: ai_mod.generate_messages(c, context), done, failed)

    # -- sessions & tasks ---------------------------------------------------
    def set_active_task(self, task_id: int | None) -> None:
        self.active_task_id = task_id
        self.active_task_changed.emit()

    def _log_session(self, kind: str, seconds: int, completed: bool) -> None:
        self.db.add_session(kind, seconds, completed, self.active_task_id, note=self.session_note)
        if self.session_note:
            self.session_note = ""
            self.session_note_cleared.emit()
        self.sessions_changed.emit()

    def _phase_completed(self, finished: str, upcoming: str) -> None:
        if finished == "focus":
            title, body = "Session complete", "Well fought. Take your break."
            sound = "focus_done"
            self._say_done()
        else:
            title, body = "Break over", "Back to the work."
            sound = "break_done"
        if self.settings.get("alerts", "sound"):
            self.alerts.play(sound)
        if self.settings.get("alerts", "notifications"):
            self.alerts.notify(title, body)

    def wipe_data(self) -> None:
        self.db.wipe()
        try:
            self._state_file.unlink()
        except OSError:
            pass
        self._last_saved = ""
        self.memory.wipe()
        self.session_note = ""
        self.session_note_cleared.emit()
        self.active_task_id = None
        self.tasks_changed.emit()
        self.sessions_changed.emit()
        self.active_task_changed.emit()
