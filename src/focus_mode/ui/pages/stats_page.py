"""Stats page: KPIs, 7-day bars, 12-week heat grid and an optional AI insight."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QInputDialog, QLineEdit, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from focus_mode import ai as ai_mod
from focus_mode import fmt
from focus_mode.services import Services
from focus_mode.ui.avatar import AvatarHeader
from focus_mode.ui.theme import BLUE, GOOD, ORANGE, theme
from focus_mode.ui.widgets import Card, IconLabel, button, caps, label


class WeekBars(QWidget):
    """Seven bars, today highlighted. Hover for exact time."""

    def __init__(self) -> None:
        super().__init__()
        self.data: list[tuple[date, int]] = []
        self._bars: list[QRectF] = []
        self.setMinimumHeight(210)
        self.setMouseTracking(True)
        theme.changed.connect(self.update)

    def set_data(self, data: list[tuple[date, int]]) -> None:
        self.data = data
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, bottom, top = 8.0, self.height() - 26.0, 18.0
        width = self.width() - left - 8
        peak = max((s for _d, s in self.data), default=0)
        scale_max = max(peak, 30 * 60)  # keep an empty chart from looking full
        # light gridlines at 0, half, full
        p.setPen(QPen(theme.alpha("border", 0.8), 1))
        for frac in (0.0, 0.5, 1.0):
            y = bottom - (bottom - top) * frac
            p.drawLine(QPointF(left, y), QPointF(left + width, y))
        n = max(1, len(self.data))
        slot = width / n
        bar_w = min(46.0, slot * 0.52)
        self._bars = []
        font = QFont(self.font())
        font.setPixelSize(12)
        p.setFont(font)
        for i, (day, secs) in enumerate(self.data):
            cx = left + slot * (i + 0.5)
            h = (bottom - top) * secs / scale_max
            rect = QRectF(cx - bar_w / 2, bottom - max(h, 0), bar_w, max(h, 0))
            self._bars.append(QRectF(cx - slot / 2, top, slot, bottom - top + 24))
            is_today = day == date.today()
            if secs > 0:
                col = theme.c("accent") if is_today else theme.alpha("accent", 0.55)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(rect, 6, 6)
                p.setPen(theme.c("text") if is_today else theme.c("muted"))
                p.drawText(QRectF(cx - slot / 2, rect.top() - 20, slot, 16), Qt.AlignmentFlag.AlignCenter, fmt.hm(secs))
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.alpha("muted", 0.25))
                p.drawRoundedRect(QRectF(cx - bar_w / 2, bottom - 3, bar_w, 3), 1.5, 1.5)
            p.setPen(theme.c("text") if is_today else theme.c("muted"))
            p.drawText(QRectF(cx - slot / 2, bottom + 6, slot, 16), Qt.AlignmentFlag.AlignCenter, day.strftime("%a"))

    def mouseMoveEvent(self, event) -> None:
        for (day, secs), rect in zip(self.data, self._bars):
            if rect.contains(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), f"{day.strftime('%A %d %b')}: {fmt.hm(secs)}", self)
                return
        QToolTip.hideText()


class HeatGrid(QWidget):
    """Twelve weeks of days, darker = more focus. Columns are weeks (Mon-Sun)."""

    WEEKS = 12

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[date, int] = {}
        self._cells: list[tuple[QRectF, date]] = []
        self.setMinimumHeight(230)
        self.setMouseTracking(True)
        theme.changed.connect(self.update)

    def set_data(self, data: list[tuple[date, int]]) -> None:
        self.data = dict(data)
        self.update()

    def _level(self, secs: int) -> float:
        if secs <= 0:
            return 0.0
        peak = max(self.data.values(), default=0) or 1
        return 0.25 + 0.75 * min(1.0, secs / peak)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        today = date.today()
        start = today - timedelta(days=today.weekday() + 7 * (self.WEEKS - 1))
        gap = 5.0
        label_w = 34.0
        cell = min((self.width() - label_w) / self.WEEKS - gap, (self.height() - 6) / 7 - gap, 34.0)
        offset = max(0.0, (self.width() - label_w - self.WEEKS * (cell + gap)) / 2)
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        p.setPen(theme.c("muted"))
        for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            p.drawText(QRectF(0, row * (cell + gap), label_w - 6, cell), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
        self._cells = []
        p.setPen(Qt.PenStyle.NoPen)
        for w in range(self.WEEKS):
            for d in range(7):
                day = start + timedelta(days=w * 7 + d)
                if day > today:
                    continue
                rect = QRectF(label_w + offset + w * (cell + gap), d * (cell + gap), cell, cell)
                lvl = self._level(self.data.get(day, 0))
                p.setBrush(theme.alpha("accent", lvl) if lvl else theme.alpha("muted", 0.16))
                p.drawRoundedRect(rect, 4, 4)
                self._cells.append((rect, day))

    def mouseMoveEvent(self, event) -> None:
        for rect, day in self._cells:
            if rect.contains(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), f"{day.strftime('%a %d %b')}: {fmt.hm(self.data.get(day, 0))}", self)
                return
        QToolTip.hideText()


class Kpi(Card):
    def __init__(self, icon: str, color: str, caption: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)
        lay.addWidget(IconLabel(icon, color, 28))
        col = QVBoxLayout()
        col.setSpacing(0)
        self.value = label("0", "value")
        col.addWidget(self.value)
        col.addWidget(label(caption, "muted"))
        lay.addLayout(col, 1)


class StatsPage(QWidget):
    def __init__(self, svc: Services) -> None:
        super().__init__()
        self.svc, self.db = svc, svc.db
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)
        root.addWidget(AvatarHeader(svc, "Numbers don't lie.", compact=True))
        root.addWidget(label("STATS", "title"))

        grid = QGridLayout()
        grid.setSpacing(14)
        self.k_today = Kpi("target", BLUE, "Focused today")
        self.k_week = Kpi("clock", GOOD, "Last 7 days")
        self.k_streak = Kpi("flame", ORANGE, "Day streak")
        self.k_total = Kpi("trend", theme.c("accent").name(), "All-time focus")
        for i, k in enumerate((self.k_today, self.k_week, self.k_streak, self.k_total)):
            grid.addWidget(k, i // 2, i % 2)
        root.addLayout(grid)

        week_card = Card()
        wl = QVBoxLayout(week_card)
        wl.setContentsMargins(24, 20, 24, 18)
        wl.addWidget(caps("Last 7 days"))
        self.bars = WeekBars()
        wl.addWidget(self.bars)
        root.addWidget(week_card)

        heat_card = Card()
        hl = QVBoxLayout(heat_card)
        hl.setContentsMargins(24, 20, 24, 18)
        hl.addWidget(caps("Last 12 weeks"))
        self.heat = HeatGrid()
        hl.addWidget(self.heat)
        root.addWidget(heat_card)

        hi = Card()
        hil = QVBoxLayout(hi)
        hil.setContentsMargins(24, 20, 24, 18)
        hil.addWidget(caps("Highlights"))
        self.highlights = label("", None, wrap=True)
        self.highlights.setTextFormat(Qt.TextFormat.RichText)
        hil.addWidget(self.highlights)
        root.addWidget(hi)

        rec = Card()
        rl = QVBoxLayout(rec)
        rl.setContentsMargins(24, 20, 24, 14)
        rl.addWidget(caps("Recent sessions"))
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        rl.addLayout(self.recent_box)
        root.addWidget(rec)

        ai_card = Card()
        al = QVBoxLayout(ai_card)
        al.setContentsMargins(24, 20, 24, 20)
        top = QHBoxLayout()
        top.addWidget(caps("AI coach"))
        top.addStretch(1)
        self.ai_btn = button("Generate insight", "ghost", "sparkles")
        top.addWidget(self.ai_btn)
        al.addLayout(top)
        self.insight = label("Ask the AI to read your numbers and suggest one next step. Set it up under Settings → AI.", "muted", wrap=True)
        al.addWidget(self.insight)
        root.addWidget(ai_card)
        root.addStretch(1)

        self.ai_btn.clicked.connect(self._insight)
        svc.sessions_changed.connect(self.refresh)
        svc.tasks_changed.connect(self.refresh)
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        week = self.db.focus_by_day(7)
        today = self.db.today_summary()
        totals = self.db.totals()
        self.k_today.value.setText(fmt.hm(today["seconds"]))
        self.k_week.value.setText(fmt.hm(sum(s for _d, s in week)))
        self.k_streak.value.setText(f"{today['streak']} day{'s' if today['streak'] != 1 else ''}")
        self.k_total.value.setText(fmt.hm(totals["seconds"]))
        self.bars.set_data(week)
        self.heat.set_data(self.db.focus_by_day(84))
        self._refresh_recent()

        hour = self.db.best_hour()
        hour_text = "–" if hour is None else f"{hour % 12 or 12}:00 {'AM' if hour < 12 else 'PM'}"
        rate = f"{round(100 * totals['tasks_done'] / totals['tasks_total'])}%" if totals["tasks_total"] else "–"
        muted = theme.c("muted").name()
        rows = [
            ("Sessions", str(totals["sessions"])),
            ("Best day", fmt.hm(totals["best_day"])),
            ("Average per active day", fmt.hm(totals["avg_active_day"])),
            ("Most productive hour", hour_text),
            ("Tasks completed", f"{totals['tasks_done']} of {totals['tasks_total']} ({rate})" if totals["tasks_total"] else "–"),
        ]
        self.highlights.setText(
            "<table cellspacing='8'>"
            + "".join(f"<tr><td style='color:{muted}'>{k}</td><td>&nbsp;&nbsp;</td><td><b>{v}</b></td></tr>" for k, v in rows)
            + "</table>"
        )

    def _refresh_recent(self) -> None:
        while self.recent_box.count():
            item = self.recent_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = self.db.recent_sessions(8)
        if not rows:
            self.recent_box.addWidget(label("No sessions yet. Finish one and it shows up here.", "muted"))
        for r in rows:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 6, 0, 6)
            h.setSpacing(12)
            when = r["started_at"].replace("T", " ")[5:16]
            h.addWidget(label(when, "muted"))
            h.addWidget(label(fmt.hm(r["seconds"])))
            detail = r["note"] or r["task"] or ""
            text = label(detail if detail else "—", None if detail else "muted")
            text.setToolTip(" · ".join(x for x in (r["task"], r["note"]) if x))
            text.setMinimumWidth(40)
            text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            h.addWidget(text, 1)
            edit = button("Note", "ghost")
            edit.clicked.connect(lambda _=False, r=r: self._edit_note(r))
            h.addWidget(edit)
            self.recent_box.addWidget(row)

    def _edit_note(self, r: dict) -> None:
        text, ok = QInputDialog.getText(self.window(), "Session note", "Note for this session:", QLineEdit.EchoMode.Normal, r["note"] or "")
        if ok:
            self.db.set_session_note(r["id"], text.strip())
            self.svc.sessions_changed.emit()

    def _insight(self) -> None:
        snapshot = self.db.stats_snapshot()
        self.ai_btn.setEnabled(False)
        self.insight.setText("Reading your numbers…")

        def done(text: str) -> None:
            self.ai_btn.setEnabled(True)
            self.insight.setText(text)

        def failed(msg: str) -> None:
            self.ai_btn.setEnabled(True)
            self.insight.setText(msg)

        if not self.svc.run_ai(lambda c: ai_mod.write_insight(c, snapshot), done, failed):
            self.ai_btn.setEnabled(True)
            self.insight.setText("AI isn't set up yet. Add your API details under Settings → AI.")
