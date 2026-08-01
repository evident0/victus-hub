"""Omen-style custom fan-control background loop.

CPU and GPU temperatures are smoothed with the custom-curve EWMA, mapped
through independent hysteretic curves, and merged into one platform PWM
target. Idle-mode handling is intentionally not implemented.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

from victus_hub import api
from victus_hub.backend import daemon_client as _daemon_client
from victus_hub.backend import fan_config as _fan_config
from victus_hub.backend.sysfs_read import read_hp_pwm_pct
from victus_hub.backend.types import FanPoint, FanProfileConfig

POLL_INTERVAL = 1.0

EWMA_LAMBDA_INCREASE = 0.1
EWMA_LAMBDA_DECREASE = 0.1
CURVE_HYSTERESIS_C = 5.0

OVERHEAT_THRESHOLD_C = 90.0
OVERHEAT_RELEASE_C = 85.0
OVERHEAT_MIN_FAN_PCT = 50.0
OVERHEAT_COOLDOWN_S = 10.0

# Pre-suspend cleanup puts the hardware back in automatic mode. Dropping
# ownership here makes custom mode reassert manual PWM after resume.
_suspend = threading.Event()
_fan_logger = logging.getLogger("fan-control")


def set_suspended(state: bool) -> None:
    """Pause or resume the fan-control loop without cancelling its thread."""
    if state:
        _suspend.set()
    else:
        _suspend.clear()


# -- Pure helpers ------------------------------------------------------------


def compute_ewma(
    previous: float | None,
    sample: float,
    lambda_increase: float = EWMA_LAMBDA_INCREASE,
    lambda_decrease: float = EWMA_LAMBDA_DECREASE,
) -> float:
    """Update one Omen custom-curve EWMA sample."""
    sample = float(sample)
    if previous is None:
        return sample
    smoothing = lambda_increase if sample > previous else lambda_decrease
    return previous + smoothing * (sample - previous)


def _rounded_temp(temp: float) -> int:
    return int(math.floor(temp + 0.5))


def hysteretic_curve_target(
    points: list[FanPoint],
    ema: float,
    previous_ema: float | None,
    previous_demand: float | None,
    hysteresis_c: float = CURVE_HYSTERESIS_C,
) -> float:
    """Map one EMA to a curve with a cooling-side temperature deadband.

    Heating follows the normal curve. Cooling uses a curve shifted by the
    hysteresis width and cannot increase the previous demand, so the target is
    held until temperature has fallen far enough to cross the cooling bound.
    """
    normal = float(_fan_config.interpolate_fan(points, _rounded_temp(ema)))
    if previous_ema is None or previous_demand is None:
        return normal
    if ema > previous_ema:
        return max(previous_demand, normal)
    if ema < previous_ema:
        cooling = float(
            _fan_config.interpolate_fan(
                points,
                _rounded_temp(ema + hysteresis_c),
            )
        )
        return min(previous_demand, cooling)
    return previous_demand


def _profile_signature(profile_idx: int, profile: FanProfileConfig) -> tuple:
    return (
        profile_idx,
        tuple((point.temp, point.speed) for point in profile.cpu_points),
        tuple((point.temp, point.speed) for point in profile.gpu_points),
    )


def _profile_idx(raw: int | None) -> int:
    return max(0, min(raw if raw is not None else 1, 2))


def _pct_to_pwm(pct: float) -> int:
    return max(0, min(int(pct * 255.0 / 100.0), 255))


def _update_sensor_overheat(previous: bool, temp: float | None) -> bool:
    if temp is None:
        return previous
    if not previous:
        return temp >= OVERHEAT_THRESHOLD_C
    return temp > OVERHEAT_RELEASE_C


@dataclass
class LoopState:
    """Mutable ownership and algorithm state for one controller thread."""

    last_written_pct: float | None = None
    was_custom: bool = False
    force_write: bool = False

    ema_cpu: float | None = None
    ema_gpu: float | None = None
    cpu_demand: float | None = None
    gpu_demand: float | None = None
    curve_signature: tuple | None = None

    cpu_overheating: bool = False
    gpu_overheating: bool = False
    overheat_active: bool = False
    overheat_clear_at: float | None = None

    def reset_algorithm(self) -> None:
        self.ema_cpu = None
        self.ema_gpu = None
        self.cpu_demand = None
        self.gpu_demand = None
        self.curve_signature = None
        self.cpu_overheating = False
        self.gpu_overheating = False
        self.overheat_active = False
        self.overheat_clear_at = None

    def reset_curve_hysteresis(self, signature: tuple) -> None:
        self.cpu_demand = None
        self.gpu_demand = None
        self.curve_signature = signature

    def on_suspend(self) -> None:
        self.last_written_pct = None
        self.was_custom = False
        self.force_write = False
        self.reset_algorithm()

    def on_manual_preset(self) -> None:
        self.last_written_pct = None
        self.was_custom = False
        self.force_write = False
        self.reset_algorithm()

    def on_leave_custom(self) -> None:
        self.last_written_pct = None
        self.was_custom = False
        self.force_write = False
        self.reset_algorithm()

    def on_enter_custom(self, seeded_pct: float | None) -> None:
        self.reset_algorithm()
        self.last_written_pct = seeded_pct
        self.force_write = True


def update_overheat(state: LoopState, now: float) -> bool:
    """Update the 90/85 C trip state and 10-second cooldown."""
    state.cpu_overheating = _update_sensor_overheat(
        state.cpu_overheating, state.ema_cpu,
    )
    state.gpu_overheating = _update_sensor_overheat(
        state.gpu_overheating, state.ema_gpu,
    )
    any_overheating = state.cpu_overheating or state.gpu_overheating

    if any_overheating:
        state.overheat_clear_at = None
        state.overheat_active = True
    elif state.overheat_active:
        if state.overheat_clear_at is None:
            state.overheat_clear_at = now + OVERHEAT_COOLDOWN_S
        if now >= state.overheat_clear_at:
            state.overheat_clear_at = None
            state.overheat_active = False

    return state.overheat_active


def update_curve_target(
    state: LoopState,
    profile: FanProfileConfig,
    cpu_sample: float | None,
    gpu_sample: float | None,
    now: float,
) -> float | None:
    """Update EWMAs and return the merged hysteretic target with safety floor."""
    previous_cpu = state.ema_cpu
    previous_gpu = state.ema_gpu

    if cpu_sample is not None:
        state.ema_cpu = compute_ewma(state.ema_cpu, cpu_sample)
        state.cpu_demand = hysteretic_curve_target(
            profile.cpu_points,
            state.ema_cpu,
            previous_cpu,
            state.cpu_demand,
        )
    if gpu_sample is not None:
        state.ema_gpu = compute_ewma(state.ema_gpu, gpu_sample)
        state.gpu_demand = hysteretic_curve_target(
            profile.gpu_points,
            state.ema_gpu,
            previous_gpu,
            state.gpu_demand,
        )

    demands = [
        demand
        for demand in (state.cpu_demand, state.gpu_demand)
        if demand is not None
    ]
    if not demands:
        return None

    target = max(demands)
    if update_overheat(state, now):
        target = max(target, OVERHEAT_MIN_FAN_PCT)
    return target


def _temp_text(temp: float | None) -> str:
    return f"{temp:.1f}C" if temp is not None else "N/A"


# -- Controller --------------------------------------------------------------


class FanController:
    """Poll sensors and drive custom fan PWM through the privileged daemon."""

    def __init__(self) -> None:
        self._st = LoopState()
        self._logged_init = False

    def run_forever(self) -> None:
        while True:
            self._poll_once()
            time.sleep(POLL_INTERVAL)

    def _poll_once(self) -> None:
        state = self._st
        if _suspend.is_set():
            state.on_suspend()
            return

        try:
            snapshot = api.read_sensors()
        except Exception:
            return

        try:
            raw_profile = api.get_current_profile()
        except Exception:
            raw_profile = None
        profile_idx = _profile_idx(raw_profile)
        if not self._logged_init:
            _fan_logger.info(
                "fan-control init: api profile=%s, idx=%d",
                raw_profile,
                profile_idx,
            )
            self._logged_init = True

        config = _fan_config.load()
        profile = config.profiles[profile_idx]

        if config.manual_preset is not None:
            state.on_manual_preset()
            return

        if not config.custom_enabled:
            if state.was_custom:
                try:
                    _daemon_client.request_fan_auto()
                except Exception:
                    pass
                state.on_leave_custom()
            return

        if not state.was_custom:
            state.on_enter_custom(read_hp_pwm_pct())
            _fan_logger.info(
                "fan-control: entering custom (fan-manual + force first write)",
            )
            try:
                _daemon_client.request_fan_manual()
            except Exception:
                pass
        state.was_custom = True

        signature = _profile_signature(profile_idx, profile)
        if signature != state.curve_signature:
            state.reset_curve_hysteresis(signature)

        self._control_tick(
            profile,
            snapshot.cpu_temp_c,
            snapshot.gpu_temp_c,
            time.monotonic(),
            config.min_fan_change_pct,
        )

    def _control_tick(
        self,
        profile: FanProfileConfig,
        cpu_sample: float | None,
        gpu_sample: float | None,
        now: float,
        min_fan_change_pct: float = 2.0,
    ) -> None:
        state = self._st
        target = update_curve_target(
            state,
            profile,
            cpu_sample,
            gpu_sample,
            now,
        )
        if target is None:
            return

        delta = abs(target - state.last_written_pct) if state.last_written_pct is not None else None
        if (
            not state.force_write
            and delta is not None
            and delta <= min_fan_change_pct
        ):
            _fan_logger.info(
                "cpu=%s gpu=%s target=%.0f%% overheat=%s delta=%.1f%% (<=%.1f%%, unchanged)",
                _temp_text(state.ema_cpu),
                _temp_text(state.ema_gpu),
                target,
                state.overheat_active,
                delta,
                min_fan_change_pct,
            )
            return

        pwm = _pct_to_pwm(target)
        reason = " (enter custom)" if state.force_write else ""
        _fan_logger.info(
            "cpu=%s gpu=%s target=%.0f%% overheat=%s -> pwm=%d WRITE%s",
            _temp_text(state.ema_cpu),
            _temp_text(state.ema_gpu),
            target,
            state.overheat_active,
            pwm,
            reason,
        )
        try:
            _daemon_client.request_fan_pwm(pwm)
        except Exception as error:
            _fan_logger.warning("fan PWM write failed: %s", error)
            return

        state.last_written_pct = target
        state.force_write = False


def start_fan_control() -> None:
    """Start the custom fan-control background thread."""
    threading.Thread(
        target=FanController().run_forever,
        daemon=True,
        name="fan-control",
    ).start()
