"""Real backend API — delegates to victus_hub.backend modules.

Replaces the Phase 1 mock stubs with live hardware reads and daemon operations.
Dataclass types are imported from victus_hub.backend.types and re-exported here
so existing UI imports (``from victus_hub.api import FanPoint, ...``) keep working.
"""

import logging
import threading
import time
from dataclasses import replace

from victus_hub.backend import hardware, profiles, fan_config, daemon_client
from victus_hub.backend.sensors import SensorReader
from victus_hub.backend.types import (
    SensorReading,
    ExtraSensor,
    SensorSnapshot,
    FanPoint,
    FanProfileConfig,
    FanConfig,
)
from victus_hub.features.keyboard.shortcut import KeyEvent

# Re-export dataclasses for backward compatibility
__all__ = [
    "SensorReading",
    "ExtraSensor",
    "SensorSnapshot",
    "FanPoint",
    "FanProfileConfig",
    "FanConfig",
    "get_hardware_title",
    "read_sensors",
    "get_current_profile",
    "set_system_profile",
    "set_fan_auto",
    "set_fan_max",
    "set_fan_manual",
    "set_fan_pwm",
    "get_keyboard_idle_elapsed",
    "get_keyboard_last_event",
    "KeyEvent",
    "save_fan_profile",
    "set_custom_fan_enabled",
    "set_smart_fan_enabled",
    "set_fan_curve_response",
    "set_manual_preset",
    "apply_power_limits",
    "set_gpu_mux_mode",
    "set_ui_active",
    "update_profile_cache",
]


# ── Background sensor reader ──

_reader = SensorReader()
_snapshot: SensorSnapshot | None = None
_profile_cache: int | None = None
_profile_reading_cache: SensorReading | None = None
_snapshot_lock = threading.Lock()
_profile_lock = threading.Lock()
_snapshot_running = True
# When False, the sensor-poll thread skips the heavy `sensors -j`
# subprocess (lm-sensors extras); cheap temps/fans/usage still run so
# the fan-control thread always has fresh CPU/GPU temps. Flipped by the
# GUI on visibility changes (api.set_ui_active).
_ui_active = True


def set_ui_active(active: bool) -> None:
    """Tell the background sensor loop whether the UI is being looked at."""
    global _ui_active
    _ui_active = active

_bg_logger = logging.getLogger("sensor-bg")


def _sensor_loop():
    """Background thread: read sensors every 1 s and cache results."""
    global _snapshot
    while _snapshot_running:
        try:
            snap = _reader.read_all(full=_ui_active)
        except Exception as e:
            _bg_logger.warning("read_all failed: %s", e)
            time.sleep(1.0)
            continue
        with _profile_lock:
            profile_reading = _profile_reading_cache
        if profile_reading is not None:
            snap.profile = profile_reading
        with _snapshot_lock:
            _snapshot = snap
        time.sleep(1.0)
_sensor_thread: threading.Thread | None = None


def start_sensor_reader() -> None:
    """Start after QApplication exists, never as an import side effect."""
    global _sensor_thread
    if _sensor_thread is not None:
        return
    from victus_hub.backend.nvidia import nvidia_queries_disabled
    nvidia_queries_disabled()  # Load Qt settings on the GUI thread first.
    _sensor_thread = threading.Thread(target=_sensor_loop, daemon=True, name="sensor-poll")
    _sensor_thread.start()


# ── API functions ──

_hardware_title: str = hardware.hardware_title()


def get_hardware_title() -> str:
    return _hardware_title


def read_sensors() -> SensorSnapshot:
    with _snapshot_lock:
        snap = _snapshot
    if snap is not None:
        return snap
    # First call before the thread has produced anything: return empty snapshot
    return SensorSnapshot()


def get_current_profile() -> int | None:
    with _profile_lock:
        return _profile_cache


def update_profile_cache(index: int, name: str, source: str) -> None:
    """Update the event-fed profile state used by the UI and fan controller."""
    global _profile_cache, _profile_reading_cache, _snapshot
    from victus_hub.backend.nvidia import set_nvidia_power_profile

    set_nvidia_power_profile(index)
    reading = SensorReading(value=name, source=source)
    with _profile_lock:
        _profile_cache = index
        _profile_reading_cache = reading
    with _snapshot_lock:
        if _snapshot is not None:
            _snapshot = replace(_snapshot, profile=reading)


