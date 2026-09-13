"""Unit tests for userspace keyboard RGB animation (omen-space port)."""

from __future__ import annotations

import math
import os
import unittest
from unittest.mock import patch

from victus_hub.features.keyboard.lighting import (
    LightingSettings,
    compute_anim_color,
    effects_for_zone_count,
    lighting_frames,
    normalize_effect,
    spatial_index,
    step_increment,
)


class TestEffectLists(unittest.TestCase):
    def test_single_zone_matches_omen_space_victus_set(self):
        values = [value for value, _label in effects_for_zone_count(1)]
        self.assertEqual(values, ["static", "breathing", "cycle"])

    def test_four_zone_includes_wave_and_spatial_modes(self):
        values = [value for value, _label in effects_for_zone_count(4)]
        self.assertIn("wave", values)
        self.assertIn("wave_rainbow", values)
        self.assertIn("chase", values)
        self.assertNotIn("static", values[1:])  # static is first
        self.assertEqual(values[0], "static")

    def test_legacy_aliases_normalize(self):
        self.assertEqual(normalize_effect("color-cycle", 1), "cycle")
        self.assertEqual(normalize_effect("pulse", 4), "breathing")
        self.assertEqual(normalize_effect("strobe", 4), "blinking")
        self.assertEqual(normalize_effect("wave", 1), "static")


class TestSpatialIndex(unittest.TestCase):
    def test_four_zone_travels_left_to_right(self):
        # hardware: 0=right, 1=center, 2=left, 3=wasd
        self.assertEqual(spatial_index(2, 4), 0)  # left leads
        self.assertEqual(spatial_index(3, 4), 1)  # wasd
        self.assertEqual(spatial_index(1, 4), 2)  # center
        self.assertEqual(spatial_index(0, 4), 3)  # right last

    def test_single_zone_is_zero(self):
        self.assertEqual(spatial_index(0, 1), 0)


class TestComputeAnimColor(unittest.TestCase):
    def test_static_fallback_returns_primary(self):
        c = compute_anim_color("static", 1.0, 0, 4, 10, 20, 30, 1, 2, 3)
        self.assertEqual((c.red, c.green, c.blue), (10, 20, 30))

    def test_breathing_at_sin_zero_is_half(self):
        # factor = sin(0)*0.5 + 0.5 = 0.5
        c = compute_anim_color("breathing", 0.0, 0, 1, 200, 100, 50, 0, 0, 0)
        self.assertEqual((c.red, c.green, c.blue), (100, 50, 25))

    def test_breathing_peak_near_full(self):
        c = compute_anim_color(
            "breathing", math.pi / 2, 0, 1, 200, 100, 50, 0, 0, 0,
        )
        self.assertEqual((c.red, c.green, c.blue), (200, 100, 50))

    def test_blinking_is_square_wave(self):
        on = compute_anim_color("blinking", 0.0, 0, 1, 255, 0, 0, 0, 0, 0)
        off = compute_anim_color("blinking", math.pi, 0, 1, 255, 0, 0, 0, 0, 0)
        self.assertEqual((on.red, on.green, on.blue), (255, 0, 0))
        self.assertEqual((off.red, off.green, off.blue), (0, 0, 0))

    def test_cycle_is_in_phase_across_zones(self):
        a = compute_anim_color("cycle", 1.2, 0, 4, 0, 0, 0, 0, 0, 0)
        b = compute_anim_color("cycle", 1.2, 3, 4, 0, 0, 0, 0, 0, 0)
        self.assertEqual((a.red, a.green, a.blue), (b.red, b.green, b.blue))

    def test_wave_rainbow_is_out_of_phase_across_zones(self):
        a = compute_anim_color("wave_rainbow", 1.2, 0, 4, 0, 0, 0, 0, 0, 0)
        b = compute_anim_color("wave_rainbow", 1.2, 1, 4, 0, 0, 0, 0, 0, 0)
        self.assertNotEqual((a.red, a.green, a.blue), (b.red, b.green, b.blue))

    def test_wave_blends_primary_and_secondary(self):
        # sin(0)=0 → factor=0.5 → midpoint of (255,0,0) and (0,0,255)
        c = compute_anim_color("wave", 0.0, 0, 1, 255, 0, 0, 0, 0, 255)
        self.assertEqual((c.red, c.green, c.blue), (127, 0, 127))

    def test_chase_highlights_one_spatial_slot(self):
        # pos = int(0.0 * 2) % 4 = 0
        hot = compute_anim_color("chase", 0.0, 0, 4, 200, 0, 0, 0, 0, 0)
        dim = compute_anim_color("chase", 0.0, 1, 4, 200, 0, 0, 0, 0, 0)
        self.assertEqual((hot.red, hot.green, hot.blue), (200, 0, 0))
        self.assertEqual((dim.red, dim.green, dim.blue), (30, 0, 0))


class TestLightingFrames(unittest.TestCase):
    def test_static_uses_per_zone_colors(self):
        settings = LightingSettings(
            enabled=True,
            effect="static",
            color="#ff0000",
            zone_colors=["#ff0000", "#00ff00", "#0000ff", "#ffffff"],
        )
        frames = lighting_frames(settings, 4, 0.0)
        self.assertEqual(
            [(c.red, c.green, c.blue) for c in frames],
            [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)],
        )

    def test_disabled_is_black(self):
        settings = LightingSettings(enabled=False, effect="breathing")
        frames = lighting_frames(settings, 4, 1.0)
        self.assertTrue(all(c.red == c.green == c.blue == 0 for c in frames))

    def test_breathing_scales_each_zone_color(self):
        settings = LightingSettings(
            enabled=True,
            effect="breathing",
            color="#ff0000",
            zone_colors=["#c80000", "#00c800", "#0000c8", "#c8c800"],
        )
        frames = lighting_frames(settings, 4, 0.0)  # factor 0.5
        self.assertEqual((frames[0].red, frames[0].green, frames[0].blue), (100, 0, 0))
        self.assertEqual((frames[1].red, frames[1].green, frames[1].blue), (0, 100, 0))

    def test_step_increment_matches_omen_space_rate(self):
        # 50 ms tick at speed 50 → (50/100)*0.25 = 0.125
        self.assertAlmostEqual(step_increment(50, 0.050), 0.125)


class TestEmulatedZones(unittest.TestCase):
    def test_env_overrides_sysfs_and_marks_module_emulated(self):
        from victus_hub.api import get_keyboard_zone_count, set_keyboard_color
        from victus_hub.backend.modules import (
            emulated_keyboard_zone_count,
            keyboard_rgb_module,
        )

        with patch.dict(os.environ, {"VICTUS_HUB_EMULATE_ZONES": "4"}):
            self.assertEqual(emulated_keyboard_zone_count(), 4)
            self.assertEqual(get_keyboard_zone_count(), 4)
            self.assertEqual(keyboard_rgb_module()[0], "emulated")
            self.assertEqual(set_keyboard_color(255, 0, 0, zone=2), "ok")

    def test_invalid_env_is_ignored(self):
        from victus_hub.backend.modules import emulated_keyboard_zone_count

        with patch.dict(os.environ, {"VICTUS_HUB_EMULATE_ZONES": "nope"}):
            self.assertIsNone(emulated_keyboard_zone_count())


if __name__ == "__main__":
    unittest.main()
