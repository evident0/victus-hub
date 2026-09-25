"""Main application window with sidebar, stacked pages, and system tray."""

import logging

from PySide6.QtCore import Qt, QSettings, QTimer, QEvent
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QMainWindow, QScrollArea,
    QStackedWidget, QSystemTrayIcon, QWidget,
)

from victus_hub.app.theme import set_accent, stylesheet
from victus_hub.app.icon_utils import load_icon
from victus_hub.widgets.popup_menu import PopupMenu
from victus_hub.widgets.profile_section import FAN_MODES, PROFILES
from victus_hub.widgets.sidebar import Sidebar
from victus_hub.pages.home_page import HomePage
from victus_hub.pages.power_page import PowerPage
from victus_hub.pages.fans_page import FansPage
from victus_hub.pages.sensors_page import SensorsPage
from victus_hub.pages.keyboard_page import KeyboardPage
from victus_hub.pages.settings_page import SettingsPage
from victus_hub.windows.sensor_graph_window import SensorGraphWindow
from victus_hub import api
from victus_hub.services.lighting_controller import LightingController
from victus_hub.services.profile_watcher import ProfileWatcher
from victus_hub.app.power_state import PowerStateWatcher
from victus_hub.services.shortcut_controller import ShortcutController
from victus_hub.features.sensors.stats import next_stats, build_rows

logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    """Main application window with tray icon and close-to-tray behavior."""

    def __init__(self):
        super().__init__()
        api.start_sensor_reader()
        api.sync_settings_with_daemon()
        api.sync_program_shortcut()
        self._capabilities = api.get_hardware_capabilities()
        self.setWindowTitle("Victus Hub")
        self.resize(self._PAGE_WIDTH[0], self._WINDOW_HEIGHT)
        self.setMinimumSize(420, self._WINDOW_HEIGHT)

        # App icon — logoV.png with native colors (no tint, no solid background)
        self._app_icon = load_icon("logoV.png", size=48)
        self.setWindowIcon(self._app_icon)

        # Central widget
        central = QWidget()
        central.setObjectName("appCentral")
        self.setCentralWidget(central)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        self._sidebar = Sidebar()
        self._sidebar.set_tab_visible(3, self._capabilities.keyboard_lighting)
        layout.addWidget(self._sidebar)

        # Stacked pages — wrapped in a scroll area so a page taller than the
        # window (min height is a fixed 700) scrolls instead of squishing.
        self._stack = QStackedWidget()
        self._stack.setObjectName("pageStack")
        self._stack.setMinimumWidth(360)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("pageScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.viewport().setObjectName("pageViewport")
        self._scroll.setWidget(self._stack)
        layout.addWidget(self._scroll, 1)

        self._home_page = HomePage(
            fan_modes=self._capabilities.fan_modes,
            keyboard_lighting=self._capabilities.keyboard_lighting,
            gpu_mux=self._capabilities.gpu_mux,
        )
        self._power_page = PowerPage()
        self._fans_page = FansPage(fan_modes=self._capabilities.fan_modes)
        self._sensors_page = SensorsPage()
        self._keyboard_page = KeyboardPage()
        self._settings_page = SettingsPage()
        self._ui_active = False

        self._pages = [
            self._home_page,
            self._power_page,
            self._fans_page,
            self._keyboard_page,
            self._sensors_page,
            self._settings_page,
        ]
        for page in self._pages:
            self._stack.addWidget(page)

        # Keep the stack's inner min height in sync so a tall page scrolls
        # instead of squishing. Window min height stays 700.
        self._stack.currentChanged.connect(self._on_current_page_changed)

        # Sidebar -> stack sync
        self._sidebar.tab_changed.connect(self._on_tab_changed)

        self._selected_profile = 1
        self._profile_before_battery = None
        self._selected_fan_mode = "auto"

        # ── System tray ──
        self._tray = QSystemTrayIcon(self._app_icon, self)
        self._tray.setToolTip("Victus Hub")
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setContextMenu(self._build_tray_menu())
        self._tray.show()

        # ── Settings ──
        self._settings = QSettings()
        self._restore_geometry()

        # ── State ──
        self._stats_by_key: dict[str, dict] = {}
        self._quitting = False

        # ── Signal connections ──

        # Home: profile selection + fan modes
        self._home_page.profile_selected.connect(self._on_profile_select)
        self._home_page.fan_mode_selected.connect(self._on_fan_mode)
        self._fans_page.fan_mode_selected.connect(self._on_fan_mode)
        self._home_page.fans_clicked.connect(lambda: self.set_active_tab(2))
        self._home_page.lighting_clicked.connect(lambda: self.set_active_tab(3))
        self._home_page.power_clicked.connect(lambda: self.set_active_tab(1))
        self._power_page.limits_applied.connect(self._home_page.refresh_power)

        # Sensors: graph pop-out requests
        self._sensors_page.open_graph_requested.connect(self._open_sensor_graph)

        # Window tracking
        self._graph_windows: dict[str, SensorGraphWindow] = {}
        self._lighting = LightingController(self)
        self._lighting.frame_changed.connect(self._keyboard_page.apply_frame)
        self._lighting.frame_changed.connect(self._home_page.apply_frame)
        self._keyboard_page.enabled_changed.connect(self._lighting.set_enabled)
        self._keyboard_page.effect_changed.connect(self._lighting.set_effect)
        self._keyboard_page.speed_changed.connect(self._lighting.set_speed)
        self._keyboard_page.color_changed.connect(self._lighting.set_color)
        self._keyboard_page.color2_changed.connect(self._lighting.set_color2)
        self._keyboard_page.zone_color_changed.connect(self._lighting.set_zone_color)
        self._keyboard_page.idle_timeout_changed.connect(self._lighting.set_idle_timeout)
        self._keyboard_page.brightness_changed.connect(self._lighting.set_brightness)
        self._keyboard_page.enabled_changed.connect(
            lambda *_: self._home_page.refresh_lighting())
        self._keyboard_page.effect_changed.connect(
            lambda *_: self._home_page.refresh_lighting())

        # System profile changes arrive through D-Bus instead of a recurring
        # tuned-adm/powerprofilesctl subprocess.
        self._profile_watcher = ProfileWatcher(self)
        self._profile_watcher.profile_changed.connect(api.update_profile_cache)
        self._profile_watcher.profile_changed.connect(self._on_profile_changed)
        QApplication.instance().aboutToQuit.connect(self._profile_watcher.stop)


        # Capture configured keys and receive daemon lighting updates.
        self._shortcut = ShortcutController(self)
        self._shortcut.lighting_changed.connect(self._on_daemon_lighting)
        self._settings_page.hardware_shortcuts_changed.connect(self._shortcut.set_hardware_enabled)
        self._settings_page.hardware_shortcuts_changed.connect(api.set_hardware_shortcuts)
        QApplication.instance().aboutToQuit.connect(self._shortcut.shutdown)
        self._settings_page.set_shortcut_controller(self._shortcut)

        # ── Timers ──

        # Sensor poll (1s)
        self._sensor_timer = QTimer(self)
        self._sensor_timer.setInterval(1000)
        self._sensor_timer.timeout.connect(self._poll_sensors)

        # UI-active gate (visibility). When False (hidden to tray /
        # minimized) the sensor timer stops and the keyboard preview
        # repaint is gated. Hardware control runs in the daemon.
        self._profile_watcher.start()
        try:
            _cfg = api.get_fan_config()
            if _cfg.custom_enabled and _cfg.smart_enabled and "smart" in self._capabilities.fan_modes:
                self._selected_fan_mode = "smart"
            elif _cfg.custom_enabled and "custom" in self._capabilities.fan_modes:
                self._selected_fan_mode = "custom"
            elif _cfg.manual_preset == "max" and "max" in self._capabilities.fan_modes:
                self._selected_fan_mode = "max"
            else:
                self._selected_fan_mode = "auto"
            self._sync_fan_mode_ui(self._selected_fan_mode)
        except Exception:
            logger.exception("init fan config check failed")
        self._sync_tray_checks()

        # Preview-only pause on suspend; hardware cleanup is in the daemon.
        self._power_state = PowerStateWatcher(self)
        self._power_state.suspending.connect(self._on_system_suspend)
        self._power_state.resuming.connect(self._on_system_resume)
        self._settings_page.battery_power_save_changed.connect(api.set_battery_power_save)

        self._update_min_height(0)
        self._apply_accent(self._selected_profile)
        self._morph_to_page(0)
    # ── Tab switching ──

    def set_active_tab(self, index: int):
        """Switch to the given tab index."""
        if index == 3 and not self._capabilities.keyboard_lighting:
            return
        self._sidebar.set_active(index)
        self._stack.setCurrentIndex(index)

    def _on_tab_changed(self, index: int):
        self._stack.setCurrentIndex(index)
        self._morph_to_page(index)

    def _on_current_page_changed(self, index: int) -> None:
        self._update_min_height(index)

    def _update_min_height(self, index: int) -> None:
        """Give the current page enough inner height to avoid squishing.
        Does not change the window minimum (always 700)."""
        page = self._stack.widget(index)
        if page is not None:
            self._stack.setMinimumHeight(page.minimumSizeHint().height())

    _PAGE_WIDTH = {
        0: 480,
        1: 480,
        2: 700,
        3: 700,
        4: 700,
        5: 480,
    }
    # A manual drag may make the window taller. That size lasts only until
    # the next tab change, which returns to this height.
    _WINDOW_HEIGHT = 680

    """
    _PAGE_WIDTH = {
            0: 460,
            1: 460,
            2: 700,
            3: 700,
            4: 720,
            5: 460,
        }
    """

    def _morph_to_page(self, index: int) -> None:
        w = self._PAGE_WIDTH.get(index, 460)
        self.resize(w, self._WINDOW_HEIGHT)

    def _apply_accent(self, index: int) -> None:
        set_accent(index)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet())
        self._sidebar.refresh_accent()
        if hasattr(self._home_page, "refresh_accent"):
            self._home_page.refresh_accent()
        if hasattr(self._fans_page, "refresh_accent"):
            self._fans_page.refresh_accent()

    def _update_ui_active(self) -> None:
        """Central visibility switch. Pauses UI-only work when the window is
        hidden to tray or minimized. Hardware control runs in the daemon."""
        active = self._ui_is_shown()
        if active == self._ui_active:
            return
        self._ui_active = active
        if active:
            self._sensor_timer.start()
        else:
            self._sensor_timer.stop()
        self._lighting.set_ui_active(active)
        api.set_ui_active(active)
        if active:
            self._poll_sensors()  # immediate fresh refresh on restore

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self._update_ui_active()

    def hideEvent(self, event: QHideEvent):
        super().hideEvent(event)
        self._update_ui_active()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_ui_active()
    # ── Tray ──

    def _tray_header(self, text: str) -> QAction:
        action = QAction(text, self)
        action.setEnabled(False)
        return action

    def _build_tray_menu(self) -> PopupMenu:
        menu = PopupMenu(self)
        show_action = QAction("Show/Hide", self)
        show_action.triggered.connect(self._toggle_visible)
        menu.addAction(show_action)
        menu.addSeparator()

        menu.addAction(self._tray_header("Performance"))
        profile_group = QActionGroup(self)
        profile_group.setExclusive(True)
        self._tray_profile_actions: list[QAction] = []
        for i, (label, icon, _accent) in enumerate(PROFILES):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setIcon(load_icon(icon, size=16))
            action.triggered.connect(
                lambda _checked=False, idx=i: self._on_profile_select(idx)
            )
            profile_group.addAction(action)
            menu.addAction(action)
            self._tray_profile_actions.append(action)

        menu.addSeparator()
        menu.addAction(self._tray_header("Fan Mode"))
        fan_group = QActionGroup(self)
        fan_group.setExclusive(True)
        self._tray_fan_actions: dict[str, QAction] = {}
        for key, label, icon, _accent, _has_action in FAN_MODES:
            if key not in self._capabilities.fan_modes:
                continue
            action = QAction(label, self)
            action.setCheckable(True)
            action.setIcon(load_icon(icon, size=16))
            action.triggered.connect(
                lambda _checked=False, mode=key: self._on_tray_fan_mode(mode)
            )
            fan_group.addAction(action)
            menu.addAction(action)
            self._tray_fan_actions[key] = action

        menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(quit_action)

        self._sync_tray_checks()
        return menu

    def _sync_tray_checks(self) -> None:
        for i, action in enumerate(getattr(self, "_tray_profile_actions", [])):
            action.setChecked(i == self._selected_profile)
        for key, action in getattr(self, "_tray_fan_actions", {}).items():
            action.setChecked(key == self._selected_fan_mode)

    def _on_tray_fan_mode(self, mode: str) -> None:
        self._on_fan_mode(mode)

    def _sync_fan_mode_ui(self, mode: str) -> None:
        self._home_page.set_selected_fan_mode(mode)
        self._fans_page.set_selected_fan_mode(mode)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_all_windows()

    def _ui_is_shown(self) -> bool:
        """True when the main window is on screen, not minimized to the taskbar."""
        return self.isVisible() and not self.isMinimized()

    def _toggle_visible(self):
        if self._ui_is_shown():
            self._hide_all_windows()
        else:
            self._show_all_windows()


    def _hide_all_windows(self):
        """Hide main window + all currently open child windows to tray."""
        for win in list(self._graph_windows.values()):
            win.hide()
        self.hide()
        self._update_ui_active()

    def _show_all_windows(self):
        """Show/restore main + all registered child windows.
        Handles both tray-hidden and minimized states. Used by left-click tray.
        """
        for win in list(self._graph_windows.values()):
            win.showNormal()
            win.raise_()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._update_ui_active()
    def _quit_app(self):
        """Quit the UI. Hardware control stays with the daemon."""
        self._lighting.shutdown()
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

    # ── System suspend / shutdown cleanup ──

    def _on_daemon_lighting(self, settings) -> None:
        """Display lighting that the daemon applied from a hardware shortcut."""
        self._keyboard_page.apply_remote(settings)
        self._lighting.apply_remote(settings)
        self._home_page.refresh_lighting()

    def _on_system_suspend(self) -> None:
        """Pause the keyboard preview; daemon sleep hook handles hardware."""
        self._lighting.pause()

    def _on_system_resume(self) -> None:
        """Restart the keyboard preview after suspend."""
        self._lighting.resume()
    # ── Geometry persistence ──

    def _restore_geometry(self):
        pos = self._settings.value("window/pos")
        if pos is not None:
            self.move(pos)
        else:
            geo = self._settings.value("window/geometry")
            if geo:
                self.restoreGeometry(geo)
                self._morph_to_page(0)

    def _save_geometry(self):
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/pos", self.pos())

    # ── Sensor polling ──

    def _poll_sensors(self):
        self._home_page.refresh_power_status()
        try:
            snapshot = api.read_sensors()
        except Exception:
            logger.exception("sensor poll failed")
            return

        # Update stats
        self._stats_by_key = next_stats(snapshot, self._stats_by_key)

        # Update live page readings
        self._home_page.update_sensor_data(snapshot)
        self._power_page.update_sensor_data(snapshot)
        self._fans_page.update_sensor_data(snapshot)

        # Update Sensors page rows
        rows = build_rows(snapshot, self._stats_by_key)
        self._sensors_page.update_rows(rows)

        # Update footer hardware title
        self._home_page.set_hardware_title(api.get_hardware_title())

    # ── Profile events ──

    def _on_profile_changed(self, profile: int, _name: str, _source: str) -> None:
        if profile != 0:
            self._profile_before_battery = None
        if profile != self._selected_profile:
            self._selected_profile = profile
            self._home_page.set_selected_profile(profile)
            self._apply_accent(profile)
            self._sync_tray_checks()
            self._fans_page.set_edit_profile(profile)

    # ── Profile selection ──

    def _cycle_profile(self) -> None:
        # The event watcher maintains this cache even while the window is in
        # the tray, so a shortcut can cycle from the actual system profile.
        try:
            current = api.get_current_profile()
        except Exception:
            current = None
        if current not in range(len(PROFILES)):
            current = self._selected_profile
        self._on_profile_select((current + 1) % len(PROFILES))

    def _on_battery_power_save(self) -> None:
        previous = api.get_current_profile()
        if previous not in range(len(PROFILES)):
            previous = self._selected_profile
        if previous != 0 and self._on_profile_select(0, automatic=True):
            self._profile_before_battery = previous

    def _restore_battery_profile(self) -> None:
        previous = self._profile_before_battery
        self._profile_before_battery = None
        if previous is not None and api.get_current_profile() == 0:
            self._on_profile_select(previous, automatic=True)

    def _on_profile_select(self, index: int, *, automatic: bool = False):
        try:
            api.set_system_profile(index)
        except Exception:
            logger.exception("set system profile failed")
            # Clicked controls may have already changed their own selection.
            self._home_page.set_selected_profile(self._selected_profile)
            self._sync_tray_checks()
            return False
        if not automatic:
            self._profile_before_battery = None
        self._selected_profile = index
        self._home_page.set_selected_profile(index)
        self._apply_accent(index)
        self._sync_tray_checks()
        self._fans_page.set_edit_profile(index)
        return True

    # ── Fan mode ──

    def _on_fan_mode(self, mode: str):
        """Handle Auto/Smart/Max/Custom fan mode button clicks."""
        if mode not in self._capabilities.fan_modes:
            self._sync_fan_mode_ui(self._selected_fan_mode)
            return
        self._selected_fan_mode = mode
        self._sync_fan_mode_ui(mode)
        self._sync_tray_checks()
        if mode == "auto":
            self._set_fan_auto()
        elif mode == "smart":
            self._set_fan_smart()
        elif mode == "max":
            self._set_fan_max()
        elif mode == "custom":
            self._set_fan_custom()

    def _set_fan_auto(self):
        try:
            api.set_smart_fan_enabled(False)
        except Exception:
            logger.exception("set smart fan enabled (auto) failed")
        try:
            api.set_manual_preset("auto")
        except Exception:
            logger.exception("set manual preset (auto) failed")
        try:
            api.set_custom_fan_enabled(False)
        except Exception:
            logger.exception("set custom fan enabled (auto) failed")

    def _set_fan_max(self):
        """Engage BIOS/EC max-fan mode (hp-wmi: pwm1_enable=0)."""
        try:
            api.set_smart_fan_enabled(False)
        except Exception:
            logger.exception("set smart fan enabled (max) failed")
        try:
            api.set_manual_preset("max")
        except Exception:
            logger.exception("set manual preset (max) failed")
        try:
            api.set_custom_fan_enabled(False)
        except Exception:
            logger.exception("set custom fan enabled (max) failed")

    def _set_fan_smart(self):
        """Software curve with the built-in Smart table and faster EWMA."""
        try:
            api.set_smart_fan_enabled(True)
        except Exception:
            logger.exception("set smart fan enabled failed")
        try:
            api.set_custom_fan_enabled(True)
        except Exception:
            logger.exception("set custom fan enabled (smart) failed")

    def _set_fan_custom(self):
        try:
            api.set_smart_fan_enabled(False)
        except Exception:
            logger.exception("set smart fan enabled (custom) failed")
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
            if not existing.isVisible():
                existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        win = SensorGraphWindow(key)
        win.setAttribute(Qt.WA_DeleteOnClose)
        # Clean up tracking when the window is closed by the user
        def _on_destroyed(obj=None, k=key):
            self._graph_windows.pop(k, None)
        win.destroyed.connect(_on_destroyed)
        self._graph_windows[key] = win
        win.show()
