"""Main application window with sidebar, stacked pages, and system tray."""

import time
import collections
import threading
import logging

from hp_helper.backend import fan_config as _fan_config
from hp_helper.backend import daemon_client as _daemon_client

from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStackedWidget,
    QSystemTrayIcon, QMenu, QApplication,
)
from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QIcon, QAction, QCloseEvent

from hp_helper.theme import COLORS
from hp_helper.widgets.sidebar import Sidebar
from hp_helper.pages.home_page import HomePage
from hp_helper.pages.fans_power_page import FansPowerPage
from hp_helper.pages.sensors_page import SensorsPage
from hp_helper.pages.keyboard_page import KeyboardPage
from hp_helper.sensor_graph_window import SensorGraphWindow
from hp_helper.fan_curves_window import FanCurvesWindow
from hp_helper import api
from hp_helper.sensor_definitions import (
    SENSOR_DEFINITIONS, dynamic_sensor_definition, SensorDefinition,
)
from hp_helper.power_limits import (
    read_power_enabled, read_power_limit_settings, write_power_enabled,
)
from hp_helper.keyboard_lighting import (
    read_lighting_settings, write_lighting_settings,
    normalize_lighting_settings, lighting_frame, frame_interval_ms,
    hex_to_rgb,
)
# ── Fan-control loop (ported from lib.rs:118-224) ──

_POLL_INTERVAL = 1.0
_CONTROL_INTERVAL = 3.0
_TEMP_WINDOW = 15
_WRITE_MIN_DELTA_PCT = 5.0
_RAMP_UP_PCT = 30.0
_RAMP_DOWN_PCT = 15.0
_RAMP_DOWN_DELAY = 10.0

_fan_logger = logging.getLogger("fan-control")


def _next_fan_percent(
    target: float,
    current: float,
    ramp_down_since: float | None,
    now: float,
    ramp_up: float,
    ramp_down: float,
    ramp_down_delay: float,
) -> tuple[float, float | None]:
    """Compute the next fan speed percent with ramp-up/down logic.

    Ports lib.rs::next_fan_percent exactly.
    Returns (next_pct, new_ramp_down_since).
    """
    if target > current:
        return (min(current + ramp_up, target), None)

    if target < current:
        if ramp_down_since is None:
            ramp_down_since = now
            if ramp_down_delay == 0.0:
                return (max(current - ramp_down, target), ramp_down_since)
            return (current, ramp_down_since)
        elif now - ramp_down_since >= ramp_down_delay:
            return (max(current - ramp_down, target), ramp_down_since)
        else:
            return (current, ramp_down_since)

    return (target, None)


def _start_fan_control() -> None:
    """Background thread that implements the custom fan-control loop."""

    def _loop():
        import time as _time

        temp_history: collections.deque = collections.deque(maxlen=_TEMP_WINDOW)
        last_written_pct: float | None = None
        ramp_down_since: float | None = None
        next_control = _time.monotonic()
        was_custom = False

        while True:
            # ── poll ──
            try:
                snap = api.read_sensors()
            except Exception:
                _time.sleep(_POLL_INTERVAL)
                continue

            temp_history.append((snap.cpu_temp_c, snap.gpu_temp_c))

            try:
                profile_idx = max(0, min(
                    api.get_current_profile() or 1, 2
                ))
            except Exception:
                profile_idx = 1

            config = _fan_config.load()
            profile = config.profiles[profile_idx]

            if not config.custom_enabled:
                if was_custom:
                    try:
                        _daemon_client.request_fan_auto()
                    except Exception:
                        pass
                    last_written_pct = None
                    ramp_down_since = None
                    was_custom = False
                _time.sleep(_POLL_INTERVAL)
                continue

            was_custom = True

            # ── control tick ──
            now = _time.monotonic()
            if now >= next_control:
                cpu_temps = [
                    int(t) for t, _ in temp_history if t is not None
                ]
                gpu_temps = [
                    int(t) for _, t in temp_history if t is not None
                ]

                cpu_avg: float | None = None
                gpu_avg: float | None = None
                target: float | None = None

                if cpu_temps:
                    cpu_avg = sum(cpu_temps) / len(cpu_temps)
                    s = _fan_config.interpolate_fan(
                        profile.cpu_points, round(cpu_avg)
                    )
                    target = s

                if gpu_temps:
                    gpu_avg = sum(gpu_temps) / len(gpu_temps)
                    s = _fan_config.interpolate_fan(
                        profile.gpu_points, round(gpu_avg)
                    )
                    target = max(target, s) if target is not None else s

                if target is not None:
                    current = last_written_pct if last_written_pct is not None else target
                    next_pct, ramp_down_since = _next_fan_percent(
                        target, current, ramp_down_since, now,
                        _RAMP_UP_PCT, _RAMP_DOWN_PCT, _RAMP_DOWN_DELAY,
                    )

                    delta = abs(next_pct - (last_written_pct or 0.0))
                    if last_written_pct is None or delta >= _WRITE_MIN_DELTA_PCT:
                        pwm = max(0, min(round(next_pct * 255.0 / 100.0), 255))
                        try:
                            _daemon_client.request_fan_pwm(pwm, cpu_avg, gpu_avg)
                        except Exception:
                            pass
                        last_written_pct = next_pct

                next_control = now + _CONTROL_INTERVAL

            _time.sleep(_POLL_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="fan-control")
    t.start()

