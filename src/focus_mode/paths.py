"""Where things live on disk.

Everything the app stores sits under one folder, ~/.focus-mode, so removing the app
completely is `rm -rf ~/.focus-mode`:

    ~/.focus-mode/data/    database and AI memory
    ~/.focus-mode/config/  settings
    ~/.focus-mode/cache/   generated sounds

Set FOCUS_MODE_HOME to use a different folder instead (handy for tests and screenshots).
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = ".focus-mode"


def _base(sub: str) -> Path:
    override = os.environ.get("FOCUS_MODE_HOME")
    root = Path(override) if override else Path.home() / APP_DIR
    path = root / sub
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    return _base("data")


def config_dir() -> Path:
    return _base("config")


def cache_dir() -> Path:
    return _base("cache")


def db_path() -> Path:
    return data_dir() / "focus.db"


def config_path() -> Path:
    return config_dir() / "config.toml"


def asset(name: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / name
