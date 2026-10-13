import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtWidgets import QApplication

from focus_mode import paths
from focus_mode.app import wire_appearance
from focus_mode.config import Settings
from focus_mode.db import Database
from focus_mode.services import Services
from focus_mode.ui.main_window import MainWindow
from focus_mode.ui.theme import theme


@pytest.fixture
def app_env(qapp, home):
    settings = Settings()
    settings.set("alerts", "mini_timer", False)
    settings.set("alerts", "sound", False)
    settings.set("alerts", "notifications", False)
    db = Database(paths.db_path())
    svc = Services(settings, db)
    wire_appearance(qapp, settings)
    win = MainWindow(svc)
    win.show()
    yield qapp, settings, svc, win
    win.close()


def pump(app, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


def test_pages_and_theme_switch(app_env):
    app, settings, svc, win = app_env
    for i in range(5):
        win.goto(i)
        pump(app, 0.05)
    settings.set("appearance", "accent", "#4d8dff")
    settings.set("appearance", "base", "amoled")
    pump(app, 0.05)
    assert theme.accent_hex == "#4d8dff" and theme.base == "amoled"
    assert "#4d8dff" in app.styleSheet()
    settings.set("appearance", "animations", False)
    assert theme.animations is False and theme.ms(200) == 0
    settings.set("appearance", "animations", True)


def test_timer_start_pause_and_title(app_env):
    app, settings, svc, win = app_env
    page = win.timer_page
    page.start_btn.click()
    assert svc.pomo.running and page.start_btn.text() == "Pause"
    assert "Focus Session" in win.windowTitle()
    page.start_btn.click()
    assert svc.pomo.state == "paused" and page.start_btn.text() == "Resume"
    page.reset_btn.click()
    assert svc.pomo.state == "idle"
    settings.set("timer", "focus_min", 10)
    assert page.ring._time == "10:00"


def test_task_flow_and_stats(app_env):
    app, settings, svc, win = app_env
    tid = svc.db.add_task("Write tests", "high", None, "09:00", "10:00")
    svc.tasks_changed.emit()
    tasks_page = win.pages[2]
    assert tasks_page.rows.count() == 0  # hidden page doesn't rebuild...
    win.goto(2)  # ...it catches up when opened
    assert len(tasks_page.findChildren(type(tasks_page.rows.itemAt(0).widget()))) >= 1
    svc.set_active_task(tid)
    win.timer_page.refresh_task()
    assert "Write tests" in win.timer_page.note_box.text()
    svc.pomo.start()
    svc.pomo._t0 -= 600
    svc.pomo.reset()  # logs a 10 minute partial session against the task
    row = svc.db.conn.execute("SELECT task_id, seconds, completed FROM sessions").fetchone()
    assert row["task_id"] == tid and 599 <= row["seconds"] <= 601 and row["completed"] == 0
    assert win.timer_page.stats.values[0].text() == "10m"
    svc.wipe_data()
    assert svc.db.list_tasks() == [] and svc.active_task_id is None


def test_messages_rotate_and_custom(app_env):
    app, settings, svc, win = app_env
    seen = []
    svc.message_changed.connect(lambda h, b: seen.append((h, b)))
    settings.set("messages", "custom", ["GO | Do the thing"])
    svc.pomo.start()
    pump(app, 0.05)
    assert seen and seen[-1] == ("GO", "Do the thing")
    settings.set("messages", "enabled", False)
    assert seen[-1][0] == "FOCUS MODE"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        reply = '{"messages": [{"headline": "AI LINE.", "body": "From the model."}]}'
        data = json.dumps({"choices": [{"message": {"content": reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_ai_messages_arrive_on_ui_thread(app_env):
    app, settings, svc, win = app_env
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        settings.set("ai", "base_url", f"http://127.0.0.1:{srv.server_port}/v1")
        settings.set("ai", "model", "m")
        settings.set("messages", "use_ai", True)
        svc.pomo.start()  # context -> focus triggers the fetch
        for _ in range(40):
            pump(app, 0.05)
            if svc.picker.ai.get("focus"):
                break
        assert [m.headline for m in svc.picker.ai["focus"]] == ["AI LINE."]
    finally:
        srv.shutdown()


def test_run_ai_without_setup_toasts(app_env):
    app, settings, svc, win = app_env
    toasts = []
    svc.toast.connect(lambda k, t: toasts.append((k, t)))
    assert svc.run_ai(lambda c: 1, lambda r: None) is False
    assert toasts and toasts[0][0] == "error"


def test_chat_panel_toggle_and_agent_settings_sync(app_env):
    app, settings, svc, win = app_env
    # the chat is open at startup and the window opens at the agreed size
    assert win.chat.is_open and win.chat.width() > 300
    assert (win.width(), win.height()) == (1000, 983) or win.width() <= 1000
    win.set_chat_open(False)
    pump(app, 0.5)
    assert win.chat.width() == 0
    win.set_chat_open(True)
    pump(app, 0.5)
    assert win.chat.is_open and win.chat.width() > 300
    # settings changed from outside (the agent) show up in the Settings page
    page = win.pages[4]
    settings.set("timer", "focus_min", 50)
    settings.set("alerts", "sound", True)
    settings.set("memory", "mode", "neural")
    assert page.focus_step.value() == 50
    assert page.mem_tabs.current() == "neural"
    win.goto_key("stats")
    assert win.current_key() == "stats"


def test_notes_flow(app_env):
    app, settings, svc, win = app_env
    tid = svc.db.add_task("Write essay", "high", "2026-01-01", None, None, notes="outline first")
    assert svc.db.get_task(tid).notes == "outline first"
    win.pages[0].note_box.setText("chapter 3")
    win.pages[0].note_box.textEdited.emit("chapter 3")
    assert svc.session_note == "chapter 3"
    svc._log_session("focus", 1500, True)
    assert svc.session_note == ""
    assert win.pages[0].note_box.text() == ""
    assert svc.db.recent_sessions(1)[0]["note"] == "chapter 3"
    win.goto_key("tasks")
    win.goto_key("stats")
    pump(app, 0.2)


def test_resizing_everywhere_and_close_button(app_env):
    import random

    app, settings, svc, win = app_env
    rnd = random.Random(3)
    for page in range(5):
        win.goto(page)
        for chat in (False, True):
            win.set_chat_open(chat)
            for _ in range(12):
                win.resize(rnd.randint(win.minimumWidth(), 1700), rnd.randint(440, 950))
                pump(app, 0.01)
    assert win.close_btn.isVisible() and win.close_btn.x() > win.centralWidget().width() - 60
