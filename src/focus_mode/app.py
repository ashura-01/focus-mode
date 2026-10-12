"""Entry point: `focus-mode`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from focus_mode import APP_NAME, __version__, paths
from focus_mode.config import Settings
from focus_mode.db import Database
from focus_mode.services import Services
from focus_mode.ui.main_window import MainWindow
from focus_mode.ui.theme import theme

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Focus Mode
Comment=Stay disciplined. Get results.
Exec={exe}
Icon={icon}
Terminal=false
Categories=Utility;
StartupWMClass=focus-mode
"""


def install_desktop_entry() -> Path:
    """Add Focus Mode to the application launcher (~/.local/share/applications)."""
    apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    exe = Path(sys.argv[0]).resolve() if Path(sys.argv[0]).name == "focus-mode" else Path(sys.executable)
    command = f"{exe}" if exe.name == "focus-mode" else f"{exe} -m focus_mode"
    target = apps / "focus-mode.desktop"
    target.write_text(DESKTOP_ENTRY.format(exe=command, icon=paths.asset("icon.png")), encoding="utf-8")
    return target


def apply_appearance(app: QApplication, settings: Settings) -> None:
    theme.configure(
        settings.get("appearance", "base"),
        settings.get("appearance", "accent"),
        settings.get("appearance", "animations"),
    )
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.stylesheet())


def wire_appearance(app: QApplication, settings: Settings) -> None:
    """Apply the saved look now, and again whenever an appearance setting changes."""
    apply_appearance(app, settings)
    settings.changed.connect(lambda section, _key: apply_appearance(app, settings) if section in ("appearance", "*") else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="focus-mode", description="A focus timer with a knight guardian.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--install-desktop", action="store_true", help="add a launcher entry to your applications menu")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    if args.install_desktop:
        print(f"Installed launcher: {install_desktop_entry()}")
        return 0

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("focus-mode")
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("focus-mode")
    app.setWindowIcon(QIcon(str(paths.asset("icon.png"))))
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(["Inter", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans"])
    font.setPixelSize(14)
    app.setFont(font)

    settings = Settings()
    db = Database(paths.db_path())
    svc = Services(settings, db)

    wire_appearance(app, settings)

    window = MainWindow(svc)
    window.show()
    code = app.exec()
    settings.save()
    db.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
