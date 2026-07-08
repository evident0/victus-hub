"""Unit tests for fan_control P0 floor + ramp helpers.

Run: python -m unittest tests.test_fan_control -v
"""

from __future__ import annotations

import unittest

from hp_helper.fan_control import (
    P0_ENGAGE_S,
    P0_RAISE_STEP_PCT,
    P0_RELEASE_S,
    P0_SETTLE_STEP_PCT,
    P0FloorState,
    apply_p0_floor,
    is_gpu_p0,
    max_fan_rpm,
    next_fan_percent,
    parse_rpm,
    update_p0_debounce,
)


class TestParseRpm(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_rpm("2700 RPM"), 2700)
        self.assertEqual(parse_rpm("4400"), 4400)

    def test_unavailable(self):
        self.assertIsNone(parse_rpm("N/A"))
        self.assertIsNone(parse_rpm("Unavailable"))
        self.assertIsNone(parse_rpm(""))

    def test_max_fan_rpm(self):
        self.assertEqual(max_fan_rpm("2700 RPM", "3000 RPM"), 3000)
        self.assertEqual(max_fan_rpm("N/A", "1800 RPM"), 1800)
        self.assertIsNone(max_fan_rpm("N/A", "Unavailable"))


class TestIsGpuP0(unittest.TestCase):
    def test_p0(self):
        self.assertTrue(is_gpu_p0("P0"))
        self.assertTrue(is_gpu_p0("p0"))
        self.assertTrue(is_gpu_p0(" P0 "))

    def test_not_p0(self):
        self.assertFalse(is_gpu_p0("P8"))
        self.assertFalse(is_gpu_p0("P2"))
        self.assertFalse(is_gpu_p0(None))
        self.assertFalse(is_gpu_p0(""))
        self.assertFalse(is_gpu_p0("N/A"))


class TestNextFanPercent(unittest.TestCase):
    def test_ramp_up(self):
        pct, since = next_fan_percent(80, 50, None, 0.0, 30, 15, 10)
        self.assertEqual(pct, 80)
        self.assertIsNone(since)

    def test_ramp_down_delay_holds(self):
        pct, since = next_fan_percent(20, 50, None, 100.0, 30, 15, 10)
        self.assertEqual(pct, 50)
        self.assertEqual(since, 100.0)

    def test_ramp_down_after_delay(self):
        pct, since = next_fan_percent(20, 50, 100.0, 110.0, 30, 15, 10)
        self.assertEqual(pct, 35)
        self.assertEqual(since, 100.0)


