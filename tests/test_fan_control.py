"""Unit tests for fan_control pure helpers (ramp + P0 floor).

Run: python -m unittest tests.test_fan_control -v
"""

from __future__ import annotations

import collections
import unittest

from hp_helper.backend.types import FanPoint, FanProfileConfig
from hp_helper.services.fan_control import (
    P0_ENGAGE_S,
    P0_RELEASE_S,
    P0FloorState,
    apply_p0_floor,
    is_gpu_p0,
    next_fan_percent,
    update_p0_debounce,
    _curve_target,
    _pct_to_pwm,
    _profile_idx,
    _should_write,
)


class TestIsGpuP0(unittest.TestCase):
    def test_p0(self):
        self.assertTrue(is_gpu_p0("P0"))
        self.assertTrue(is_gpu_p0(" p0 "))
        self.assertTrue(is_gpu_p0("p0"))

    def test_not_p0(self):
        self.assertFalse(is_gpu_p0(None))
        self.assertFalse(is_gpu_p0("P2"))
        self.assertFalse(is_gpu_p0(""))
        self.assertFalse(is_gpu_p0("P8"))


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


class TestP0Debounce(unittest.TestCase):
    def test_no_engage_before_engage_s(self):
        st = P0FloorState()
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=True, now=0.0, last_written_pct=40.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(st.floor_active)
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=True, now=P0_ENGAGE_S - 0.1,
            last_written_pct=40.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(st.floor_active)

    def test_engage_after_continuous_p0(self):
        st = P0FloorState()
        st, _ = update_p0_debounce(
            st, enabled=True, is_p0=True, now=0.0, last_written_pct=40.0,
        )
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=True, now=P0_ENGAGE_S, last_written_pct=40.0,
        )
        self.assertEqual(ev, "engage")
        self.assertTrue(st.floor_active)
        self.assertEqual(st.floor_pwm_min, 40.0)

    def test_brief_p0_does_not_engage(self):
        st = P0FloorState()
        st, _ = update_p0_debounce(
            st, enabled=True, is_p0=True, now=0.0, last_written_pct=40.0,
        )
        # drop out of P0 before engage
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=False, now=5.0, last_written_pct=40.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(st.floor_active)
        self.assertIsNone(st.p0_since)

    def test_p0_gap_resets_engage_timer(self):
        st = P0FloorState()
        st, _ = update_p0_debounce(
            st, enabled=True, is_p0=True, now=0.0, last_written_pct=40.0,
        )
        st, _ = update_p0_debounce(
            st, enabled=True, is_p0=False, now=5.0, last_written_pct=40.0,
        )
        st, _ = update_p0_debounce(
            st, enabled=True, is_p0=True, now=6.0, last_written_pct=40.0,
        )
        # only 4s of continuous P0 from t=6
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=True, now=6.0 + P0_ENGAGE_S - 1.0,
            last_written_pct=40.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(st.floor_active)
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=True, now=6.0 + P0_ENGAGE_S,
            last_written_pct=40.0,
        )
        self.assertEqual(ev, "engage")

    def test_release_only_after_release_s_non_p0(self):
        st = P0FloorState(floor_active=True, floor_pwm_min=50.0, p0_since=0.0)
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=False, now=100.0, last_written_pct=50.0,
        )
        self.assertIsNone(ev)
        self.assertTrue(st.floor_active)
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=False, now=100.0 + P0_RELEASE_S - 0.1,
            last_written_pct=50.0,
        )
        self.assertIsNone(ev)
        self.assertTrue(st.floor_active)
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=False, now=100.0 + P0_RELEASE_S,
            last_written_pct=50.0,
        )
        self.assertEqual(ev, "release")
        self.assertFalse(st.floor_active)

    def test_brief_game_dip_does_not_release(self):
        st = P0FloorState(floor_active=True, floor_pwm_min=50.0, p0_since=0.0)
        st, _ = update_p0_debounce(
            st, enabled=True, is_p0=False, now=100.0, last_written_pct=50.0,
        )
        # back to P0 before release window
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=True, now=110.0, last_written_pct=50.0,
        )
        self.assertIsNone(ev)
        self.assertTrue(st.floor_active)
        self.assertIsNone(st.non_p0_since)

    def test_disabled_clears_immediately(self):
        st = P0FloorState(floor_active=True, floor_pwm_min=50.0, p0_since=1.0)
        st, ev = update_p0_debounce(
            st, enabled=False, is_p0=True, now=50.0, last_written_pct=50.0,
        )
        self.assertEqual(ev, "disabled")
        self.assertFalse(st.floor_active)
        self.assertIsNone(st.p0_since)

    def test_engage_seeds_from_last_written(self):
        st = P0FloorState()
        st, _ = update_p0_debounce(
            st, enabled=True, is_p0=True, now=0.0, last_written_pct=62.0,
        )
        st, ev = update_p0_debounce(
            st, enabled=True, is_p0=True, now=P0_ENGAGE_S, last_written_pct=62.0,
        )
        self.assertEqual(ev, "engage")
        self.assertEqual(st.floor_pwm_min, 62.0)


