"""Custom fan-control background loop.

Ported from hp_tauri src-tauri/src/lib.rs:85-224 (start_fan_control +
next_fan_percent).

P0 fan-floor override logic is factored into pure helpers (``P0FloorState``,
``update_p0_debounce``, ``apply_p0_floor``) so it can be unit-tested
without the background thread.
"""

from __future__ import annotations

import collections
import logging
import math
import threading
from dataclasses import dataclass, replace

from hp_helper import api
from hp_helper.backend import fan_config as _fan_config
from hp_helper.backend import daemon_client as _daemon_client
from hp_helper.backend.sysfs_read import read_hp_pwm_pct

POLL_INTERVAL = 1.0
CONTROL_INTERVAL = 3.0

# Suspend gate: when set, the control loop skips all sysfs writes so the
# pre-suspend cleanup (fan-auto) is not immediately re-asserted as manual.
_suspend = threading.Event()  # noqa: PLW0602 (module-level Event, intentional)


def set_suspended(state: bool) -> None:
    """Pause/resume the fan-control loop without cancelling the thread."""
    if state:
        _suspend.set()
    else:
        _suspend.clear()
# P0 fan-floor override debounce (custom mode only).
P0_ENGAGE_S = 10.0
P0_RELEASE_S = 25.0

_fan_logger = logging.getLogger("fan-control")