class TestP0Debounce(unittest.TestCase):
    def test_no_engage_before_6s(self):
        s = P0FloorState()
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=True, now=0.0, last_written_pct=60.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(s.floor_active)
        self.assertEqual(s.p0_since, 0.0)

        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=True, now=5.9, last_written_pct=60.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(s.floor_active)

    def test_engage_after_6s_continuous_p0(self):
        s = P0FloorState()
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=0.0, last_written_pct=60.0,
        )
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=True, now=P0_ENGAGE_S, last_written_pct=60.0,
        )
        self.assertEqual(ev, "engage")
        self.assertTrue(s.floor_active)
        self.assertEqual(s.floor_pwm_min, 60.0)

    def test_brief_p0_does_not_engage(self):
        """Desktop flash: 2s P0 then idle — must not engage."""
        s = P0FloorState()
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=0.0, last_written_pct=40.0,
        )
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=2.0, last_written_pct=40.0,
        )
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=False, now=2.1, last_written_pct=40.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(s.floor_active)
        self.assertIsNone(s.p0_since)

        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=10.0, last_written_pct=40.0,
        )
        self.assertEqual(s.p0_since, 10.0)
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=True, now=15.0, last_written_pct=40.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(s.floor_active)

    def test_release_only_after_25s_non_p0(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=70.0, p0_since=0.0)
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=False, now=100.0, last_written_pct=70.0,
        )
        self.assertIsNone(ev)
        self.assertTrue(s.floor_active)
        self.assertEqual(s.non_p0_since, 100.0)
        self.assertEqual(s.floor_pwm_min, 70.0)

        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=False, now=100.0 + P0_RELEASE_S - 0.1,
            last_written_pct=70.0,
        )
        self.assertIsNone(ev)
        self.assertTrue(s.floor_active)
        self.assertEqual(s.floor_pwm_min, 70.0)

        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=False, now=100.0 + P0_RELEASE_S,
            last_written_pct=70.0,
        )
        self.assertEqual(ev, "release")
        self.assertFalse(s.floor_active)
        self.assertIsNone(s.floor_pwm_min)

    def test_brief_game_dip_does_not_release(self):
        """Game dips out of P0 for a few seconds — floor must hold."""
        s = P0FloorState(floor_active=True, floor_pwm_min=75.0)
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=False, now=200.0, last_written_pct=75.0,
        )
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=False, now=205.0, last_written_pct=75.0,
        )
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=True, now=206.0, last_written_pct=75.0,
        )
        self.assertIsNone(ev)
        self.assertTrue(s.floor_active)
        self.assertIsNone(s.non_p0_since)
        self.assertEqual(s.floor_pwm_min, 75.0)

        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=False, now=300.0, last_written_pct=75.0,
        )
        self.assertEqual(s.non_p0_since, 300.0)
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=False, now=320.0, last_written_pct=75.0,
        )
        self.assertIsNone(ev)
        self.assertTrue(s.floor_active)
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=False, now=325.0, last_written_pct=75.0,
        )
        self.assertEqual(ev, "release")
        self.assertFalse(s.floor_active)

    def test_disabled_clears_immediately(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=80.0, p0_since=1.0)
        s, ev = update_p0_debounce(
            s, enabled=False, is_p0=True, now=50.0, last_written_pct=80.0,
        )
        self.assertEqual(ev, "disabled")
        self.assertFalse(s.floor_active)
        self.assertIsNone(s.floor_pwm_min)

    def test_engage_seeds_from_last_written(self):
        s = P0FloorState()
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=0.0, last_written_pct=55.0,
        )
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=True, now=6.0, last_written_pct=55.0,
        )
        self.assertEqual(ev, "engage")
        self.assertEqual(s.floor_pwm_min, 55.0)

    def test_p0_gap_resets_engage_timer(self):
        """Non-continuous P0 must not accumulate toward engage."""
        s = P0FloorState()
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=0.0, last_written_pct=50.0,
        )
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=5.0, last_written_pct=50.0,
        )
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=False, now=5.1, last_written_pct=50.0,
        )
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=5.0, last_written_pct=50.0,
        )
        self.assertEqual(s.p0_since, 5.0)
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=True, now=10.0, last_written_pct=50.0,
        )
        self.assertIsNone(ev)
        self.assertFalse(s.floor_active)
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=True, now=11.0, last_written_pct=50.0,
        )
        self.assertEqual(ev, "engage")


