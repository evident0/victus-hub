"""Custom fan-control background loop.

Ported from hp_tauri src-tauri/src/lib.rs:85-224 (start_fan_control +
next_fan_percent).
"""

import collections
import logging
import threading

from hp_helper import api
from hp_helper.backend import fan_config as _fan_config
from hp_helper.backend import daemon_client as _daemon_client
from hp_helper.backend.sysfs_read import read_hp_pwm_pct

POLL_INTERVAL = 1.0
CONTROL_INTERVAL = 3.0

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


def start_fan_control() -> None:
    """Background thread that implements the custom fan-control loop."""

    def _loop():
        import time as _time
        import math as _math

        # Seed with config values
        cfg = _fan_config.load()
        temp_window = cfg.temp_window
        temp_history: collections.deque = collections.deque(maxlen=temp_window)
        last_written_pct: float | None = None
        ramp_down_since: float | None = None
        next_control = _time.monotonic()
        was_custom = False

        while True:
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
                _time.sleep(POLL_INTERVAL)
                continue
            if not was_custom:
                last_written_pct = read_hp_pwm_pct()
                ramp_down_since = None
            was_custom = True

            # ── control tick ──
            now = _time.monotonic()
            if now >= next_control:
                # Rebuild temp history if window size changed
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
                        profile.cpu_points, int(_math.floor(cpu_avg + 0.5))
                    )
                    target = s

                if gpu_temps:
                    gpu_avg = sum(gpu_temps) / len(gpu_temps)
                    s = _fan_config.interpolate_fan(
                        profile.gpu_points, int(_math.floor(gpu_avg + 0.5))
                    )
                    target = max(target, s) if target is not None else s

                if target is not None:
                    current = last_written_pct if last_written_pct is not None else target
                    next_pct, ramp_down_since = next_fan_percent(
                        target, current, ramp_down_since, now,
                        cfg.ramp_up_pct, cfg.ramp_down_pct, cfg.ramp_down_delay,
                    )

                    delta = abs(next_pct - (last_written_pct if last_written_pct is not None else 0.0))
                    if last_written_pct is None or delta >= cfg.write_min_delta_pct:
                        pwm = max(0, min(int(next_pct * 255.0 / 100.0), 255))
                        _fan_logger.info(
                            "cpu=%.0f°C gpu=%s target=%.0f%% cur=%.0f%% → next=%.0f%% delta=%.1f → pwm=%d WRITE",
                            cpu_avg, f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A",
                            target, current, next_pct, delta, pwm,
                        )
                        try:
                            _daemon_client.request_fan_pwm(pwm)
                        except Exception:
                            pass
                        last_written_pct = next_pct
                    else:
                        if target < current and next_pct == current:
                            _fan_logger.info(
                                "cpu=%.0f°C gpu=%s target=%.0f%% cur=%.0f%% → next=%.0f%% (ramp-down delay %.0fs)",
                                cpu_avg, f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A",
                                target, current, next_pct, cfg.ramp_down_delay,
                            )
                        else:
                            _fan_logger.info(
                                "cpu=%.0f°C gpu=%s target=%.0f%% cur=%.0f%% → next=%.0f%% delta=%.1f (skip, <%.0f)",
                                cpu_avg, f"{gpu_avg:.0f}°C" if gpu_avg is not None else "N/A",
                                target, current, next_pct, delta, cfg.write_min_delta_pct,
                            )

                next_control = now + CONTROL_INTERVAL

            _time.sleep(POLL_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="fan-control")
    t.start()
