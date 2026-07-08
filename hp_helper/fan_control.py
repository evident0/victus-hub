"""Custom fan-control background loop.

Ported from hp_tauri src-tauri/src/lib.rs:85-224 (start_fan_control +
next_fan_percent).

P0 min-RPM floor logic is factored into pure helpers (``P0FloorState``,
``update_p0_debounce``, ``apply_p0_floor``) so it can be unit-tested
without the background thread.
"""

from __future__ import annotations

import collections
import logging
import math
import re
import threading
from dataclasses import dataclass, replace

from hp_helper import api
from hp_helper.backend import fan_config as _fan_config
from hp_helper.backend import daemon_client as _daemon_client
from hp_helper.backend.sysfs_read import read_hp_pwm_pct

POLL_INTERVAL = 1.0
CONTROL_INTERVAL = 3.0

# P0 min-RPM floor debounce (custom mode only).
P0_ENGAGE_S = 6.0
P0_RELEASE_S = 25.0
# Closed-loop PWM steps while floor is active (not full curve ramp_up).
P0_RAISE_STEP_PCT = 5.0
P0_SETTLE_STEP_PCT = 5.0

_fan_logger = logging.getLogger("fan-control")
_RPM_RE = re.compile(r"(\d+)")


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


def parse_rpm(reading_value: str) -> int | None:
    """Extract an integer RPM from a sensor reading string."""
    m = _RPM_RE.search(reading_value or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def max_fan_rpm(cpu_fan_value: str, gpu_fan_value: str) -> int | None:
    """Return max of the two fan RPM readings, or None if neither parses."""
    rpms = []
    for val in (cpu_fan_value, gpu_fan_value):
        rpm = parse_rpm(val)
        if rpm is not None:
            rpms.append(rpm)
    return max(rpms) if rpms else None


def is_gpu_p0(pstate: str | None) -> bool:
    """True when nvidia-smi reports performance state P0."""
    if pstate is None:
        return False
    return pstate.strip().upper() == "P0"


@dataclass(frozen=True)
class P0FloorState:
    """Debounce + closed-loop floor state for the GPU P0 min-RPM feature."""

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
    is_p0: bool,
    curve_pct: float,
    last_written_pct: float | None,
    measured_rpm: int | None,
    min_rpm: int,
    raise_step_pct: float = P0_RAISE_STEP_PCT,
    settle_step_pct: float = P0_SETTLE_STEP_PCT,
) -> tuple[float, P0FloorState, str | None]:
    """Clamp curve output to the P0 closed-loop floor.

    While the floor is active:
    - If measured RPM is below ``min_rpm``, raise ``floor_pwm_min`` by a
      small step (default 5%), never by the full curve ramp-up rate.
    - If currently P0 and measured RPM is at/above ``min_rpm``, soft-settle
      the floor toward the curve (unstick overshoot). Settle is **disabled**
      during the post-P0 hold window so the 25s release is real.
    - During non-P0 hold: freeze floor (raise only if RPM drops below min).
    - Final command is ``max(curve_pct, floor_pwm_min)``.

    Returns ``(next_pct, new_state, event)`` with event ``\"raise\"``,
    ``\"settle\"``, or None.
    """
    if not enabled or not state.floor_active:
        return curve_pct, state, None

    floor = state.floor_pwm_min
    event = None
    base = last_written_pct if last_written_pct is not None else curve_pct
    raise_step = max(float(raise_step_pct), 1.0)
    settle_step = max(float(settle_step_pct), 1.0)

    if measured_rpm is not None and measured_rpm < min_rpm:
        # Raise from the current floor/command only — small steps.
        origin = floor if floor is not None else base
        raised = min(100.0, origin + raise_step)
        # Also never sit below the curve while hunting upward.
        raised = max(raised, curve_pct)
        if floor is None or raised > floor + 1e-9:
            floor = raised
            event = "raise"
        else:
            floor = raised
    elif measured_rpm is not None and measured_rpm >= min_rpm:
        if floor is None:
            floor = base
        if is_p0:
            # Soft-settle only while still in P0 (unstick 100% overshoot).
            settled = max(curve_pct, floor - settle_step)
            if settled < floor - 1e-9:
                event = "settle"
            floor = settled
        # else: non-P0 hold window — freeze floor, do not settle down
    else:
        # No RPM reading — hold last floor (or seed from base).
        if floor is None:
            floor = base

    next_pct = max(curve_pct, floor)
    new_state = replace(state, floor_pwm_min=floor)
    return next_pct, new_state, event


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
                enabled=cfg.p0_min_rpm_enabled,
                is_p0=is_gpu_p0(snap.gpu_pstate),
                now=now,
                last_written_pct=last_written_pct,
            )
            if p0_event == "engage":
                _fan_logger.info(
                    "P0 floor: engage (min %d RPM) after %.0fs P0",
                    cfg.p0_min_rpm, P0_ENGAGE_S,
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

                    measured = max_fan_rpm(snap.cpu_fan.value, snap.gpu_fan.value)
                    next_pct, p0_state, floor_event = apply_p0_floor(
                        p0_state,
                        enabled=cfg.p0_min_rpm_enabled,
                        is_p0=is_gpu_p0(snap.gpu_pstate),
                        curve_pct=curve_pct,
                        last_written_pct=last_written_pct,
                        measured_rpm=measured,
                        min_rpm=cfg.p0_min_rpm,
                    )
                    if floor_event == "raise":
                        _fan_logger.info(
                            "P0 floor: rpm=%s < %d → raise floor to %.0f%%",
                            measured if measured is not None else "N/A",
                            cfg.p0_min_rpm,
                            p0_state.floor_pwm_min if p0_state.floor_pwm_min is not None else 0.0,
                        )
                    elif floor_event == "settle":
                        _fan_logger.info(
                            "P0 floor: rpm=%s ≥ %d → settle floor to %.0f%%",
                            measured if measured is not None else "N/A",
                            cfg.p0_min_rpm,
                            p0_state.floor_pwm_min if p0_state.floor_pwm_min is not None else 0.0,
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
                                "cpu=%.0f°C gpu=%s pstate=%s target=%.0f%% cur=%.0f%% → next=%.0f%% (P0 floor hold, rpm=%s)",
                                cpu_avg if cpu_avg is not None else 0.0,
                                f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A",
                                snap.gpu_pstate or "N/A",
                                target, current, next_pct,
                                measured if measured is not None else "N/A",
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
