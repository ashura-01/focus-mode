"""Tasks page: list, filters, add/edit dialog, and optional AI prioritising."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QDate, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from focus_mode import ai as ai_mod
from focus_mode import fmt
from focus_mode.db import PRIORITIES, Task
from focus_mode.messages import quote_of_the_day
from focus_mode.services import Services
from focus_mode.ui import icons
from focus_mode.ui.avatar import AvatarHeader
from focus_mode.ui.theme import BLUE, GOOD, theme
from focus_mode.ui.widgets import (
    Card,
    ElideLabel,
    IconLabel,
    PriorityChip,
    QuoteBar,
    RoundCheck,
    StatRow,
    Tabs,
    button,
    label,
)


class TaskRow(QFrame):
    toggled = Signal(int, bool)
    action = Signal(int, str)

    def __init__(self, task: Task, active: bool) -> None:
        super().__init__()
        self.task = task
        self.setObjectName("TaskCard")  # every task is its own card (see theme.py)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.signature = self.make_signature(task, active)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 8, 10)
        lay.setSpacing(12)

        self.check = RoundCheck()
        self.check.setChecked(task.done)
        self.check.toggled.connect(lambda on: self.toggled.emit(task.id, on))
        lay.addWidget(self.check)

        col = QVBoxLayout()
        col.setSpacing(1)
        title = ElideLabel(task.title)
        title.setStyleSheet("font-size: 14px;" + (f"color: {theme.c('muted').name()};" if task.done else ""))
        font = title.font()
        font.setStrikeOut(task.done)
        title.setFont(font)
        col.addWidget(title)

        meta = QHBoxLayout()
        meta.setSpacing(5)
        meta.addWidget(IconLabel("clock", "muted", 12))
        meta.addWidget(label(self._when(task), "small"))
        if task.notes.strip():
            first = " ".join(task.notes.split())
            note = ElideLabel("·  ✎ " + first, "small")
            note.setToolTip(task.notes)
            meta.addWidget(note, 1)
        else:
            meta.addStretch(1)
        col.addLayout(meta)
        lay.addLayout(col, 1)

        if active:
            lay.addWidget(label("● focusing", "accent"))
        lay.addWidget(PriorityChip(task.priority))

        more = QPushButton()
        more.setIcon(icons.icon("dots", theme.c("muted"), 16))
        more.setIconSize(QSize(16, 16))
        more.setFlat(True)
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.setFixedSize(28, 28)
        more.setStyleSheet("QPushButton{border:0;background:transparent;padding:0}QPushButton:hover{background:%s;border-radius:8px}" % theme.c("border").name())
        more.clicked.connect(lambda: self._menu(more))
        lay.addWidget(more)

    @staticmethod
    def make_signature(task: Task, active: bool) -> tuple:
        return (task.id, task.title, task.priority, task.done, task.due_date, task.start_time, task.end_time, task.notes, active)

    @staticmethod
    def _when(task: Task) -> str:
        parts = []
        try:
            d = date.fromisoformat(task.due_date)
            today = date.today()
            if d == today:
                pass
            elif (d - today).days == 1:
                parts.append("Tomorrow")
            else:
                parts.append(d.strftime("%a %d %b"))
        except ValueError:
            pass
        if task.start_time and task.end_time:
            parts.append(f"{fmt.clock12(task.start_time)} – {fmt.clock12(task.end_time)}")
        return "  ·  ".join(parts) or "Anytime today"

    def _menu(self, anchor: QWidget) -> None:
        menu = QMenu(self)
        if not self.task.done:
            menu.addAction("Focus on this task").triggered.connect(lambda: self.action.emit(self.task.id, "focus"))
        menu.addAction("Edit").triggered.connect(lambda: self.action.emit(self.task.id, "edit"))
        menu.addSeparator()
        menu.addAction("Delete").triggered.connect(lambda: self.action.emit(self.task.id, "delete"))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class TaskDialog(QDialog):
    def __init__(self, svc: Services, task: Task | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.svc = svc
        self.setWindowTitle("Edit task" if task else "New task")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(12)

        lay.addWidget(label("Edit task" if task else "New task", "title"))
        lay.addWidget(label("What needs doing?", "muted"))
        row = QHBoxLayout()
        self.title = QLineEdit(task.title if task else "")
        self.title.setPlaceholderText("e.g. Study for Data Structures")
        self.suggest = button("Suggest", "ghost", "sparkles")
        self.suggest.setToolTip("Ask the AI for a priority (set up in Settings)")
        row.addWidget(self.title, 1)
        row.addWidget(self.suggest)
        lay.addLayout(row)
        self.hint = label("", "hint", wrap=True)
        lay.addWidget(self.hint)

        lay.addWidget(label("Priority", "muted"))
        prow = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.pbuttons: dict[str, QPushButton] = {}
        for pr in PRIORITIES:
            b = QPushButton(pr.capitalize())
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("variant", "seg")
            self.group.addButton(b)
            self.pbuttons[pr] = b
            prow.addWidget(b)
        self.pbuttons[task.priority if task else "medium"].setChecked(True)
        lay.addLayout(prow)

        lay.addWidget(label("Date", "muted"))
        self.date = QDateEdit(QDate.fromString(task.due_date, Qt.DateFormat.ISODate) if task else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("ddd, d MMM yyyy")
        lay.addWidget(self.date)

        self.use_time = QCheckBox("Add a time range")
        self.use_time.setChecked(bool(task and task.start_time))
        lay.addWidget(self.use_time)
        trow = QHBoxLayout()
        self.start = QTimeEdit(QTime.fromString(task.start_time, "HH:mm") if task and task.start_time else QTime(9, 0))
        self.end = QTimeEdit(QTime.fromString(task.end_time, "HH:mm") if task and task.end_time else QTime(10, 0))
        for t in (self.start, self.end):
            t.setDisplayFormat("h:mm AP")
            t.setEnabled(self.use_time.isChecked())
        trow.addWidget(self.start)
        trow.addWidget(label("to", "muted"))
        trow.addWidget(self.end)
        lay.addLayout(trow)
        self.use_time.toggled.connect(self.start.setEnabled)
        self.use_time.toggled.connect(self.end.setEnabled)

        lay.addWidget(label("Notes", "muted"))
        self.notes = QPlainTextEdit(task.notes if task else "")
        self.notes.setPlaceholderText("Details, links, what \"done\" looks like…")
        self.notes.setFixedHeight(84)
        self.notes.setTabChangesFocus(True)
        lay.addWidget(self.notes)

        self.error = label("", "bad")
        lay.addWidget(self.error)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("Cancel", "ghost")
        self.save = button("Save task", "primary")
        actions.addWidget(cancel)
        actions.addWidget(self.save)
        lay.addLayout(actions)

        cancel.clicked.connect(self.reject)
        self.save.clicked.connect(self._accept)
        self.suggest.clicked.connect(self._suggest)
        self.title.returnPressed.connect(self._accept)

    def _priority(self) -> str:
        return next(p for p, b in self.pbuttons.items() if b.isChecked())

    def _accept(self) -> None:
        if not self.title.text().strip():
            self.error.setText("Give the task a title.")
            return
        if self.use_time.isChecked() and self.end.time() <= self.start.time():
            self.error.setText("The end time must be after the start time.")
            return
        self.accept()

    def values(self) -> dict:
        timed = self.use_time.isChecked()
        return {
            "title": self.title.text().strip(),
            "priority": self._priority(),
            "due": self.date.date().toString(Qt.DateFormat.ISODate),
            "start": self.start.time().toString("HH:mm") if timed else None,
            "end": self.end.time().toString("HH:mm") if timed else None,
            "notes": self.notes.toPlainText().strip(),
        }

    def _suggest(self) -> None:
        title = self.title.text().strip()
        if not title:
            self.hint.setText("Type the task first.")
            return
        due = self.date.date().toString(Qt.DateFormat.ISODate)
        self.suggest.setEnabled(False)
        self.hint.setText("Thinking…")

        def done(result: dict) -> None:
            self.suggest.setEnabled(True)
            self.pbuttons[result["priority"]].setChecked(True)
            mins = f" · about {result['minutes']} min" if result["minutes"] else ""
            self.hint.setText(f"AI: {result['priority']}{mins}. {result['reason']}")

        def failed(msg: str) -> None:
            self.suggest.setEnabled(True)
            self.hint.setText(msg)

        if not self.svc.run_ai(lambda c: ai_mod.suggest_priority(c, title, due), done, failed):
            self.suggest.setEnabled(True)
            self.hint.setText("Add your AI details in Settings first.")


class TasksPage(QWidget):
    def __init__(self, svc: Services) -> None:
        super().__init__()
        self.svc, self.db = svc, svc.db
        self.view = "all"
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 16, 32, 16)
        root.setSpacing(10)
        root.addWidget(AvatarHeader(svc, compact=True, base_height=170))
        self.title_row = QHBoxLayout()
        self.title_row.addWidget(label("TASKS", "title"))
        self.title_row.addStretch(1)
        root.addLayout(self.title_row)

        self.bar = bar = QHBoxLayout()
        self.tabs = Tabs([("all", "All"), ("today", "Today"), ("upcoming", "Upcoming"), ("completed", "Completed")], height=38)
        self.tabs.changed.connect(self._set_view)
        bar.addWidget(self.tabs)
        bar.addStretch(1)
        self.ai_btn = button("AI Prioritize", "ghost", "sparkles")
        self.ai_btn.setToolTip("Let the AI set priorities for your open tasks (set up in Settings)")
        self.add_btn = button("Add Task", "primary", "plus")
        for b in (self.ai_btn, self.add_btn):
            b.setProperty("size", "sm")
        self._compact_bar = False
        bar.addWidget(self.ai_btn)
        bar.addWidget(self.add_btn)
        root.addLayout(bar)

        self.rows_host = QWidget()
        self.rows = QVBoxLayout(self.rows_host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(10)  # gap between task cards
        root.addWidget(self.rows_host)
        self.empty_card = Card()
        empty_lay = QVBoxLayout(self.empty_card)
        empty_lay.setContentsMargins(6, 4, 6, 4)
        self.empty = label("Nothing here yet. Add a task to get started.", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(90)
        empty_lay.addWidget(self.empty)
        root.addWidget(self.empty_card)

        root.addWidget(label("FOCUS SUMMARY", "section"))
        self.summary = StatRow(
            "",
            [("target", theme.c("accent").name(), "Tasks Completed"), ("check-circle", GOOD, "Tasks Remaining"), ("clock", BLUE, "Focus Time Planned")],
            compact=True,
        )
        root.addWidget(self.summary)
        root.addWidget(QuoteBar(quote_of_the_day(), compact=True))
        root.addStretch(1)

        self.add_btn.clicked.connect(self._add)
        self.ai_btn.clicked.connect(self._ai_prioritize)
        # An agent can add many tasks in a burst: coalesce the signals into one rebuild,
        # and don't rebuild at all while this page isn't on screen (on_show catches up).
        self._dirty = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(80)
        self._debounce.timeout.connect(self._flush)
        svc.tasks_changed.connect(self._schedule_refresh)
        svc.active_task_changed.connect(self._schedule_refresh)
        self.refresh()

    def _schedule_refresh(self) -> None:
        self._dirty = True
        if self.isVisible():
            self._debounce.start()

    def _flush(self) -> None:
        if self._dirty and self.isVisible():
            self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Narrow window: move the action buttons up beside the title so the tabs get the whole row.
        # Hysteresis (640/720) stops the layout flip-flopping while the edge is dragged.
        width = self.width()
        compact = self._compact_bar
        if width < 640:
            compact = True
        elif width > 720:
            compact = False
        if compact != self._compact_bar:
            self._compact_bar = compact
            src, dst = (self.bar, self.title_row) if compact else (self.title_row, self.bar)
            self.setUpdatesEnabled(False)
            for btn in (self.ai_btn, self.add_btn):
                src.removeWidget(btn)
                dst.addWidget(btn)
            self.setUpdatesEnabled(True)

    def _set_view(self, key: str) -> None:
        self.view = key
        self.refresh()

    def refresh(self) -> None:
        """Bring the card list in line with the database, touching only what changed.

        An agent can change many tasks in a burst, and the user may be resizing the window at
        the same time, so rebuilding every row each time is wasteful and risky.
        """
        self._dirty = False
        tasks = self.db.list_tasks(self.view)
        active_id = self.svc.active_task_id
        wanted = [(t, TaskRow.make_signature(t, t.id == active_id)) for t in tasks]
        current = [r.signature for r in self._ordered_rows()]
        if current != [sig for _t, sig in wanted]:
            self.rows_host.setUpdatesEnabled(False)
            try:
                for i in reversed(range(self.rows.count())):
                    item = self.rows.takeAt(i)
                    w = item.widget()
                    if w is not None:
                        w.hide()
                        w.deleteLater()
                for task, _sig in wanted:
                    row = TaskRow(task, task.id == active_id)
                    row.toggled.connect(self._toggle)
                    row.action.connect(self._action)
                    self.rows.addWidget(row)
            finally:
                self.rows_host.setUpdatesEnabled(True)
        self.empty_card.setVisible(not tasks)
        self.rows_host.setVisible(bool(tasks))
        s = self.db.task_summary()
        self.summary.set_values(
            f"{s['done']}<span style='font-size:16px;color:{theme.c('muted').name()}'>/{s['total']}</span>",
            str(s["remaining"]),
            fmt.hm(s["planned_seconds"]),
        )

    def _ordered_rows(self) -> list[TaskRow]:
        rows = []
        for i in range(self.rows.count()):
            w = self.rows.itemAt(i).widget()
            if isinstance(w, TaskRow):
                rows.append(w)
        return rows

    # -- actions ------------------------------------------------------------
    def _toggle(self, task_id: int, done: bool) -> None:
        self.db.set_done(task_id, done)
        if done and self.svc.active_task_id == task_id:
            self.svc.set_active_task(None)
        self.svc.tasks_changed.emit()

    def _add(self) -> None:
        dlg = TaskDialog(self.svc, parent=self.window())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.values()
            self.db.add_task(v["title"], v["priority"], v["due"], v["start"], v["end"], notes=v["notes"])
            self.svc.tasks_changed.emit()

    def _action(self, task_id: int, what: str) -> None:
        task = self.db.get_task(task_id)
        if not task:
            return
        if what == "focus":
            self.svc.set_active_task(task_id)
            self.svc.toast.emit("ok", f"Sessions will count toward “{task.title}”.")
        elif what == "delete":
            if self.svc.active_task_id == task_id:
                self.svc.set_active_task(None)
            self.db.delete_task(task_id)
            self.svc.tasks_changed.emit()
        elif what == "edit":
            dlg = TaskDialog(self.svc, task, parent=self.window())
            if dlg.exec() == QDialog.DialogCode.Accepted:
                v = dlg.values()
                self.db.update_task(
                    task_id, title=v["title"], priority=v["priority"], due_date=v["due"], start_time=v["start"], end_time=v["end"], notes=v["notes"]
                )
                self.svc.tasks_changed.emit()

    def _ai_prioritize(self) -> None:
        open_tasks = [t for t in self.db.list_tasks("all") if not t.done]
        if not open_tasks:
            self.svc.toast.emit("info", "No open tasks to prioritise.")
            return
        payload = [
            {"id": t.id, "title": t.title, "due": t.due_date, "start": t.start_time, "end": t.end_time}
            for t in open_tasks
        ]
        self.ai_btn.setEnabled(False)
        self.ai_btn.setText("Thinking…")

        def finish() -> None:
            self.ai_btn.setEnabled(True)
            self.ai_btn.setText("AI Prioritize")

        def done(result: dict) -> None:
            for tid, (priority, _reason) in result.items():
                self.db.update_task(tid, priority=priority)
            finish()
            self.svc.tasks_changed.emit()
            self.svc.toast.emit("ok", f"AI updated {len(result)} task priorities.")

        def failed(msg: str) -> None:
            finish()
            self.svc.toast.emit("error", msg)

        if not self.svc.run_ai(lambda c: ai_mod.prioritize_tasks(c, payload), done, failed):
            finish()
