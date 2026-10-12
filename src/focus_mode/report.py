"""PDF progress report (reportlab). Pure function of the database; no Qt."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from focus_mode import APP_NAME, fmt, paths

_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF")
_font_cache: tuple[str, str, object | None] | None = None


def _fonts() -> tuple[str, str, object | None]:
    """DejaVu Sans when installed (wide glyph coverage), else the built-in Helvetica."""
    global _font_cache
    if _font_cache is None:
        _font_cache = ("Helvetica", "Helvetica-Bold", None)
        for folder in _FONT_DIRS:
            regular, bold = Path(folder) / "DejaVuSans.ttf", Path(folder) / "DejaVuSans-Bold.ttf"
            if regular.exists() and bold.exists():
                try:
                    pdfmetrics.registerFont(TTFont("FM-Regular", str(regular)))
                    pdfmetrics.registerFont(TTFont("FM-Bold", str(bold)))
                    _font_cache = ("FM-Regular", "FM-Bold", pdfmetrics.getFont("FM-Regular").face)
                    break
                except Exception:
                    pass
    return _font_cache


def _safe(text: str) -> str:
    """Replace characters the PDF font can't draw (they'd show as black boxes)."""
    _regular, _bold, face = _fonts()
    out = []
    for ch in str(text):
        if ch in "\n\t":
            out.append(ch)
        elif face is not None:
            out.append(ch if ord(ch) in face.charToGlyph else "?")
        else:
            out.append(ch if ord(ch) < 256 else "?")
    return "".join(out)


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_safe(text)).replace("\n", "<br/>"), style)


def default_folder() -> Path:
    return Path.home() / "Documents" / "FocusMode"


def build_report(
    db,
    out_path: str | Path,
    days: int | None = 30,
    name: str = "",
    commentary: str = "",
    accent: str = "#e0474f",
    today: date | None = None,
) -> dict:
    """Write the PDF and return {path, period, pages_hint, ...}. days=None means all time."""
    today = today or db.today()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    totals = db.totals()
    if days is None:
        first = db.conn.execute("SELECT MIN(substr(started_at,1,10)) FROM sessions").fetchone()[0]
        start = date.fromisoformat(first) if first else today
        days = max(1, (today - start).days + 1)
    start = today - timedelta(days=days - 1)
    series = db.focus_by_day(days)
    sessions = [s for s in db.recent_sessions(limit=5000, since=start) if s["kind"] in ("focus", "stopwatch")]
    total_s = sum(s for _d, s in series)
    active = [s for _d, s in series if s > 0]
    best_day = max(series, key=lambda x: x[1]) if series else (today, 0)
    done_tasks = [t for t in db.list_tasks("completed") if t.done_at and t.done_at[:10] >= start.isoformat()]
    due_tasks = [t for t in db.list_tasks("all") if start.isoformat() <= t.due_date <= today.isoformat()]
    open_tasks = [t for t in db.list_tasks("all") if not t.done]
    rate = round(100 * sum(t.done for t in due_tasks) / len(due_tasks)) if due_tasks else None

    regular, bold, _face = _fonts()
    acc = colors.HexColor(accent)
    ink, muted, panel = colors.HexColor("#1b1f2a"), colors.HexColor("#6b7385"), colors.HexColor("#f2f3f7")
    base = ParagraphStyle("base", fontName=regular, fontSize=9.5, leading=13.5, textColor=ink)
    small = ParagraphStyle("small", parent=base, fontSize=8, leading=11, textColor=muted)
    h2 = ParagraphStyle("h2", parent=base, fontName=bold, fontSize=12.5, leading=16, spaceBefore=14, spaceAfter=6, textColor=ink)
    title = ParagraphStyle("title", parent=base, fontName=bold, fontSize=22, leading=26)
    kpi_v = ParagraphStyle("kpi_v", parent=base, fontName=bold, fontSize=17, leading=20)
    kpi_l = ParagraphStyle("kpi_l", parent=small)

    story = []
    icon = paths.asset("icon.png")
    head = [[Image(str(icon), 15 * mm, 15 * mm) if icon.exists() else "", [_p("Focus Report", title), _p(
        f"{name + '  ·  ' if name else ''}{start:%d %b %Y} – {today:%d %b %Y}  ({days} day{'s' if days != 1 else ''})", small)]]]
    t = Table(head, colWidths=[19 * mm, 150 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [t, Spacer(1, 3 * mm)]
    rule = Table([[""]], colWidths=[174 * mm], rowHeights=[1.2])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), acc)]))
    story += [rule, Spacer(1, 5 * mm)]

    tiles = [
        (fmt.hm(total_s), "Total focus time"),
        (str(len(sessions)), "Sessions"),
        (fmt.hm(sum(active) // len(active)) if active else "0m", "Average per active day"),
        (f"{fmt.hm(best_day[1])}", f"Best day ({best_day[0]:%d %b})" if best_day[1] else "Best day"),
        (f"{db.streak()} day{'s' if db.streak() != 1 else ''}", "Current streak"),
        (f"{rate}%" if rate is not None else "–", f"Tasks completed ({sum(t.done for t in due_tasks)}/{len(due_tasks)})"),
    ]
    cells = [[[_p(v, kpi_v), _p(lab, kpi_l)] for v, lab in tiles[i : i + 3]] for i in (0, 3)]
    kpi = Table(cells, colWidths=[58 * mm] * 3, rowHeights=[19 * mm] * 2)
    kpi.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), panel),
                ("LINEBELOW", (0, 0), (-1, 0), 3, colors.white),
                ("LINEAFTER", (0, 0), (1, -1), 3, colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(kpi)

    if commentary.strip():
        story += [_p("Coach's notes", h2), _p(commentary.strip(), base)]

    # daily (or weekly, for long periods) focus chart
    if days > 60:
        weeks: dict[date, int] = {}
        for d, s in series:
            weeks[d - timedelta(days=d.weekday())] = weeks.get(d - timedelta(days=d.weekday()), 0) + s
        labels, values = [f"{d:%d %b}" for d in weeks], [round(v / 60) for v in weeks.values()]
        caption = "Focus minutes per week"
    else:
        labels, values = [f"{d:%d}" if days > 14 else f"{d:%a %d}" for d, _s in series], [round(s / 60) for _d, s in series]
        caption = "Focus minutes per day"
    story.append(KeepTogether([_p(caption, h2), _chart(labels, values, acc, ink, muted, regular)]))

    hours = db.focus_by_hour(since=start)
    if any(hours):
        story.append(
            KeepTogether(
                [
                    _p("When you focus (minutes by hour of day)", h2),
                    _chart([f"{h:02d}" for h in range(24)], [round(v / 60) for v in hours], acc, ink, muted, regular, height=110),
                ]
            )
        )

    story.append(_p("Tasks", h2))
    story.append(_table(
        [("Completed in this period", "Priority", "Done")] + [(t.title, t.priority.capitalize(), t.done_at[:10]) for t in done_tasks[:15]]
        or [("Completed in this period", "Priority", "Done")],
        [110 * mm, 30 * mm, 34 * mm], acc, ink, regular, bold, empty="No tasks completed in this period.",
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(_table(
        [("Still open", "Priority", "Due")] + [(t.title, t.priority.capitalize(), t.due_date) for t in open_tasks[:15]],
        [110 * mm, 30 * mm, 34 * mm], acc, ink, regular, bold, empty="Nothing open. Nice.",
    ))

    noted = [s for s in sessions if s["note"]][:15]
    if noted:
        story.append(_p("Session notes", h2))
        rows = [("Date", "Time", "Note")] + [
            (s["started_at"][:10], fmt.hm(s["seconds"]), (s["note"] + (f"  [{s['task']}]" if s["task"] else ""))) for s in noted
        ]
        story.append(_table(rows, [24 * mm, 20 * mm, 130 * mm], acc, ink, regular, bold))

    story += [Spacer(1, 8 * mm), _p(f"Generated by {APP_NAME} on {today:%d %b %Y}. All data stays on your computer.", small)]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(muted)
        canvas.drawString(18 * mm, 10 * mm, f"{APP_NAME}  ·  progress report")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
        title="Focus Report", author=name or APP_NAME,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return {
        "path": str(out_path),
        "period": f"{start.isoformat()} to {today.isoformat()}",
        "total_focus": fmt.hm(total_s),
        "sessions": len(sessions),
        "tasks_completed": len(done_tasks),
    }


def _chart(labels, values, acc, ink, muted, font, height: int = 130) -> Drawing:
    width = 174 * mm
    d = Drawing(width, height)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 28, 20, width - 36, height - 30
    chart.data = [values or [0]]
    chart.bars[0].fillColor = acc
    chart.bars[0].strokeColor = None
    chart.barWidth = 6
    chart.groupSpacing = 3
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(max(values or [0]), 10) * 1.15
    chart.valueAxis.labels.fontName = font
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labels.fillColor = muted
    chart.valueAxis.strokeColor = colors.HexColor("#d5d8e0")
    chart.valueAxis.gridStrokeColor = colors.HexColor("#e6e8ee")
    chart.valueAxis.visibleGrid = True
    chart.categoryAxis.categoryNames = labels or [""]
    chart.categoryAxis.labels.fontName = font
    chart.categoryAxis.labels.fontSize = 6.5
    chart.categoryAxis.labels.fillColor = muted
    chart.categoryAxis.strokeColor = colors.HexColor("#d5d8e0")
    n = len(labels)
    if n > 16:  # thin the labels so they don't overlap
        step = max(1, n // 12)
        chart.categoryAxis.categoryNames = [l if i % step == 0 else "" for i, l in enumerate(labels)]
    d.add(chart)
    return d


def _table(rows, widths, acc, ink, regular, bold, empty: str | None = None) -> Table:
    if len(rows) == 1 and empty:
        rows = rows + [(empty, "", "")] if len(rows[0]) == 3 else rows + [(empty,)]
    style = ParagraphStyle("cell", fontName=regular, fontSize=8.8, leading=11.5, textColor=ink)
    head_style = ParagraphStyle("hcell", parent=style, fontName=bold, textColor=colors.white)
    data = [[_p(c, head_style if i == 0 else style) for c in row] for i, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), acc),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7fa")]),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#d5d8e0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t
