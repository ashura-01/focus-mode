"""Small formatting helpers shared by the UI."""

from __future__ import annotations

import math


def mmss(seconds: float) -> str:
    """Countdown text; rounds up so 0.4s left still reads 00:01."""
    s = max(0, math.ceil(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def stopwatch(ms: int) -> str:
    ms = max(0, int(ms))
    total_s, millis = divmod(ms, 1000)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    cs = millis // 10
    if h:
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"
    return f"{m:02d}:{s:02d}.{cs:02d}"


def hm(seconds: int) -> str:
    """2h 45m / 45m / 0m"""
    minutes = int(seconds) // 60
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def clock12(hhmm: str | None) -> str:
    """'14:30' -> '2:30 PM'"""
    if not hhmm:
        return ""
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"
