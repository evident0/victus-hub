"""Unit tests for the Omen-style custom fan controller."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from victus_hub.backend.fan_config import interpolate_fan, smart_cpu_points, smart_gpu_points
from victus_hub.backend.types import FanConfig, FanPoint, FanProfileConfig
from victus_hub.services.fan_control import (
    EWMA_LAMBDA_DECREASE,
    EWMA_LAMBDA_INCREASE,
    FanController,
    FanIO,
    LoopState,
    SMART_EWMA_LAMBDA_DECREASE,
    SMART_EWMA_LAMBDA_INCREASE,
    _pct_to_pwm,
    _profile_idx,
    compute_ewma,
    hysteretic_curve_target,
    update_curve_target,
    update_overheat,
)


def make_io(**overrides) -> FanIO:
    values = dict(
        read_temps=lambda: (50.0, None),
        get_profile=lambda: 1,
        load_config=lambda: FanConfig(
            profiles=[linear_profile(), linear_profile(), linear_profile()],
            custom_enabled=True,
        ),
        request_auto=Mock(),
        request_manual=Mock(),
        request_pwm=Mock(),
        read_pwm_pct=lambda: 20.0,
        is_suspended=lambda: False,
    )
    values.update(overrides)
    return FanIO(**values)


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

    def test_smart_lambda_on_increase(self):
        self.assertEqual(
            compute_ewma(50.0, 60.0, SMART_EWMA_LAMBDA_INCREASE, SMART_EWMA_LAMBDA_DECREASE),
            57.0,
        )

    def test_smart_lambda_on_decrease(self):
        self.assertEqual(
            compute_ewma(50.0, 40.0, SMART_EWMA_LAMBDA_INCREASE, SMART_EWMA_LAMBDA_DECREASE),
            49.5,
        )


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
        write = Mock()
        controller = FanController(make_io(request_pwm=write))
        controller._st.on_enter_custom(20.0)
        controller._control_tick(low_profile(40), 50.0, None, 0.0)
        controller._control_tick(low_profile(40), 50.0, None, 1.0)
        write.assert_called_once_with(102)
        self.assertEqual(controller._st.last_written_pct, 40.0)
        self.assertFalse(controller._st.force_write)

    def test_failed_write_is_retried(self):
        write = Mock(side_effect=RuntimeError("daemon unavailable"))
        controller = FanController(make_io(request_pwm=write))
        controller._st.on_enter_custom(20.0)
        controller._control_tick(low_profile(40), 50.0, None, 0.0)
        controller._control_tick(low_profile(40), 50.0, None, 1.0)
        self.assertEqual(write.call_count, 2)
        self.assertEqual(controller._st.last_written_pct, 20.0)
        self.assertTrue(controller._st.force_write)

    def test_small_target_change_is_suppressed(self):
        write = Mock()
        controller = FanController(make_io(request_pwm=write))
        controller._st.last_written_pct = 40.0
        controller._control_tick(low_profile(42), 50.0, None, 0.0)
        write.assert_not_called()
        self.assertEqual(controller._st.last_written_pct, 40.0)

    def test_target_change_above_threshold_is_written(self):
        write = Mock()
        controller = FanController(make_io(request_pwm=write))
        controller._st.last_written_pct = 40.0
        controller._control_tick(low_profile(43), 50.0, None, 0.0)
        write.assert_called_once_with(109)
        self.assertEqual(controller._st.last_written_pct, 43.0)


class TestPollSkipsTempsOutsideCustom(unittest.TestCase):
    def test_auto_preset_does_not_read_temperatures(self):
        read_temps = Mock(return_value=(90.0, 90.0))
        config = FanConfig(
            profiles=[linear_profile(), linear_profile(), linear_profile()],
            custom_enabled=False,
            manual_preset="auto",
        )
        controller = FanController(make_io(load_config=lambda: config, read_temps=read_temps))
        controller._poll_once()
        read_temps.assert_not_called()

    def test_max_preset_does_not_read_temperatures(self):
        read_temps = Mock(return_value=(90.0, 90.0))
        config = FanConfig(
            profiles=[linear_profile(), linear_profile(), linear_profile()],
            custom_enabled=False,
            manual_preset="max",
        )
        controller = FanController(make_io(load_config=lambda: config, read_temps=read_temps))
        controller._poll_once()
        read_temps.assert_not_called()


class TestCurveResponse(unittest.TestCase):
    def _poll_once(self, *, response="smooth", smart=False, min_fan_change_pct=2.0):
        config = FanConfig(
            profiles=[linear_profile(), linear_profile(), linear_profile()],
            custom_enabled=True,
            smart_enabled=smart,
            curve_response=response,
            min_fan_change_pct=min_fan_change_pct,
        )
        controller = FanController(make_io(load_config=lambda: config))
        with patch.object(controller, "_control_tick") as control_tick:
            controller._poll_once()
        return control_tick.call_args.args

    def test_aggressive_response_uses_smart_lambdas(self):
        args = self._poll_once(response="aggressive")
        self.assertEqual(args[5], SMART_EWMA_LAMBDA_INCREASE)
        self.assertEqual(args[6], SMART_EWMA_LAMBDA_DECREASE)

    def test_smart_mode_stays_aggressive(self):
        args = self._poll_once(smart=True)
        self.assertEqual(args[5], SMART_EWMA_LAMBDA_INCREASE)
        self.assertEqual(args[6], SMART_EWMA_LAMBDA_DECREASE)

    def test_smart_mode_uses_fixed_min_fan_change(self):
        args = self._poll_once(smart=True, min_fan_change_pct=4.5)
        self.assertEqual(args[4], 2.0)

    def test_custom_mode_uses_configured_min_fan_change(self):
        args = self._poll_once(min_fan_change_pct=4.5)
        self.assertEqual(args[4], 4.5)

    def test_smooth_response_uses_default_lambdas(self):
        args = self._poll_once()
        self.assertEqual(args[5], EWMA_LAMBDA_INCREASE)
        self.assertEqual(args[6], EWMA_LAMBDA_DECREASE)


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


class TestSmartCurve(unittest.TestCase):
    def test_cpu_idle_is_off(self):
        self.assertEqual(interpolate_fan(smart_cpu_points(), 58), 0)

    def test_cpu_spinup_skips_stall_band(self):
        self.assertEqual(interpolate_fan(smart_cpu_points(), 60), 32)

    def test_cpu_gaming_cruise(self):
        self.assertEqual(interpolate_fan(smart_cpu_points(), 80), 62)

    def test_gpu_spinup(self):
        self.assertEqual(interpolate_fan(smart_gpu_points(), 54), 32)

    def test_gpu_gaming_cruise(self):
        self.assertEqual(interpolate_fan(smart_gpu_points(), 75), 68)

    def test_first_sample_at_cruise_uses_smart_table(self):
        state = LoopState()
        target = update_curve_target(
            state,
            FanProfileConfig(
                cpu_points=smart_cpu_points(),
                gpu_points=smart_gpu_points(),
            ),
            cpu_sample=80.0,
            gpu_sample=None,
            now=0.0,
            lambda_increase=SMART_EWMA_LAMBDA_INCREASE,
            lambda_decrease=SMART_EWMA_LAMBDA_DECREASE,
        )
        self.assertEqual(target, 62.0)


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