POWER_APPLY_DEDUPE_MS = 1000


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
        central.setStyleSheet(f"background-color: {COLORS['bg']};")
        self.setCentralWidget(central)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        self._sidebar = Sidebar()
        layout.addWidget(self._sidebar)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background-color: {COLORS['bg']};")
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
        self._lighting_settings = read_lighting_settings()
        self._lighting_started_at = time.time() * 1000
        self._last_power_apply_ms = 0
        self._power_apply_in_flight = False
        self._last_lighting_send = 0.0
        self._last_sent_color: tuple[int, int, int] | None = None
        self._quitting = False

        # ── Signal connections ──

        # Home: profile selection + fan modes
        self._home_page.profile_selected.connect(self._on_profile_select)
        self._home_page.fan_mode_selected.connect(self._on_fan_mode)
        self._home_page.fan_curves_popout_requested.connect(self._open_fan_curves_window)

        # Fans: pop-out request
        self._fans_page.fan_curves_popout_requested.connect(self._open_fan_curves_window)

        # Keyboard: controls
        self._keyboard_page.enabled_changed.connect(self._on_lighting_enabled)
        self._keyboard_page.effect_changed.connect(self._on_lighting_effect)
        self._keyboard_page.color_changed.connect(self._on_lighting_color)
        self._keyboard_page.speed_changed.connect(self._on_lighting_speed)
        # Sensors: graph request opens a standalone sensor graph window
        self._sensors_page.open_graph_requested.connect(self._open_sensor_graph)


        # Window tracking
        self._graph_windows: dict[str, SensorGraphWindow] = {}
        self._hidden_graph_windows: set[str] = set()  # keys hidden by tray-close
        self._fan_curves_window: FanCurvesWindow | None = None
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

        # Power reapply check (1s)
        self._power_timer = QTimer(self)
        self._power_timer.setInterval(1000)
        self._power_timer.timeout.connect(self._check_power_reapply)
        self._power_timer.start()

        # Lighting animation (50ms)
        self._lighting_timer = QTimer(self)
        self._lighting_timer.setInterval(50)
        self._lighting_timer.timeout.connect(self._tick_lighting)
        self._lighting_timer.start()
        # Fan-control background thread
        _start_fan_control()

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
            pass
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
            return

        # Update stats
        self._stats_by_key = _next_stats(snapshot, self._stats_by_key)

        # Update Home page gauges
        self._home_page.update_sensor_data(snapshot)

        # Update Sensors page rows
        rows = _build_rows(snapshot, self._stats_by_key)
        self._sensors_page.update_rows(rows)

        # Update footer hardware title
        self._home_page.set_hardware_title(api.get_hardware_title())

    # ── Profile polling ──

    def _poll_profile(self):
        try:
            profile = api.get_current_profile()
        except Exception:
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
            pass

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
            api.set_custom_fan_enabled(False)
        except Exception:
            pass
        try:
            api.set_fan_auto()
        except Exception:
            pass

    def _set_fan_max(self):
        try:
            api.set_custom_fan_enabled(False)
        except Exception:
            pass
        try:
            api.set_fan_pwm(255)
        except Exception:
            pass

    def _set_fan_custom(self):
        try:
            api.set_custom_fan_enabled(True)
        except Exception:
            pass

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
        win.destroyed.connect(_on_destroyed)
        self._fan_curves_window = win
        self._fans_page.set_fan_curves_window_open(True)
        win.show()

    # ── Power reapply ──
    def _check_power_reapply(self):
        if not read_power_enabled() or self._power_apply_in_flight:
            return
        now = time.time() * 1000
        if now - self._last_power_apply_ms < POWER_APPLY_DEDUPE_MS:
            return
        settings = read_power_limit_settings()
        self._last_power_apply_ms = now
        self._power_apply_in_flight = True
        try:
            api.apply_power_limits(settings.stapm_limit, settings.fast_limit, settings.slow_limit)
        except Exception:
            pass
        finally:
            self._power_apply_in_flight = False

    # ── Lighting ──

    def _on_lighting_enabled(self, enabled: bool):
        self._lighting_settings.enabled = enabled
        write_lighting_settings(self._lighting_settings)

    def _on_lighting_effect(self, effect: str):
        self._lighting_settings.effect = effect
        write_lighting_settings(self._lighting_settings)

    def _on_lighting_color(self, color: str):
        self._lighting_settings.color = color
        write_lighting_settings(self._lighting_settings)

    def _on_lighting_speed(self, speed: int):
        self._lighting_settings.speed = speed
        write_lighting_settings(self._lighting_settings)

    def _tick_lighting(self):
        settings = normalize_lighting_settings(self._lighting_settings)
        elapsed = int(time.time() * 1000 - self._lighting_started_at)
        frame = lighting_frame(settings, elapsed)
        # Throttle daemon call: only send every 200 ms and only on color change
        now = time.monotonic()
        color = (frame.red, frame.green, frame.blue)
        if now - self._last_lighting_send >= 0.200 and color != self._last_sent_color:
            try:
                api.set_keyboard_color(*color)
                self._last_lighting_send = now
                self._last_sent_color = color
            except Exception:
                pass
        # Update visual keyboard (always, for responsive UI)
        self._keyboard_page.apply_frame(frame)
        if settings.enabled:
            self._keyboard_page.set_status("Applied" if settings.effect != "static" else "On")
        else:
            self._keyboard_page.set_status("Off")


