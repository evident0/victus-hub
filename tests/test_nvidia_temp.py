"""NVIDIA temperature source checks.

Run: python -m unittest tests.test_nvidia_temp -v
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from victus_hub.backend import temps


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
        result = subprocess.run(
            [smi, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


@unittest.skipUnless(_has_nvidia() and shutil.which("nvidia-smi"), "NVIDIA required")
class TestNvidiaTemp(unittest.TestCase):
    def test_daemon_temp_matches_smi(self):
        if temps.dgpu_runtime_suspended():
            self.skipTest("dGPU suspended")
        smi = _smi_temp()
        if smi is None:
            self.skipTest("nvidia-smi failed")
        first = temps.read_gpu_temp_c()
        if first is None:
            self.skipTest("no metrics")
        self.assertLessEqual(abs(first - smi), 1.0)
        self.assertIsNotNone(temps.read_gpu_temp_c())


if __name__ == "__main__":
    unittest.main(verbosity=2)