class TestApplyP0Floor(unittest.TestCase):
    def test_inactive_passthrough(self):
        s = P0FloorState(floor_active=False)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=30.0, last_written_pct=40.0,
            measured_rpm=2000, min_rpm=4400,
        )
        self.assertEqual(pct, 30.0)
        self.assertIsNone(ev)

    def test_disabled_passthrough(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=70.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=False, is_p0=True, curve_pct=20.0, last_written_pct=70.0,
            measured_rpm=2000, min_rpm=4400,
        )
        self.assertEqual(pct, 20.0)

    def test_holds_floor_when_rpm_ok_and_curve_lower(self):
        """While floor active and RPM OK, command stays above curve via floor."""
        s = P0FloorState(floor_active=True, floor_pwm_min=70.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=20.0, last_written_pct=70.0,
            measured_rpm=4500, min_rpm=4400,
        )
        # Soft-settle one step toward curve: 70 - 5 = 65, still >> 20
        self.assertEqual(ev, "settle")
        self.assertEqual(s2.floor_pwm_min, 65.0)
        self.assertEqual(pct, 65.0)

    def test_does_not_jump_to_100_on_raise(self):
        """Regression: raise used full ramp_up (30%) and max(base,curve)+step → 100."""
        s = P0FloorState(floor_active=True, floor_pwm_min=60.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=70.0, last_written_pct=60.0,
            measured_rpm=3000, min_rpm=4400,
            raise_step_pct=P0_RAISE_STEP_PCT,
        )
        self.assertEqual(ev, "raise")
        # 60 + 5 = 65, then max with curve 70 → 70 — NOT 100
        self.assertEqual(s2.floor_pwm_min, 70.0)
        self.assertEqual(pct, 70.0)
        self.assertLess(pct, 100.0)

    def test_raises_when_rpm_below_min(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=50.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=40.0, last_written_pct=50.0,
            measured_rpm=3000, min_rpm=4400,
        )
        self.assertEqual(ev, "raise")
        self.assertEqual(s2.floor_pwm_min, 55.0)  # 50 + 5
        self.assertEqual(pct, 55.0)

    def test_raise_step_default_5(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=40.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=30.0, last_written_pct=40.0,
            measured_rpm=2000, min_rpm=4400,
        )
        self.assertEqual(ev, "raise")
        self.assertEqual(s2.floor_pwm_min, 45.0)

    def test_raise_caps_at_100(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=98.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=90.0, last_written_pct=98.0,
            measured_rpm=1000, min_rpm=4400,
        )
        self.assertEqual(s2.floor_pwm_min, 100.0)
        self.assertEqual(pct, 100.0)

    def test_soft_settle_unsticks_100(self):
        """Regression: floor stuck at 100 while curve was 85 and RPM already OK."""
        s = P0FloorState(floor_active=True, floor_pwm_min=100.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=85.0, last_written_pct=100.0,
            measured_rpm=5000, min_rpm=4400,
        )
        self.assertEqual(ev, "settle")
        self.assertEqual(s2.floor_pwm_min, 95.0)
        self.assertEqual(pct, 95.0)

        # Keep settling until floor meets curve
        for expected in (90.0, 85.0):
            pct, s2, ev = apply_p0_floor(
                s2, enabled=True, is_p0=True, curve_pct=85.0, last_written_pct=pct,
                measured_rpm=5000, min_rpm=4400,
            )
            self.assertEqual(s2.floor_pwm_min, expected)
            self.assertEqual(pct, expected)

        # At curve: further settle stays at curve
        pct, s2, ev = apply_p0_floor(
            s2, enabled=True, is_p0=True, curve_pct=85.0, last_written_pct=85.0,
            measured_rpm=5000, min_rpm=4400,
        )
        self.assertIsNone(ev)
        self.assertEqual(pct, 85.0)

    def test_settle_stops_if_rpm_drops_again(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=80.0)
        # RPM still below min → raise, not settle
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=50.0, last_written_pct=80.0,
            measured_rpm=3000, min_rpm=4400,
        )
        self.assertEqual(ev, "raise")
        self.assertEqual(s2.floor_pwm_min, 85.0)

    def test_curve_above_floor_wins(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=50.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=80.0, last_written_pct=70.0,
            measured_rpm=5000, min_rpm=4400,
        )
        # Settle toward curve but curve is higher → next = curve
        self.assertEqual(pct, 80.0)
        self.assertEqual(s2.floor_pwm_min, 80.0)  # max(80, 50-5)

    def test_seeds_floor_when_none_and_rpm_ok(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=None)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=15.0, last_written_pct=55.0,
            measured_rpm=4500, min_rpm=4400,
        )
        # seed base=55, settle max(15, 55-5)=50
        self.assertEqual(s2.floor_pwm_min, 50.0)
        self.assertEqual(pct, 50.0)
        self.assertEqual(ev, "settle")

    def test_no_rpm_holds_floor(self):
        s = P0FloorState(floor_active=True, floor_pwm_min=70.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=20.0, last_written_pct=70.0,
            measured_rpm=None, min_rpm=4400,
        )
        self.assertIsNone(ev)
        self.assertEqual(s2.floor_pwm_min, 70.0)
        self.assertEqual(pct, 70.0)

    def test_no_settle_during_non_p0_hold(self):
        """Regression: soft-settle during P4/P5 cancelled the 25s hold."""
        s = P0FloorState(floor_active=True, floor_pwm_min=91.0)
        pct, s2, ev = apply_p0_floor(
            s, enabled=True, is_p0=False, curve_pct=70.0, last_written_pct=91.0,
            measured_rpm=5000, min_rpm=4400,
        )
        self.assertIsNone(ev)
        self.assertEqual(s2.floor_pwm_min, 91.0)
        self.assertEqual(pct, 91.0)

        # Curve can still rise above the frozen floor
        pct, s2, ev = apply_p0_floor(
            s2, enabled=True, is_p0=False, curve_pct=95.0, last_written_pct=91.0,
            measured_rpm=5000, min_rpm=4400,
        )
        self.assertEqual(pct, 95.0)
        self.assertEqual(s2.floor_pwm_min, 91.0)


