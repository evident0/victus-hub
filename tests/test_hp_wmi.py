"""Regression coverage for the integrated hp-wmi MUX and dual fan interface."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from victus_hub.features.gpu import mux
from victus_hub.backend.modules import GREEN, RED, mux_module
from victus_hubd import sysfs


class TestHpWmi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_mux_modes_come_from_firmware_capabilities(self):
        (self.root / "gpu_mux_supported_names").write_text("hybrid discrete uma\n")
        (self.root / "gpu_mux_mode").write_text("3\n")
        with patch.object(mux, "GPU_MUX_PLATFORM", self.root):
            state = mux.read_gpu_mux_state()
            self.assertEqual([m.index for m in state.modes], [0, 1, 3])
            self.assertEqual(state.current_index, 3)
            self.assertEqual(mux_module(), ("hp-wmi", GREEN))
        with patch.object(sysfs, "GPU_MUX_PLATFORM", self.root):
            sysfs.write_gpu_mux_mode(1)
        self.assertEqual((self.root / "gpu_mux_mode").read_text(), "1")

    def test_platform_presence_alone_does_not_enable_mux(self):
        with patch.object(mux, "GPU_MUX_PLATFORM", self.root):
            self.assertIsNone(mux.read_gpu_mux_state())
            self.assertEqual(mux_module(), ("not supported", RED))

    def test_unreadable_or_invalid_mux_state_is_unavailable(self):
        (self.root / "gpu_mux_supported_names").write_text("hybrid discrete\n")
        with patch.object(mux, "GPU_MUX_PLATFORM", self.root):
            for value in (None, "invalid", "127"):
                if value is not None:
                    (self.root / "gpu_mux_mode").write_text(value)
                self.assertIsNone(mux.read_gpu_mux_state())

    def test_manual_fan_target_reaches_both_channels(self):
        for name in ("pwm1_enable", "pwm1", "pwm2"):
            (self.root / name).write_text("0")
        with patch.object(sysfs, "hp_hwmon", return_value=self.root):
            sysfs.write_pwm(180)
        self.assertEqual((self.root / "pwm1_enable").read_text(), "1")
        self.assertEqual((self.root / "pwm1").read_text(), "180")
        self.assertEqual((self.root / "pwm2").read_text(), "180")
        self.assertFalse((self.root / "pwm2_enable").exists())

    def test_old_single_pwm_driver_still_works(self):
        (self.root / "pwm1").write_text("0")
        (self.root / "pwm1_enable").write_text("2")
        with patch.object(sysfs, "hp_hwmon", return_value=self.root):
            sysfs.write_pwm(200)
        self.assertEqual((self.root / "pwm1").read_text(), "200")
        self.assertFalse((self.root / "pwm2").exists())

    def test_gpu_fan_write_failure_is_reported(self):
        (self.root / "pwm2").mkdir()
        with patch.object(sysfs, "hp_hwmon", return_value=self.root):
            with self.assertRaisesRegex(RuntimeError, "pwm2"):
                sysfs.write_pwm(180)
