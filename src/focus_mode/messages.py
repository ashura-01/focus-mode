"""Avatar messages: built-in lines, your own lines, and AI-written lines."""

from __future__ import annotations

import random
from datetime import date
from typing import NamedTuple


class Message(NamedTuple):
    headline: str
    body: str


CONTEXTS = ("idle", "focus", "break", "done")

DEFAULTS: dict[str, list[Message]] = {
    "idle": [
        Message("READY.", "One session. Phone away."),
        Message("BEGIN.", "Start the timer. Start the work."),
        Message("SHARPEN.", "Pick one task and own it."),
        Message("STAND TALL.", "Small steps still win the war."),
    ],
    "focus": [
        Message("FOCUS.", "Don't touch your phone."),
        Message("STAY.", "Close the extra tabs."),
        Message("HOLD THE LINE.", "Distraction is the enemy."),
        Message("NO SCROLLING.", "The feed can wait."),
        Message("DISCIPLINE.", "Do the work, not the urge."),
        Message("ONE TASK.", "Finish what you started."),
        Message("EYES UP.", "Your goal is on the screen."),
        Message("STEADY.", "Breathe. Keep going."),
    ],
    "break": [
        Message("REST.", "Breathe. Stretch. Drink water."),
        Message("RECOVER.", "Look away from the screen."),
        Message("WELL FOUGHT.", "Walk around for a minute."),
        Message("BREAK.", "Phone still stays down."),
    ],
    "done": [
        Message("VICTORY.", "Session complete. Well done."),
        Message("DONE.", "Another battle won."),
        Message("EARNED.", "That counts. Take your break."),
    ],
}

QUOTES = [
    "Discipline is the bridge between goals and accomplishment.",
    "You do not rise to the level of your goals. You fall to the level of your systems.",
    "Focus is saying no to a hundred good ideas.",
    "Small daily improvements lead to stunning results.",
    "The cost of distraction is the work you never finished.",
    "Action is the foundational key to all success.",
    "Be where your hands are.",
]

HEADLINE_FOR = {"idle": "READY.", "focus": "FOCUS.", "break": "REST.", "done": "DONE."}


def parse_line(line: str, context: str) -> Message | None:
    """'HEADLINE | body' or just 'body' (headline then comes from the context)."""
    line = line.strip()
    if not line:
        return None
    if "|" in line:
        head, _, body = line.partition("|")
        head, body = head.strip(), body.strip()
        if head and body:
            return Message(head.upper()[:18], body[:120])
    return Message(HEADLINE_FOR.get(context, "FOCUS."), line[:120])


def quote_of_the_day(today: date | None = None) -> str:
    today = today or date.today()
    return QUOTES[today.toordinal() % len(QUOTES)]


class MessagePicker:
    """Chooses the next line to show, avoiding immediate repeats."""

    def __init__(self) -> None:
        self.custom: list[str] = []
        self.ai: dict[str, list[Message]] = {}
        self.use_ai = False
        self._last: Message | None = None

    def pool(self, context: str) -> list[Message]:
        pool: list[Message] = []
        # Your own lines replace the built-in focus lines, so they show up while you work.
        if context == "focus" and self.custom:
            pool += [m for line in self.custom if (m := parse_line(line, context))]
        else:
            pool += DEFAULTS.get(context, DEFAULTS["idle"])
        if self.use_ai:
            pool += self.ai.get(context, [])
        return pool

    def next(self, context: str) -> Message:
        pool = self.pool(context)
        choices = [m for m in pool if m != self._last] or pool
        self._last = random.choice(choices)
        return self._last