# ── Stats accumulation (ported from App.tsx) ──

def _format_stat(value: float, unit: str) -> str:
    if value == int(value):
        formatted = str(int(value))
    else:
        formatted = f"{value:.1f}"
    return f"{formatted} {unit}" if unit else formatted


def _parse_reading_num(reading) -> float | None:
    try:
        return float(str(reading.value).split()[0])
    except (ValueError, AttributeError):
        return None


def _next_stats(snapshot, previous: dict) -> dict:
    """Accumulate min/max/avg per sensor."""
    changed = False
    next_stats = dict(previous)
    defs: list[SensorDefinition] = list(SENSOR_DEFINITIONS)
    for es in snapshot.extra_sensors:
        defs.append(dynamic_sensor_definition(es))

    for d in defs:
        value = d.numeric_value(snapshot)
        if value is None:
            continue
        if not isinstance(value, (int, float)):
            continue
        cur = next_stats.get(d.key)
        if cur:
            next_stats[d.key] = {
                "minimum": min(cur["minimum"], value),
                "maximum": max(cur["maximum"], value),
                "sum": cur["sum"] + value,
                "count": cur["count"] + 1,
            }
        else:
            next_stats[d.key] = {
                "minimum": value,
                "maximum": value,
                "sum": value,
                "count": 1,
            }
        changed = True
    return next_stats if changed else previous


def _missing_stats(reading) -> dict:
    return {
        "current": {"value": "—", "source": ""},
        "minimum": "—",
        "maximum": "—",
        "average": "—",
    }


def _build_rows(snapshot, stats_by_key: dict) -> list:
    """Build sensor table rows from snapshot + accumulated stats."""
    defs: list[SensorDefinition] = list(SENSOR_DEFINITIONS)
    for es in snapshot.extra_sensors:
        defs.append(dynamic_sensor_definition(es))

    rows = []
    for d in defs:
        current = d.reading(snapshot)
        stats = stats_by_key.get(d.key)
        if not stats:
            rows.append({
                "definition": d,
                "stats": _missing_stats(current),
            })
        else:
            rows.append({
                "definition": d,
                "stats": {
                    "current": {"value": current.value, "source": getattr(current, "source", "")},
                    "minimum": _format_stat(stats["minimum"], d.unit),
                    "maximum": _format_stat(stats["maximum"], d.unit),
                    "average": _format_stat(stats["sum"] / stats["count"], d.unit),
                },
            })
    return rows
