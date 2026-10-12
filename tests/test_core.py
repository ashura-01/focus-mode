import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from focus_mode import ai, fmt
from focus_mode.config import Settings, dumps
from focus_mode.db import Database
from focus_mode.engine import PomodoroEngine, StopwatchEngine
from focus_mode.messages import MessagePicker, parse_line


# -- settings -----------------------------------------------------------------
def test_settings_roundtrip_and_clamp(qapp, home):
    s = Settings()
    s.set("timer", "focus_min", 50)
    s.set("timer", "cycles", 999)  # clamped
    s.set("messages", "custom", ['FOCUS. | say "hi"', "second"])
    s.set("ai", "api_key", "sk-test")
    again = Settings()
    assert again.get("timer", "focus_min") == 50
    assert again.get("timer", "cycles") == 12
    assert again.get("messages", "custom") == ['FOCUS. | say "hi"', "second"]
    assert again.path.stat().st_mode & 0o777 == 0o600  # holds an API key


def test_settings_bad_file_falls_back(qapp, home):
    s = Settings()
    s.path.write_text("this is [not toml", encoding="utf-8")
    assert Settings().get("timer", "focus_min") == 25


def test_settings_wrong_types_ignored(qapp, home):
    s = Settings()
    s.path.write_text('[timer]\nfocus_min = "lots"\n[alerts]\nsound = 3\n', encoding="utf-8")
    again = Settings()
    assert again.get("timer", "focus_min") == 25
    assert again.get("alerts", "sound") is True


def test_dumps_is_valid_toml():
    import tomllib

    text = dumps({"a": {"s": 'q"uote\nnl', "n": 3, "b": False, "l": ["x", "y"]}})
    assert tomllib.loads(text)["a"] == {"s": 'q"uote\nnl', "n": 3, "b": False, "l": ["x", "y"]}


# -- database -----------------------------------------------------------------
def make_db(now):
    return Database(":memory:", now=lambda: now)


def test_tasks_views_and_summary():
    now = datetime(2026, 10, 12, 10, 0)
    db = make_db(now)
    a = db.add_task("today", "high", "2026-10-12", "09:00", "10:30")
    b = db.add_task("tomorrow", due="2026-10-13")
    c = db.add_task("overdue", due="2026-10-10")
    d = db.add_task("done old", due="2026-10-09")
    db.set_done(d, True)
    assert {t.title for t in db.list_tasks("today")} == {"today", "overdue"}
    assert [t.title for t in db.list_tasks("upcoming")] == ["tomorrow"]
    assert [t.title for t in db.list_tasks("completed")] == ["done old"]
    assert len(db.list_tasks("all")) == 4
    summary = db.task_summary()
    assert summary["total"] == 2 and summary["remaining"] == 2
    assert summary["planned_seconds"] == 90 * 60
    db.update_task(a, priority="bogus")
    assert db.get_task(a).priority == "medium"
    db.delete_task(b)
    assert len(db.list_tasks("all")) == 3
    with pytest.raises(ValueError):
        db.add_task("   ")


def test_streak_and_stats():
    now = datetime(2026, 10, 12, 10, 0)
    db = make_db(now)
    for days_ago in (0, 1, 2, 4):  # gap at day 3
        db.add_session("focus", 1500, True, started_at=now - timedelta(days=days_ago, hours=1))
    assert db.streak() == 3
    db.add_session("stopwatch", 600, True, started_at=now - timedelta(hours=2))
    today = db.today_summary()
    assert today["seconds"] == 1500 + 600
    assert today["sessions"] == 2
    week = db.focus_by_day(7)
    assert len(week) == 7 and week[-1][1] == 2100
    assert db.best_hour() == 8 or db.best_hour() == 9
    assert db.totals()["sessions"] == 5


def test_streak_counts_from_yesterday_if_today_empty():
    now = datetime(2026, 10, 12, 10, 0)
    db = make_db(now)
    db.add_session("focus", 1500, True, started_at=now - timedelta(days=1))
    assert db.streak() == 1
    assert make_db(now).streak() == 0


def test_wipe_and_zero_session_ignored():
    db = make_db(datetime(2026, 10, 12, 10, 0))
    db.add_task("x")
    db.add_session("focus", 0)
    db.add_session("focus", 100)
    db.wipe()
    assert db.list_tasks() == [] and db.totals()["sessions"] == 0


# -- engines ------------------------------------------------------------------
def test_pomodoro_cycle_and_logging(qapp, home):
    s = Settings()
    s.set("timer", "cycles", 2)
    e = PomodoroEngine(s)
    logged, completed = [], []
    e.session_logged.connect(lambda k, sec, ok: logged.append((k, sec, ok)))
    e.phase_completed.connect(lambda a, b: completed.append((a, b)))
    assert (e.phase, e.cycle) == ("focus", 1)
    e.start()
    e._t0 -= e.duration() + 1  # fast-forward past the end
    e._on_timer()
    assert completed == [("focus", "short")] and logged == [("focus", 25 * 60, True)]
    assert e.state == "idle"
    e.skip()  # break -> focus, cycle 2
    assert (e.phase, e.cycle) == ("focus", 2)
    e.start()
    e._t0 -= e.duration() + 1
    e._on_timer()
    assert completed[-1] == ("focus", "long")  # cycles == 2 -> long break
    e.skip()
    assert (e.phase, e.cycle) == ("focus", 1)


