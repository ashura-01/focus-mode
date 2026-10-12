"""User settings, stored as TOML in ~/.focus-mode/config/config.toml.

Settings is a thin QObject so the UI can react to changes, but the file
format and defaults live here and have no UI dependencies.
"""

from __future__ import annotations

import copy
import json
import os
import tomllib
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from focus_mode import paths

DEFAULTS: dict[str, dict[str, Any]] = {
    "appearance": {"accent": "#e0474f", "base": "charcoal", "animations": True},
    "timer": {
        "focus_min": 25,
        "short_min": 5,
        "long_min": 15,
        "cycles": 4,
        "auto_start": False,
    },
    "alerts": {"sound": True, "notifications": True, "mini_timer": True},
    "messages": {"enabled": True, "interval_sec": 45, "use_ai": False, "custom": []},
    "memory": {"mode": "light"},
    "profile": {"name": "", "report_folder": ""},
    "ai": {"base_url": "https://api.openai.com/v1", "api_key": "", "model": "gpt-4o-mini"},
}

_LIMITS = {
    ("timer", "focus_min"): (1, 240),
    ("timer", "short_min"): (1, 60),
    ("timer", "long_min"): (1, 120),
    ("timer", "cycles"): (2, 12),
    ("messages", "interval_sec"): (10, 600),
}


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    # JSON string escapes are valid TOML basic-string escapes.
    return json.dumps(str(value), ensure_ascii=False)


def dumps(data: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = ["# Focus Mode settings. Safe to edit while the app is closed.", ""]
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _coerce(default: Any, value: Any) -> Any:
    """Keep a loaded value only if it has the same type as the default."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, list):
        return [str(v) for v in value] if isinstance(value, list) else default
    return value if isinstance(value, str) else default


class Settings(QObject):
    changed = Signal(str, str)  # section, key

    def __init__(self, path: Path | None = None) -> None:
        super().__init__()
        self.path = Path(path) if path else paths.config_path()
        self.data: dict[str, dict[str, Any]] = copy.deepcopy(DEFAULTS)
        self.load()

    # -- persistence --------------------------------------------------------
    def load(self) -> None:
        self.data = copy.deepcopy(DEFAULTS)
        if not self.path.exists():
            return
        try:
            raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return  # corrupt file: fall back to defaults, don't crash
        for section, defaults in DEFAULTS.items():
            loaded = raw.get(section, {})
            if not isinstance(loaded, dict):
                continue
            for key, default in defaults.items():
                if key in loaded:
                    self.data[section][key] = self._clamp(section, key, _coerce(default, loaded[key]))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # The file can hold an API key, so keep it private to the user.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dumps(self.data))
        os.replace(tmp, self.path)

    # -- access -------------------------------------------------------------
    def get(self, section: str, key: str) -> Any:
        return self.data[section][key]

    def set(self, section: str, key: str, value: Any) -> None:
        value = self._clamp(section, key, _coerce(DEFAULTS[section][key], value))
        if self.data[section][key] == value:
            return
        self.data[section][key] = value
        self.save()
        self.changed.emit(section, key)

    def reset(self) -> None:
        self.data = copy.deepcopy(DEFAULTS)
        self.save()
        self.changed.emit("*", "*")

    @staticmethod
    def _clamp(section: str, key: str, value: Any) -> Any:
        limits = _LIMITS.get((section, key))
        if limits and isinstance(value, int):
            return max(limits[0], min(limits[1], value))
        return value
