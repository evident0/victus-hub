"""Unit tests for fan_control pure helpers (ramp + curve targeting).

Run: python -m unittest tests.test_fan_control -v
"""

from __future__ import annotations

import collections
import unittest

from victus_hub.backend.types import FanPoint, FanProfileConfig
from victus_hub.services.fan_control import (
    compute_ema,
    next_fan_percent,
    _curve_target,
    _pct_to_pwm,
    _profile_idx,
    _should_write,
)


class TestNextFanPercent(unittest.TestCase):
    def test_ramp_up(self):
        nxt, since = next_fan_percent(
            target=80, current=40, ramp_down_since=None, now=0.0,
            ramp_up=30, ramp_down=15, ramp_down_delay=10,
        )
        self.assertEqual(nxt, 70)
        self.assertIsNone(since)

    def test_ramp_up_caps_at_target(self):
        nxt, since = next_fan_percent(
            target=50, current=40, ramp_down_since=None, now=0.0,
            ramp_up=30, ramp_down=15, ramp_down_delay=10,
        )
        self.assertEqual(nxt, 50)
        self.assertIsNone(since)

    def test_ramp_down_delay_holds(self):
        nxt, since = next_fan_percent(
            target=20, current=50, ramp_down_since=None, now=100.0,
            ramp_up=30, ramp_down=15, ramp_down_delay=10,
        )
        self.assertEqual(nxt, 50)
        self.assertEqual(since, 100.0)

        nxt2, since2 = next_fan_percent(
            target=20, current=50, ramp_down_since=since, now=105.0,
            ramp_up=30, ramp_down=15, ramp_down_delay=10,
        )
        self.assertEqual(nxt2, 50)
        self.assertEqual(since2, 100.0)

    def test_ramp_down_after_delay(self):
        nxt, since = next_fan_percent(
            target=20, current=50, ramp_down_since=100.0, now=110.0,
            ramp_up=30, ramp_down=15, ramp_down_delay=10,
        )
        self.assertEqual(nxt, 35)
        self.assertEqual(since, 100.0)

    def test_ramp_down_immediate_when_delay_zero(self):
        nxt, since = next_fan_percent(
            target=20, current=50, ramp_down_since=None, now=0.0,
            ramp_up=30, ramp_down=15, ramp_down_delay=0.0,
        )
        self.assertEqual(nxt, 35)
        self.assertEqual(since, 0.0)

    def test_equal_clears_ramp(self):
        nxt, since = next_fan_percent(
            target=40, current=40, ramp_down_since=50.0, now=100.0,
            ramp_up=30, ramp_down=15, ramp_down_delay=10,
        )
        self.assertEqual(nxt, 40)
        self.assertIsNone(since)


class TestLoopHelpers(unittest.TestCase):
    def test_profile_idx(self):
        self.assertEqual(_profile_idx(None), 1)
        self.assertEqual(_profile_idx(0), 0)
        self.assertEqual(_profile_idx(2), 2)
        self.assertEqual(_profile_idx(9), 2)
        self.assertEqual(_profile_idx(-1), 0)

    def test_pct_to_pwm(self):
        self.assertEqual(_pct_to_pwm(0), 0)
        self.assertEqual(_pct_to_pwm(100), 255)
        self.assertEqual(_pct_to_pwm(50), 127)

    def test_should_write(self):
        self.assertTrue(_should_write(True, 50.0, 50.0, 5.0))
        self.assertTrue(_should_write(False, None, 40.0, 5.0))
        self.assertTrue(_should_write(False, 40.0, 50.0, 5.0))
        self.assertFalse(_should_write(False, 40.0, 43.0, 5.0))

    def test_curve_target_max_of_cpu_gpu(self):
        profile = FanProfileConfig(
            cpu_points=[FanPoint(30, 0), FanPoint(100, 100)],
            gpu_points=[FanPoint(30, 0), FanPoint(90, 100)],
        )
        hist = collections.deque([(65.0, 60.0)], maxlen=15)
        cpu_avg, gpu_avg, target = _curve_target(hist, profile)
        self.assertEqual(cpu_avg, 65.0)
        self.assertEqual(gpu_avg, 60.0)
        self.assertIsNotNone(target)
        self.assertAlmostEqual(target, 50.0, places=0)

    def test_curve_target_ema_weights_recent_temps(self):
        profile = FanProfileConfig(
            cpu_points=[FanPoint(30, 0), FanPoint(100, 100)],
            gpu_points=[FanPoint(30, 0), FanPoint(90, 100)],
        )
        temps = [(float(t), None) for t in (40, 45, 50, 55, 70)]
        hist = collections.deque(temps, maxlen=5)
        cpu_ema, _, _ = _curve_target(hist, profile)
        simple_mean = sum(t for t, _ in temps) / len(temps)
        self.assertIsNotNone(cpu_ema)
        self.assertGreater(cpu_ema, simple_mean)


class TestComputeEma(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(compute_ema([], 8))

    def test_single_sample(self):
        self.assertEqual(compute_ema([42.0], 8), 42.0)

    def test_constant_input_returns_constant(self):
        self.assertEqual(compute_ema([50.0] * 20, 10), 50.0)

    def test_period_one_is_latest_sample(self):
        self.assertEqual(compute_ema([10.0, 20.0, 30.0, 40.0], 1), 40.0)

    def test_step_input_converges_below_target(self):
        samples = [0.0] * 50 + [100.0] * 50
        result = compute_ema(samples, 5)
        self.assertIsNotNone(result)
        self.assertGreater(result, 95.0)
        self.assertLess(result, 100.0)

    def test_order_dependent(self):
        ascending = [10.0, 20.0, 30.0, 40.0, 50.0]
        descending = list(reversed(ascending))
        ema_up = compute_ema(ascending, 5)
        ema_down = compute_ema(descending, 5)
        self.assertIsNotNone(ema_up)
        self.assertIsNotNone(ema_down)
        self.assertGreater(abs(ema_up - ema_down), 1.0)
        self.assertGreater(ema_up, ema_down)


if __name__ == "__main__":
    unittest.main()
