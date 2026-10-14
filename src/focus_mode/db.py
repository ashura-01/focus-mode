"""SQLite storage: tasks and focus sessions. No Qt in here."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

PRIORITIES = ("high", "medium", "low")
FOCUS_KINDS = ("focus", "stopwatch")  # sessions that count as focused time

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    priority    TEXT NOT NULL DEFAULT 'medium',
    due_date    TEXT NOT NULL,
    start_time  TEXT,
    end_time    TEXT,
    done        INTEGER NOT NULL DEFAULT 0,
    done_at     TEXT,
    created_at  TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    seconds     INTEGER NOT NULL,
    completed   INTEGER NOT NULL DEFAULT 1,
    task_id     INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chat (
    id          INTEGER PRIMARY KEY,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
"""


@dataclass
class Task:
    id: int
    title: str
    priority: str
    due_date: str
    start_time: str | None
    end_time: str | None
    done: bool
    done_at: str | None
    created_at: str
    notes: str = ""

    @property
    def seconds(self) -> int:
        """Planned length from the time range, 0 if none."""
        if not (self.start_time and self.end_time):
            return 0
        s = _minutes(self.start_time)
        e = _minutes(self.end_time)
        return max(0, e - s) * 60


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        priority=row["priority"],
        due_date=row["due_date"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        done=bool(row["done"]),
        done_at=row["done_at"],
        created_at=row["created_at"],
        notes=row["notes"] or "",
    )


