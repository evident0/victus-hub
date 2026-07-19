"""Custom fan-control background loop.

Ported from hp_tauri src-tauri/src/lib.rs (start_fan_control + next_fan_percent).

Pure helpers (``next_fan_percent``, ``compute_ema``, curve targeting) are
unit-testable without the background thread. The poll/control loop lives
in ``FanController``; ownership resets are on ``LoopState``.
"""

from __future__ import annotations

import collections
import logging
import math
import threading
import time
from dataclasses import dataclass, field

from hp_helper import api
from hp_helper.backend import daemon_client as _daemon_client
from hp_helper.backend import fan_config as _fan_config
from hp_helper.backend.sysfs_read import read_hp_pwm_pct
from hp_helper.backend.types import FanProfileConfig

POLL_INTERVAL = 1.0
CONTROL_INTERVAL = 3.0

# When set, skip sysfs writes and drop custom ownership so resume re-enters
# custom (fan-manual + force first write). Pre-suspend cleanup sets fans auto.
_suspend = threading.Event()
_fan_logger = logging.getLogger("fan-control")


def set_suspended(state: bool) -> None:
    """Pause/resume the fan-control loop without cancelling the thread."""
    if state:
        _suspend.set()
    else:
        _suspend.clear()


# ── Pure helpers ──────────────────────────────────────────────────────────────


def next_fan_percent(
    target: float,
    current: float,
    ramp_down_since: float | None,
    now: float,
    ramp_up: float,
    ramp_down: float,
    ramp_down_delay: float,
) -> tuple[float, float | None]:
    """Next fan % with ramp-up/down. Returns (next_pct, new_ramp_down_since)."""
    if target > current:
        return min(current + ramp_up, target), None
    if target < current:
        if ramp_down_since is None:
            ramp_down_since = now
            if ramp_down_delay == 0.0:
                return max(current - ramp_down, target), ramp_down_since
            return current, ramp_down_since
        if now - ramp_down_since >= ramp_down_delay:
            return max(current - ramp_down, target), ramp_down_since
        return current, ramp_down_since
    return target, None


# ── Loop helpers ──────────────────────────────────────────────────────────────


def _profile_idx(raw: int | None) -> int:
    return max(0, min(raw if raw is not None else 1, 2))


def _pct_to_pwm(pct: float) -> int:
    return max(0, min(int(pct * 255.0 / 100.0), 255))


def _should_write(
    force_write: bool,
    last_written_pct: float | None,
    next_pct: float,
    min_delta_pct: float,
) -> bool:
    if force_write or last_written_pct is None:
        return True
    return abs(next_pct - last_written_pct) >= min_delta_pct


def compute_ema(samples: list[float], period: int) -> float | None:
    """Single exponential moving average (oldest first, newest last).

    Matches CoolerControl's plain EMA (not triple EMA):
    ``alpha = 2 / (period + 1)`` — the standard smoothing factor for an
    equivalent SMA window of ``period`` samples. Seeded with the first
    sample, then iteratively updated. Returns ``None`` if samples is empty.
    """
    if not samples:
        return None
    period = max(int(period), 1)
    alpha = 2.0 / (period + 1.0)
    ema = float(samples[0])
    for value in samples[1:]:
        ema = (float(value) - ema) * alpha + ema
    return ema


def _curve_target(
    history: collections.deque,
    profile: FanProfileConfig,
) -> tuple[float | None, float | None, float | None]:
    """EMA of temp window → CPU/GPU curve speeds; target is max of both.

    Uses a single exponential moving average over the temperature history
    (newest samples weighted more). ``period`` is the configured window
    size (deque maxlen), which sets EMA alpha independently of how many
    samples are currently filled.
    """
    period = history.maxlen if history.maxlen is not None else max(len(history), 1)
    cpu_temps = [float(t) for t, _ in history if t is not None]
    gpu_temps = [float(t) for _, t in history if t is not None]
    cpu_avg = gpu_avg = target = None
    if cpu_temps:
        cpu_avg = compute_ema(cpu_temps, period)
        if cpu_avg is not None:
            target = _fan_config.interpolate_fan(
                profile.cpu_points, int(math.floor(cpu_avg + 0.5)),
            )
    if gpu_temps:
        gpu_avg = compute_ema(gpu_temps, period)
        if gpu_avg is not None:
            gpu_s = _fan_config.interpolate_fan(
                profile.gpu_points, int(math.floor(gpu_avg + 0.5)),
            )
            target = max(target, gpu_s) if target is not None else gpu_s
    return cpu_avg, gpu_avg, target


def _gpu_s(gpu_avg: float | None) -> str:
    return f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A"


# ── Loop state + controller ───────────────────────────────────────────────────