def set_system_profile(profile: int) -> str:
    profile = max(0, min(profile, len(profiles.PROFILE_KEYS) - 1))
    result = profiles.apply_system_profile(profile)
    # Keep the local consumers correct even before the backend emits its event.
    update_profile_cache(profile, profiles.PROFILE_KEYS[profile], "local selection")
    return result


def set_gpu_mux_mode(mode: int) -> str:
    return daemon_client.request_gpu_mux_mode(mode)


def set_fan_auto() -> str:
    return daemon_client.request_fan_auto()


def set_fan_max() -> str:
    """Set pwm_enable=0 — BIOS/EC max-fan mode (hp-wmi PWM_MODE_MAX)."""
    return daemon_client.request_fan_max()


def set_fan_manual() -> str:
    """Set pwm_enable=1 — call when switching to custom (manual PWM) mode."""
    return daemon_client.request_fan_manual()


def set_fan_pwm(pwm: int) -> str:
    return daemon_client.request_fan_pwm(pwm)


def get_keyboard_zone_count() -> int:
    """Return keyboard RGB zone count from the kernel module (1 or 4).

    zone_count is world-readable under the platform device; no daemon needed.
    ``VICTUS_HUB_EMULATE_ZONES`` overrides sysfs (ui-test uses this).
    Falls back to 1 when the module is absent.
    """
    from pathlib import Path

    from victus_hub.backend.modules import emulated_keyboard_zone_count

    emulated = emulated_keyboard_zone_count()
    if emulated is not None:
        return emulated
    try:
        raw = Path("/sys/devices/platform/hp-kbd-rgb/zone_count").read_text().strip()
        count = int(raw)
        return count if count >= 1 else 1
    except (OSError, ValueError):
        return 1


def set_keyboard_color(
    red: int, green: int, blue: int, zone: int | None = None,
) -> str:
    """Set keyboard color on all zones, or on one zone when *zone* is given."""
    from victus_hub.backend.modules import emulated_keyboard_zone_count

    if emulated_keyboard_zone_count() is not None:
        return "ok"
    return daemon_client.request_keyboard_color(red, green, blue, zone=zone)


def set_keyboard_brightness(level: int) -> str:
    from victus_hub.backend.modules import emulated_keyboard_zone_count

    if emulated_keyboard_zone_count() is not None:
        return "ok"
    return daemon_client.request_keyboard_brightness(level)

def set_keyboard_user_brightness(level: int) -> str:
    """Set the user-preferred brightness — stored by the daemon and applied
    atomically on subsequent color writes (prevents the 100% flash)."""
    from victus_hub.backend.modules import emulated_keyboard_zone_count

    if emulated_keyboard_zone_count() is not None:
        return "ok"
    return daemon_client.request_keyboard_user_brightness(level)

def get_keyboard_idle_elapsed() -> float:
    """Return seconds since the last physical keypress on the laptop keyboard."""
    try:
        return daemon_client.request_keyboard_last_input()
    except Exception:
        return 0.0


def get_keyboard_last_event() -> KeyEvent:
    """Return the last non-modifier keypress seen by the daemon.

    Used by the program-shortcut feature to capture a keybind and to detect
    when the configured shortcut is pressed (so the GUI can unhide/restore).
    """
    mods, key, seq = daemon_client.request_keyboard_last_event()
    return KeyEvent(mods=tuple(sorted(mods)), key=key, seq=seq)


def apply_power_limits(stapm: int, fast: int, slow: int, tctl_temp: int = 95) -> str:
    from victus_hub.backend.cpu import is_intel_cpu

    if is_intel_cpu():
        return daemon_client.request_intel_power_limits(slow, fast)
    return daemon_client.request_power_limits(stapm, fast, slow, tctl_temp)


def get_fan_config() -> FanConfig:
    return fan_config.load()


def save_fan_profile(profile: int, cpu_points: list[FanPoint],
                     gpu_points: list[FanPoint]) -> FanConfig:
    return fan_config.save_profile(profile, cpu_points, gpu_points)


def set_custom_fan_enabled(enabled: bool) -> FanConfig:
    return fan_config.save_custom_enabled(enabled)


def set_smart_fan_enabled(enabled: bool) -> FanConfig:
    return fan_config.save_smart_enabled(enabled)


def set_fan_curve_response(response: str) -> FanConfig:
    return fan_config.save_curve_response(response)


def set_manual_preset(preset: str | None) -> FanConfig:
    """Record which preset the user clicked (auto / max) or clear it.

    When set to 'max' or 'auto', the fan-control background loop backs
    off and will not override the hardware fan mode (max flag or
    pwm1 / pwm1_enable).
    """
    return fan_config.save_manual_preset(preset)
