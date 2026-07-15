"""Custom fan-control background loop.

Ported from hp_tauri src-tauri/src/lib.rs:85-224 (start_fan_control +
next_fan_percent).

Pure helpers (``next_fan_percent``, ``P0FloorState``, ``update_p0_debounce``,
``apply_p0_floor``) are unit-testable without the background thread.
The poll/control loop lives in ``FanController``; ownership resets are on
``LoopState``.
"""

from __future__ import annotations

import collections
import logging
import math
import threading
import time
from dataclasses import dataclass, field, replace

from hp_helper import api
from hp_helper.backend import daemon_client as _daemon_client
from hp_helper.backend import fan_config as _fan_config
from hp_helper.backend.sysfs_read import read_hp_pwm_pct
from hp_helper.backend.types import FanProfileConfig

POLL_INTERVAL = 1.0
CONTROL_INTERVAL = 3.0
P0_ENGAGE_S = 10.0
P0_RELEASE_S = 25.0

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


def is_gpu_p0(pstate: str | None) -> bool:
    """True when nvidia-smi reports performance state P0."""
    return pstate is not None and pstate.strip().upper() == "P0"


@dataclass(frozen=True)
class P0FloorState:
    """Debounce + override floor state for the GPU P0 fan-floor feature."""

    p0_since: float | None = None
    non_p0_since: float | None = None
    floor_active: bool = False
    floor_pwm_min: float | None = None

    def reset(self) -> P0FloorState:
        return P0FloorState()


def update_p0_debounce(
    state: P0FloorState,
    *,
    enabled: bool,
    is_p0: bool,
    now: float,
    last_written_pct: float | None,
    engage_s: float = P0_ENGAGE_S,
    release_s: float = P0_RELEASE_S,
) -> tuple[P0FloorState, str | None]:
    """Update P0 engage/release debounce.

    Returns ``(new_state, event)`` with event in
    ``engage`` / ``release`` / ``disabled`` / ``None``.

    Continuous P0 for ``engage_s`` engages (seed floor from last write).
    Continuous non-P0 for ``release_s`` while active releases. Brief gaps
    do not release; brief non-P0 blips reset the engage timer.
    """
    if not enabled:
        if state.floor_active or state.p0_since is not None or state.non_p0_since is not None:
            return state.reset(), "disabled"
        return state, None

    if is_p0:
        p0_since = state.p0_since if state.p0_since is not None else now
        floor_active, floor_pwm_min, event = state.floor_active, state.floor_pwm_min, None
        if not floor_active and (now - p0_since) >= engage_s:
            floor_active, floor_pwm_min, event = True, last_written_pct, "engage"
        return P0FloorState(p0_since, None, floor_active, floor_pwm_min), event

    if not state.floor_active:
        return P0FloorState(), None

    non_p0_since = state.non_p0_since if state.non_p0_since is not None else now
    if (now - non_p0_since) >= release_s:
        return state.reset(), "release"
    return P0FloorState(None, non_p0_since, True, state.floor_pwm_min), None


def apply_p0_floor(
    state: P0FloorState,
    *,
    enabled: bool,
    curve_pct: float,
    last_written_pct: float | None,
    min_pct: float,
    ramp_up_pct: float = 30.0,
) -> tuple[float, P0FloorState, str | None]:
    """Override curve with a rising floor while P0 floor is active.

    Ramps floor toward ``min_pct`` by ``ramp_up_pct`` per tick; output is
    ``max(curve_pct, floor)``. Event is ``"raise"`` when floor advances.
    """
    if not enabled or not state.floor_active:
        return curve_pct, state, None

    ramp_up = max(float(ramp_up_pct), 0.0)
    floor = state.floor_pwm_min
    if floor is None:
        floor = last_written_pct if last_written_pct is not None else curve_pct

    if ramp_up > 0 and floor < min_pct:
        floor = min(floor + ramp_up, float(min_pct))
    elif floor < min_pct:
        floor = float(min_pct)

    floor = float(int(round(floor)))
    advanced = state.floor_pwm_min is None or floor > state.floor_pwm_min
    return max(curve_pct, floor), replace(state, floor_pwm_min=floor), ("raise" if advanced else None)


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


def _curve_target(
    history: collections.deque,
    profile: FanProfileConfig,
) -> tuple[float | None, float | None, float | None]:
    """Window averages → CPU/GPU curve speeds; target is max of both."""
    cpu_temps = [int(t) for t, _ in history if t is not None]
    gpu_temps = [int(t) for _, t in history if t is not None]
    cpu_avg = gpu_avg = target = None
    if cpu_temps:
        cpu_avg = sum(cpu_temps) / len(cpu_temps)
        target = _fan_config.interpolate_fan(
            profile.cpu_points, int(math.floor(cpu_avg + 0.5)),
        )
    if gpu_temps:
        gpu_avg = sum(gpu_temps) / len(gpu_temps)
        gpu_s = _fan_config.interpolate_fan(
            profile.gpu_points, int(math.floor(gpu_avg + 0.5)),
        )
        target = max(target, gpu_s) if target is not None else gpu_s
    return cpu_avg, gpu_avg, target


