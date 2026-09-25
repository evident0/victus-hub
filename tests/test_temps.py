"""Daemon temperature reads must not wake a suspended dGPU."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from victus_hub.backend.temps import read_cpu_temp_c, read_gpu_temp_c


class TestCpuTempSkipsGpuHwmon(unittest.TestCase):
    def test_nvidia_hwmon_inputs_are_not_read(self):
        hwmon = MagicMock()
        with patch("victus_hub.backend.temps.iter_hwmon_dirs", return_value=[hwmon]), \
                patch("victus_hub.backend.temps.read_text", return_value="nvidia"), \
                patch("victus_hub.backend.temps.read_int") as read_int:
            self.assertIsNone(read_cpu_temp_c())
            read_int.assert_not_called()
            hwmon.iterdir.assert_not_called()

    def test_amdgpu_hwmon_inputs_are_not_read(self):
        hwmon = MagicMock()
        with patch("victus_hub.backend.temps.iter_hwmon_dirs", return_value=[hwmon]), \
                patch("victus_hub.backend.temps.read_text", return_value="amdgpu"), \
                patch("victus_hub.backend.temps.read_int") as read_int:
            self.assertIsNone(read_cpu_temp_c())
            read_int.assert_not_called()


class TestGpuTempRespectsRuntimeSuspend(unittest.TestCase):
    def test_disabled_queries_close_nvml_without_touching_active_gpu(self):
        with patch("victus_hub.backend.temps._has_nvidia", return_value=True), \
                patch("victus_hub.backend.temps.dgpu_runtime_suspended", return_value=False), \
                patch("victus_hub.backend.temps._nvidia_hwmon_temp_c") as hwmon, \
                patch("victus_hub.backend.temps._nvml_ensure") as nvml, \
                patch("victus_hub.backend.temps._smi_selected") as smi, \
                patch("victus_hub.backend.temps._nvml_shutdown") as shutdown:
            self.assertIsNone(read_gpu_temp_c(disable_nvidia=True))
            hwmon.assert_not_called()
            nvml.assert_not_called()
            smi.assert_not_called()
            shutdown.assert_called_once()

    def test_suspended_dgpu_does_not_touch_sensors(self):
        with patch("victus_hub.backend.temps._has_nvidia", return_value=True), \
                patch("victus_hub.backend.temps.dgpu_runtime_suspended", return_value=True), \
                patch("victus_hub.backend.temps._nvidia_hwmon_temp_c") as hwmon, \
                patch("victus_hub.backend.temps._nvml_ensure") as nvml, \
                patch("victus_hub.backend.temps._smi_selected") as smi, \
                patch("victus_hub.backend.temps._nvml_shutdown") as shutdown:
            self.assertIsNone(read_gpu_temp_c())
            hwmon.assert_not_called()
            nvml.assert_not_called()
            smi.assert_not_called()
            shutdown.assert_called_once()

    def test_active_nvidia_uses_nvml_when_hwmon_missing(self):
        with patch("victus_hub.backend.temps._has_nvidia", return_value=True), \
                patch("victus_hub.backend.temps.dgpu_runtime_suspended", return_value=False), \
                patch("victus_hub.backend.temps._nvidia_hwmon_temp_c", return_value=None), \
                patch("victus_hub.backend.temps._nvml_ensure", return_value=True), \
                patch("victus_hub.backend.temps._nvml_read_temp", return_value=71.0) as nvml, \
                patch("victus_hub.backend.temps._smi_selected") as smi:
            self.assertEqual(read_gpu_temp_c(), 71.0)
            nvml.assert_called_once()
            smi.assert_not_called()

    def test_smi_is_last_resort_after_hwmon_and_nvml(self):
        with patch("victus_hub.backend.temps._has_nvidia", return_value=True), \
                patch("victus_hub.backend.temps.dgpu_runtime_suspended", return_value=False), \
                patch("victus_hub.backend.temps._nvidia_hwmon_temp_c", return_value=None), \
                patch("victus_hub.backend.temps._nvml_ensure", return_value=False), \
                patch("victus_hub.backend.temps._smi_selected", return_value=(66.0, None, None)) as smi:
            self.assertEqual(read_gpu_temp_c(), 66.0)
            smi.assert_called_once()
