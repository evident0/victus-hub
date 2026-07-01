"""Real backend API — delegates to hp_helper.backend modules.

Replaces the Phase 1 mock stubs with live hardware reads and daemon operations.
Dataclass types are imported from hp_helper.backend.types and re-exported here
so existing UI imports (``from hp_helper.api import FanPoint, ...``) keep working.
"""

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

# ── Persistent state (replaces module-level mocks) ──

_reader = SensorReader()


# ── API functions ──

def get_hardware_title() -> str:
    return hardware.hardware_title()


def read_sensors() -> SensorSnapshot:
    return _reader.read_all()


def get_current_profile() -> int | None:
    return profiles.current_ui_profile_index()


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