class Database:
    def __init__(self, path: str | Path = ":memory:", now: Callable[[], datetime] | None = None) -> None:
        self._now = now or datetime.now
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after v1.0 to existing databases."""
        for table, column in (("tasks", "notes"), ("sessions", "note")):
            cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    def close(self) -> None:
        self.conn.close()

    # -- helpers ------------------------------------------------------------
    def today(self) -> date:
        return self._now().date()

    def _iso(self, dt: datetime) -> str:
        return dt.replace(microsecond=0).isoformat()

    # -- tasks --------------------------------------------------------------
    def add_task(
        self,
        title: str,
        priority: str = "medium",
        due: date | str | None = None,
        start: str | None = None,
        end: str | None = None,
        notes: str = "",
    ) -> int:
        title = title.strip()
        if not title:
            raise ValueError("Task title is empty")
        priority = priority if priority in PRIORITIES else "medium"
        due_s = (due.isoformat() if isinstance(due, date) else due) or self.today().isoformat()
        cur = self.conn.execute(
            "INSERT INTO tasks(title, priority, due_date, start_time, end_time, created_at, notes) "
            "VALUES (?,?,?,?,?,?,?)",
            (title, priority, due_s, start, end, self._iso(self._now()), notes.strip()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_task(self, task_id: int, **fields) -> None:
        allowed = {"title", "priority", "due_date", "start_time", "end_time", "notes"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "priority" in sets and sets["priority"] not in PRIORITIES:
            sets["priority"] = "medium"
        if not sets:
            return
        cols = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*sets.values(), task_id))
        self.conn.commit()

    def set_done(self, task_id: int, done: bool) -> None:
        self.conn.execute(
            "UPDATE tasks SET done = ?, done_at = ? WHERE id = ?",
            (int(done), self._iso(self._now()) if done else None, task_id),
        )
        self.conn.commit()

    def delete_task(self, task_id: int) -> None:
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()

    def get_task(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row) if row else None

    def list_tasks(self, view: str = "all") -> list[Task]:
        today = self.today().isoformat()
        where, args = "1=1", ()
        if view == "today":
            # Today's tasks, plus anything unfinished that slipped from earlier days.
            where, args = "(due_date = ? OR (done = 0 AND due_date < ?))", (today, today)
        elif view == "upcoming":
            where, args = "done = 0 AND due_date > ?", (today,)
        elif view == "completed":
            where = "done = 1"
        rows = self.conn.execute(
            f"SELECT * FROM tasks WHERE {where} "
            "ORDER BY due_date, COALESCE(start_time, '99:99'), id",
            args,
        ).fetchall()
        return [_task(r) for r in rows]

    def task_summary(self) -> dict:
        """Counts for today's view: done / total, remaining, planned seconds."""
        tasks = self.list_tasks("today")
        done = sum(t.done for t in tasks)
        return {
            "total": len(tasks),
            "done": done,
            "remaining": len(tasks) - done,
            "planned_seconds": sum(t.seconds for t in tasks),
        }

    # -- sessions -----------------------------------------------------------
    def add_session(
        self,
        kind: str,
        seconds: int,
        completed: bool = True,
        task_id: int | None = None,
        started_at: datetime | None = None,
        note: str = "",
    ) -> int | None:
        seconds = int(seconds)
        if seconds <= 0:
            return None
        started = started_at or (self._now() - timedelta(seconds=seconds))
        cur = self.conn.execute(
            "INSERT INTO sessions(kind, started_at, seconds, completed, task_id, note) VALUES (?,?,?,?,?,?)",
            (kind, self._iso(started), seconds, int(completed), task_id, note.strip()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def set_session_note(self, session_id: int, note: str) -> bool:
        cur = self.conn.execute("UPDATE sessions SET note = ? WHERE id = ?", (note.strip(), session_id))
        self.conn.commit()
        return cur.rowcount > 0

    def get_session(self, session_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT s.id, s.kind, s.started_at, s.seconds, s.completed, s.note, s.task_id, t.title AS task "
            "FROM sessions s LEFT JOIN tasks t ON t.id = s.task_id WHERE s.id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_session(self, session_id: int, **fields) -> bool:
        allowed = {"kind", "seconds", "completed", "task_id", "note", "started_at"}
        sets, args = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "started_at" and isinstance(value, datetime):
                value = self._iso(value)
            if key == "completed":
                value = int(bool(value))
            if key == "note":
                value = str(value).strip()
            sets.append(f"{key} = ?")
            args.append(value)
        if not sets:
            return False
        cur = self.conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", (*args, session_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_session(self, session_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def recent_sessions(self, limit: int = 10, since: date | None = None) -> list[dict]:
        where, args = "1=1", []
        if since:
            where, args = "substr(s.started_at, 1, 10) >= ?", [since.isoformat()]
        rows = self.conn.execute(
            "SELECT s.id, s.kind, s.started_at, s.seconds, s.completed, s.note, t.title AS task "
            "FROM sessions s LEFT JOIN tasks t ON t.id = s.task_id "
            f"WHERE {where} ORDER BY s.started_at DESC, s.id DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def focus_by_hour(self, since: date | None = None) -> list[int]:
        """Focused seconds for each hour of the day (24 values)."""
        marks = ",".join("?" * len(FOCUS_KINDS))
        extra, args = "", []
        if since:
            extra, args = " AND substr(started_at, 1, 10) >= ?", [since.isoformat()]
        rows = self.conn.execute(
            f"SELECT CAST(substr(started_at, 12, 2) AS INTEGER) AS h, SUM(seconds) AS s FROM sessions "
            f"WHERE kind IN ({marks}){extra} GROUP BY h",
            (*FOCUS_KINDS, *args),
        ).fetchall()
        out = [0] * 24
        for r in rows:
            out[int(r["h"])] = int(r["s"])
        return out

    # -- chat history ---------------------------------------------------------
    def add_chat(self, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO chat(role, content, created_at) VALUES (?,?,?)", (role, content, self._iso(self._now()))
        )
        self.conn.execute("DELETE FROM chat WHERE id NOT IN (SELECT id FROM chat ORDER BY id DESC LIMIT 200)")
        self.conn.commit()

    def recent_chat(self, limit: int = 40) -> list[tuple[str, str]]:
        rows = self.conn.execute("SELECT role, content FROM chat ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [(r["role"], r["content"]) for r in reversed(rows)]

    def clear_chat(self) -> None:
        self.conn.execute("DELETE FROM chat")
        self.conn.commit()

    def focus_by_day(self, days: int) -> list[tuple[date, int]]:
        """Focused seconds per day for the last `days` days, oldest first."""
        today = self.today()
        first = today - timedelta(days=days - 1)
        marks = ",".join("?" * len(FOCUS_KINDS))
        rows = self.conn.execute(
            f"SELECT substr(started_at, 1, 10) AS d, SUM(seconds) AS s FROM sessions "
            f"WHERE kind IN ({marks}) AND d >= ? GROUP BY d",
            (*FOCUS_KINDS, first.isoformat()),
        ).fetchall()
        by_day = {r["d"]: int(r["s"]) for r in rows}
        return [
            (first + timedelta(days=i), by_day.get((first + timedelta(days=i)).isoformat(), 0))
            for i in range(days)
        ]

    def today_summary(self) -> dict:
        today = self.today().isoformat()
        marks = ",".join("?" * len(FOCUS_KINDS))
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(seconds), 0) AS s, COUNT(*) AS n FROM sessions "
            f"WHERE kind IN ({marks}) AND completed = 1 AND substr(started_at, 1, 10) = ?",
            (*FOCUS_KINDS, today),
        ).fetchone()
        # Partial sessions still count as time, just not as a finished session.
        seconds = self.focus_by_day(1)[0][1]
        return {"seconds": seconds, "sessions": int(row["n"]), "streak": self.streak()}

    def streak(self, min_seconds: int = 60) -> int:
        """Consecutive days with focus time, ending today (or yesterday if today is empty)."""
        marks = ",".join("?" * len(FOCUS_KINDS))
        rows = self.conn.execute(
            f"SELECT substr(started_at, 1, 10) AS d FROM sessions WHERE kind IN ({marks}) "
            f"GROUP BY d HAVING SUM(seconds) >= ?",
            (*FOCUS_KINDS, min_seconds),
        ).fetchall()
        active = {r["d"] for r in rows}
        day = self.today()
        if day.isoformat() not in active:
            day -= timedelta(days=1)
        count = 0
        while day.isoformat() in active:
            count += 1
            day -= timedelta(days=1)
        return count

    def totals(self) -> dict:
        marks = ",".join("?" * len(FOCUS_KINDS))
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(seconds),0) AS s, COUNT(*) AS n FROM sessions WHERE kind IN ({marks})",
            FOCUS_KINDS,
        ).fetchone()
        per_day = self.conn.execute(
            f"SELECT substr(started_at,1,10) AS d, SUM(seconds) AS s FROM sessions "
            f"WHERE kind IN ({marks}) GROUP BY d",
            FOCUS_KINDS,
        ).fetchall()
        best = max((int(r["s"]) for r in per_day), default=0)
        avg = int(row["s"]) // len(per_day) if per_day else 0
        tasks_done = self.conn.execute("SELECT COUNT(*) FROM tasks WHERE done = 1").fetchone()[0]
        tasks_all = self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        return {
            "seconds": int(row["s"]),
            "sessions": int(row["n"]),
            "best_day": best,
            "avg_active_day": avg,
            "active_days": len(per_day),
            "tasks_done": int(tasks_done),
            "tasks_total": int(tasks_all),
        }

    def best_hour(self) -> int | None:
        """Hour of day (0-23) with the most focused time, or None with no data."""
        marks = ",".join("?" * len(FOCUS_KINDS))
        row = self.conn.execute(
            f"SELECT CAST(substr(started_at, 12, 2) AS INTEGER) AS h, SUM(seconds) AS s "
            f"FROM sessions WHERE kind IN ({marks}) GROUP BY h ORDER BY s DESC LIMIT 1",
            FOCUS_KINDS,
        ).fetchone()
        return int(row["h"]) if row else None

    def stats_snapshot(self) -> dict:
        """Compact facts for the AI insight prompt."""
        week = self.focus_by_day(7)
        totals = self.totals()
        return {
            "last_7_days_minutes": {d.strftime("%a"): s // 60 for d, s in week},
            "streak_days": self.streak(),
            "total_hours": round(totals["seconds"] / 3600, 1),
            "sessions": totals["sessions"],
            "best_day_minutes": totals["best_day"] // 60,
            "avg_minutes_per_active_day": totals["avg_active_day"] // 60,
            "best_hour_of_day": self.best_hour(),
            "tasks_done": totals["tasks_done"],
            "tasks_total": totals["tasks_total"],
        }

    # -- maintenance --------------------------------------------------------
    def wipe(self) -> None:
        self.conn.execute("DELETE FROM sessions")
        self.conn.execute("DELETE FROM tasks")
        self.conn.execute("DELETE FROM chat")
        self.conn.commit()
        self.conn.execute("VACUUM")
