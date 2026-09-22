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

logger = logging.getLogger(__name__)

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
    "push_fan_config",
    "set_lighting_config",
    "set_power_policy",
    "set_cpu_frequency_policy",
    "set_battery_power_save",
    "set_hardware_shortcuts",
    "set_disable_nvidia_queries",
    "get_keyboard_idle_elapsed",
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
# When False, the sensor-poll thread sleeps. Fan/lighting/power loops live
# in the daemon, so hidden UI does not need temps. Flipped by the GUI on
# visibility changes (api.set_ui_active).
_ui_active = True
_ui_gate = threading.Event()
_ui_gate.set()


def set_ui_active(active: bool) -> None:
    """Tell the background sensor loop whether the UI is being looked at."""
    global _ui_active
    _ui_active = active
    if active:
        _ui_gate.set()
    else:
        _ui_gate.clear()

_bg_logger = logging.getLogger("sensor-bg")


def _sensor_loop():
    """Background thread: read sensors every 1 s and cache results."""
    global _snapshot
    while _snapshot_running:
        _ui_gate.wait()
        if not _snapshot_running:
            break
        if not _ui_active:
            continue
        try:
            snap = _reader.read_all(full=True)
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


def apply_power_limits(stapm: int, fast: int, slow: int, tctl_temp: int = 95) -> str:
    from victus_hub.backend.cpu import is_intel_cpu

    if is_intel_cpu():
        return daemon_client.request_intel_power_limits(slow, fast)
    return daemon_client.request_power_limits(stapm, fast, slow, tctl_temp)


def get_fan_config() -> FanConfig:
    return fan_config.load()


def _push_policy(label: str, send) -> None:
    """Best-effort daemon policy push; UI still saves locally if it fails."""
    try:
        send()
    except Exception as error:
        logger.debug("%s: %s", label, error)


def push_fan_config(config: FanConfig | None = None) -> None:
    """Send the current fan config to the daemon control loop."""
    if config is None:
        config = fan_config.load()
    _push_policy("push fan-config", lambda: daemon_client.request_fan_config(config))


def set_lighting_config(settings) -> None:
    _push_policy(
        "push lighting-config",
        lambda: daemon_client.request_lighting_config(settings),
    )


def set_power_policy(enabled: bool, settings) -> None:
    _push_policy(
        "push power-config",
        lambda: daemon_client.request_power_config(enabled, settings),
    )


def set_cpu_frequency_policy(minimum: int, maximum: int) -> None:
    _push_policy(
        "push cpu-frequency-config",
        lambda: daemon_client.request_cpu_frequency_config(minimum, maximum),
    )


def set_battery_power_save(enabled: bool) -> None:
    _push_policy(
        "push battery-power-save",
        lambda: daemon_client.request_battery_power_save(enabled),
    )


def set_hardware_shortcuts(enabled: bool) -> None:
    _push_policy(
        "push hardware-shortcuts",
        lambda: daemon_client.request_hardware_shortcuts(enabled),
    )


def set_disable_nvidia_queries(enabled: bool) -> None:
    from victus_hub.backend.nvidia import set_nvidia_queries_disabled

    set_nvidia_queries_disabled(enabled)
    _push_policy(
        "push disable-nvidia-queries",
        lambda: daemon_client.request_disable_nvidia_queries(enabled),
    )


def sync_settings_with_daemon() -> None:
    """Make daemon policy authoritative, migrating local settings once.

    Must run before UI pages load QSettings / config.json. If the daemon has
    not been initialized yet, local files are uploaded. Otherwise local
    files are replaced with the daemon snapshot so UI reopen cannot clobber
    headless or CLI changes.
    """
    try:
        remote = daemon_client.request_get_state()
    except Exception as error:
        logger.debug("sync with daemon: %s", error)
        return
    if not remote.get("initialized"):
        _migrate_local_to_daemon()
        return
    _hydrate_local_from_daemon(remote)