def test_pomodoro_partial_logging(qapp, home):
    e = PomodoroEngine(Settings())
    logged = []
    e.session_logged.connect(lambda k, sec, ok: logged.append((k, sec, ok)))
    e.start()
    e._t0 -= 30
    e.reset()  # under a minute: not recorded
    assert logged == []
    e.start()
    e._t0 -= 300
    e.reset()
    assert logged and logged[0][0] == "focus" and logged[0][2] is False
    assert 299 <= logged[0][1] <= 301


def test_pause_keeps_elapsed(qapp, home):
    e = PomodoroEngine(Settings())
    e.start()
    e._t0 -= 100
    e.pause()
    first = e.elapsed()
    assert 99 < first < 101 and e.state == "paused"
    assert abs(e.elapsed() - first) < 0.01  # paused: clock does not advance


def test_stopwatch(qapp):
    w = StopwatchEngine()
    logged = []
    w.session_logged.connect(lambda k, sec, ok: logged.append((k, sec)))
    w.start()
    w._t0 -= 5
    w.reset()
    assert logged == []  # under 10s
    w.start()
    w._t0 -= 75
    w.lap()
    w.pause()
    assert len(w.laps) == 1
    w.reset()
    assert logged and logged[0][0] == "stopwatch" and 74 <= logged[0][1] <= 76 and w.laps == []


# -- messages / formatting ----------------------------------------------------
def test_messages():
    assert parse_line("STAY | eyes up", "focus") == ("STAY", "eyes up")
    assert parse_line("just a line", "focus").headline == "FOCUS."
    assert parse_line("   ", "focus") is None
    p = MessagePicker()
    p.custom = ["ONE | a", "TWO | b"]
    assert {p.next("focus").headline for _ in range(30)} == {"ONE", "TWO"}
    assert p.next("break").headline != "ONE"  # custom lines only apply while focusing
    seen = [p.next("idle") for _ in range(20)]
    assert all(a != b for a, b in zip(seen, seen[1:]))  # no immediate repeats


def test_fmt():
    assert fmt.mmss(24 * 60 + 36.2) == "24:37"
    assert fmt.mmss(0) == "00:00"
    assert fmt.hm(2 * 3600 + 45 * 60) == "2h 45m" and fmt.hm(0) == "0m" and fmt.hm(3600) == "1h"
    assert fmt.clock12("14:30") == "2:30 PM" and fmt.clock12("00:05") == "12:05 AM"
    assert fmt.stopwatch(83_400) == "01:23.40"


# -- AI client ----------------------------------------------------------------
def test_extract_json():
    assert ai.extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert ai.extract_json('text [1, 2] more') == [1, 2]
    with pytest.raises(ai.AIError):
        ai.extract_json("no json here")


