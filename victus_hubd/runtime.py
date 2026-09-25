"""Daemon-owned hardware control loops (fan, lighting, power, host policy)."""

from __future__ import annotations

import logging
import threading
import time

from victus_hub.backend import profiles
from victus_hub.backend import temps
from victus_hub.backend.cpu import is_intel_cpu
from victus_hub.backend.sysfs_read import read_hp_pwm_pct
from victus_hub.backend.types import FanConfig
from victus_hub.features.keyboard.lighting import (
    ANIM_INTERVAL_MS,
    STATIC_INTERVAL_MS,
    LightingSettings,
    effect_is_animated,
    lighting_frames,
    normalize_lighting_settings,
    step_brightness_settings,
    step_effect_settings,
    step_increment,
)
from victus_hub.services.fan_control import FanController, FanIO
from victus_hubd import cpufreq, host, intel, ryzenadj, sysfs
from victus_hubd.program_shortcuts import ProgramShortcuts
from victus_hubd.state import DaemonState, PowerPolicy, load_state, save_state

logger = logging.getLogger(__name__)

_KEY_LEFTCTRL = 29
_KEY_LEFTSHIFT = 42
_SHORTCUT_MODS = frozenset({_KEY_LEFTCTRL, _KEY_LEFTSHIFT})


class Runtime:
    """Owns persisted policy and the threads that apply it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hardware_lock = threading.RLock()
        self._state = load_state()
        self.program_shortcuts = ProgramShortcuts()
        self._stop = threading.Event()
        self._suspend = threading.Event()
        self._light_wake = threading.Event()
        self._power_wake = threading.Event()
        self._profile = host.read_profile_index(allow_cli=True)
        self._on_battery: bool | None = None
        self._last_power_apply = 0.0
        self._threads: list[threading.Thread] = []
        self._host_watch: host.HostWatch | None = None
        # Lighting write coalescing (mirrors the former UI controller).
        self._light_last_send = 0.0
        self._light_last_color: tuple[int, int, int] | None = None
        self._light_last_zone: list[tuple[int, int, int] | None] = []
        self._light_backlight_on: bool | None = None
        self._light_last_idle_poll = 0.0
        self._light_dimmed = False
        self._light_last_brightness: int | None = None
        self._light_anim_step = 0.0
        self._light_last_anim = time.monotonic()
        self._idle_elapsed = lambda: -1.0
        self._publish_lighting = lambda _settings: None
        self._fan = FanController(self._fan_io())

    def set_idle_elapsed(self, fn) -> None:
        self._idle_elapsed = fn

    def set_publish_lighting(self, fn) -> None:
        self._publish_lighting = fn

    def _fan_io(self) -> FanIO:
        return FanIO(
            read_temps=self._read_temps,
            get_profile=self.current_profile,
            load_config=self.fan_config,
            request_auto=lambda: self._write_hardware(sysfs.write_pwm_enable, 2),
            request_manual=lambda: self._write_hardware(sysfs.write_pwm_enable, 1),
            request_pwm=lambda pwm: self._write_hardware(sysfs.write_pwm, pwm),
            read_pwm_pct=read_hp_pwm_pct,
            is_suspended=self._suspend.is_set,
        )

    def _write_hardware(self, write, *args):
        # Temperature sampling can block; only serialize the actual writes.
        with self._hardware_lock:
            if not self._suspend.is_set():
                return write(*args)

    def nvidia_queries_disabled(self) -> bool:
        with self._lock:
            return self._state.disable_nvidia_queries and self._profile == 0

    def fan_needs_gpu_temp(self) -> bool:
        """Custom/smart curve is driving PWM, so the fan loop must read GPU temp."""
        config = self.fan_config()
        return (
            config.custom_enabled
            and config.manual_preset is None
            and sysfs.manual_fan_supported()
        )

    def _read_temps(self):
        return temps.read_cpu_gpu_temps(disable_nvidia=self.nvidia_queries_disabled())

    def start(self) -> None:
        """Start control threads and apply persisted policy."""
        self._threads = [
            threading.Thread(target=self._fan_loop, daemon=True, name="fan-control"),
            threading.Thread(target=self._lighting_loop, daemon=True, name="lighting"),
            threading.Thread(target=self._power_loop, daemon=True, name="power-reapply"),
        ]
        for thread in self._threads:
            thread.start()
        self._host_watch = host.HostWatch(self._on_host_event, self._stop)
        self._host_watch.start()
        self._refresh_host(force=True)
        self._apply_fan_mode()
        self._invalidate_lighting()
        with self._lock:
            freq = self._state.cpu_frequency
        if freq is not None:
            try:
                cpufreq.apply_frequency_limits(*freq)
            except Exception:
                logger.exception("startup: cpu frequency apply failed")
        self._light_wake.set()
        self._power_wake.set()
        logger.info("runtime: control loops started")

    def stop(self, *, reset_hardware: bool = True) -> None:
        """Stop loops; optionally restore fans to auto and keyboard off."""
        self._stop.set()
        self._light_wake.set()
        self._power_wake.set()
        if reset_hardware:
            self.prepare_sleep()
        for thread in self._threads:
            thread.join(timeout=2.0)

    # ── Snapshot accessors ──

    def snapshot(self) -> DaemonState:
        with self._lock:
            return self._copy_state()

    def fan_config(self) -> FanConfig:
        with self._lock:
            return self._state.fan

    def current_profile(self) -> int | None:
        with self._lock:
            return self._profile

    def _copy_state(self) -> DaemonState:
        state = self._state
        return DaemonState(
            fan=state.fan,
            lighting=state.lighting,
            power=state.power,
            cpu_frequency=state.cpu_frequency,
            battery_power_save=state.battery_power_save,
            hardware_shortcuts=state.hardware_shortcuts,
            profile_before_battery=state.profile_before_battery,
            initialized=state.initialized,
            disable_nvidia_queries=state.disable_nvidia_queries,
        )

    def _persist(self) -> None:
        self._state.initialized = True
        save_state(self._state)

    # ── Policy setters (socket handlers) ──

    def set_fan_config(self, config: FanConfig) -> str:
        with self._lock:
            self._state.fan = config
            self._persist()
        self._apply_fan_mode()
        return "fan-config"

    def set_lighting(self, settings: LightingSettings) -> str:
        with self._lock:
            self._state.lighting = settings
            self._persist()
        self._invalidate_lighting()
        self._light_wake.set()
        return "lighting-config"

    def set_power(self, policy: PowerPolicy) -> str:
        with self._lock:
            self._state.power = policy
            self._persist()
        self._last_power_apply = 0.0
        self._power_wake.set()
        return "power-config"

    def set_cpu_frequency(self, limits: tuple[int, int] | None) -> str:
        with self._lock:
            self._state.cpu_frequency = limits
            self._persist()
        if limits is not None:
            cpufreq.apply_frequency_limits(*limits)
        return "cpu-frequency-config"

    def set_battery_power_save(self, enabled: bool) -> str:
        with self._lock:
            self._state.battery_power_save = enabled
            if not enabled:
                self._state.profile_before_battery = None
            self._persist()
        self._on_battery = None
        self._refresh_host(force=True)
        return "battery-power-save"

    def set_hardware_shortcuts(self, enabled: bool) -> str:
        with self._lock:
            self._state.hardware_shortcuts = enabled
            self._persist()
        return "hardware-shortcuts"

    def set_disable_nvidia_queries(self, enabled: bool) -> str:
        with self._lock:
            self._state.disable_nvidia_queries = enabled
            self._persist()
        return "disable-nvidia-queries"

    def set_profile(self, index: int) -> str:
        index = max(0, min(int(index), 2))
        result = profiles.apply_system_profile(index)
        with self._lock:
            self._profile = index
        return result

    def handle_key(self, mods: tuple[int, ...], key: int) -> None:
        """Apply hardware shortcuts locally so they work with no UI."""
        if key == 0:
            return
        with self._lock:
            enabled = self._state.hardware_shortcuts
        if not enabled or frozenset(mods) != _SHORTCUT_MODS:
            return
        if key in (103, 108):  # Up / Down
            self._step_brightness(1 if key == 103 else -1)
            return
        if key in (105, 106):  # Left / Right
            self._step_effect(1 if key == 106 else -1)
            return
        if key == 50:  # M
            current = self.current_profile()
            if current is None:
                current = 1
            try:
                self.set_profile((current + 1) % 3)
            except Exception:
                logger.exception("hardware shortcut: profile cycle failed")

    def prepare_sleep(self) -> None:
        """Safe hardware state before suspend/shutdown/SIGTERM."""
        self._suspend.set()
        with self._hardware_lock:
            self._park_hardware()

    def _park_hardware(self) -> None:
        try:
            sysfs.write_pwm_enable(2)
        except Exception:
            logger.exception("prepare-sleep: fan auto failed")
        try:
            sysfs.write_keyboard_brightness(0, quiet=True)
        except Exception:
            logger.exception("prepare-sleep: keyboard off failed")
        self._invalidate_lighting()

    def resume(self) -> None:
        """Restore policy after suspend."""
        self._fan._st.on_suspend()
        self._invalidate_lighting()
        self._suspend.clear()
        self._apply_fan_mode()
        self._last_power_apply = 0.0
        self._light_wake.set()
        self._power_wake.set()
        with self._lock:
            freq = self._state.cpu_frequency
        if freq is not None:
            try:
                cpufreq.apply_frequency_limits(*freq)
            except Exception:
                logger.exception("resume: cpu frequency restore failed")

    # ── Fan ──

    def _apply_fan_mode(self) -> None:
        with self._hardware_lock:
            self._apply_fan_mode_locked()

    def _apply_fan_mode_locked(self) -> None:
        if self._suspend.is_set():
            return
        with self._lock:
            config = self._state.fan
        try:
            if config.manual_preset == "max":
                sysfs.write_pwm_max()
            elif config.manual_preset == "auto" or not config.custom_enabled:
                sysfs.write_pwm_enable(2)
            elif not sysfs.manual_fan_supported():
                logger.warning("Manual fan control unavailable; using EC automatic mode")
                sysfs.write_pwm_enable(2)
            else:
                sysfs.write_pwm_enable(1)
        except Exception:
            logger.exception("apply fan mode failed")

    def _fan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                config = self.fan_config()
                if self._suspend.is_set() or not config.custom_enabled or config.manual_preset is not None:
                    if not temps.display_gpu_held():
                        temps.close_nvidia()
                if config.custom_enabled and config.manual_preset is None and not sysfs.manual_fan_supported():
                    self._fan._st.on_leave_custom()
                    if not temps.display_gpu_held():
                        temps.close_nvidia()
                else:
                    self._fan._poll_once()
            except Exception:
                logger.exception("fan-control tick failed")
            self._stop.wait(1.0)

    # ── Host (profile + battery) ──

    def _on_host_event(self) -> None:
        self._refresh_host()

    def _refresh_host(self, *, force: bool = False) -> None:
        index = host.read_profile_index(allow_cli=force)
        if index is not None:
            with self._lock:
                self._profile = index
        on_ac = host.ac_online()
        if on_ac is None:
            return
        on_battery = not on_ac
        if not force and on_battery is self._on_battery:
            return
        previous = self._on_battery
        self._on_battery = on_battery
        with self._lock:
            enabled = self._state.battery_power_save
        if not enabled:
            return
        if on_battery and previous is not True:
            current = self.current_profile()
            if current is None:
                current = 1
            if current != 0:
                with self._lock:
                    self._state.profile_before_battery = current
                    self._persist()
                try:
                    self.set_profile(0)
                except Exception:
                    logger.exception("battery power-save: profile apply failed")
        elif not on_battery:
            with self._lock:
                restore = self._state.profile_before_battery
                if restore is None:
                    return
                self._state.profile_before_battery = None
                self._persist()
            current = self.current_profile()
            if restore is not None and current == 0:
                try:
                    self.set_profile(restore)
                except Exception:
                    logger.exception("battery restore: profile apply failed")

    # ── Lighting ──

    def _invalidate_lighting(self, *, reset_backlight: bool = True) -> None:
        zone_count = max(1, sysfs.get_keyboard_zone_count())
        self._light_last_color = None
        self._light_last_zone = [None] * zone_count
        if reset_backlight:
            self._light_backlight_on = None
        self._light_last_anim = time.monotonic()

    def _step_brightness(self, direction: int) -> None:
        with self._lock:
            self._state.lighting = step_brightness_settings(
                self._state.lighting, direction,
            )
            settings = self._state.lighting
            self._persist()
        self._invalidate_lighting()
        self._light_wake.set()
        self._publish_lighting(settings)

    def _step_effect(self, direction: int) -> None:
        zone_count = max(1, sysfs.get_keyboard_zone_count())
        with self._lock:
            self._state.lighting = step_effect_settings(
                self._state.lighting, direction, zone_count,
            )
            settings = self._state.lighting
            self._persist()
        self._light_anim_step = 0.0
        self._invalidate_lighting()
        self._light_wake.set()
        self._publish_lighting(settings)

    def _lighting_interval(self) -> float:
        with self._lock:
            settings = self._state.lighting
        zone_count = max(1, sysfs.get_keyboard_zone_count())
        settings = normalize_lighting_settings(settings, zone_count)
        if settings.enabled and effect_is_animated(settings.effect):
            return ANIM_INTERVAL_MS / 1000.0
        return STATIC_INTERVAL_MS / 1000.0

    def _lighting_loop(self) -> None:
        while not self._stop.is_set():
            if self._suspend.is_set():
                self._stop.wait(0.2)
                continue
            try:
                self._lighting_tick()
            except Exception:
                logger.exception("lighting tick failed")
            self._light_wake.wait(self._lighting_interval())
            self._light_wake.clear()

    def _lighting_tick(self) -> None:
        with self._hardware_lock:
            if not self._suspend.is_set():
                self._lighting_tick_locked()

    def _lighting_tick_locked(self) -> None:
        zone_count = max(1, sysfs.get_keyboard_zone_count())
        with self._lock:
            raw = self._state.lighting
        settings = normalize_lighting_settings(raw, zone_count)
        now = time.monotonic()
        dt = max(0.0, now - self._light_last_anim)
        self._light_last_anim = now
        animated = settings.enabled and effect_is_animated(settings.effect)
        if animated:
            self._light_anim_step += step_increment(settings.speed, dt)
        else:
            self._light_anim_step = 0.0

        if now - self._light_last_idle_poll >= 0.5:
            self._light_last_idle_poll = now
            if settings.idle_timeout > 0 and settings.enabled:
                idle_elapsed = self._idle_elapsed()
                if idle_elapsed < 0:
                    if self._light_dimmed:
                        self._light_dimmed = False
                        self._invalidate_lighting()
                elif idle_elapsed >= settings.idle_timeout and not self._light_dimmed:
                    self._light_dimmed = True
                    self._invalidate_lighting()
                elif idle_elapsed < settings.idle_timeout and self._light_dimmed:
                    self._light_dimmed = False
                    self._invalidate_lighting()
            elif self._light_dimmed:
                self._light_dimmed = False
                self._invalidate_lighting()

        want_off = (not settings.enabled) or self._light_dimmed
        if want_off:
            if self._light_backlight_on is not False and now - self._light_last_send >= 0.200:
                try:
                    sysfs.write_keyboard_brightness(0, quiet=True)
                except Exception:
                    pass
                self._light_backlight_on = False
                self._light_last_send = now
                self._light_last_brightness = 0
                self._invalidate_lighting(reset_backlight=False)
            return

        if self._light_last_brightness != settings.brightness:
            try:
                sysfs.set_keyboard_user_brightness(settings.brightness)
                self._light_last_brightness = settings.brightness
            except Exception:
                self._light_last_brightness = None

        frames = lighting_frames(settings, zone_count, self._light_anim_step)
        min_interval = 0.050 if animated else 0.200
        if zone_count <= 1:
            self._tick_single_zone(now, frames[0], min_interval)
        else:
            self._tick_multi_zone(now, frames, min_interval)

    def _tick_single_zone(self, now: float, frame, min_interval: float) -> None:
        color = (frame.red, frame.green, frame.blue)
        need = (self._light_backlight_on is not True) or (color != self._light_last_color)
        if now - self._light_last_send >= min_interval and need:
            try:
                sysfs.write_keyboard_color(*color, quiet=True)
            except Exception:
                pass
            self._light_last_send = now
            self._light_last_color = color
            self._light_backlight_on = True

    def _tick_multi_zone(self, now: float, frames, min_interval: float) -> None:
        if now - self._light_last_send < min_interval:
            return
        any_write = False
        for zone, frame in enumerate(frames):
            color = (frame.red, frame.green, frame.blue)
            last = (
                self._light_last_zone[zone]
                if zone < len(self._light_last_zone)
                else None
            )
            need = (self._light_backlight_on is not True) or (color != last)
            if not need:
                continue
            try:
                sysfs.write_keyboard_zone_color(zone, *color, quiet=True)
            except Exception:
                pass
            if zone < len(self._light_last_zone):
                self._light_last_zone[zone] = color
            any_write = True
        if any_write or self._light_backlight_on is not True:
            self._light_last_send = now
            self._light_backlight_on = True

    # ── Power / frequency reapply ──

    def _power_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_apply_power()
            except Exception:
                logger.exception("power reapply failed")
            wait = 1.0
            with self._lock:
                if self._state.power.enabled and self._state.power.reapply_seconds > 0:
                    wait = min(1.0, float(self._state.power.reapply_seconds))
            self._power_wake.wait(wait)
            self._power_wake.clear()

    def _maybe_apply_power(self) -> None:
        if self._suspend.is_set():
            return
        with self._lock:
            policy = self._state.power
            freq = self._state.cpu_frequency
        if not policy.enabled or policy.reapply_seconds <= 0:
            return
        now = time.monotonic()
        if self._last_power_apply != 0.0 and now - self._last_power_apply < policy.reapply_seconds:
            return
        self._apply_power(policy)
        if freq is not None:
            try:
                cpufreq.apply_frequency_limits(*freq)
            except Exception:
                logger.exception("cpu frequency apply failed")
        self._last_power_apply = now

    def _apply_power(self, policy: PowerPolicy) -> None:
        try:
            if is_intel_cpu():
                intel.apply_power_limits(policy.slow_limit, policy.fast_limit)
            else:
                ryzenadj.apply_power_limits(
                    policy.stapm_limit, policy.fast_limit,
                    policy.slow_limit, policy.tctl_temp,
                )
        except Exception:
            logger.exception("apply power limits failed")