def _migrate_local_to_daemon() -> None:
    from PySide6.QtCore import QSettings

    from victus_hub.backend.nvidia import nvidia_query_disable_enabled
    from victus_hub.features.keyboard.lighting import read_lighting_settings
    from victus_hub.features.keyboard.shortcut import HARDWARE_SHORTCUTS_KEY
    from victus_hub.features.power.limits import (
        read_frequency_limits, read_power_enabled, read_power_limit_settings,
    )
    from victus_hub.services.battery_power import BATTERY_POWER_SAVE_KEY

    push_fan_config()
    set_lighting_config(read_lighting_settings())
    set_power_policy(read_power_enabled(), read_power_limit_settings())
    saved = read_frequency_limits()
    if saved is not None:
        set_cpu_frequency_policy(*saved)
    settings = QSettings()
    set_battery_power_save(settings.value(BATTERY_POWER_SAVE_KEY, False, type=bool))
    set_hardware_shortcuts(settings.value(HARDWARE_SHORTCUTS_KEY, False, type=bool))
    set_disable_nvidia_queries(nvidia_query_disable_enabled())


def _hydrate_local_from_daemon(remote: dict) -> None:
    from PySide6.QtCore import QSettings

    from victus_hub.backend.fan_config import config_from_dict, save_all
    from victus_hub.backend.nvidia import set_nvidia_queries_disabled
    from victus_hub.backend.power_limits import PowerLimitSettings
    from victus_hub.features.keyboard.lighting import (
        lighting_from_dict, write_lighting_settings,
    )
    from victus_hub.features.keyboard.shortcut import HARDWARE_SHORTCUTS_KEY
    from victus_hub.features.power.limits import (
        write_frequency_limits, write_power_enabled, write_power_limit_settings,
    )
    from victus_hub.services.battery_power import BATTERY_POWER_SAVE_KEY

    fan_raw = remote.get("fan")
    if isinstance(fan_raw, dict):
        save_all(config_from_dict(fan_raw))
    lighting_raw = remote.get("lighting")
    if isinstance(lighting_raw, dict) and lighting_raw:
        write_lighting_settings(lighting_from_dict(lighting_raw))
    power_raw = remote.get("power")
    if isinstance(power_raw, dict):
        write_power_enabled(bool(power_raw.get("enabled", False)))
        write_power_limit_settings(PowerLimitSettings(
            stapm_limit=int(power_raw.get("stapm_limit", 25000)),
            fast_limit=int(power_raw.get("fast_limit", 25000)),
            slow_limit=int(power_raw.get("slow_limit", 25000)),
            tctl_temp=int(power_raw.get("tctl_temp", 95)),
            reapply_seconds=int(power_raw.get("reapply_seconds", 5)),
        ))
    freq = remote.get("cpu_frequency")
    if isinstance(freq, dict) and freq.get("min") and freq.get("max"):
        write_frequency_limits(int(freq["min"]), int(freq["max"]))
    settings = QSettings()
    settings.setValue(BATTERY_POWER_SAVE_KEY, bool(remote.get("battery_power_save", False)))
    settings.setValue(HARDWARE_SHORTCUTS_KEY, bool(remote.get("hardware_shortcuts", False)))
    set_nvidia_queries_disabled(bool(remote.get("disable_nvidia_queries", False)))


def save_fan_profile(profile: int, cpu_points: list[FanPoint],
                     gpu_points: list[FanPoint]) -> FanConfig:
    config = fan_config.save_profile(profile, cpu_points, gpu_points)
    push_fan_config(config)
    return config


def set_custom_fan_enabled(enabled: bool) -> FanConfig:
    config = fan_config.save_custom_enabled(enabled)
    push_fan_config(config)
    return config


def set_smart_fan_enabled(enabled: bool) -> FanConfig:
    config = fan_config.save_smart_enabled(enabled)
    push_fan_config(config)
    return config


def set_fan_curve_response(response: str) -> FanConfig:
    config = fan_config.save_curve_response(response)
    push_fan_config(config)
    return config


def set_manual_preset(preset: str | None) -> FanConfig:
    """Record which preset the user clicked (auto / max) or clear it.

    The daemon fan loop backs off while a preset is active so the
    hardware fan mode (max flag or manual pwm) survives.
    """
    config = fan_config.save_manual_preset(preset)
    push_fan_config(config)
    return config


def save_min_fan_change(pct: float) -> FanConfig:
    config = fan_config.load()
    config.min_fan_change_pct = max(float(pct), 0.0)
    fan_config.save_all(config)
    push_fan_config(config)
    return config
