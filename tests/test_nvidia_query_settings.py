"""Query policy tests without NVIDIA hardware or real settings writes."""

import unittest
import os
import subprocess
import sys
from threading import Event, Thread
from unittest.mock import Mock, patch

from victus_hub.backend import nvidia


class NvidiaQuerySettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings_patch = patch.object(nvidia, "QSettings")
        self.settings = self.settings_patch.start().return_value
        self.settings.value.return_value = True
        self.addCleanup(self.settings_patch.stop)
        self.policy_patch = patch.object(nvidia, "_queries_disabled", None)
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)
        self.power_save_patch = patch.object(nvidia, "_power_save_active", True)
        self.power_save_patch.start()
        self.addCleanup(self.power_save_patch.stop)

    def test_saved_preference_only_blocks_in_power_save(self):
        reader = nvidia.NvidiaReader()
        metrics = nvidia.NvidiaMetrics("42", 5.0, 0.0, "nvml")
        with patch.object(reader, "has_nvidia", return_value=True), \
                patch.object(reader, "is_runtime_suspended", return_value=False), \
                patch.object(reader, "_query_nvml", return_value=metrics), \
                patch.object(nvidia, "_hwmon_nvidia_temp_c", return_value=None):
            for profile in (1, 2, 0, 1):
                nvidia.set_nvidia_power_profile(profile)
                self.assertTrue(nvidia.nvidia_query_disable_enabled())
                self.assertEqual(reader.read(), None if profile == 0 else metrics)
        self.settings.setValue.assert_not_called()

    def test_enabling_outside_power_save_keeps_session_open(self):
        nvidia.set_nvidia_power_profile(1)
        reader = nvidia.NvidiaReader()
        reader._lib = Mock()
        reader._inited = True
        nvidia.set_nvidia_queries_disabled(True)
        nvidia._close_disabled_readers()
        reader._lib.nvmlShutdown.assert_not_called()
        nvidia.set_nvidia_power_profile(0)
        nvidia._close_disabled_readers()
        reader._lib.nvmlShutdown.assert_called_once()

    def test_saved_disable_blocks_all_entry_points_without_polling(self):
        reader = nvidia.NvidiaReader()
        with patch.object(nvidia.ctypes, "CDLL") as library, \
                patch.object(nvidia.subprocess, "run") as process, \
                patch.object(nvidia, "_hwmon_nvidia_temp_c") as hwmon, \
                patch.object(reader, "has_nvidia") as detect:
            for _ in range(100):
                self.assertIsNone(reader.read())
                self.assertIsNone(reader._query_nvml())
                self.assertIsNone(reader._query_smi())
                self.assertIsNone(nvidia.get_gpu_name())
            library.assert_not_called()
            process.assert_not_called()
            hwmon.assert_not_called()
            detect.assert_not_called()
        self.settings.value.assert_called_once()

    def test_toggle_releases_session_once_and_can_resume(self):
        reader = nvidia.NvidiaReader()
        reader._lib = Mock()
        reader._inited = True
        nvidia.set_nvidia_queries_disabled(True)
        nvidia._close_disabled_readers()  # Wait for cleanup only in the test.
        self.assertFalse(reader._inited)
        reader._lib.nvmlShutdown.assert_called_once()
        for _ in range(10):
            self.assertIsNone(reader.read())
        reader._lib.nvmlShutdown.assert_called_once()
        self.settings.setValue.assert_called_with(nvidia.DISABLE_NVIDIA_QUERIES_KEY, True)

        nvidia.set_nvidia_queries_disabled(False)
        metrics = nvidia.NvidiaMetrics("42", 5.0, 0.0, "nvml")
        with patch.object(reader, "has_nvidia", return_value=True), \
                patch.object(reader, "is_runtime_suspended", return_value=False), \
                patch.object(reader, "_query_nvml", return_value=metrics), \
                patch.object(nvidia, "_hwmon_nvidia_temp_c", return_value=None):
            self.assertEqual(reader.read(), metrics)
        self.settings.setValue.assert_called_with(nvidia.DISABLE_NVIDIA_QUERIES_KEY, False)

    def test_disable_does_not_wait_for_inflight_query(self):
        entered = Event()
        release = Event()
        toggled = Event()

        def busy_query():
            with nvidia._query_lock:
                entered.set()
                release.wait(5)

        worker = Thread(target=busy_query)
        worker.start()
        self.assertTrue(entered.wait(2))
        self.addCleanup(worker.join)
        self.addCleanup(release.set)

        def toggle():
            nvidia.set_nvidia_queries_disabled(True)
            # A disabled name read also must not wait for the lock.
            self.assertIsNone(nvidia.get_gpu_name())
            toggled.set()

        caller = Thread(target=toggle)
        caller.start()
        try:
            self.assertTrue(toggled.wait(1), "toggle blocked behind driver query")
        finally:
            release.set()
            worker.join()
            caller.join()
            nvidia._close_disabled_readers()

    def test_api_import_does_not_start_worker_before_qapplication(self):
        result = subprocess.run(
            [sys.executable, "-c", """
import threading
from victus_hub import api
assert api._sensor_thread is None
assert not any(t.name == 'sensor-poll' for t in threading.enumerate())
from PySide6.QtWidgets import QApplication
app = QApplication([])
from unittest.mock import patch
with patch.object(api, '_sensor_loop') as loop:
    api.start_sensor_reader()
    api._sensor_thread.join(2)
    api.start_sensor_reader()
    loop.assert_called_once()
"""],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("QApplication was not created", result.stderr)


if __name__ == "__main__":
    unittest.main()
