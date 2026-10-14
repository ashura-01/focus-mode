import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from focus_mode import paths
from focus_mode.agent import Agent
from focus_mode.agent_tools import SETTING_KEYS, TOOL_SPECS, AppTools
from focus_mode.config import Settings
from focus_mode.db import Database
from focus_mode.services import Services


class FakeUI:
    def __init__(self):
        self.tab, self.confirm_answer, self.asked = "timer", True, []

    def goto_key(self, key):
        self.tab = key

    def current_key(self):
        return self.tab

    def confirm(self, text):
        self.asked.append(text)
        return self.confirm_answer


class Script(BaseHTTPRequestHandler):
    queue: list = []
    requests: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Script.requests.append(body)
        step = Script.queue.pop(0) if Script.queue else {"content": "(script exhausted)"}
        if step.get("http_error"):
            data = json.dumps({"error": {"message": step["http_error"]["message"]}}).encode()
            self.send_response(step["http_error"]["code"])
        else:
            data = json.dumps({"choices": [{"message": {"role": "assistant", **step}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def call(name, **args):
    return {"id": f"c_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def env(qapp, home):
    srv = HTTPServer(("127.0.0.1", 0), Script)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Script.queue, Script.requests = [], []
    settings = Settings()
    settings.set("ai", "base_url", f"http://127.0.0.1:{srv.server_port}/v1")
    settings.set("ai", "api_key", "k")
    settings.set("ai", "model", "m")
    settings.set("profile", "report_folder", str(home / "reports"))
    settings.set("alerts", "sound", False)
    settings.set("alerts", "notifications", False)
    db = Database(paths.db_path())
    svc = Services(settings, db)
    ui = FakeUI()
    tools = AppTools(svc, ui)
    agent = Agent(svc, tools)
    yield qapp, svc, ui, tools, agent
    svc.memory.unload()
    srv.shutdown()


def ask(app, agent, text, timeout=15):
    out = {"reply": None, "error": None, "steps": []}
    agent.reply.connect(lambda t: out.__setitem__("reply", t))
    agent.failed.connect(lambda t: out.__setitem__("error", t))
    agent.step.connect(out["steps"].append)
    assert agent.send(text)
    end = time.time() + timeout
    while time.time() < end and out["reply"] is None and out["error"] is None:
        app.processEvents()
        time.sleep(0.01)
    agent.reply.disconnect()
    agent.failed.disconnect()
    agent.step.disconnect()
    return out


def test_tool_specs_are_valid_and_match_implementations():
    names = [s["function"]["name"] for s in TOOL_SPECS]
    assert len(names) == len(set(names))
    for n in names:
        assert hasattr(AppTools, f"t_{n}"), n
        params = next(s for s in TOOL_SPECS if s["function"]["name"] == n)["function"]["parameters"]
        assert params["type"] == "object" and set(params["required"]) <= set(params["properties"])
    # never expose secrets or mass deletion
    assert not any("key" in k or "url" in k for k in SETTING_KEYS)
    assert not any("wipe" in n or "delete_all" in n for n in names)


def test_navigate_and_settings_in_one_turn(env):
    app, svc, ui, tools, agent = env
    Script.queue = [
        {"content": None, "tool_calls": [call("navigate", tab="stats"), call("set_settings", changes={"focus_min": 50, "accent_color": "Azure", "api_key": "hack", "style": "amoled"})]},
        {"content": "Switched to Stats and set focus to 50 minutes."},
    ]
    out = ask(app, agent, "go to stats and make focus 50 min, blue")
    assert out["reply"] == "Switched to Stats and set focus to 50 minutes."
    assert ui.tab == "stats"
    assert svc.settings.get("timer", "focus_min") == 50
    assert svc.settings.get("appearance", "accent") == "#4d8dff"
    assert svc.settings.get("appearance", "base") == "amoled"
    assert svc.settings.get("ai", "api_key") == "k"  # the model can't touch it
    assert any("stats" in s.lower() for s in out["steps"])
    # the second request carried the tool results, including the rejection
    tool_msgs = [m for m in Script.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and "not a changeable setting" in tool_msgs[1]["content"]
    assert Script.requests[0]["tools"] and Script.requests[0]["messages"][0]["role"] == "system"


def test_timer_and_tasks_and_notes(env):
    app, svc, ui, tools, agent = env
    Script.queue = [
        {"tool_calls": [
            call("add_task", title="Revise trees", priority="high", due="tomorrow", start="09:00", end="10:00", notes="AVL rotations"),
            call("timer_control", action="start"),
            call("set_next_session_note", note="Binary trees chapter"),
        ]},
        {"content": "Added the task, started the timer."},
    ]
    out = ask(app, agent, "add a task and start")
    assert out["reply"]
    task = svc.db.list_tasks()[0]
    assert task.title == "Revise trees" and task.notes == "AVL rotations" and task.start_time == "09:00"
    assert svc.pomo.running and svc.session_note == "Binary trees chapter"
    svc.pomo._t0 -= 120
    svc.pomo.reset()  # logs a 2 minute partial session with the note
    row = svc.db.recent_sessions(1)[0]
    assert row["note"] == "Binary trees chapter" and svc.session_note == ""

    Script.queue = [{"tool_calls": [call("update_task", id=task.id, notes="done chapter 1", priority="low"), call("complete_task", id=task.id)]}, {"content": "ok"}]
    ask(app, agent, "finish it")
    t = svc.db.get_task(task.id)
    assert t.done and t.priority == "low" and t.notes == "done chapter 1"


def test_delete_requires_confirmation(env):
    app, svc, ui, tools, agent = env
    tid = svc.db.add_task("Keep me")
    ui.confirm_answer = False
    Script.queue = [{"tool_calls": [call("delete_task", id=tid)]}, {"content": "The user declined."}]
    ask(app, agent, "delete it")
    assert svc.db.get_task(tid) is not None and ui.asked and "Keep me" in ui.asked[0]
    ui.confirm_answer = True
    Script.queue = [{"tool_calls": [call("delete_task", id=tid)]}, {"content": "Deleted."}]
    ask(app, agent, "yes really")
    assert svc.db.get_task(tid) is None


def test_bad_arguments_come_back_as_errors_not_crashes(env):
    app, svc, ui, tools, agent = env
    bad = {"id": "c1", "type": "function", "function": {"name": "add_task", "arguments": "{not json"}}
    Script.queue = [
        {"tool_calls": [bad, call("add_task", title="x", due="31/12/2026"), call("navigate", tab="nowhere"), call("nope"), call("update_task", id=999)]},
        {"content": "Sorry, those failed."},
    ]
    out = ask(app, agent, "do broken things")
    assert out["reply"] == "Sorry, those failed."
    results = [json.loads(m["content"]) for m in Script.requests[1]["messages"] if m["role"] == "tool"]
    assert all("error" in r for r in results) and len(results) == 5
    assert svc.db.list_tasks() == []


def test_memory_search_and_report(env, home):
    app, svc, ui, tools, agent = env
    t = svc.db.add_task("Study trees", "high", notes="AVL rotations confuse me")
    svc.db.add_session("focus", 1500, True, t, note="Struggled with AVL rotations")
    svc.db.add_session("focus", 1500, True, None, note="Essay intro done")
    Script.queue = [
        {"tool_calls": [call("search_memory", query="what did I struggle with", limit=3)]},
        {"tool_calls": [call("generate_report", days=7, commentary="Solid week. Tackle AVL trees next.")]},
        {"content": "Report ready."},
    ]
    reports = []
    svc.report_ready.connect(reports.append)
    out = ask(app, agent, "make me a report", timeout=30)
    assert out["reply"] == "Report ready."
    search_result = json.loads([m for m in Script.requests[1]["messages"] if m["role"] == "tool"][0]["content"])
    assert search_result["results"] and "AVL" in search_result["results"][0]["text"]
    assert reports and Path(reports[0]).exists() and Path(reports[0]).read_bytes()[:4] == b"%PDF"
    assert Path(reports[0]).parent == home / "reports"


def test_stats_and_state_tools(env):
    app, svc, ui, tools, agent = env
    svc.db.add_session("focus", 1800, True)
    stats = json.loads(tools.run("get_stats", {"period": "week"}))
    assert stats["total_minutes"] == 30 and stats["streak_days"] == 1
    state = json.loads(tools.run("get_app_state", {}))
    assert state["tab"] == "timer" and state["timer"]["state"] == "idle"
    assert "error" in json.loads(tools.run("get_stats", {"period": "decade"}))


def test_model_without_tool_support_gives_friendly_error(env):
    app, svc, ui, tools, agent = env
    Script.queue = [{"http_error": {"code": 404, "message": "No endpoints found that support tool use"}}]
    out = ask(app, agent, "hi")
    assert out["error"] and "tool calling" in out["error"]
    assert not agent.busy


def test_history_persists_and_clear(env):
    app, svc, ui, tools, agent = env
    Script.queue = [{"content": "Hello!"}]
    ask(app, agent, "hi there")
    assert svc.db.recent_chat() == [("user", "hi there"), ("assistant", "Hello!")]
    assert Agent(svc, tools).history[-1]["content"] == "Hello!"
    agent.clear()
    assert svc.db.recent_chat() == [] and agent.history == []


def test_not_configured(qapp, home):
    settings = Settings()
    svc = Services(settings, Database(paths.db_path()))
    agent = Agent(svc, AppTools(svc, FakeUI()))
    errors = []
    agent.failed.connect(errors.append)
    assert agent.send("hi") is False and "isn't set up" in errors[0]


def test_overloaded_provider_is_retried(env, monkeypatch):
    app, svc, ui, tools, agent = env
    monkeypatch.setattr("focus_mode.agent.RETRY_DELAYS", (0.01, 0.01, 0.01))
    Script.queue = [{"http_error": {"code": 503, "message": "Service temporarily overloaded"}}, {"content": "All good."}]
    out = ask(app, agent, "hi")
    assert out["reply"] == "All good." and out["error"] is None
    assert any("retrying" in s for s in out["steps"])


def test_failure_keeps_progress_and_retry_does_not_duplicate(env, monkeypatch):
    app, svc, ui, tools, agent = env
    monkeypatch.setattr("focus_mode.agent.RETRY_DELAYS", (0.01, 0.01, 0.01))
    overloaded = {"http_error": {"code": 503, "message": "Service temporarily overloaded"}}
    Script.queue = [{"content": "", "tool_calls": [call("add_task", title="SQLi room", priority="high")]}, overloaded, overloaded, overloaded, overloaded]
    out = ask(app, agent, "add an sql injection task")
    assert out["error"] and "1 step(s)" in out["error"] and not agent.busy
    assert "Interrupted" in agent.history[-1]["content"] and "SQLi room" in agent.history[-1]["content"]
    # the model repeats the call after the error: nothing is added twice
    again = json.loads(tools.run("add_task", {"title": "sqli room", "priority": "high"}))
    assert again["duplicate"] is True and len(svc.db.list_tasks("all")) == 1


def test_avatar_ai_pauses_while_chatting(env):
    app, svc, ui, tools, agent = env
    seen = []
    svc.message_changed.connect(lambda h, b: seen.append(h))
    Script.queue = [{"content": "Hello!"}]
    assert agent.send("hi")
    assert svc.chat_busy and svc._ai_paused()
    svc.rotate_message()  # ignored while the agent works
    assert seen == []
    end = time.time() + 10
    while agent.busy and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert not svc.chat_busy
    assert svc._ai_paused()  # and a quiet period follows, so avatar fetches don't land right after a chat


def test_session_crud(env):
    app, svc, ui, tools, agent = env
    tid = svc.db.add_task("Lab")
    out = json.loads(tools.run("add_session", {"minutes": 40, "started": "2026-01-05 09:00", "task_id": tid, "note": "labs"}))
    sid = out["session_id"]
    row = svc.db.get_session(sid)
    assert row["seconds"] == 2400 and row["task"] == "Lab" and row["note"] == "labs"
    assert json.loads(tools.run("update_session", {"session_id": sid, "minutes": 50, "note": "better", "task_id": 0}))["ok"]
    row = svc.db.get_session(sid)
    assert row["seconds"] == 3000 and row["note"] == "better" and row["task_id"] is None
    assert "error" in json.loads(tools.run("update_session", {"session_id": 9999, "note": "x"}))
    assert "error" in json.loads(tools.run("add_session", {"minutes": 5, "task_id": 777}))
    assert "error" in json.loads(tools.run("add_session", {"minutes": 5, "started": "2999-01-01 10:00"}))
    ui.confirm_answer = False
    assert json.loads(tools.run("delete_session", {"session_id": sid}))["ok"] is False
    assert svc.db.get_session(sid)
    ui.confirm_answer = True
    assert json.loads(tools.run("delete_session", {"session_id": sid}))["ok"]
    assert svc.db.get_session(sid) is None
