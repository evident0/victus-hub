"""Query policy tests without NVIDIA hardware or real settings writes."""

import unittest
import os
import subprocess
import sys
from threading import Event, Thread
from unittest.mock import patch

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
        with patch.object(nvidia.Path, "is_dir") as listed:
            for profile in (1, 2, 0, 1):
                nvidia.set_nvidia_power_profile(profile)
                self.assertTrue(nvidia.nvidia_query_disable_enabled())
                blocked = profile == 0
                self.assertEqual(nvidia.nvidia_queries_disabled(), blocked)
                if blocked:
                    self.assertIsNone(nvidia.get_gpu_name())
        listed.assert_not_called()
        self.settings.setValue.assert_not_called()

    def test_preference_outside_power_save_does_not_block(self):
        nvidia.set_nvidia_power_profile(1)
        nvidia.set_nvidia_queries_disabled(True)
        self.assertFalse(nvidia.nvidia_queries_disabled())
        nvidia.set_nvidia_power_profile(0)
        self.assertTrue(nvidia.nvidia_queries_disabled())
        self.assertIsNone(nvidia.get_gpu_name())

    def test_saved_disable_blocks_name_reads_without_io(self):
        with patch.object(nvidia.Path, "is_dir") as listed:
            for _ in range(100):
                self.assertIsNone(nvidia.get_gpu_name())
        listed.assert_not_called()
        self.settings.value.assert_called_once()

    def test_toggle_persists_and_can_resume(self):
        nvidia.set_nvidia_queries_disabled(True)
        self.assertIsNone(nvidia.get_gpu_name())
        self.settings.setValue.assert_called_with(nvidia.DISABLE_NVIDIA_QUERIES_KEY, True)
        nvidia.set_nvidia_queries_disabled(False)
        self.assertFalse(nvidia.nvidia_queries_disabled())
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
