"""Everything the chat agent is allowed to do inside the app.

Each tool takes validated arguments and returns a small JSON string. Anything
unexpected comes back as {"error": ...} so the model can correct itself. Not
exposed on purpose: the API key and AI endpoint settings, and "delete all data".
Tools run on the UI thread (the executor in agent.py hops there for us).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from focus_mode import fmt
from focus_mode.db import PRIORITIES
from focus_mode.memory import Memory, MemoryUnavailable, build_documents, keyword_search
from focus_mode.ui.theme import ACCENTS, BASE_LABELS

TABS = ("timer", "stopwatch", "tasks", "stats", "settings")
MAX_RESULT_CHARS = 3500

# settings the agent may change: friendly name -> (section, key, kind)
SETTING_KEYS: dict[str, tuple[str, str, str]] = {
    "focus_min": ("timer", "focus_min", "int"),
    "short_break_min": ("timer", "short_min", "int"),
    "long_break_min": ("timer", "long_min", "int"),
    "sessions_before_long_break": ("timer", "cycles", "int"),
    "auto_start_next": ("timer", "auto_start", "bool"),
    "accent_color": ("appearance", "accent", "color"),
    "style": ("appearance", "base", "base"),
    "animations": ("appearance", "animations", "bool"),
    "sound": ("alerts", "sound", "bool"),
    "notifications": ("alerts", "notifications", "bool"),
    "mini_timer": ("alerts", "mini_timer", "bool"),
    "knight_messages": ("messages", "enabled", "bool"),
    "message_interval_sec": ("messages", "interval_sec", "int"),
    "ai_knight_messages": ("messages", "use_ai", "bool"),
    "memory_mode": ("memory", "mode", "memory"),
    "your_name": ("profile", "name", "str"),
}


class Deferred:
    """A tool result whose slow part must run on the worker thread (no SQLite / widgets in it)."""

    def __init__(self, fn: Callable[[], object]) -> None:
        self.fn = fn


def _fn(name: str, description: str, props: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []},
        },
    }


_STR, _INT, _BOOL = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
TOOL_SPECS: list[dict] = [
    _fn("get_app_state", "Current tab, timer and stopwatch state, today's progress, active task and key settings."),
    _fn("navigate", "Switch the app to a tab.", {"tab": {"type": "string", "enum": list(TABS)}}, ["tab"]),
    _fn("timer_control", "Control the Pomodoro timer.", {"action": {"type": "string", "enum": ["start", "pause", "resume", "reset", "skip"]}}, ["action"]),
    _fn("stopwatch_control", "Control the stopwatch. 'reset' saves the time to stats.", {"action": {"type": "string", "enum": ["start", "pause", "resume", "lap", "reset"]}}, ["action"]),
    _fn(
        "set_settings",
        "Change settings. Keys: " + ", ".join(SETTING_KEYS) + ". accent_color takes a name (" + ", ".join(n for n, _ in ACCENTS) + ") or #hex; style is one of " + ", ".join(BASE_LABELS) + " (lowercase); memory_mode is light or neural. Minutes are integers.",
        {"changes": {"type": "object", "description": "Map of setting name to new value."}},
        ["changes"],
    ),
    _fn("get_settings", "Read the current values of the settings you can change."),
    _fn("set_custom_messages", "Replace the knight's custom focus messages. Empty list restores the defaults. Lines may be 'HEADLINE | message'.", {"lines": {"type": "array", "items": _STR}}, ["lines"]),
    _fn("list_tasks", "List tasks.", {"view": {"type": "string", "enum": ["all", "today", "upcoming", "completed"]}}),
    _fn(
        "add_task",
        "Create a task. due is YYYY-MM-DD (or 'today'/'tomorrow'); start/end are HH:MM 24h.",
        {"title": _STR, "priority": {"type": "string", "enum": list(PRIORITIES)}, "due": _STR, "start": _STR, "end": _STR, "notes": _STR},
        ["title"],
    ),
    _fn(
        "update_task",
        "Edit a task by id (only the fields you pass change).",
        {"id": _INT, "title": _STR, "priority": {"type": "string", "enum": list(PRIORITIES)}, "due": _STR, "start": _STR, "end": _STR, "notes": _STR},
        ["id"],
    ),
    _fn("complete_task", "Mark a task done (or not done).", {"id": _INT, "done": _BOOL}, ["id"]),
    _fn("delete_task", "Delete a task. The user is asked to confirm.", {"id": _INT}, ["id"]),
    _fn("focus_on_task", "Make a task the active one so new sessions count toward it. id 0 clears it.", {"id": _INT}, ["id"]),
    _fn("list_sessions", "Recent focus sessions with notes.", {"limit": _INT, "days": _INT}),
    _fn("set_session_note", "Set the note on a past session (see list_sessions for ids).", {"session_id": _INT, "note": _STR}, ["session_id", "note"]),
    _fn("set_next_session_note", "Set the note that will be saved with the next session that finishes.", {"note": _STR}, ["note"]),
    _fn("get_stats", "Focus statistics for a period, with daily minutes.", {"period": {"type": "string", "enum": ["today", "week", "month", "all"]}}),
    _fn(
        "search_memory",
        "Semantic search over the user's tasks, session notes and daily summaries. Use for 'when did I...', 'what did I struggle with', patterns in notes.",
        {"query": _STR, "kind": {"type": "string", "enum": ["task", "session", "day"]}, "limit": _INT},
        ["query"],
    ),
    _fn(
        "generate_report",
        "Create a PDF progress report. First gather facts with get_stats/search_memory, then pass 3-6 sentences of honest commentary and next steps.",
        {"days": {"type": "integer", "description": "Period length in days (7, 30, 90...). Omit or 0 for all time."}, "commentary": _STR},
    ),
]


def describe(name: str, args: dict) -> str:
    """One-line human text for the chat ('Switching to Stats')."""
    a = args if isinstance(args, dict) else {}
    table = {
        "get_app_state": "Checking the app state",
        "navigate": f"Switching to {a.get('tab', '?')}",
        "timer_control": f"Timer: {a.get('action', '?')}",
        "stopwatch_control": f"Stopwatch: {a.get('action', '?')}",
        "set_settings": "Changing settings: " + ", ".join(f"{k}={v}" for k, v in (a.get("changes") or {}).items()) if isinstance(a.get("changes"), dict) else "Changing settings",
        "get_settings": "Reading settings",
        "set_custom_messages": "Updating knight messages",
        "list_tasks": "Looking at your tasks",
        "add_task": f"Adding task “{a.get('title', '')}”",
        "update_task": f"Editing task #{a.get('id', '?')}",
        "complete_task": f"Completing task #{a.get('id', '?')}",
        "delete_task": f"Deleting task #{a.get('id', '?')}",
        "focus_on_task": "Choosing the active task",
        "list_sessions": "Reading recent sessions",
        "set_session_note": f"Saving a note on session #{a.get('session_id', '?')}",
        "set_next_session_note": "Saving a note for the next session",
        "get_stats": f"Reading your stats ({a.get('period', 'week')})",
        "search_memory": f"Searching memory: “{a.get('query', '')}”",
        "generate_report": "Writing your PDF report",
    }
    return table.get(name, name)


def _json(obj) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[: MAX_RESULT_CHARS - 40] + '... [truncated]"}'
    return text


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def _date(value, today: date) -> str | None:
    if value in (None, ""):
        return None
    v = str(value).strip().lower()
    if v == "today":
        return today.isoformat()
    if v == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValueError(f"Bad date {value!r}; use YYYY-MM-DD, 'today' or 'tomorrow'.") from None


def _time(value) -> str | None:
    if value in (None, ""):
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"Bad time {value!r}; use HH:MM in 24h format.")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


class AppTools:
    """ui must provide: goto_key(key), current_key() and confirm(text) -> bool."""

    def __init__(self, svc, ui) -> None:
        self.svc, self.ui = svc, ui
        self.db, self.settings = svc.db, svc.settings

    def run(self, name: str, args: dict):
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return _err(f"Unknown tool {name!r}.")
        try:
            return fn(**(args if isinstance(args, dict) else {}))
        except TypeError as exc:
            return _err(f"Bad arguments for {name}: {exc}")
        except ValueError as exc:
            return _err(str(exc))

    # -- state ----------------------------------------------------------------
    def _state(self) -> dict:
        p, w, s = self.svc.pomo, self.svc.watch, self.svc
        today = self.db.today_summary()
        task = self.db.get_task(s.active_task_id) if s.active_task_id else None
        return {
            "now": datetime.now().strftime("%A %Y-%m-%d %H:%M"),
            "tab": self.ui.current_key(),
            "timer": {
                "state": p.state,
                "phase": p.phase,
                "cycle": f"{p.cycle}/{p.cycles}",
                "remaining": fmt.mmss(p.remaining()),
            },
            "stopwatch": {"state": w.state, "elapsed": fmt.stopwatch(w.elapsed_ms())[:-3], "laps": len(w.laps)},
            "today": {"focus": fmt.hm(today["seconds"]), "sessions": today["sessions"], "streak_days": today["streak"]},
            "active_task": {"id": task.id, "title": task.title} if task else None,
            "pending_session_note": s.session_note or None,
        }

    def t_get_app_state(self) -> str:
        return _json(self._state())

    def t_navigate(self, tab: str) -> str:
        if tab not in TABS:
            return _err(f"tab must be one of {', '.join(TABS)}")
        self.ui.goto_key(tab)
        return _json({"ok": True, "tab": tab})

    def t_timer_control(self, action: str) -> str:
        p = self.svc.pomo
        if action == "start" or action == "resume":
            p.start()
        elif action == "pause":
            p.pause()
        elif action == "reset":
            p.reset()
        elif action == "skip":
            p.skip()
        else:
            return _err("action must be start, pause, resume, reset or skip")
        return _json({"ok": True, "timer": self._state()["timer"]})

    def t_stopwatch_control(self, action: str) -> str:
        w = self.svc.watch
        if action in ("start", "resume"):
            w.start()
        elif action == "pause":
            w.pause()
        elif action == "lap":
            w.lap()
        elif action == "reset":
            w.reset()
        else:
            return _err("action must be start, pause, resume, lap or reset")
        return _json({"ok": True, "stopwatch": self._state()["stopwatch"]})

    # -- settings ---------------------------------------------------------------
    def _current_settings(self) -> dict:
        return {name: self.settings.get(sec, key) for name, (sec, key, _k) in SETTING_KEYS.items()}

    def t_get_settings(self) -> str:
        return _json(self._current_settings())

    def t_set_settings(self, changes: dict) -> str:
        if not isinstance(changes, dict) or not changes:
            return _err("changes must be an object like {\"focus_min\": 50}")
        applied, rejected = {}, {}
        for name, value in changes.items():
            spec = SETTING_KEYS.get(name)
            if spec is None:
                rejected[name] = "not a changeable setting"
                continue
            section, key, kind = spec
            try:
                value = self._coerce(kind, value)
            except ValueError as exc:
                rejected[name] = str(exc)
                continue
            self.settings.set(section, key, value)
            applied[name] = self.settings.get(section, key)
        return _json({"applied": applied, "rejected": rejected})

    @staticmethod
    def _coerce(kind: str, value):
        if kind == "int":
            try:
                return int(float(value))
            except (TypeError, ValueError):
                raise ValueError("must be a whole number") from None
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if str(value).strip().lower() in ("true", "on", "yes", "1"):
                return True
            if str(value).strip().lower() in ("false", "off", "no", "0"):
                return False
            raise ValueError("must be true or false")
        if kind == "color":
            v = str(value).strip()
            for name, hexc in ACCENTS:
                if v.lower() == name.lower():
                    return hexc
            if re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                return v.lower()
            raise ValueError("use a colour name (" + ", ".join(n for n, _ in ACCENTS) + ") or #rrggbb")
        if kind == "base":
            v = str(value).strip().lower()
            if v in BASE_LABELS:
                return v
            raise ValueError("style must be " + ", ".join(BASE_LABELS))
        if kind == "memory":
            v = str(value).strip().lower()
            if v in ("light", "neural"):
                return v
            raise ValueError("memory_mode must be light or neural")
        return str(value).strip()[:80]

    def t_set_custom_messages(self, lines: list) -> str:
        if not isinstance(lines, list):
            return _err("lines must be a list of strings")
        clean = [str(x).strip()[:140] for x in lines if str(x).strip()][:50]
        self.settings.set("messages", "custom", clean)
        self.svc.rotate_message()
        return _json({"ok": True, "count": len(clean)})

    # -- tasks ----------------------------------------------------------------------
    @staticmethod
    def _task_row(t) -> dict:
        return {
            "id": t.id, "title": t.title, "priority": t.priority, "due": t.due_date,
            "time": f"{t.start_time}-{t.end_time}" if t.start_time else None,
            "done": t.done, "notes": t.notes[:200] or None,
        }

    def t_list_tasks(self, view: str = "all") -> str:
        if view not in ("all", "today", "upcoming", "completed"):
            return _err("view must be all, today, upcoming or completed")
        tasks = self.db.list_tasks(view)
        return _json({"count": len(tasks), "tasks": [self._task_row(t) for t in tasks[:40]]})

    def t_add_task(self, title: str, priority: str = "medium", due=None, start=None, end=None, notes: str = "") -> str:
        today = self.db.today()
        start, end = _time(start), _time(end)
        if bool(start) != bool(end):
            return _err("give both start and end, or neither")
        if start and end and end <= start:
            return _err("end must be after start")
        if priority not in PRIORITIES:
            return _err("priority must be high, medium or low")
        due_s = _date(due, today) or today.isoformat()
        dup = next((t for t in self.db.list_tasks("all") if not t.done and t.due_date == due_s and t.title.strip().lower() == str(title).strip().lower()), None)
        if dup:  # a retry after an error must not pile up copies
            return _json({"ok": True, "duplicate": True, "note": "An identical open task already exists, so nothing was added.", "task": self._task_row(dup)})
        try:
            tid = self.db.add_task(title, priority, due_s, start, end, notes)
        except ValueError as exc:
            return _err(str(exc))
        self.svc.tasks_changed.emit()
        return _json({"ok": True, "task": self._task_row(self.db.get_task(tid))})

    def t_update_task(self, id: int, **fields) -> str:
        task = self.db.get_task(int(id))
        if not task:
            return _err(f"No task with id {id}.")
        changes = {}
        if "title" in fields:
            if not str(fields["title"]).strip():
                return _err("title can't be empty")
            changes["title"] = str(fields["title"]).strip()
        if "priority" in fields:
            if fields["priority"] not in PRIORITIES:
                return _err("priority must be high, medium or low")
            changes["priority"] = fields["priority"]
        if "due" in fields:
            changes["due_date"] = _date(fields["due"], self.db.today()) or task.due_date
        if "start" in fields:
            changes["start_time"] = _time(fields["start"])
        if "end" in fields:
            changes["end_time"] = _time(fields["end"])
        if "notes" in fields:
            changes["notes"] = str(fields["notes"]).strip()
        st, en = changes.get("start_time", task.start_time), changes.get("end_time", task.end_time)
        if st and en and en <= st:
            return _err("end must be after start")
        unknown = set(fields) - {"title", "priority", "due", "start", "end", "notes"}
        self.db.update_task(task.id, **changes)
        self.svc.tasks_changed.emit()
        out = {"ok": True, "task": self._task_row(self.db.get_task(task.id))}
        if unknown:
            out["ignored"] = sorted(unknown)
        return _json(out)

    def t_complete_task(self, id: int, done: bool = True) -> str:
        task = self.db.get_task(int(id))
        if not task:
            return _err(f"No task with id {id}.")
        self.db.set_done(task.id, bool(done))
        if done and self.svc.active_task_id == task.id:
            self.svc.set_active_task(None)
        self.svc.tasks_changed.emit()
        return _json({"ok": True, "task": self._task_row(self.db.get_task(task.id))})

    def t_delete_task(self, id: int) -> str:
        task = self.db.get_task(int(id))
        if not task:
            return _err(f"No task with id {id}.")
        if not self.ui.confirm(f"The assistant wants to delete the task “{task.title}”. Allow?"):
            return _json({"ok": False, "reason": "The user declined."})
        if self.svc.active_task_id == task.id:
            self.svc.set_active_task(None)
        self.db.delete_task(task.id)
        self.svc.tasks_changed.emit()
        return _json({"ok": True, "deleted": task.title})

    def t_focus_on_task(self, id: int) -> str:
        if int(id) == 0:
            self.svc.set_active_task(None)
            return _json({"ok": True, "active_task": None})
        task = self.db.get_task(int(id))
        if not task:
            return _err(f"No task with id {id}.")
        self.svc.set_active_task(task.id)
        return _json({"ok": True, "active_task": task.title})

    # -- sessions & notes ---------------------------------------------------------------------
    def t_list_sessions(self, limit: int = 10, days: int = 0) -> str:
        since = self.db.today() - timedelta(days=int(days)) if days else None
        rows = self.db.recent_sessions(limit=max(1, min(int(limit), 30)), since=since)
        return _json({"sessions": [
            {"id": r["id"], "when": r["started_at"][:16], "minutes": r["seconds"] // 60, "kind": r["kind"],
             "completed": bool(r["completed"]), "task": r["task"], "note": r["note"] or None}
            for r in rows
        ]})

    def t_set_session_note(self, session_id: int, note: str) -> str:
        if not self.db.set_session_note(int(session_id), str(note)[:500]):
            return _err(f"No session with id {session_id}.")
        self.svc.sessions_changed.emit()
        return _json({"ok": True})

    def t_set_next_session_note(self, note: str) -> str:
        self.svc.session_note = str(note).strip()[:500]
        self.svc.pending_note_changed.emit()
        return _json({"ok": True, "note": self.svc.session_note})

    # -- stats ------------------------------------------------------------------------------------
    def t_get_stats(self, period: str = "week") -> str:
        days = {"today": 1, "week": 7, "month": 30}.get(period)
        if period not in ("today", "week", "month", "all"):
            return _err("period must be today, week, month or all")
        totals = self.db.totals()
        if days is None:
            first = self.db.conn.execute("SELECT MIN(substr(started_at,1,10)) FROM sessions").fetchone()[0]
            days = min(365, (self.db.today() - date.fromisoformat(first)).days + 1) if first else 1
        series = self.db.focus_by_day(days)
        total = sum(s for _d, s in series)
        active = [s for _d, s in series if s]
        hours = self.db.focus_by_hour(since=self.db.today() - timedelta(days=days - 1))
        best_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else None
        daily = {d.isoformat(): s // 60 for d, s in series[-31:]}
        return _json({
            "period": period, "days": days, "total_focus": fmt.hm(total), "total_minutes": total // 60,
            "active_days": len(active), "avg_minutes_per_active_day": (sum(active) // len(active) // 60) if active else 0,
            "streak_days": self.db.streak(), "best_hour_of_day": best_hour,
            "all_time": {"focus": fmt.hm(totals["seconds"]), "sessions": totals["sessions"],
                         "tasks_done": totals["tasks_done"], "tasks_total": totals["tasks_total"]},
            "daily_minutes": daily,
        })

    # -- memory -----------------------------------------------------------------------------------------
    def t_search_memory(self, query: str, kind: str | None = None, limit: int = 5) -> Deferred:
        docs = build_documents(self.db)  # SQLite work stays on this (UI) thread
        memory: Memory = self.svc.memory
        n = max(1, min(int(limit), 8))
        kind = kind if kind in ("task", "session", "day") else None

        def work():
            note = None
            try:
                memory.sync(docs)
                hits = memory.search(str(query), n, kind)
            except MemoryUnavailable as exc:
                hits, note = keyword_search(docs, str(query), n, kind), f"Semantic memory unavailable ({exc}); used keyword search."
            out = {"results": [{"date": h["date"], "kind": h["kind"], "text": h["text"][:400], "score": h["score"]} for h in hits]}
            if note:
                out["note"] = note
            if not hits:
                out["note"] = (out.get("note", "") + " No matching entries.").strip()
            return _json(out)

        return Deferred(work)

    # -- report ------------------------------------------------------------------------------------------------
    def t_generate_report(self, days: int = 0, commentary: str = "") -> str:
        from focus_mode.report import build_report, default_folder  # reportlab is slow to import: load on first use
        days_n = int(days) if days else None
        if days_n is not None and not 1 <= days_n <= 3650:
            return _err("days must be between 1 and 3650 (or omit for all time)")
        folder = Path(self.settings.get("profile", "report_folder") or default_folder()).expanduser()
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        out = folder / f"focus-report-{stamp}{'' if days_n is None else f'-{days_n}d'}.pdf"
        try:
            info = build_report(
                self.db, out, days_n, self.settings.get("profile", "name"), str(commentary)[:2500],
                self.settings.get("appearance", "accent"),
            )
        except OSError as exc:
            return _err(f"Couldn't write the report: {exc}")
        self.svc.report_ready.emit(info["path"])
        return _json({"ok": True, **info})
