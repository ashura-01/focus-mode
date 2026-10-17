"""Settings: appearance, timer, alerts, avatar messages, AI, data."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QBoxLayout,
    QColorDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from focus_mode import ai as ai_mod
from focus_mode import paths
from focus_mode import report as report_mod
from focus_mode.memory import build_documents
from focus_mode.services import Services
from focus_mode.ui import icons
from focus_mode.ui.avatar import AvatarHeader
from focus_mode.ui.theme import ACCENTS, BASE_LABELS, theme
from focus_mode.ui.widgets import Card, Stepper, Tabs, Toggle, button, caps, label


class SwatchRow(QWidget):
    """Accent presets plus a custom colour picker."""

    picked = Signal(str)

    def __init__(self, current: str) -> None:
        super().__init__()
        self.current = current.lower()
        self._targets: list[tuple[QRectF, str | None]] = []
        self.setFixedHeight(40)
        self.setMinimumWidth(8 * 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_current(self, color: str) -> None:
        self.current = color.lower()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(8 * 40, 40)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._targets = []
        x = 4.0
        presets = {hexc.lower() for _n, hexc in ACCENTS}
        for _name, hexc in ACCENTS:
            rect = QRectF(x, 5, 30, 30)
            self._targets.append((rect, hexc))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(hexc))
            p.drawEllipse(rect.adjusted(4, 4, -4, -4))
            if hexc.lower() == self.current:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(theme.c("text"), 2))
                p.drawEllipse(rect.adjusted(1, 1, -1, -1))
            x += 38
        rect = QRectF(x, 5, 30, 30)
        self._targets.append((rect, None))
        custom = self.current not in presets
        p.setPen(QPen(theme.c("text") if custom else theme.c("muted"), 2 if custom else 1.4, Qt.PenStyle.SolidLine if custom else Qt.PenStyle.DashLine))
        p.setBrush(QColor(self.current) if custom else Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect.adjusted(4, 4, -4, -4))
        if not custom:
            p.drawPixmap(int(rect.center().x() - 8), int(rect.center().y() - 8), icons.pixmap("plus", theme.c("muted"), 16))

    def mousePressEvent(self, event) -> None:
        for rect, hexc in self._targets:
            if rect.contains(QPointF(event.position())):
                if hexc is None:
                    col = QColorDialog.getColor(QColor(self.current), self, "Pick an accent colour")
                    if col.isValid():
                        self.picked.emit(col.name())
                else:
                    self.picked.emit(hexc)
                return


def setting_row(title: str, desc: str, control: QWidget | None = None, stack: bool = False) -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row) if not stack else QVBoxLayout(row)
    lay.setContentsMargins(0, 6, 0, 6)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(label(title))
    if desc:
        text.addWidget(label(desc, "hint", wrap=True))
    lay.addLayout(text, 1)
    if control is not None:
        if stack:
            lay.addWidget(control)
        else:
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
    return row


class SettingsPage(QWidget):
    def __init__(self, svc: Services) -> None:
        super().__init__()
        self.svc, self.settings = svc, svc.settings
        s = self.settings
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(14)
        root.addWidget(AvatarHeader(svc, "Make it yours.", compact=True))
        root.addWidget(label("SETTINGS", "title"))

        # -- appearance -------------------------------------------------------
        card, box = self._card("Appearance")
        self.swatches = SwatchRow(s.get("appearance", "accent"))
        box.addWidget(setting_row("Accent colour", "Used for the ring, buttons and highlights.", self.swatches, stack=True))
        self.base_tabs = Tabs([(k, v) for k, v in BASE_LABELS.items()], s.get("appearance", "base"))
        box.addWidget(setting_row("Style", "Charcoal is soft, AMOLED is pure black, Midnight leans blue.", self.base_tabs, stack=True))
        self.anim_toggle = Toggle(s.get("appearance", "animations"))
        box.addWidget(setting_row("Animations", "Breathing knight, fades and smooth ring movement.", self.anim_toggle))
        root.addWidget(card)

        # -- timer ------------------------------------------------------------
        card, box = self._card("Timer")
        self.focus_step = Stepper(s.get("timer", "focus_min"), 1, 240, " min")
        self.short_step = Stepper(s.get("timer", "short_min"), 1, 60, " min")
        self.long_step = Stepper(s.get("timer", "long_min"), 1, 120, " min")
        self.cycles_step = Stepper(s.get("timer", "cycles"), 2, 12, "")
        self.auto_toggle = Toggle(s.get("timer", "auto_start"))
        box.addWidget(setting_row("Focus length", "", self.focus_step))
        box.addWidget(setting_row("Short break", "", self.short_step))
        box.addWidget(setting_row("Long break", "", self.long_step))
        box.addWidget(setting_row("Sessions before a long break", "", self.cycles_step))
        box.addWidget(setting_row("Auto-start next session", "Roll straight into the next phase when one ends.", self.auto_toggle))
        root.addWidget(card)

        # -- alerts -----------------------------------------------------------
        card, box = self._card("Alerts & distraction")
        self.sound_toggle = Toggle(s.get("alerts", "sound"))
        self.notify_toggle = Toggle(s.get("alerts", "notifications"))
        self.mini_toggle = Toggle(s.get("alerts", "mini_timer"))
        box.addWidget(setting_row("Sound", "A short chime when a session or break ends.", self.sound_toggle))
        box.addWidget(setting_row("Desktop notifications", "Pop-up when a session ends.", self.notify_toggle))
        box.addWidget(
            setting_row(
                "Floating mini timer",
                "A small always-on-top timer while the timer runs and this window is in the background. Some Wayland desktops ignore always-on-top.",
                self.mini_toggle,
            )
        )
        root.addWidget(card)

        # -- messages ---------------------------------------------------------
        card, box = self._card("Knight messages")
        self.msg_toggle = Toggle(s.get("messages", "enabled"))
        self.interval_step = Stepper(s.get("messages", "interval_sec"), 10, 600, " s", step=5)
        self.msg_ai_toggle = Toggle(s.get("messages", "use_ai"))
        box.addWidget(setting_row("Show messages", "The knight talks while you work and rest.", self.msg_toggle))
        box.addWidget(setting_row("Rotate every", "", self.interval_step))
        box.addWidget(setting_row("Add AI-written messages", "Fresh lines from your AI model (needs the AI section below).", self.msg_ai_toggle))
        self.custom_edit = QPlainTextEdit("\n".join(s.get("messages", "custom")))
        self.custom_edit.setPlaceholderText("One message per line.\nOptional format:  HEADLINE | message\n\nFOCUS. | Phone down. Eyes up.")
        self.custom_edit.setFixedHeight(130)
        box.addWidget(
            setting_row(
                "Your own messages",
                "Shown during focus sessions instead of the built-in ones. Leave empty to use the defaults.",
                self.custom_edit,
                stack=True,
            )
        )
        actions = QHBoxLayout()
        self.msg_save = button("Save messages", "primary")
        self.msg_clear = button("Clear", "ghost")
        actions.addWidget(self.msg_save)
        actions.addWidget(self.msg_clear)
        actions.addStretch(1)
        box.addLayout(actions)
        root.addWidget(card)

        # -- AI ---------------------------------------------------------------
        card, box = self._card("AI (optional)")
        box.addWidget(
            label(
                "Works with any OpenAI-compatible API (OpenAI, OpenRouter, Groq, LM Studio, Ollama at http://localhost:11434/v1). "
                "It prioritises tasks, writes knight messages and explains your stats. Your key is stored in a private file on this computer "
                "and only sent to the URL below.",
                "hint",
                wrap=True,
            )
        )
        self.url_edit = QLineEdit(s.get("ai", "base_url"))
        self.url_edit.setPlaceholderText("https://api.openai.com/v1")
        self.key_edit = QLineEdit(s.get("ai", "api_key"))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-…  (leave empty for local models)")
        self.model_edit = QLineEdit(s.get("ai", "model"))
        self.model_edit.setPlaceholderText("gpt-4o-mini")
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        self.eye = QPushButton()
        self.eye.setCheckable(True)
        self.eye.setIcon(icons.icon("eye", theme.c("muted"), 18))
        self.eye.setFixedSize(40, 38)
        self.eye.setToolTip("Show / hide key")
        key_row.addWidget(self.eye)
        key_widget = QWidget()
        key_widget.setLayout(key_row)
        key_row.setContentsMargins(0, 0, 0, 0)
        box.addWidget(setting_row("Base URL", "", self.url_edit, stack=True))
        box.addWidget(setting_row("API key", "", key_widget, stack=True))
        box.addWidget(setting_row("Model", "", self.model_edit, stack=True))
        self.instr_edit = QPlainTextEdit(s.get("ai", "instructions"))
        self.instr_edit.setPlaceholderText("e.g. Call me Fahim. Keep answers under 3 lines. Plan my study around exams first.")
        self.instr_edit.setFixedHeight(110)
        box.addWidget(
            setting_row(
                "Important instructions",
                "The Knight sees these in every chat, so you don't have to repeat yourself. Max 1500 characters.",
                self.instr_edit,
                stack=True,
            )
        )
        self.instr_save = button("Save instructions", "primary")
        box.addWidget(self.instr_save)
        test_row = QHBoxLayout()
        self.test_btn = button("Test connection", "ghost", "zap")
        self.test_status = label("", "hint", wrap=True)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_status, 1)
        box.addLayout(test_row)
        root.addWidget(card)

        # -- memory & agent ---------------------------------------------------
        card, box = self._card("Memory & agent")
        box.addWidget(
            label(
                "The chat assistant can search your tasks, session notes and daily history. Light mode uses a tiny built-in "
                "index (no download, almost no RAM). Neural mode understands meaning better but downloads a ~90 MB model once "
                "and uses more memory. The index loads only when needed and unloads after 90 s idle.",
                "hint",
                wrap=True,
            )
        )
        self.mem_tabs = Tabs([("light", "Light"), ("neural", "Neural")], s.get("memory", "mode"))
        box.addWidget(setting_row("Memory mode", "", self.mem_tabs))
        self.name_edit = QLineEdit(s.get("profile", "name"))
        self.name_edit.setPlaceholderText("Used in PDF reports")
        box.addWidget(setting_row("Your name", "", self.name_edit, stack=True))
        self.folder_edit = QLineEdit(s.get("profile", "report_folder"))
        self.folder_edit.setPlaceholderText(str(report_mod.default_folder()))
        box.addWidget(setting_row("Report folder", "Where PDF reports are saved.", self.folder_edit, stack=True))
        self.mrow = mrow = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._mrow_stacked = False
        self.rebuild_btn = button("Rebuild memory index", "ghost", "reset")
        self.clear_chat_btn = button("Clear chat history", "ghost", "trash")
        self.mem_status = label("", "hint", wrap=True)
        mrow.addWidget(self.rebuild_btn)
        mrow.addWidget(self.clear_chat_btn)
        mrow.addWidget(self.mem_status, 1)
        box.addLayout(mrow)
        root.addWidget(card)

        # -- data -------------------------------------------------------------
        card, box = self._card("Data")
        box.addWidget(label(f"Stored in {paths.data_dir()}", "hint", wrap=True))
        self.reset_btn = button("Reset settings", "ghost", "reset")
        self.wipe_btn = button("Delete all data", "danger", "trash")
        box.addWidget(setting_row("Reset settings", "Restore every option above to its default (keeps your tasks and stats).", self.reset_btn))
        box.addWidget(setting_row("Delete all data", "Permanently erase all tasks, sessions and stats. This can't be undone.", self.wipe_btn))
        root.addWidget(card)
        root.addStretch(1)

        self._wire()

    # -- helpers ------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Narrow window: stack the two memory buttons instead of forcing the page wider.
        stacked = self.width() < 600
        if stacked != self._mrow_stacked:
            self._mrow_stacked = stacked
            self.mrow.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)

    @staticmethod
    def _card(title: str) -> tuple[Card, QVBoxLayout]:
        card = Card()
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 20, 24, 18)
        box.setSpacing(8)
        box.addWidget(caps(title))
        return card, box

    def _wire(self) -> None:
        s = self.settings
        self.swatches.picked.connect(lambda c: s.set("appearance", "accent", c))
        self.base_tabs.changed.connect(lambda k: s.set("appearance", "base", k))
        self.anim_toggle.toggled.connect(lambda v: s.set("appearance", "animations", v))

        self.focus_step.valueChanged.connect(lambda v: s.set("timer", "focus_min", v))
        self.short_step.valueChanged.connect(lambda v: s.set("timer", "short_min", v))
        self.long_step.valueChanged.connect(lambda v: s.set("timer", "long_min", v))
        self.cycles_step.valueChanged.connect(lambda v: s.set("timer", "cycles", v))
        self.auto_toggle.toggled.connect(lambda v: s.set("timer", "auto_start", v))

        self.sound_toggle.toggled.connect(lambda v: s.set("alerts", "sound", v))
        self.notify_toggle.toggled.connect(lambda v: s.set("alerts", "notifications", v))
        self.mini_toggle.toggled.connect(lambda v: s.set("alerts", "mini_timer", v))

        self.msg_toggle.toggled.connect(lambda v: s.set("messages", "enabled", v))
        self.interval_step.valueChanged.connect(lambda v: s.set("messages", "interval_sec", v))
        self.msg_ai_toggle.toggled.connect(lambda v: s.set("messages", "use_ai", v))
        self.msg_save.clicked.connect(self._save_messages)
        self.msg_clear.clicked.connect(self._clear_messages)

        self.url_edit.editingFinished.connect(self._save_ai)
        self.key_edit.editingFinished.connect(self._save_ai)
        self.model_edit.editingFinished.connect(self._save_ai)
        self.eye.toggled.connect(
            lambda on: self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        self.test_btn.clicked.connect(self._test_ai)
        self.instr_save.clicked.connect(self._save_instructions)

        self.mem_tabs.changed.connect(lambda k: s.set("memory", "mode", k))
        self.name_edit.editingFinished.connect(lambda: s.set("profile", "name", self.name_edit.text().strip()))
        self.folder_edit.editingFinished.connect(lambda: s.set("profile", "report_folder", self.folder_edit.text().strip()))
        self.rebuild_btn.clicked.connect(self._rebuild_memory)
        self.clear_chat_btn.clicked.connect(self._clear_chat)

        self.reset_btn.clicked.connect(self._reset_settings)
        self.wipe_btn.clicked.connect(self._wipe)
        s.changed.connect(self._on_changed)

    def _on_changed(self, section: str, key: str) -> None:
        # The agent can change settings too, so keep every control in step.
        self.reload_from_settings(everything=(section == "*"))

    def reload_from_settings(self, everything: bool = True) -> None:
        s = self.settings
        self.swatches.set_current(s.get("appearance", "accent"))
        self.base_tabs.set_current(s.get("appearance", "base"))
        for widget, (sec, key) in (
            (self.anim_toggle, ("appearance", "animations")),
            (self.auto_toggle, ("timer", "auto_start")),
            (self.sound_toggle, ("alerts", "sound")),
            (self.notify_toggle, ("alerts", "notifications")),
            (self.mini_toggle, ("alerts", "mini_timer")),
            (self.msg_toggle, ("messages", "enabled")),
            (self.msg_ai_toggle, ("messages", "use_ai")),
        ):
            widget.set_checked_silent(s.get(sec, key))
        for step, (sec, key) in (
            (self.focus_step, ("timer", "focus_min")),
            (self.short_step, ("timer", "short_min")),
            (self.long_step, ("timer", "long_min")),
            (self.cycles_step, ("timer", "cycles")),
            (self.interval_step, ("messages", "interval_sec")),
        ):
            step.blockSignals(True)
            step.setValue(s.get(sec, key))
            step.blockSignals(False)
            step._refresh()
        self.mem_tabs.set_current(s.get("memory", "mode"))
        # Text fields: don't overwrite something the person is typing right now.
        for edit, value in (
            (self.custom_edit, "\n".join(s.get("messages", "custom"))),
            (self.url_edit, s.get("ai", "base_url")),
            (self.key_edit, s.get("ai", "api_key")),
            (self.model_edit, s.get("ai", "model")),
            (self.instr_edit, s.get("ai", "instructions")),
            (self.name_edit, s.get("profile", "name")),
            (self.folder_edit, s.get("profile", "report_folder")),
        ):
            if everything or not edit.hasFocus():
                current = edit.toPlainText() if isinstance(edit, QPlainTextEdit) else edit.text()
                if current != value:
                    edit.setPlainText(value) if isinstance(edit, QPlainTextEdit) else edit.setText(value)

    # -- actions ------------------------------------------------------------
    def _save_messages(self) -> None:
        lines = [ln.strip() for ln in self.custom_edit.toPlainText().splitlines() if ln.strip()]
        self.settings.set("messages", "custom", lines)
        self.svc.rotate_message()
        self.svc.toast.emit("ok", f"Saved {len(lines)} custom message{'s' if len(lines) != 1 else ''}." if lines else "Using the built-in messages.")

    def _clear_messages(self) -> None:
        self.custom_edit.clear()
        self._save_messages()

    def _save_ai(self) -> None:
        s = self.settings
        s.set("ai", "base_url", self.url_edit.text().strip())
        s.set("ai", "api_key", self.key_edit.text().strip())
        s.set("ai", "model", self.model_edit.text().strip())

    def _save_instructions(self) -> None:
        text = self.instr_edit.toPlainText().strip()[:1500]
        self.settings.set("ai", "instructions", text)
        self.test_status.setText("Instructions saved.")

    def _test_ai(self) -> None:
        self._save_ai()
        self.test_btn.setEnabled(False)
        self.test_status.setProperty("role", "hint")
        self.test_status.setText("Connecting…")

        def finish(role: str, text: str) -> None:
            self.test_btn.setEnabled(True)
            self.test_status.setProperty("role", role)
            self.test_status.setText(text)
            self.test_status.style().unpolish(self.test_status)
            self.test_status.style().polish(self.test_status)

        if not self.svc.run_ai(
            ai_mod.check_connection,
            lambda reply: finish("ok", f"Connected. Model replied: {reply}"),
            lambda msg: finish("bad", msg),
        ):
            finish("bad", "Fill in the base URL, API key and model first.")

    def _rebuild_memory(self) -> None:
        self.rebuild_btn.setEnabled(False)
        self.mem_status.setText("Indexing…")
        docs = build_documents(self.svc.db)  # SQLite stays on the UI thread

        def finish(text: str) -> None:
            self.rebuild_btn.setEnabled(True)
            self.mem_status.setText(text)

        self.svc.run_bg(
            lambda: self.svc.memory.rebuild(docs),
            lambda r: finish(f"Indexed {len(docs)} items."),
            lambda msg: finish(f"Couldn't build the index: {msg}"),
        )

    def _clear_chat(self) -> None:
        self.svc.agent_clear_requested.emit()
        self.mem_status.setText("Chat history cleared.")

    def _reset_settings(self) -> None:
        box = QMessageBox(self.window())
        box.setWindowTitle("Reset settings")
        box.setText("Reset all settings to their defaults?")
        box.setInformativeText("Your tasks and stats are kept. Your AI key will be cleared.")
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok)
        if box.exec() == QMessageBox.StandardButton.Ok:
            self.settings.reset()
            self.svc.toast.emit("ok", "Settings reset.")

    def _wipe(self) -> None:
        box = QMessageBox(self.window())
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete all data")
        box.setText("Delete all tasks, sessions and stats?")
        box.setInformativeText("This permanently erases your history and can't be undone.")
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText("Delete everything")
        if box.exec() == QMessageBox.StandardButton.Yes:
            self.svc.wipe_data()
            self.svc.toast.emit("ok", "All data deleted.")
