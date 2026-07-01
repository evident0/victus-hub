"""Real backend API — delegates to hp_helper.backend modules.

Replaces the Phase 1 mock stubs with live hardware reads and daemon operations.
Dataclass types are imported from hp_helper.backend.types and re-exported here
so existing UI imports (``from hp_helper.api import FanPoint, ...``) keep working.
"""

import threading
import time

from hp_helper.backend import hardware, profiles, fan_config, daemon_client
from hp_helper.backend.sensors import SensorReader
from hp_helper.backend.types import (
    SensorReading,
    ExtraSensor,
    SensorSnapshot,
    FanPoint,
    FanProfileConfig,
    FanConfig,
)

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
    "set_fan_pwm",
    "set_keyboard_color",
    "get_fan_config",
    "save_fan_profile",
    "set_custom_fan_enabled",
    "apply_power_limits",
]


# ── Background sensor reader ──

_reader = SensorReader()
_snapshot: SensorSnapshot | None = None
_profile_cache: int | None = None
_snapshot_lock = threading.Lock()
_profile_lock = threading.Lock()
_snapshot_running = True

def _sensor_loop():
    """Background thread: read sensors and profile every 1 s, cache results."""
    global _snapshot, _profile_cache
    while _snapshot_running:
        try:
            snap = _reader.read_all()
        except Exception:
            time.sleep(1.0)
            continue
        with _snapshot_lock:
            _snapshot = snap
        try:
            prof = profiles.current_ui_profile_index()
        except Exception:
            prof = None
        with _profile_lock:
            _profile_cache = prof
        time.sleep(1.0)
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


def set_system_profile(profile: int) -> str:
    return profiles.apply_system_profile(profile)


def set_fan_auto() -> str:
    return daemon_client.request_fan_auto()


def set_fan_pwm(pwm: int) -> str:
    return daemon_client.request_fan_pwm(pwm, None, None)


def set_keyboard_color(red: int, green: int, blue: int) -> str:
    return daemon_client.request_keyboard_color(red, green, blue)


def apply_power_limits(stapm: int, fast: int, slow: int) -> str:
    return daemon_client.request_power_limits(stapm, fast, slow)


def get_fan_config() -> FanConfig:
    return fan_config.load()


def save_fan_profile(profile: int, cpu_points: list[FanPoint],
                     gpu_points: list[FanPoint]) -> FanConfig:
    return fan_config.save_profile(profile, cpu_points, gpu_points)


def set_custom_fan_enabled(enabled: bool) -> FanConfig:
    return fan_config.save_custom_enabled(enabled)
