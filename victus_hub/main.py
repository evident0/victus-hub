"""Entry point for Victus Hub Qt application."""

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from victus_hub.app.main_window import MainWindow
from victus_hub.app.single_instance import SingleInstanceGuard, default_socket_path


# Surface daemon-traffic logs in the terminal; daemon_client + main_window
# both rely on this being configured at startup.
logging.basicConfig(level=logging.INFO, format="%(message)s")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("victus-hub")
    app.setOrganizationName("victus-hub")
    # App-wide window icon (logoV) so every top-level window (main, sensor graphs, fan curves, etc.) gets it
    from victus_hub.app.icon_utils import load_icon
    app_icon = load_icon("logoV.png", size=48)
    app.setWindowIcon(app_icon)


    # Load global QSS stylesheet
    resources_dir = Path(__file__).parent / "resources"
    style_path = resources_dir / "style.qss"
    if style_path.exists():
        with open(style_path, "r") as f:
            qss = f.read()
        qss = qss.replace("url(icons/", f"url({resources_dir.as_posix()}/icons/")
        app.setStyleSheet(qss)

    # Allow dark title bar on Windows
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(app.activePopupWidget().winId()) if app.activePopupWidget() else 0,
            20,  # DWMWA_USE_IMMERSIVE_DARK_MODE
            ctypes.byref(ctypes.c_int(1)),
            ctypes.sizeof(ctypes.c_int),
        )
    # ── Single-instance guard ──
    # A second launch connects to this socket, sends "raise", and exits;
    # we (the primary) receive it and bring our windows to the front.
    guard = SingleInstanceGuard(default_socket_path())
    if not guard.claim():
        # We were a secondary launch; the primary has already been notified.
        app.quit()
        return

    window = MainWindow()
    window.show()

    guard.raise_requested.connect(window._show_all_windows)
    guard.flush_pending()

    # Keep the guard alive for the whole app lifetime.
    app.setProperty("__single_instance_guard", guard)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
