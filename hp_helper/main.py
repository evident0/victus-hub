"""Entry point for HP Helper Qt application."""

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from hp_helper.main_window import MainWindow


# Surface daemon-traffic logs in the terminal; daemon_client + main_window
# both rely on this being configured at startup.
logging.basicConfig(level=logging.INFO, format="%(message)s")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("hp-helper")
    app.setOrganizationName("hp-helper")

    # Load global QSS stylesheet
    style_path = Path(__file__).parent / "resources" / "style.qss"
    if style_path.exists():
        with open(style_path, "r") as f:
            app.setStyleSheet(f.read())

    # Allow dark title bar on Windows
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(app.activePopupWidget().winId()) if app.activePopupWidget() else 0,
            20,  # DWMWA_USE_IMMERSIVE_DARK_MODE
            ctypes.byref(ctypes.c_int(1)),
            ctypes.sizeof(ctypes.c_int),
        )

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
