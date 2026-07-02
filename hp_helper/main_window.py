"""Main application window with sidebar, stacked pages, and system tray."""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QIcon, QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QMainWindow, QMenu, QStackedWidget,
    QSystemTrayIcon, QWidget,
)

from hp_helper.theme import COLORS
from hp_helper.widgets.sidebar import Sidebar
from hp_helper.pages.home_page import HomePage
from hp_helper.pages.fans_power_page import FansPowerPage
from hp_helper.pages.sensors_page import SensorsPage
from hp_helper.pages.keyboard_page import KeyboardPage
from hp_helper.sensor_graph_window import SensorGraphWindow
from hp_helper.fan_curves_window import FanCurvesWindow
from hp_helper import api
from hp_helper.fan_control import start_fan_control
from hp_helper.lighting_controller import LightingController
from hp_helper.power_controller import PowerLimitController
from hp_helper.sensor_stats import next_stats, build_rows

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    """Main application window with tray icon and close-to-tray behavior."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HP Helper")
        self.resize(960, 640)
        self.setMinimumSize(520, 400)

        # App icon
        icon_path = Path(__file__).parent / "resources" / "icons" / "icon.png"
        self._app_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self.setWindowIcon(self._app_icon)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        self._sidebar = Sidebar()
        layout.addWidget(self._sidebar)

        # Stacked pages
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        # Create pages
        self._home_page = HomePage()
        self._fans_page = FansPowerPage()
        self._sensors_page = SensorsPage()
        self._keyboard_page = KeyboardPage()

        self._pages = [
            self._home_page,
            self._fans_page,
            self._sensors_page,
            self._keyboard_page,
        ]
        for page in self._pages:
            self._stack.addWidget(page)

        # Sidebar -> stack sync
        self._sidebar.tab_changed.connect(self._on_tab_changed)

        # ── System tray ──
        self._tray = QSystemTrayIcon(self._app_icon, self)
        self._tray.setToolTip("HP Helper")
        self._tray.activated.connect(self._on_tray_activated)

        tray_menu = QMenu()
        show_action = QAction("Show/Hide", self)
        show_action.triggered.connect(self._toggle_visible)
        tray_menu.addAction(show_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.show()

        # ── Settings ──
        self._settings = QSettings()
        self._restore_geometry()

        # ── State ──
        self._stats_by_key: dict[str, dict] = {}
        self._selected_profile = 1
        self._quitting = False

        # ── Signal connections ──

        # Home: profile selection + fan modes
        self._home_page.profile_selected.connect(self._on_profile_select)
        self._home_page.fan_mode_selected.connect(self._on_fan_mode)
        self._home_page.fan_curves_popout_requested.connect(self._open_fan_curves_window)

        # Fans: pop-out request
        self._fans_page.fan_curves_popout_requested.connect(self._open_fan_curves_window)

        # Sensors: graph pop-out requests
        self._sensors_page.open_graph_requested.connect(self._open_sensor_graph)

        # Window tracking
        self._graph_windows: dict[str, SensorGraphWindow] = {}
        self._hidden_graph_windows: set[str] = set()  # keys hidden by tray-close
        self._fan_curves_window: FanCurvesWindow | None = None
        self._lighting = LightingController(self)
        self._lighting.frame_changed.connect(self._keyboard_page.apply_frame)
        self._keyboard_page.enabled_changed.connect(self._lighting.set_enabled)
        self._keyboard_page.effect_changed.connect(self._lighting.set_effect)
        self._keyboard_page.color_changed.connect(self._lighting.set_color)
        self._keyboard_page.speed_changed.connect(self._lighting.set_speed)
        self._keyboard_page.idle_timeout_changed.connect(self._lighting.set_idle_timeout)

        self._power = PowerLimitController(self)

        # ── Timers ──

        # Sensor poll (1s)
        self._sensor_timer = QTimer(self)
        self._sensor_timer.setInterval(1000)
        self._sensor_timer.timeout.connect(self._poll_sensors)
        self._sensor_timer.start()

        # Profile poll (2s)
        self._profile_timer = QTimer(self)
        self._profile_timer.setInterval(2000)
        self._profile_timer.timeout.connect(self._poll_profile)
        self._profile_timer.start()

        # Fan-control background thread
        start_fan_control()
        # Sync fan mode buttons from the persisted config — if custom was
        # enabled when the app was last used, the fan-control loop will resume
        # writing PWM values, so the UI must show "custom" as selected.
        try:
            _cfg = api.get_fan_config()
            if _cfg.custom_enabled:
                self._home_page.set_selected_fan_mode("custom")
        except Exception:
            logger.exception("init fan config check failed")
    # ── Tab switching ──

    def set_active_tab(self, index: int):
        """Switch to the given tab index."""
        self._sidebar.set_active(index)
        self._stack.setCurrentIndex(index)

    def _on_tab_changed(self, index: int):
        self._stack.setCurrentIndex(index)

    # ── Tray ──
    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self._show_all_windows()

    def _toggle_visible(self):
        if self.isVisible():
            self._hide_all_windows()
        else:
            self._show_all_windows()


    def _hide_all_windows(self):
        """Hide main window + all child windows to tray."""
        for key, win in list(self._graph_windows.items()):
            if win.isVisible():
                win.hide()
                self._hidden_graph_windows.add(key)
        if self._fan_curves_window is not None and self._fan_curves_window.isVisible():
            self._fan_curves_window.hide()
            self._hidden_graph_windows.add("__fan_curves__")
        self.hide()

    def _show_all_windows(self):
        """Show main window + restore hidden child windows."""
        self.show()
        self.raise_()
        self.activateWindow()
        for key in list(self._hidden_graph_windows):
            if key == "__fan_curves__":
                if self._fan_curves_window is not None:
                    self._fan_curves_window.show()
            else:
                win = self._graph_windows.get(key)
                if win is not None:
                    win.show()
        self._hidden_graph_windows.clear()

    def _quit_app(self):
        """Restore fan to auto, then quit the entire application."""
        try:
            api.set_fan_auto()
        except Exception:
            logger.exception("set fan auto during quit failed")
        self._tray.hide()
        self._quitting = True
        QApplication.instance().quit()
    def closeEvent(self, event: QCloseEvent):
        """Hide all windows to tray instead of quitting, unless actually quitting."""
        if getattr(self, "_quitting", False):
            event.accept()
            return
        self._save_geometry()
        self._hide_all_windows()
        event.ignore()
    # ── Geometry persistence ──

    def _restore_geometry(self):
        geo = self._settings.value("window/geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(960, 640)

    def _save_geometry(self):
        self._settings.setValue("window/geometry", self.saveGeometry())

    # ── Sensor polling ──

    def _poll_sensors(self):
        try:
            snapshot = api.read_sensors()
        except Exception:
            logger.exception("sensor poll failed")
            return

        # Update stats
        self._stats_by_key = next_stats(snapshot, self._stats_by_key)

        # Update Home page gauges
        self._home_page.update_sensor_data(snapshot)

        # Update Sensors page rows
        rows = build_rows(snapshot, self._stats_by_key)
        self._sensors_page.update_rows(rows)

        # Update footer hardware title
        self._home_page.set_hardware_title(api.get_hardware_title())

    # ── Profile polling ──

    def _poll_profile(self):
        try:
            profile = api.get_current_profile()
        except Exception:
            logger.exception("profile poll failed")
            return
        if profile is not None and profile != self._selected_profile:
            self._selected_profile = profile
            self._home_page.set_selected_profile(profile)
            self._fans_page.set_edit_profile(profile)
            if self._fan_curves_window is not None:
                self._fan_curves_window.set_edit_profile(profile)

    # ── Profile selection ──

    def _on_profile_select(self, index: int):
        self._selected_profile = index
        self._home_page.set_selected_profile(index)
        self._fans_page.set_edit_profile(index)
        if self._fan_curves_window is not None:
            self._fan_curves_window.set_edit_profile(index)
        try:
            api.set_system_profile(index)
        except Exception:
            logger.exception("set system profile failed")

    # ── Fan mode ──

    def _on_fan_mode(self, mode: str):
        """Handle Auto/Max/Custom fan mode button clicks."""
        if mode == "auto":
            self._set_fan_auto()
        elif mode == "max":
            self._set_fan_max()
        elif mode == "custom":
            self._set_fan_custom()

    def _set_fan_auto(self):
        try:
            api.set_manual_preset("auto")
        except Exception:
            logger.exception("set manual preset (auto) failed")
        try:
            api.set_custom_fan_enabled(False)
        except Exception:
            logger.exception("set custom fan enabled (auto) failed")
        try:
            api.set_fan_auto()
        except Exception:
            logger.exception("set fan auto failed")

    def _set_fan_max(self):
        try:
            api.set_manual_preset("max")
        except Exception:
            logger.exception("set manual preset (max) failed")
        try:
            api.set_custom_fan_enabled(False)
        except Exception:
            logger.exception("set custom fan enabled (max) failed")
        try:
            api.set_fan_pwm(255)
        except Exception:
            logger.exception("set fan pwm failed")

    def _set_fan_custom(self):
        try:
            api.set_manual_preset(None)
        except Exception:
            logger.exception("set manual preset (custom) failed")
        try:
            api.set_custom_fan_enabled(True)
        except Exception:
            logger.exception("set custom fan enabled failed")
    def _open_sensor_graph(self, key: str):
        """Open or focus a sensor graph window for the given sensor key."""
        existing = self._graph_windows.get(key)
        if existing is not None:
            # If previously hidden-by-tray, just show it
            if not existing.isVisible():
                existing.show()
            existing.raise_()
            existing.activateWindow()
            self._hidden_graph_windows.discard(key)
            return
        win = SensorGraphWindow(key)
        win.setAttribute(Qt.WA_DeleteOnClose)
        # Clean up from both tracking dicts when the window is closed by the user
        def _on_destroyed(obj=None, k=key):
            self._graph_windows.pop(k, None)
            self._hidden_graph_windows.discard(k)
        win.destroyed.connect(_on_destroyed)
        self._graph_windows[key] = win
        win.show()


    # ── Fan curves pop-out ──

    def _open_fan_curves_window(self):
        """Open or focus the standalone fan curves window."""
        if self._fan_curves_window is not None:
            self._fan_curves_window.show()
            self._fan_curves_window.raise_()
            self._fan_curves_window.activateWindow()
            return
        win = FanCurvesWindow()
        win.set_edit_profile(self._selected_profile)
        win.setAttribute(Qt.WA_DeleteOnClose)
        def _on_destroyed(obj=None):
            self._fan_curves_window = None
            self._fans_page.set_fan_curves_window_open(False)
            self._fans_page.refresh_fan_curves()
        win.destroyed.connect(_on_destroyed)
        self._fan_curves_window = win
        self._fans_page.set_fan_curves_window_open(True)
        win.show()