@dataclass
class LoopState:
    """Mutable ownership/control state for one fan-control thread."""

    last_written_pct: float | None = None
    ramp_down_since: float | None = None
    next_control: float = 0.0
    was_custom: bool = False
    force_write: bool = False
    first_tick: bool = True
    temp_window: int = 15
    temp_history: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=15),
    )

    def on_suspend(self) -> None:
        """Drop ownership so resume re-enters custom with force write."""
        self.was_custom = False
        self.force_write = False
        self.last_written_pct = None
        self.ramp_down_since = None
        self.next_control = 0.0

    def on_manual_preset(self) -> None:
        """Idle under auto/max; keep last/ramp for a later custom re-entry."""
        self.was_custom = False
        self.force_write = False

    def on_leave_custom(self) -> None:
        """Clear after handing hardware back to auto."""
        self.last_written_pct = None
        self.ramp_down_since = None
        self.was_custom = False
        self.force_write = False

    def on_enter_custom(self, seeded_pct: float | None) -> None:
        """Seed duty, force first write, skip control-interval wait."""
        self.last_written_pct = seeded_pct
        self.ramp_down_since = None
        self.force_write = True
        self.next_control = 0.0


class FanController:
    """Poll sensors and drive custom fan PWM via the privileged daemon."""

    def __init__(self) -> None:
        cfg = _fan_config.load()
        self._st = LoopState(
            temp_window=cfg.temp_window,
            temp_history=collections.deque(maxlen=cfg.temp_window),
        )
        self._logged_init = False

    def run_forever(self) -> None:
        while True:
            self._poll_once()
            time.sleep(POLL_INTERVAL)

    def _poll_once(self) -> None:
        st = self._st
        if _suspend.is_set():
            st.on_suspend()
            return

        try:
            snap = api.read_sensors()
        except Exception:
            return

        st.temp_history.append((snap.cpu_temp_c, snap.gpu_temp_c))
        try:
            raw_profile = api.get_current_profile()
        except Exception:
            raw_profile = None
        profile_idx = _profile_idx(raw_profile)
        if not self._logged_init:
            _fan_logger.info(
                "fan-control init: api profile=%s → idx=%d", raw_profile, profile_idx,
            )
            self._logged_init = True

        cfg = _fan_config.load()
        profile = cfg.profiles[profile_idx]

        if cfg.manual_preset is not None:
            st.on_manual_preset()
            return

        if not cfg.custom_enabled:
            if st.was_custom:
                try:
                    _daemon_client.request_fan_auto()
                except Exception:
                    pass
                st.on_leave_custom()
            return

        if not st.was_custom:
            st.on_enter_custom(read_hp_pwm_pct())
            _fan_logger.info("fan-control: entering custom (fan-manual + force first write)")
            try:
                _daemon_client.request_fan_manual()
            except Exception:
                pass
        st.was_custom = True

        now = time.monotonic()
        if st.first_tick:
            st.next_control = now
            st.first_tick = False

        if now >= st.next_control:
            self._control_tick(cfg, profile, now)

    def _control_tick(self, cfg, profile: FanProfileConfig, now: float) -> None:
        st = self._st
        if cfg.temp_window != st.temp_window:
            st.temp_window = cfg.temp_window
            st.temp_history = collections.deque(list(st.temp_history), maxlen=st.temp_window)

        cpu_avg, gpu_avg, target = _curve_target(st.temp_history, profile)
        if target is not None:
            current = st.last_written_pct if st.last_written_pct is not None else target
            next_pct, st.ramp_down_since = next_fan_percent(
                target, current, st.ramp_down_since, now,
                cfg.ramp_up_pct, cfg.ramp_down_pct, cfg.ramp_down_delay,
            )
            delta = abs(next_pct - (st.last_written_pct if st.last_written_pct is not None else 0.0))
            enter = st.force_write
            cpu_s = cpu_avg if cpu_avg is not None else 0.0
            g = _gpu_s(gpu_avg)

            if _should_write(st.force_write, st.last_written_pct, next_pct, cfg.write_min_delta_pct):
                pwm = _pct_to_pwm(next_pct)
                reason = " (enter custom)" if enter else ""
                _fan_logger.info(
                    "cpu=%.0f°C gpu=%s target=%.0f%% cur=%.0f%% → next=%.0f%% delta=%.1f → pwm=%d WRITE%s",
                    cpu_s, g, target, current, next_pct, delta, pwm, reason,
                )
                try:
                    _daemon_client.request_fan_pwm(pwm)
                except Exception:
                    pass
                st.last_written_pct = next_pct
                st.force_write = False
            elif target < current and next_pct == current:
                _fan_logger.info(
                    "cpu=%.0f°C gpu=%s target=%.0f%% cur=%.0f%% → next=%.0f%% (ramp-down delay %.0fs)",
                    cpu_s, g, target, current, next_pct, cfg.ramp_down_delay,
                )
            else:
                _fan_logger.info(
                    "cpu=%.0f°C gpu=%s target=%.0f%% cur=%.0f%% → next=%.0f%% delta=%.1f (skip, <%.0f)",
                    cpu_s, g, target, current, next_pct, delta, cfg.write_min_delta_pct,
                )

        st.next_control = now + CONTROL_INTERVAL


def start_fan_control() -> None:
    """Start the custom fan-control background thread."""
    threading.Thread(
        target=FanController().run_forever, daemon=True, name="fan-control",
    ).start()
