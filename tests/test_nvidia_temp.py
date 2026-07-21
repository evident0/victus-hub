"""NVIDIA temperature source checks.

Run: python -m unittest tests.test_nvidia_temp -v
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from victus_hub.backend.nvidia import NVML_TEMPERATURE_GPU, NvidiaReader


def _has_nvidia() -> bool:
    return (
        Path("/sys/module/nvidia").is_dir()
        or Path("/proc/driver/nvidia").is_dir()
        or shutil.which("nvidia-smi") is not None
    )


def _smi_temp() -> float | None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        r = subprocess.run(
            [smi, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


@unittest.skipUnless(_has_nvidia() and shutil.which("nvidia-smi"), "NVIDIA required")
class TestNvidiaTemp(unittest.TestCase):
    def test_sensor_id(self):
        self.assertEqual(NVML_TEMPERATURE_GPU, 0)

    def test_reader_matches_smi(self):
        reader = NvidiaReader()
        try:
            if reader.is_runtime_suspended():
                self.skipTest("dGPU suspended")
            m = reader.read()
            if m is None:
                self.skipTest("no metrics")
            smi = _smi_temp()
            if smi is None:
                self.skipTest("nvidia-smi failed")
            self.assertLessEqual(abs(float(m.temperature) - smi), 1.0)
            # no idle-disarm: second read still works
            self.assertIsNotNone(reader.read())
        finally:
            reader.close()

    def test_no_idle_disarm_state(self):
        r = NvidiaReader()
        self.assertFalse(hasattr(r, "_armed"))
        self.assertFalse(hasattr(r, "_idle_streak"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