def test_extract_json_messy_model_output():
    # reasoning block, fence, trailing comma
    assert ai.extract_json('<think>maybe {"x": 0}</think>\n```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    # only the closing think tag is present (opening tag lived in the prompt template)
    assert ai.extract_json('long reasoning {not json}\n</think>{"a": 1}') == {"a": 1}
    # single quotes / Python literals
    assert ai.extract_json("{'a': True, 'b': None}") == {"a": True, "b": None}
    # braces inside strings and chatter around the object
    assert ai.extract_json('Here: {"a": "x {y} z"} Hope that helps!') == {"a": "x {y} z"}
    # truncated by the token limit: keep the complete items
    cut = '{"messages": [{"headline": "A.", "body": "one"}, {"headline": "B.", "body": "two"}, {"headline": "C.", "bo'
    assert [m["headline"] for m in ai.extract_json(cut)["messages"]] == ["A.", "B."]
    # an unclosed code fence
    assert ai.extract_json('```json\n{"messages": [{"headline": "A.", "body": "x"}, {"hea') == {"messages": [{"headline": "A.", "body": "x"}]}
    # reasoning that never finished is not an answer
    with pytest.raises(ai.AIError):
        ai.extract_json("<think>still thinking {")


def test_clean_reply_strips_reasoning():
    assert ai.clean_reply("<think>plan</think>Hello") == "Hello"
    assert ai.clean_reply("plan</think>Hello") == "Hello"
    assert ai.clean_reply("<think>never ends") == ""
    assert ai.clean_reply("plain") == "plain"


def test_client_configured():
    assert not ai.AIClient("https://api.openai.com/v1", "", "m").configured
    assert ai.AIClient("https://api.openai.com/v1", "k", "m").configured
    assert ai.AIClient("http://localhost:11434/v1", "", "llama3").configured
    assert ai.AIClient("https://x.com/v1/", "k", "m").endpoint() == "https://x.com/v1/chat/completions"
    assert ai.AIClient("https://x.com/v1/chat/completions", "k", "m").endpoint().endswith("/v1/chat/completions")


class FakeAPI(BaseHTTPRequestHandler):
    reply = ""
    status = 200
    seen: dict = {}

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAPI.seen = {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        payload = (
            {"choices": [{"message": {"content": FakeAPI.reply}}]}
            if FakeAPI.status == 200
            else {"error": {"message": "bad key"}}
        )
        data = json.dumps(payload).encode()
        self.send_response(FakeAPI.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), FakeAPI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeAPI.status = 200
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_ai_features_against_fake_server(server):
    client = ai.AIClient(server, "sk-secret", "test-model")
    FakeAPI.reply = '{"priority": "HIGH", "minutes": 45, "reason": "Exam soon"}'
    r = ai.suggest_priority(client, "Study", "2026-10-13")
    assert r == {"priority": "high", "minutes": 45, "reason": "Exam soon"}
    assert FakeAPI.seen["path"] == "/v1/chat/completions"
    assert FakeAPI.seen["auth"] == "Bearer sk-secret"
    assert FakeAPI.seen["body"]["model"] == "test-model"

    FakeAPI.reply = '```json\n{"priorities": [{"id": 1, "priority": "low", "reason": "meh"}, {"id": 99, "priority": "high"}, {"id": 2, "priority": "nonsense"}]}\n```'
    out = ai.prioritize_tasks(client, [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}])
    assert out == {1: ("low", "meh")}  # unknown ids and invalid priorities dropped

    FakeAPI.reply = '{"messages": [{"headline": "stay.", "body": "Phone down."}, {"headline": "", "body": "x"}]}'
    msgs = ai.generate_messages(client, "focus")
    assert [(m.headline, m.body) for m in msgs] == [("STAY.", "Phone down.")]

    FakeAPI.reply = "You are on a roll."
    assert ai.write_insight(client, {"streak_days": 3}) == "You are on a roll."


def test_chat_json_retries_and_falls_back(server):
    """First reply is chatty/invalid -> one stricter retry succeeds; response_format refusal is remembered."""
    client = ai.AIClient(server, "k", "m")
    calls: list[dict] = []
    replies = iter(["Sure, here you go! (no json)", '{"messages": [{"headline": "go.", "body": "Now."}]}'])

    original = FakeAPI.do_POST

    def scripted(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        calls.append(body)
        if "response_format" in body:  # this server rejects JSON mode
            data = json.dumps({"error": {"message": "response_format unsupported"}}).encode()
            self.send_response(400)
        else:
            data = json.dumps({"choices": [{"message": {"content": next(replies)}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    FakeAPI.do_POST = scripted
    try:
        msgs = ai.generate_messages(client, "focus")
    finally:
        FakeAPI.do_POST = original
    assert [(m.headline, m.body) for m in msgs] == [("GO.", "Now.")]
    assert client._json_mode is False
    assert "response_format" in calls[0] and all("response_format" not in c for c in calls[1:])
    assert "not valid JSON" in calls[-1]["messages"][-1]["content"]  # the stricter retry prompt


def test_generate_messages_salvages_plain_text(server):
    client = ai.AIClient(server, "k", "m")
    FakeAPI.reply = '<think>ok</think>\n1. STAY. Phone face down.\n2. HOLD THE LINE. One more page.\nrandom chatter'
    msgs = ai.generate_messages(client, "focus")
    assert [(m.headline, m.body) for m in msgs] == [("STAY.", "Phone face down."), ("HOLD THE LINE.", "One more page.")]
    # no braces at all, so it isn't JSON, but the pairs are still recoverable
    FakeAPI.reply = '"headline": "NO.", "body": "Scroll later.",\n"headline": "DEEP WORK.", "body": "Stay."'
    assert [m.headline for m in ai.generate_messages(client, "focus")] == ["NO.", "DEEP WORK."]
    # other shapes models use: a bare list, a single object, a different key
    FakeAPI.reply = '[{"headline": "A.", "body": "one"}]'
    assert [m.headline for m in ai.generate_messages(client, "focus")] == ["A."]
    FakeAPI.reply = '{"headline": "B.", "body": "two"}'
    assert [m.headline for m in ai.generate_messages(client, "focus")] == ["B."]
    FakeAPI.reply = '{"lines": [{"headline": "C.", "body": "three"}]}'
    assert [m.headline for m in ai.generate_messages(client, "focus")] == ["C."]
    FakeAPI.reply = "total nonsense"
    with pytest.raises(ai.AIError, match="valid JSON"):
        ai.generate_messages(client, "focus")


def test_ai_errors(server):
    client = ai.AIClient(server, "bad", "m")
    FakeAPI.status = 401
    with pytest.raises(ai.AIError, match="rejected.*bad key"):
        ai.check_connection(client)
    FakeAPI.status = 200
    FakeAPI.reply = "not json"
    with pytest.raises(ai.AIError, match="valid JSON"):
        ai.suggest_priority(client, "x")
    with pytest.raises(ai.AIError, match="Can't reach"):
        ai.AIClient("http://127.0.0.1:1/v1", "k", "m", timeout=2).chat("s", "u")
    with pytest.raises(ai.AIError, match="not set up"):
        ai.AIClient("", "", "").chat("s", "u")
