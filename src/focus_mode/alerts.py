"""End-of-session sound and desktop notifications."""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon

from focus_mode import paths

# Sounds go through the desktop's own players instead of QtMultimedia, so a machine
# with no audio stack can never crash the app.
PLAYERS = ("pw-play", "paplay", "aplay", "ffplay")

RATE = 44100


def _tone(freq: float, seconds: float, volume: float = 0.5) -> list[int]:
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        t = i / RATE
        env = min(1.0, t / 0.01) * math.exp(-4.5 * t / seconds)  # soft attack, decaying bell
        wave_ = math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(2 * math.pi * freq * 2 * t)
        out.append(int(32767 * volume * env * wave_ / 1.3))
    return out


def _write_wav(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def ensure_sounds() -> dict[str, Path]:
    """Generate the two chimes once; no audio files ship with the app."""
    folder = paths.cache_dir()
    files = {
        # rising pair = focus finished, falling pair = break over
        "focus_done": (folder / "focus_done.wav", [(659.25, 0.28), (880.0, 0.28), (1318.5, 0.7)]),
        "break_done": (folder / "break_done.wav", [(880.0, 0.28), (659.25, 0.5)]),
    }
    result = {}
    for name, (path, notes) in files.items():
        if not path.exists():
            samples: list[int] = []
            for freq, length in notes:
                samples += _tone(freq, length)
            _write_wav(path, samples)
        result[name] = path
    return result


class Alerts:
    def __init__(self) -> None:
        self._tray: QSystemTrayIcon | None = None
        self._files: dict[str, Path] = {}
        self._player = next((p for p in PLAYERS if shutil.which(p)), None)
        try:
            self._files = ensure_sounds()
        except OSError:
            pass

    def play(self, name: str) -> None:
        path = self._files.get(name)
        if not (path and self._player):
            return
        cmd = [shutil.which(self._player), str(path)]
        if self._player == "ffplay":
            cmd = [cmd[0], "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def notify(self, title: str, body: str) -> None:
        """Prefer notify-send (reliable on most Linux desktops), fall back to the tray."""
        exe = shutil.which("notify-send")
        if exe:
            try:
                subprocess.Popen(
                    [exe, "-a", "Focus Mode", "-i", str(paths.asset("icon.png")), title, body],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except OSError:
                pass
        if QSystemTrayIcon.isSystemTrayAvailable():
            if self._tray is None:
                self._tray = QSystemTrayIcon(QIcon(str(paths.asset("icon.png"))))
                self._tray.show()
            self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 6000)