class TestP0IntegrationScenario(unittest.TestCase):
    """End-to-end pure-function scenario matching the intended plan."""

    def test_user_log_regression_no_100_stick(self):
        """Reproduce: engage @60, curve 70, low RPM must not jump to 100 and stick."""
        s = P0FloorState(floor_active=True, floor_pwm_min=60.0)
        last = 60.0
        # Several raises with low RPM — should climb by 5% steps, never 100
        for _ in range(6):
            pct, s, ev = apply_p0_floor(
                s, enabled=True, is_p0=True, curve_pct=70.0, last_written_pct=last,
                measured_rpm=3000, min_rpm=4400,
            )
            self.assertEqual(ev, "raise")
            self.assertLess(pct, 100.0)
            last = pct
        # After 6 steps from 60: 65,70,75,80,85,90 (curve floors at 70 first)
        self.assertLessEqual(last, 95.0)

        # RPM now OK — soft settle back toward curve 85
        s = P0FloorState(floor_active=True, floor_pwm_min=100.0)
        pct = 100.0
        for _ in range(5):
            pct, s, _ = apply_p0_floor(
                s, enabled=True, is_p0=True, curve_pct=85.0, last_written_pct=pct,
                measured_rpm=5000, min_rpm=4400,
            )
        self.assertEqual(pct, 85.0)

    def test_game_p0_hold_through_dip_then_release(self):
        s = P0FloorState()
        last = 30.0

        for t in range(0, 7):
            s, ev = update_p0_debounce(
                s, enabled=True, is_p0=True, now=float(t), last_written_pct=last,
            )
        self.assertEqual(ev, "engage")
        self.assertTrue(s.floor_active)
        self.assertEqual(s.floor_pwm_min, 30.0)

        # Raise a few steps while RPM low (not enough to hit 100)
        for _ in range(4):
            pct, s, ev = apply_p0_floor(
                s, enabled=True, is_p0=True, curve_pct=25.0, last_written_pct=last,
                measured_rpm=3500, min_rpm=4400,
            )
            self.assertEqual(ev, "raise")
            last = pct
        self.assertGreater(last, 30.0)
        self.assertLess(last, 100.0)

        # RPM ok at current PWM — settle one step toward low curve
        pct, s, ev = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=10.0, last_written_pct=last,
            measured_rpm=4500, min_rpm=4400,
        )
        self.assertEqual(ev, "settle")
        floor_held = pct
        self.assertGreater(floor_held, 10.0)

        # Dip out of P0 for 10s — feature stays engaged; if RPM falls
        # below min, closed-loop raises again (no free-fall without care).
        t0 = 100.0
        for dt in range(0, 11):
            s, ev = update_p0_debounce(
                s, enabled=True, is_p0=False, now=t0 + dt, last_written_pct=floor_held,
            )
            self.assertIsNone(ev)
            self.assertTrue(s.floor_active)
            # Simulate RPM dipping under min when PWM got too low
            rpm = 4500 if floor_held >= 40 else 3000
            pct, s, fev = apply_p0_floor(
                s, enabled=True, is_p0=True, curve_pct=5.0, last_written_pct=floor_held,
                measured_rpm=rpm, min_rpm=4400,
            )
            if rpm < 4400:
                self.assertEqual(fev, "raise")
                self.assertGreater(pct, floor_held - 1e-9)
            floor_held = pct

        # Back to P0 cancels release timer
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=True, now=120.0, last_written_pct=floor_held,
        )
        self.assertTrue(s.floor_active)
        self.assertIsNone(s.non_p0_since)

        # Full release after 25s non-P0
        s, _ = update_p0_debounce(
            s, enabled=True, is_p0=False, now=200.0, last_written_pct=floor_held,
        )
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=False, now=224.9, last_written_pct=floor_held,
        )
        self.assertIsNone(ev)
        self.assertTrue(s.floor_active)
        s, ev = update_p0_debounce(
            s, enabled=True, is_p0=False, now=225.0, last_written_pct=floor_held,
        )
        self.assertEqual(ev, "release")
        self.assertFalse(s.floor_active)

        pct, s, _ = apply_p0_floor(
            s, enabled=True, is_p0=True, curve_pct=5.0, last_written_pct=floor_held,
            measured_rpm=2000, min_rpm=4400,
        )
        self.assertEqual(pct, 5.0)


if __name__ == "__main__":
    unittest.main()
