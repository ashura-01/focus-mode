import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_MODE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _no_ai_pacing(monkeypatch):
    """Tests hit a local fake server: no pacing delay, and no cooldown leaking between tests."""
    from focus_mode import ai

    monkeypatch.setattr(ai, "MIN_GAP", 0.0)
    monkeypatch.setattr(ai, "_next_ok", 0.0)
    ai._json_ok.clear()
    ai._cache.clear()
