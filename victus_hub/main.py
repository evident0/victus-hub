"""Entry point for Victus Hub Qt application."""

import sys

from PySide6.QtDBus import QDBusConnection
from PySide6.QtWidgets import QApplication

from victus_hub.app.activation import (
    BUS_NAME, OBJECT_PATH, ApplicationService, notify_existing_instance,
    request_activation,
)
from victus_hub.app.main_window import MainWindow
from victus_hub.app.theme import load_fonts, stylesheet, ui_font
from victus_hub.backend.session_log import install as install_session_log
from victus_hub.logging_config import configure_terminal_logging


configure_terminal_logging()
install_session_log()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("victus-hub")
    app.setOrganizationName("victus-hub")
    # Wayland app id. Must match victus-hub.desktop; otherwise GNOME uses the python binary name.
    app.setDesktopFileName("victus-hub")
    # App-wide icon for the main window and sensor graphs.
    from victus_hub.app.icon_utils import load_icon
    app_icon = load_icon("logoV.png", size=48)
    app.setWindowIcon(app_icon)


    load_fonts()
    app.setFont(ui_font(13))

    # Load global QSS stylesheet
    app.setStyleSheet(stylesheet())

    # Allow dark title bar on Windows
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(app.activePopupWidget().winId()) if app.activePopupWidget() else 0,
            20,  # DWMWA_USE_IMMERSIVE_DARK_MODE
            ctypes.byref(ctypes.c_int(1)),
            ctypes.sizeof(ctypes.c_int),
        )
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise RuntimeError("Victus Hub needs an active desktop session bus")
    service = ApplicationService()
    if not bus.registerObject(OBJECT_PATH, service, QDBusConnection.RegisterOption.ExportAllSlots):
        raise RuntimeError(f"Could not register Victus Hub D-Bus object: {bus.lastError().message()}")
    if not bus.registerService(BUS_NAME):
        bus.unregisterObject(OBJECT_PATH)
        if request_activation(bus):
            notify_existing_instance()
            return
        raise RuntimeError(f"Could not claim Victus Hub D-Bus name: {bus.lastError().message()}")

    window = MainWindow()
    window.show()

    service.activate_requested.connect(window._show_all_windows)
    service.flush_pending()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
