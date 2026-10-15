"""Main window: sidebar navigation, page stack, toast, mini timer."""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton,
    QMessageBox,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from focus_mode import APP_NAME, fmt, paths
from focus_mode.agent import Agent
from focus_mode.agent_tools import TABS, AppTools
from focus_mode.services import Services
from focus_mode.ui import icons
from focus_mode.ui.avatar import AvatarHeader
from focus_mode.ui.chat_panel import ChatPanel
from focus_mode.ui.mini import MiniTimer
from focus_mode.ui.pages.settings_page import SettingsPage
from focus_mode.ui.pages.stats_page import StatsPage
from focus_mode.ui.pages.stopwatch_page import StopwatchPage
from focus_mode.ui.pages.tasks_page import TasksPage
from focus_mode.ui.pages.timer_page import PHASE_NAMES, TimerPage
from focus_mode.ui.theme import theme
from focus_mode.ui.widgets import Toast


class NavButton(QAbstractButton):
    def __init__(self, icon_name: str, tip: str) -> None:
        super().__init__()
        self.icon_name = icon_name
        self.setCheckable(True)
        self.setToolTip(tip)
        self.setFixedSize(52, 52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def enterEvent(self, _e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self.isChecked():
            p.setBrush(theme.c("panel2"))
            p.drawRoundedRect(QRectF(0, 0, 52, 52), 15, 15)
            p.setBrush(theme.c("accent"))
            p.drawRoundedRect(QRectF(self.width() - 4, 14, 3, 24), 1.5, 1.5)
        elif self._hover:
            p.setBrush(theme.alpha("panel2", 0.6))
            p.drawRoundedRect(QRectF(0, 0, 52, 52), 15, 15)
        color = theme.c("text") if self.isChecked() else theme.c("muted")
        p.drawPixmap(13, 13, icons.pixmap(self.icon_name, color, 26))


class MainWindow(QMainWindow):
    SIDEBAR_W = 78
    STACK_MIN_W = 490  # the page area never gets narrower than this (widest page needs ~471), so it can't slide under the chat
    # smallest window: sidebar + page area + outer margins (10 + 2*EDGE); wider while the AI chat panel is open
    BASE_MIN = (SIDEBAR_W + STACK_MIN_W + 10 + 12, 440)
    START_SIZE = (1000, 983)  # opening size, with the AI chat panel open

    def __init__(self, svc: Services) -> None:
        super().__init__()
        self.svc = svc
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(paths.asset("icon.png"))))
        # Use the native window frame/title bar. The window manager owns move, resize,
        # minimize, maximize, and close operations; custom resize grips caused instability.
        self.setMinimumSize(*self.BASE_MIN)
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(self.START_SIZE[0], avail.width()), min(self.START_SIZE[1], avail.height()))
        self._last_resize = 0.0

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(10, 10, 0, 10)
        outer.setSpacing(0)

        # sidebar
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(self.SIDEBAR_W)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(13, 18, 13, 18)
        side.setSpacing(10)
        outer.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(self.STACK_MIN_W)
        outer.addWidget(self.stack, 1)

        self.agent = Agent(svc, AppTools(svc, self))
        self.chat = ChatPanel(svc, self.agent)
        outer.addWidget(self.chat)

        self.timer_page = TimerPage(svc)
        pages = [
            ("timer", "Timer  (Ctrl+1)", self.timer_page),
            ("stopwatch", "Stopwatch  (Ctrl+2)", StopwatchPage(svc)),
            ("tasks", "Tasks  (Ctrl+3)", TasksPage(svc)),
            ("stats", "Stats  (Ctrl+4)", StatsPage(svc)),
            ("settings", "Settings  (Ctrl+5)", SettingsPage(svc)),
        ]
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.pages: list[QWidget] = []
        for i, (key, tip, page) in enumerate(pages):
            icon_name = {"timer": "timer", "stopwatch": "stopwatch", "tasks": "tasks", "stats": "stats", "settings": "settings"}[key]
            btn = NavButton(icon_name, tip)
            self.group.addButton(btn, i) 
            if key == "settings":
                side.addStretch(1)
                self.chat_btn = NavButton("sparkles", "AI assistant  (Ctrl+J)")
                self.chat_btn.clicked.connect(lambda: self.set_chat_open(not self.chat.is_open))
                side.addWidget(self.chat_btn, 0, Qt.AlignmentFlag.AlignHCenter)
            side.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
            self.stack.addWidget(self._scrolled(page))
            self.pages.append(page)
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.goto(i))
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self.goto)
        theme.changed.connect(lambda: [b.update() for b in self.group.buttons()] + [self.chat_btn.update()])
        QShortcut(QKeySequence("Ctrl+J"), self, activated=lambda: self.set_chat_open(not self.chat.is_open))
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self.showMinimized)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        svc.agent_clear_requested.connect(self.chat._clear)
        self.chat.collapse_requested.connect(lambda: QTimer.singleShot(0, lambda: self.set_chat_open(False)))
        self.chat.open_settings.connect(lambda: self.goto_key("settings"))
        # The AI chat is open from the start (no animation, no window growth: START_SIZE already includes it).
        self.setMinimumSize(self.BASE_MIN[0] + self.chat.full_width(), self.BASE_MIN[1])
        self.chat.set_open(True, animate=False)
        self.chat_btn.setChecked(True)

        self.toast = Toast(central)
        svc.toast.connect(self.toast.show_message)
        if svc.restored:
            QTimer.singleShot(700, lambda: svc.toast.emit("info", svc.restored))

        # Heavy responsive work (header scaling, page margins) is throttled while the window is dragged.
        self._headers = self.findChildren(AvatarHeader)
        self._page_side: int | None = None
        self._responsive_timer = QTimer(self)
        self._responsive_timer.setSingleShot(True)
        self._responsive_timer.setInterval(40)
        self._responsive_timer.timeout.connect(self._apply_responsive)

        self.mini = MiniTimer()
        self.mini.clicked.connect(self._restore)
        self._mini_task = ""
        svc.active_task_changed.connect(self._refresh_mini_task)
        svc.tasks_changed.connect(self._refresh_mini_task)
        self._refresh_mini_task()
        svc.pomo.state_changed.connect(self._sync_mini)
        svc.pomo.phase_completed.connect(self._phase_done)
        svc.watch.state_changed.connect(self._sync_mini)
        svc.pomo.tick.connect(self._sync_mini)
        svc.watch.tick.connect(self._sync_mini)
        svc.pomo.tick.connect(self._sync_title)
        svc.pomo.state_changed.connect(self._sync_title)

    # -- navigation ---------------------------------------------------------
    @staticmethod
    def _scrolled(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        # Pages are built to fit the minimum width; a (hidden) horizontal scrollbar would only hide content.
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    def goto(self, index: int) -> None:
        if index == self.stack.currentIndex():
            return
        self.group.button(index).setChecked(True)
        area = self.stack.widget(index)
        self.stack.setCurrentIndex(index)
        page = self.pages[index]
        if hasattr(page, "on_show"):
            page.on_show()

    # -- hooks used by the agent --------------------------------------------
    def goto_key(self, key: str) -> None:
        self.goto(TABS.index(key))

    def current_key(self) -> str:
        return TABS[self.stack.currentIndex()]

    def confirm(self, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, APP_NAME, text,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def is_resizing(self) -> bool:
        """True while the window geometry is still changing (a drag, or the chat opening)."""
        return time.monotonic() - self._last_resize < 0.2 or self.chat.is_animating()

    def set_chat_open(self, on: bool) -> None:
        """Open/collapse the AI chat in ONE layout pass.

        Changing the minimum size, the window size and the panel width as separate steps (or over an
        animation) made the window geometry fight itself and crashed the app on collapse. Here the
        target width is computed up front, then everything is applied together with repaints paused.
        """
        if on == self.chat.is_open or getattr(self, "_toggling", False):
            return
        self._toggling = True
        try:
            extra = self.chat.full_width()
            floor = self.BASE_MIN[0] + (extra if on else 0)
            resizable = not (self.isMaximized() or self.isFullScreen())
            target = max(floor, self.width() + (extra if on else -extra))
            self.setUpdatesEnabled(False)
            try:
                if on:  # grow first, then raise the minimum, so Qt never has to correct us
                    if resizable:
                        self.resize(target, self.height())
                    self.setMinimumSize(floor, self.BASE_MIN[1])
                else:  # lower the minimum first, then shrink
                    self.setMinimumSize(floor, self.BASE_MIN[1])
                    if resizable:
                        self.resize(target, self.height())
                self.chat.set_open(on, animate=False)
                self.chat_btn.setChecked(on)
            finally:
                self.setUpdatesEnabled(True)
        finally:
            self._toggling = False

    # -- title & mini timer -------------------------------------------------
    def _sync_title(self) -> None:
        p = self.svc.pomo
        if p.state == "idle":
            self.setWindowTitle(APP_NAME)
        else:
            mark = "⏸ " if p.state == "paused" else ""
            self.setWindowTitle(f"{mark}{fmt.mmss(p.remaining())} · {PHASE_NAMES[p.phase].title()} — {APP_NAME}")

    def _phase_done(self, finished: str, _upcoming: str) -> None:
        s = self.svc
        if not s.settings.get("alerts", "mini_timer") or self.isActiveWindow():
            return
        detail = "Focus session complete" if finished == "focus" else "Break is over"
        # With auto-start the next phase is already running, so don't cover the timer for long.
        self.mini.show_done("TIME'S UP!", detail, expire=bool(s.settings.get("timer", "auto_start")))
        self._sync_mini()

    def _refresh_mini_task(self) -> None:
        """Cache the focused task's title (the mini timer repaints many times a second)."""
        s = self.svc
        task = s.db.get_task(s.active_task_id) if s.active_task_id else None
        self._mini_task = task.title if task and not task.done else ""
        self._sync_mini()

    def _sync_mini(self) -> None:
        s = self.svc
        want = bool(s.settings.get("alerts", "mini_timer")) and not self.isActiveWindow()
        if not want:
            self.mini.clear_done()
        if want and self.mini.done:
            pass  # keep showing "time's up" until it is dismissed
        elif want and s.pomo.running:
            self.mini.update_state(fmt.mmss(s.pomo.remaining()), PHASE_NAMES[s.pomo.phase], s.pomo.progress(), self._mini_task)
        elif want and s.watch.running:
            self.mini.update_state(fmt.stopwatch(s.watch.elapsed_ms())[:-3], "STOPWATCH", (s.watch.elapsed_ms() % 60000) / 60000, self._mini_task)
        else:
            want = False
        if self.mini.done and bool(s.settings.get("alerts", "mini_timer")) and not self.isActiveWindow():
            want = True
        if want and not self.mini.isVisible():
            geo = QGuiApplication.primaryScreen().availableGeometry()
            self.mini.move(geo.right() - self.mini.width() - 24, geo.top() + 24)
            self.mini.show()
        elif not want and self.mini.isVisible():
            self.mini.hide()

    def _restore(self) -> None:
        self.mini.clear_done()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def event(self, e) -> bool:
        if e.type() in (QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange):
            QTimer.singleShot(0, self._sync_mini)
        return super().event(e)

    # Native window decorations handle resizing and title-bar actions.
    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._last_resize = time.monotonic()
        self.toast.reposition()
        if not self._responsive_timer.isActive():
            self._responsive_timer.start()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange:
            if not self._responsive_timer.isActive():
                self._responsive_timer.start()

    def _apply_responsive(self) -> None:
        # Shrink the knight header on short windows so the timer isn't pushed off-screen.
        height_scale = (self.height() - 60) / 840
        for header in self._headers:
            header.set_height_scale(height_scale)
        # Tighter page side-margins on small windows so more of the page fits.
        side = 32 if self.width() >= 800 else 16
        if side != self._page_side:
            self._page_side = side
            for page in self.pages:
                lay = page.layout()
                if lay is not None:
                    lay.setContentsMargins(side, 16, side, 16)

    def closeEvent(self, e) -> None:
        self.mini.hide()
        # Keep an unfinished session: it is restored (paused) the next time the app starts.
        self.svc.save_state()
        super().closeEvent(e)