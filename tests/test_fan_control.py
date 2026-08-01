"""Unit tests for the Omen-style custom fan controller."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from victus_hub.backend.types import FanPoint, FanProfileConfig
from victus_hub.services.fan_control import (
    FanController,
    LoopState,
    _pct_to_pwm,
    _profile_idx,
    compute_ewma,
    hysteretic_curve_target,
    update_curve_target,
    update_overheat,
)


def linear_profile() -> FanProfileConfig:
    return FanProfileConfig(
        cpu_points=[FanPoint(30, 0), FanPoint(100, 100)],
        gpu_points=[FanPoint(30, 0), FanPoint(90, 100)],
    )


def low_profile(speed: int = 20) -> FanProfileConfig:
    return FanProfileConfig(
        cpu_points=[FanPoint(30, speed), FanPoint(100, speed)],
        gpu_points=[FanPoint(30, speed), FanPoint(90, speed)],
    )


class TestEwma(unittest.TestCase):
    def test_first_sample_initializes_state(self):
        self.assertEqual(compute_ewma(None, 52.0), 52.0)

    def test_custom_curve_lambda_on_increase(self):
        self.assertEqual(compute_ewma(50.0, 60.0), 51.0)

    def test_custom_curve_lambda_on_decrease(self):
        self.assertEqual(compute_ewma(50.0, 40.0), 49.0)

    def test_persistent_updates_use_previous_result(self):
        ema = compute_ewma(None, 50.0)
        ema = compute_ewma(ema, 60.0)
        ema = compute_ewma(ema, 41.0)
        self.assertEqual(ema, 50.0)


class TestCurveHysteresis(unittest.TestCase):
    def setUp(self):
        self.points = [FanPoint(30, 0), FanPoint(100, 100)]

    def test_first_value_uses_normal_curve(self):
        demand = hysteretic_curve_target(self.points, 65.0, None, None)
        self.assertEqual(demand, 50.0)

    def test_heating_follows_curve_immediately(self):
        demand = hysteretic_curve_target(self.points, 72.0, 65.0, 50.0)
        self.assertEqual(demand, 60.0)

    def test_cooling_holds_within_five_degrees(self):
        demand = hysteretic_curve_target(self.points, 68.0, 70.0, 57.0)
        self.assertEqual(demand, 57.0)

    def test_cooling_releases_after_deadband(self):
        demand = hysteretic_curve_target(self.points, 64.0, 70.0, 57.0)
        self.assertEqual(demand, 55.0)

    def test_flat_temperature_holds_previous_demand(self):
        demand = hysteretic_curve_target(self.points, 70.0, 70.0, 57.0)
        self.assertEqual(demand, 57.0)

    def test_cpu_and_gpu_demands_are_merged_by_maximum(self):
        state = LoopState()
        target = update_curve_target(
            state,
            linear_profile(),
            cpu_sample=65.0,
            gpu_sample=60.0,
            now=0.0,
        )
        self.assertEqual(target, 50.0)
        self.assertEqual(state.cpu_demand, 50.0)
        self.assertEqual(state.gpu_demand, 50.0)


class TestOverheatSafety(unittest.TestCase):
    def test_cpu_trip_applies_fifty_percent_floor(self):
        state = LoopState()
        target = update_curve_target(
            state,
            low_profile(),
            cpu_sample=92.0,
            gpu_sample=None,
            now=0.0,
        )
        self.assertEqual(target, 50.0)
        self.assertTrue(state.cpu_overheating)
        self.assertTrue(state.overheat_active)

    def test_gpu_trip_is_independent(self):
        state = LoopState()
        target = update_curve_target(
            state,
            low_profile(),
            cpu_sample=50.0,
            gpu_sample=92.0,
            now=0.0,
        )
        self.assertEqual(target, 50.0)
        self.assertFalse(state.cpu_overheating)
        self.assertTrue(state.gpu_overheating)

    def test_floor_does_not_lower_a_higher_target(self):
        state = LoopState(ema_cpu=92.0, cpu_demand=75.0)
        self.assertTrue(update_overheat(state, now=0.0))
        target = update_curve_target(
            state,
            FanProfileConfig(
                cpu_points=[FanPoint(30, 75), FanPoint(100, 75)],
                gpu_points=[FanPoint(30, 0), FanPoint(90, 0)],
            ),
            cpu_sample=92.0,
            gpu_sample=None,
            now=1.0,
        )
        self.assertEqual(target, 75.0)

    def test_release_requires_eighty_five_or_lower(self):
        state = LoopState(ema_cpu=92.0)
        self.assertTrue(update_overheat(state, now=0.0))
        state.ema_cpu = 88.0
        self.assertTrue(update_overheat(state, now=1.0))
        self.assertTrue(state.cpu_overheating)
        state.ema_cpu = 85.0
        self.assertTrue(update_overheat(state, now=2.0))
        self.assertFalse(state.cpu_overheating)
        self.assertEqual(state.overheat_clear_at, 12.0)

    def test_cooldown_lasts_ten_seconds(self):
        state = LoopState(ema_cpu=92.0)
        update_overheat(state, now=0.0)
        state.ema_cpu = 80.0
        self.assertTrue(update_overheat(state, now=1.0))
        self.assertTrue(update_overheat(state, now=10.9))
        self.assertFalse(update_overheat(state, now=11.0))

    def test_retrip_cancels_pending_cooldown(self):
        state = LoopState(ema_cpu=92.0)
        update_overheat(state, now=0.0)
        state.ema_cpu = 80.0
        update_overheat(state, now=1.0)
        self.assertEqual(state.overheat_clear_at, 11.0)
        state.ema_cpu = 91.0
        self.assertTrue(update_overheat(state, now=5.0))
        self.assertIsNone(state.overheat_clear_at)


class TestControllerWrites(unittest.TestCase):
    def test_forced_first_write_then_unchanged_target_is_suppressed(self):
        controller = FanController()
        controller._st.on_enter_custom(20.0)
        with patch(
            "victus_hub.services.fan_control._daemon_client.request_fan_pwm",
        ) as write:
            controller._control_tick(low_profile(40), 50.0, None, 0.0)
            controller._control_tick(low_profile(40), 50.0, None, 1.0)
        write.assert_called_once_with(102)
        self.assertEqual(controller._st.last_written_pct, 40.0)
        self.assertFalse(controller._st.force_write)

    def test_failed_write_is_retried(self):
        controller = FanController()
        controller._st.on_enter_custom(20.0)
        with patch(
            "victus_hub.services.fan_control._daemon_client.request_fan_pwm",
            side_effect=RuntimeError("daemon unavailable"),
        ) as write:
            controller._control_tick(low_profile(40), 50.0, None, 0.0)
            controller._control_tick(low_profile(40), 50.0, None, 1.0)
        self.assertEqual(write.call_count, 2)
        self.assertEqual(controller._st.last_written_pct, 20.0)
        self.assertTrue(controller._st.force_write)


class TestLoopState(unittest.TestCase):
    def test_suspend_resets_algorithm_and_ownership(self):
        state = LoopState(
            last_written_pct=40.0,
            was_custom=True,
            force_write=True,
            ema_cpu=70.0,
            cpu_demand=50.0,
            cpu_overheating=True,
            overheat_active=True,
        )
        state.on_suspend()
        self.assertIsNone(state.last_written_pct)
        self.assertFalse(state.was_custom)
        self.assertFalse(state.force_write)
        self.assertIsNone(state.ema_cpu)
        self.assertIsNone(state.cpu_demand)
        self.assertFalse(state.overheat_active)

    def test_curve_change_resets_only_hysteresis(self):
        state = LoopState(
            ema_cpu=70.0,
            ema_gpu=60.0,
            cpu_demand=50.0,
            gpu_demand=40.0,
            overheat_active=True,
        )
        signature = (1, ((30, 0), (100, 100)), ((30, 0), (90, 100)))
        state.reset_curve_hysteresis(signature)
        self.assertEqual(state.ema_cpu, 70.0)
        self.assertEqual(state.ema_gpu, 60.0)
        self.assertIsNone(state.cpu_demand)
        self.assertIsNone(state.gpu_demand)
        self.assertTrue(state.overheat_active)
        self.assertEqual(state.curve_signature, signature)


class TestSmallHelpers(unittest.TestCase):
    def test_profile_idx(self):
        self.assertEqual(_profile_idx(None), 1)
        self.assertEqual(_profile_idx(0), 0)
        self.assertEqual(_profile_idx(2), 2)
        self.assertEqual(_profile_idx(9), 2)
        self.assertEqual(_profile_idx(-1), 0)

    def test_pct_to_pwm(self):
        self.assertEqual(_pct_to_pwm(0), 0)
        self.assertEqual(_pct_to_pwm(50), 127)
        self.assertEqual(_pct_to_pwm(100), 255)


if __name__ == "__main__":
    unittest.main()