def next_fan_percent(
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


def is_gpu_p0(pstate: str | None) -> bool:
    """True when nvidia-smi reports performance state P0."""
    if pstate is None:
        return False
    return pstate.strip().upper() == "P0"


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

    Returns ``(new_state, event)`` where event is ``\"engage\"``,
    ``\"release\"``, ``\"disabled\"``, or ``None``.

    Rules:
    - Feature off → clear all state (immediate release).
    - Continuous P0 for ``engage_s`` → engage floor; seed floor_pwm_min
      from last written PWM (never lower later while active).
    - Continuous non-P0 for ``release_s`` while active → release.
    - Brief P0 gaps do not release until ``release_s`` elapses.
    - Brief non-P0 blips during P0 reset the engage timer (must be
      continuous P0 to engage).
    """
    if not enabled:
        if state.floor_active or state.p0_since is not None or state.non_p0_since is not None:
            return state.reset(), "disabled"
        return state, None

    if is_p0:
        p0_since = state.p0_since if state.p0_since is not None else now
        non_p0_since = None
        floor_active = state.floor_active
        floor_pwm_min = state.floor_pwm_min
        event = None
        if not floor_active and (now - p0_since) >= engage_s:
            floor_active = True
            floor_pwm_min = last_written_pct
            event = "engage"
        return (
            P0FloorState(
                p0_since=p0_since,
                non_p0_since=non_p0_since,
                floor_active=floor_active,
                floor_pwm_min=floor_pwm_min,
            ),
            event,
        )

    # Not P0
    p0_since = None
    if not state.floor_active:
        return (
            P0FloorState(
                p0_since=None,
                non_p0_since=None,
                floor_active=False,
                floor_pwm_min=None,
            ),
            None,
        )

    non_p0_since = state.non_p0_since if state.non_p0_since is not None else now
    if (now - non_p0_since) >= release_s:
        return state.reset(), "release"

    return (
        P0FloorState(
            p0_since=p0_since,
            non_p0_since=non_p0_since,
            floor_active=True,
            floor_pwm_min=state.floor_pwm_min,
        ),
        None,
    )


def apply_p0_floor(
    state: P0FloorState,
    *,
    enabled: bool,
    curve_pct: float,
    last_written_pct: float | None,
    min_pct: float,
    ramp_up_pct: float = 30.0,
) -> tuple[float, P0FloorState, str | None]:
    """P0 fan-floor override applied over the fan-curve command.

    While the floor is active it ramps from the last written PWM toward
    ``min_pct`` in steps of ``ramp_up_pct`` per control tick
    (``floor = min(floor + ramp_up, min_pct)``) and never decreases. The
    output is ``max(curve_pct, floor)``: the curve may command a *higher*
    speed, but any curve command below the floor — including below
    ``min_pct`` once it is reached — is blocked until the debounce releases
    the floor after ``P0_RELEASE_S`` of continuous non-P0.

    Example (min_pct=80, ramp_up=30, fans at 40% on engage):
    tick 1 → 40+30 = 70%, tick 2 → min(70+30, 80) = 80% (minimum reached).
    Once at 80% the curve may raise higher; commands below 80% are held
    at 80% until ~25s of non-P0 elapse.

    Returns ``(next_pct, new_state, event)`` where event is ``"raise"`` on a
    tick that advanced the floor, or ``None`` (floor holding at ``min_pct``).
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
    next_pct = max(curve_pct, floor)
    new_state = replace(state, floor_pwm_min=floor)
    return next_pct, new_state, "raise" if advanced else None


def start_fan_control() -> None:
    """Background thread that implements the custom fan-control loop."""

    def _loop():
        # Seed with config values
        cfg = _fan_config.load()
        temp_window = cfg.temp_window
        temp_history: collections.deque = collections.deque(maxlen=temp_window)
        last_written_pct: float | None = None
        ramp_down_since: float | None = None
        next_control = 0.0
        was_custom = False
        p0_state = P0FloorState()
        first_tick = True

        while True:
            import time as _time

            # Suspend gate: skip all control writes while the system is
            # suspending so the pre-suspend fan-auto cleanup is not undone.
            if _suspend.is_set():
                _time.sleep(POLL_INTERVAL)
                continue

            # ── poll ──
            try:
                snap = api.read_sensors()
            except Exception:
                _time.sleep(POLL_INTERVAL)
                continue

            temp_history.append((snap.cpu_temp_c, snap.gpu_temp_c))

            raw_profile = None
            try:
                raw_profile = api.get_current_profile()
            except Exception:
                raw_profile = None
            profile_idx = raw_profile if raw_profile is not None else 1
            profile_idx = max(0, min(profile_idx, 2))
            if not hasattr(_fan_logger, "_logged_init"):
                _fan_logger.info(
                    "fan-control init: api profile=%s → idx=%d",
                    raw_profile, profile_idx,
                )
                _fan_logger._logged_init = True

            cfg = _fan_config.load()
            profile = cfg.profiles[profile_idx]
            if cfg.manual_preset is not None:
                was_custom = False
                p0_state = p0_state.reset()
                _time.sleep(POLL_INTERVAL)
                continue
            if not cfg.custom_enabled:
                if was_custom:
                    try:
                        _daemon_client.request_fan_auto()
                    except Exception:
                        pass
                    last_written_pct = None
                    ramp_down_since = None
                    was_custom = False
                    p0_state = p0_state.reset()
                _time.sleep(POLL_INTERVAL)
                continue
            if not was_custom:
                last_written_pct = read_hp_pwm_pct()
                ramp_down_since = None
                p0_state = p0_state.reset()
            was_custom = True

            now = _time.monotonic()
            if first_tick:
                next_control = now
                first_tick = False

            # ── P0 debounce (every poll) ──
            p0_state, p0_event = update_p0_debounce(
                p0_state,
                enabled=cfg.p0_min_pct_enabled,
                is_p0=is_gpu_p0(snap.gpu_pstate),
                now=now,
                last_written_pct=last_written_pct,
            )
            if p0_event == "engage":
                _fan_logger.info(
                    "P0 floor: engage (min %.0f%%) after %.0fs P0",
                    cfg.p0_min_pct, P0_ENGAGE_S,
                )
            elif p0_event == "release":
                _fan_logger.info(
                    "P0 floor: release after %.0fs non-P0",
                    P0_RELEASE_S,
                )
            elif p0_event == "disabled":
                _fan_logger.info("P0 floor: disabled by settings")

            # ── control tick ──
            if now >= next_control:
                if cfg.temp_window != temp_window:
                    temp_window = cfg.temp_window
                    old = list(temp_history)
                    temp_history = collections.deque(old, maxlen=temp_window)

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
                        profile.cpu_points, int(math.floor(cpu_avg + 0.5))
                    )
                    target = s

                if gpu_temps:
                    gpu_avg = sum(gpu_temps) / len(gpu_temps)
                    s = _fan_config.interpolate_fan(
                        profile.gpu_points, int(math.floor(gpu_avg + 0.5))
                    )
                    target = max(target, s) if target is not None else s

                if target is not None:
                    current = last_written_pct if last_written_pct is not None else target
                    next_pct, ramp_down_since = next_fan_percent(
                        target, current, ramp_down_since, now,
                        cfg.ramp_up_pct, cfg.ramp_down_pct, cfg.ramp_down_delay,
                    )
                    curve_pct = next_pct

                    next_pct, p0_state, floor_event = apply_p0_floor(
                        p0_state,
                        enabled=cfg.p0_min_pct_enabled,
                        curve_pct=curve_pct,
                        last_written_pct=last_written_pct,
                        min_pct=cfg.p0_min_pct,
                        ramp_up_pct=cfg.ramp_up_pct,
                    )
                    if floor_event == "raise":
                        _fan_logger.info(
                            "P0 floor: raise floor to %.0f%% (min %.0f%%)",
                            p0_state.floor_pwm_min if p0_state.floor_pwm_min is not None else 0.0,
                            cfg.p0_min_pct,
                        )

                    floor_holds = (
                        p0_state.floor_active
                        and p0_state.floor_pwm_min is not None
                        and next_pct > curve_pct + 0.01
                    )
                    delta = abs(next_pct - (last_written_pct if last_written_pct is not None else 0.0))
                    if last_written_pct is None or delta >= cfg.write_min_delta_pct:
                        pwm = max(0, min(int(next_pct * 255.0 / 100.0), 255))
                        _fan_logger.info(
                            "cpu=%.0f°C gpu=%s pstate=%s target=%.0f%% cur=%.0f%% → next=%.0f%% delta=%.1f → pwm=%d WRITE%s",
                            cpu_avg if cpu_avg is not None else 0.0,
                            f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A",
                            snap.gpu_pstate or "N/A",
                            target, current, next_pct, delta, pwm,
                            " (P0 floor)" if floor_holds else "",
                        )
                        try:
                            _daemon_client.request_fan_pwm(pwm)
                        except Exception:
                            pass
                        last_written_pct = next_pct
                    else:
                        if floor_holds:
                            _fan_logger.info(
                                "cpu=%.0f°C gpu=%s pstate=%s target=%.0f%% cur=%.0f%% → next=%.0f%% (P0 floor hold)",
                                cpu_avg if cpu_avg is not None else 0.0,
                                f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A",
                                snap.gpu_pstate or "N/A",
                                target, current, next_pct,
                            )
                        elif target < current and next_pct == current:
                            _fan_logger.info(
                                "cpu=%.0f°C gpu=%s target=%.0f%% cur=%.0f%% → next=%.0f%% (ramp-down delay %.0fs)",
                                cpu_avg if cpu_avg is not None else 0.0,
                                f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A",
                                target, current, next_pct, cfg.ramp_down_delay,
                            )
                        else:
                            _fan_logger.info(
                                "cpu=%.0f°C gpu=%s target=%.0f%% cur=%.0f%% → next=%.0f%% delta=%.1f (skip, <%.0f)",
                                cpu_avg if cpu_avg is not None else 0.0,
                                f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A",
                                target, current, next_pct, delta, cfg.write_min_delta_pct,
                            )

                next_control = now + CONTROL_INTERVAL

            _time.sleep(POLL_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="fan-control")
    t.start()