class TestApplyP0Floor(unittest.TestCase):
    def test_inactive_passthrough(self):
        st = P0FloorState()
        nxt, st2, ev = apply_p0_floor(
            st, enabled=True, curve_pct=40.0, last_written_pct=40.0,
            min_pct=80.0, ramp_up_pct=30.0,
        )
        self.assertEqual(nxt, 40.0)
        self.assertIsNone(ev)

    def test_disabled_passthrough(self):
        st = P0FloorState(floor_active=True, floor_pwm_min=40.0)
        nxt, _, ev = apply_p0_floor(
            st, enabled=False, curve_pct=40.0, last_written_pct=40.0,
            min_pct=80.0, ramp_up_pct=30.0,
        )
        self.assertEqual(nxt, 40.0)
        self.assertIsNone(ev)

    def test_raise_steps_toward_min(self):
        st = P0FloorState(floor_active=True, floor_pwm_min=40.0)
        nxt, st2, ev = apply_p0_floor(
            st, enabled=True, curve_pct=40.0, last_written_pct=40.0,
            min_pct=80.0, ramp_up_pct=30.0,
        )
        self.assertEqual(ev, "raise")
        self.assertEqual(nxt, 70.0)
        self.assertEqual(st2.floor_pwm_min, 70.0)

        nxt2, st3, ev2 = apply_p0_floor(
            st2, enabled=True, curve_pct=40.0, last_written_pct=70.0,
            min_pct=80.0, ramp_up_pct=30.0,
        )
        self.assertEqual(ev2, "raise")
        self.assertEqual(nxt2, 80.0)
        self.assertEqual(st3.floor_pwm_min, 80.0)

    def test_curve_above_floor_wins(self):
        st = P0FloorState(floor_active=True, floor_pwm_min=80.0)
        nxt, _, ev = apply_p0_floor(
            st, enabled=True, curve_pct=95.0, last_written_pct=80.0,
            min_pct=80.0, ramp_up_pct=30.0,
        )
        self.assertEqual(nxt, 95.0)
        self.assertIsNone(ev)  # floor did not advance past 80

    def test_hold_at_min_blocks_curve_drop(self):
        st = P0FloorState(floor_active=True, floor_pwm_min=80.0)
        nxt, st2, ev = apply_p0_floor(
            st, enabled=True, curve_pct=30.0, last_written_pct=80.0,
            min_pct=80.0, ramp_up_pct=30.0,
        )
        self.assertEqual(nxt, 80.0)
        self.assertEqual(st2.floor_pwm_min, 80.0)
        self.assertIsNone(ev)


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
        # Linear 30→0%, 100→100% CPU; 30→0%, 90→100% GPU
        profile = FanProfileConfig(
            cpu_points=[FanPoint(30, 0), FanPoint(100, 100)],
            gpu_points=[FanPoint(30, 0), FanPoint(90, 100)],
        )
        hist = collections.deque([(65.0, 60.0)], maxlen=15)
        cpu_avg, gpu_avg, target = _curve_target(hist, profile)
        self.assertEqual(cpu_avg, 65.0)
        self.assertEqual(gpu_avg, 60.0)
        self.assertIsNotNone(target)
        # CPU at 65: (65-30)/(100-30)*100 ≈ 50; GPU at 60: (60-30)/(90-30)*100 = 50
        self.assertAlmostEqual(target, 50.0, places=0)


if __name__ == "__main__":
    unittest.main()