def _gpu_s(gpu_avg: float | None) -> str:
    return f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A"


def _log_p0_event(event: str | None, p0_min_pct: float) -> None:
    if event == "engage":
        _fan_logger.info("P0 floor: engage (min %.0f%%) after %.0fs P0", p0_min_pct, P0_ENGAGE_S)
    elif event == "release":
        _fan_logger.info("P0 floor: release after %.0fs non-P0", P0_RELEASE_S)
    elif event == "disabled":
        _fan_logger.info("P0 floor: disabled by settings")


# ── Loop state + controller ───────────────────────────────────────────────────


@dataclass
class LoopState:
    """Mutable ownership/control state for one fan-control thread."""

    last_written_pct: float | None = None
    ramp_down_since: float | None = None
    next_control: float = 0.0
    was_custom: bool = False
    force_write: bool = False
    p0_state: P0FloorState = field(default_factory=P0FloorState)
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
        self.p0_state = self.p0_state.reset()
        self.next_control = 0.0

    def on_manual_preset(self) -> None:
        """Idle under auto/max; keep last/ramp for a later custom re-entry."""
        self.was_custom = False
        self.force_write = False
        self.p0_state = self.p0_state.reset()

    def on_leave_custom(self) -> None:
        """Clear after handing hardware back to auto."""
        self.last_written_pct = None
        self.ramp_down_since = None
        self.was_custom = False
        self.force_write = False
        self.p0_state = self.p0_state.reset()

    def on_enter_custom(self, seeded_pct: float | None) -> None:
        """Seed duty, force first write, skip control-interval wait."""
        self.last_written_pct = seeded_pct
        self.ramp_down_since = None
        self.p0_state = self.p0_state.reset()
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

        st.p0_state, p0_event = update_p0_debounce(
            st.p0_state,
            enabled=cfg.p0_min_pct_enabled,
            is_p0=is_gpu_p0(snap.gpu_pstate),
            now=now,
            last_written_pct=st.last_written_pct,
        )
        _log_p0_event(p0_event, cfg.p0_min_pct)

        if now >= st.next_control:
            self._control_tick(cfg, profile, snap.gpu_pstate, now)

    def _control_tick(self, cfg, profile: FanProfileConfig, gpu_pstate: str | None, now: float) -> None:
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
            curve_pct = next_pct
            next_pct, st.p0_state, floor_event = apply_p0_floor(
                st.p0_state,
                enabled=cfg.p0_min_pct_enabled,
                curve_pct=curve_pct,
                last_written_pct=st.last_written_pct,
                min_pct=cfg.p0_min_pct,
                ramp_up_pct=cfg.ramp_up_pct,
            )
            if floor_event == "raise":
                _fan_logger.info(
                    "P0 floor: raise floor to %.0f%% (min %.0f%%)",
                    st.p0_state.floor_pwm_min if st.p0_state.floor_pwm_min is not None else 0.0,
                    cfg.p0_min_pct,
                )

            floor_holds = (
                st.p0_state.floor_active
                and st.p0_state.floor_pwm_min is not None
                and next_pct > curve_pct + 0.01
            )
            delta = abs(next_pct - (st.last_written_pct if st.last_written_pct is not None else 0.0))
            enter = st.force_write
            cpu_s = cpu_avg if cpu_avg is not None else 0.0
            g = _gpu_s(gpu_avg)

            if _should_write(st.force_write, st.last_written_pct, next_pct, cfg.write_min_delta_pct):
                pwm = _pct_to_pwm(next_pct)
                reason = " (enter custom)" if enter else ""
                _fan_logger.info(
                    "cpu=%.0f°C gpu=%s pstate=%s target=%.0f%% cur=%.0f%% → next=%.0f%% delta=%.1f → pwm=%d WRITE%s%s",
                    cpu_s, g, gpu_pstate or "N/A",
                    target, current, next_pct, delta, pwm,
                    " (P0 floor)" if floor_holds else "", reason,
                )
                try:
                    _daemon_client.request_fan_pwm(pwm)
                except Exception:
                    pass
                st.last_written_pct = next_pct
                st.force_write = False
            elif floor_holds:
                _fan_logger.info(
                    "cpu=%.0f°C gpu=%s pstate=%s target=%.0f%% cur=%.0f%% → next=%.0f%% (P0 floor hold)",
                    cpu_s, g, gpu_pstate or "N/A", target, current, next_pct,
                )
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
