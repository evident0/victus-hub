"""Informational sensors are requested by the visible page and read once, in the daemon."""

import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from victus_hub.backend.rapl import CpuPowerSample
from victus_hub.backend.sensor_keys import (
    FANS_PAGE_KEYS,
    GPU_QUERY_KEYS,
    HOME_PAGE_KEYS,
    KEYBOARD_PAGE_KEYS,
    POWER_PAGE_KEYS,
    SENSORS_PAGE_KEYS,
    SETTINGS_PAGE_KEYS,
    keys_for_page,
    request_key_for_graph,
)
from victus_hub.backend.sensors import SensorReader
from victus_hub.backend import temps
from victus_hub.backend.temps import NvidiaFields, _smi_selected, _smi_temp_c, close_nvidia, query_nvidia_fields, read_gpu_temp_c
from victus_hub.backend.types import SensorSnapshot
from victus_hubd.daemon import _make_dispatch


class PageSensorKeyTests(unittest.TestCase):
    def test_power_page_is_cpu_only(self):
        self.assertEqual(keys_for_page(1), POWER_PAGE_KEYS)
        self.assertEqual(POWER_PAGE_KEYS, frozenset({"cpu-power", "cpu-frequency"}))
        self.assertTrue(POWER_PAGE_KEYS.isdisjoint(GPU_QUERY_KEYS))

    def test_home_asks_for_what_it_displays_and_nothing_else(self):
        self.assertEqual(keys_for_page(0), HOME_PAGE_KEYS)
        self.assertTrue({"cpu-temp", "cpu-usage", "cpu-power", "gpu-temp", "gpu-usage", "gpu-power", "cpu-fan", "gpu-fan", "ram-usage"} <= HOME_PAGE_KEYS)
        self.assertNotIn("cpu-frequency", HOME_PAGE_KEYS)
        self.assertNotIn("lm-sensors", HOME_PAGE_KEYS)
        self.assertNotIn("pwm-value", HOME_PAGE_KEYS)

    def test_fans_page_is_rpm_only(self):
        self.assertEqual(keys_for_page(2), FANS_PAGE_KEYS)
        self.assertEqual(FANS_PAGE_KEYS, frozenset({"cpu-fan", "gpu-fan"}))

    def test_keyboard_and_settings_request_nothing(self):
        self.assertEqual(keys_for_page(3), KEYBOARD_PAGE_KEYS)
        self.assertEqual(keys_for_page(5), SETTINGS_PAGE_KEYS)
        self.assertEqual(KEYBOARD_PAGE_KEYS, frozenset())
        self.assertEqual(SETTINGS_PAGE_KEYS, frozenset())

    def test_sensors_page_is_the_only_page_that_asks_for_everything(self):
        self.assertEqual(keys_for_page(4), SENSORS_PAGE_KEYS)
        self.assertIn("lm-sensors", SENSORS_PAGE_KEYS)
        self.assertIn("gpu-power", SENSORS_PAGE_KEYS)
        self.assertNotIn("profile", SENSORS_PAGE_KEYS)

    def test_graph_of_one_core_does_not_become_a_gpu_request(self):
        self.assertEqual(request_key_for_graph("cpu-frequency-3"), "cpu-frequency")
        self.assertEqual(request_key_for_graph("lm-nvme-composite"), "lm-sensors")
        self.assertEqual(request_key_for_graph("gpu-power"), "gpu-power")


class RequestedReadTests(unittest.TestCase):
    def test_empty_request_does_not_touch_nvidia(self):
        reader = SensorReader()
        with patch("victus_hub.backend.sensors.temps.query_nvidia_fields") as query:
            snap = reader.read_requested(frozenset())
        query.assert_not_called()
        self.assertIsNone(snap.gpu_temp_c)
        self.assertIsNone(reader._nvidia)

    def test_cpu_power_does_not_query_gpu(self):
        reader = SensorReader()
        sample = CpuPowerSample(kind="watts", watts=15.5, source="package")
        with patch("victus_hub.backend.sensors.temps.query_nvidia_fields") as query:
            snap = reader.read_requested(
                POWER_PAGE_KEYS,
                read_cpu_power=lambda: sample,
            )
        query.assert_not_called()
        self.assertEqual(snap.cpu_power.value, "15.5 W")
        self.assertIsNone(snap.gpu_usage_pct)
        self.assertEqual(snap.gpu_power.value, "0 W")

    def test_gpu_power_alone_does_not_ask_for_temp_or_util(self):
        reader = SensorReader()
        fields = NvidiaFields(power_w=9.5, power_source="nvml")
        with patch("victus_hub.backend.sensors.temps._has_nvidia", return_value=True), \
                patch("victus_hub.backend.sensors.temps.dgpu_runtime_suspended", return_value=False), \
                patch("victus_hub.backend.sensors.temps.query_nvidia_fields", return_value=fields) as query:
            snap = reader.read_requested(frozenset({"gpu-power"}))
        query.assert_called_once_with(temperature=False, power=True, utilization=False)
        self.assertEqual(snap.gpu_power.value, "9.5 W")
        self.assertIsNone(snap.gpu_temp_c)
        self.assertIsNone(snap.gpu_usage_pct)


class NvidiaFieldQueryTests(unittest.TestCase):
    def test_temperature_only_skips_power_and_utilization(self):
        with patch("victus_hub.backend.temps._has_nvidia", return_value=True), \
                patch("victus_hub.backend.temps.dgpu_runtime_suspended", return_value=False), \
                patch("victus_hub.backend.temps._nvidia_hwmon_temp_c", return_value=None), \
                patch("victus_hub.backend.temps._nvml_ensure", return_value=True), \
                patch("victus_hub.backend.temps._nvml_read_temp", return_value=41.0) as temp, \
                patch("victus_hub.backend.temps._nvml_power_w") as power, \
                patch("victus_hub.backend.temps._nvml_util_pct") as util, \
                patch("victus_hub.backend.temps._smi_selected") as smi:
            fields = query_nvidia_fields(temperature=True)
        temp.assert_called_once()
        power.assert_not_called()
        util.assert_not_called()
        smi.assert_not_called()
        self.assertEqual(fields.temp_c, 41.0)
        self.assertIsNone(fields.power_w)
        self.assertIsNone(fields.util_pct)

    def test_hwmon_temperature_does_not_init_nvml_when_nothing_else_is_requested(self):
        with patch("victus_hub.backend.temps._has_nvidia", return_value=True), \
                patch("victus_hub.backend.temps.dgpu_runtime_suspended", return_value=False), \
                patch("victus_hub.backend.temps._nvidia_hwmon_temp_c", return_value=36.0), \
                patch("victus_hub.backend.temps._nvml_ensure") as ensure:
            fields = query_nvidia_fields(temperature=True, power=False, utilization=False)
        ensure.assert_not_called()
        self.assertEqual(fields.temp_c, 36.0)
        self.assertEqual(fields.temp_source, "hwmon:nvidia")


class NvidiaSmiIsolationTests(unittest.TestCase):
    def setUp(self):
        self._saved = (
            temps._smi_temp_retry_at,
            temps._smi_power_retry_at,
            temps._smi_util_retry_at,
            temps._smi_known_missing,
        )
        temps._smi_temp_retry_at = 0.0
        temps._smi_power_retry_at = 0.0
        temps._smi_util_retry_at = 0.0
        temps._smi_known_missing = False

    def tearDown(self):
        (
            temps._smi_temp_retry_at,
            temps._smi_power_retry_at,
            temps._smi_util_retry_at,
            temps._smi_known_missing,
        ) = self._saved

    def test_display_smi_does_not_block_the_fan_temperature_read(self):
        started = threading.Event()
        fan_done = threading.Event()

        def slow_smi(**_kwargs):
            started.set()
            self.assertTrue(fan_done.wait(1), "fan temperature waited on nvidia-smi")
            return (None, None, None)

        def fan():
            self.assertTrue(started.wait(1))
            close_nvidia()
            self.assertEqual(read_gpu_temp_c(), 40.0)
            fan_done.set()

        with patch("victus_hub.backend.temps._has_nvidia", return_value=True), \
                patch("victus_hub.backend.temps.dgpu_runtime_suspended", return_value=False), \
                patch("victus_hub.backend.temps._nvidia_hwmon_temp_c", return_value=40.0), \
                patch("victus_hub.backend.temps._nvml_ensure", return_value=True), \
                patch("victus_hub.backend.temps._nvml_power_w", return_value=None), \
                patch("victus_hub.backend.temps._smi_selected", side_effect=slow_smi):
            worker = threading.Thread(target=fan)
            worker.start()
            try:
                query_nvidia_fields(temperature=False, power=True, utilization=False)
            finally:
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(fan_done.is_set())

    def test_power_miss_does_not_backoff_temperature(self):
        missed = subprocess.CompletedProcess(args=[], returncode=0, stdout="N/A\n", stderr="")
        with patch("victus_hub.backend.temps.shutil.which", return_value="/usr/bin/nvidia-smi"), \
                patch("victus_hub.backend.temps.subprocess.run", return_value=missed) as run:
            self.assertEqual(
                _smi_selected(temperature=False, power=True, utilization=False),
                (None, None, None),
            )
        self.assertEqual(temps._smi_temp_retry_at, 0.0)
        self.assertGreater(temps._smi_power_retry_at, time.monotonic())
        found = subprocess.CompletedProcess(args=[], returncode=0, stdout="55\n", stderr="")
        with patch("victus_hub.backend.temps.shutil.which", return_value="/usr/bin/nvidia-smi"), \
                patch("victus_hub.backend.temps.subprocess.run", return_value=found) as run:
            self.assertEqual(_smi_temp_c(), 55.0)
        self.assertEqual(run.call_count, 1)
        query = run.call_args.args[0]
        self.assertIn("temperature.gpu", query[1])
        self.assertNotIn("power.draw", query[1])

    def test_temperature_miss_backs_off_the_fan_fallback(self):
        with patch("victus_hub.backend.temps.shutil.which", return_value="/usr/bin/nvidia-smi"), \
                patch(
                    "victus_hub.backend.temps.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=1.2),
                ):
            self.assertEqual(
                _smi_selected(temperature=True, power=False, utilization=False),
                (None, None, None),
            )
        self.assertGreater(temps._smi_temp_retry_at, time.monotonic())
        with patch("victus_hub.backend.temps.subprocess.run") as run:
            self.assertIsNone(_smi_temp_c())
        run.assert_not_called()


class SensorReleaseThreadTests(unittest.TestCase):
    def test_hide_does_not_block_the_caller_on_the_release(self):
        import victus_hub.api as api

        saw_query = threading.Event()
        saw_release = threading.Event()
        release_gate = threading.Event()
        caller = threading.current_thread()
        saved = (
            api._snapshot_running,
            api._ui_active,
            api._requested_keys,
            api._release_needed,
            api._snapshot,
            api._snapshot_keys,
        )

        def fake_request(keys, timeout=6.0):
            if frozenset(keys):
                saw_query.set()
                return SensorSnapshot()
            self.assertIsNot(threading.current_thread(), caller)
            self.assertEqual(timeout, 1.0)
            saw_release.set()
            release_gate.wait(2)
            return SensorSnapshot()

        api._snapshot_running = True
        api._ui_active = True
        api._requested_keys = frozenset({"cpu-power"})
        api._release_needed = False
        worker = threading.Thread(target=api._sensor_loop, daemon=True)
        try:
            with patch.object(api.daemon_client, "request_sensors", side_effect=fake_request):
                try:
                    worker.start()
                    self.assertTrue(saw_query.wait(2))
                    started = time.monotonic()
                    api.set_ui_active(False)
                    self.assertLess(time.monotonic() - started, 0.25)
                    self.assertTrue(saw_release.wait(2))
                finally:
                    release_gate.set()
                    with api._sensor_cv:
                        api._snapshot_running = False
                        api._sensor_cv.notify_all()
                    worker.join(2)
        finally:
            (
                api._snapshot_running,
                api._ui_active,
                api._requested_keys,
                api._release_needed,
                api._snapshot,
                api._snapshot_keys,
            ) = saved
        self.assertFalse(worker.is_alive())


class DaemonSensorRequestTests(unittest.TestCase):
    def test_handler_reads_only_the_keys_it_was_given(self):
        runtime = Mock()
        runtime.nvidia_queries_disabled.return_value = False
        runtime.fan_needs_gpu_temp.return_value = False
        reader = Mock()
        reader.read_requested.return_value = SensorSnapshot()
        sampler = Mock()
        handlers = dict(_make_dispatch(sampler, None, runtime, None))
        with patch("victus_hubd.daemon._get_sensor_reader", return_value=reader), \
                patch("victus_hubd.daemon.temps.hold_display_gpu") as hold, \
                patch("victus_hubd.daemon.temps.close_nvidia") as close:
            response = handlers["sensors"]("\tcpu-power,cpu-frequency")
        self.assertTrue(response.startswith("OK\t"))
        passed = reader.read_requested.call_args.args[0]
        self.assertEqual(set(passed), {"cpu-power", "cpu-frequency"})
        hold.assert_called_once_with(False)
        close.assert_called_once()

    def test_gpu_request_keeps_the_session_when_the_page_needs_it(self):
        runtime = Mock()
        runtime.nvidia_queries_disabled.return_value = False
        runtime.fan_needs_gpu_temp.return_value = False
        reader = Mock()
        reader.read_requested.return_value = SensorSnapshot()
        handlers = dict(_make_dispatch(Mock(), None, runtime, None))
        with patch("victus_hubd.daemon._get_sensor_reader", return_value=reader), \
                patch("victus_hubd.daemon.temps.hold_display_gpu") as hold, \
                patch("victus_hubd.daemon.temps.close_nvidia") as close:
            handlers["sensors"]("gpu-temp")
        hold.assert_called_once_with(True)
        close.assert_not_called()
        self.assertEqual(set(reader.read_requested.call_args.args[0]), {"gpu-temp"})

    def test_unknown_key_is_rejected(self):
        handlers = dict(_make_dispatch(Mock(), None, Mock(), None))
        with self.assertRaises(RuntimeError):
            handlers["sensors"]("gpu-watts")


class GpuNameProcTests(unittest.TestCase):
    def test_model_line_does_not_load_nvml(self):
        from pathlib import Path
        import tempfile

        from victus_hub.backend import nvidia

        nvidia._queries_disabled = False
        nvidia._power_save_active = False
        with tempfile.TemporaryDirectory() as directory:
            info = Path(directory) / "0000:01:00.0"
            info.mkdir()
            (info / "information").write_text(
                "Model:  NVIDIA GeForce RTX 4070 Laptop GPU\nIRQ: 1\n",
                encoding="utf-8",
            )
            with patch.object(nvidia, "_NVIDIA_PROC_GPUS", Path(directory)), \
                    patch.object(nvidia.ctypes, "CDLL") as library, \
                    patch.object(nvidia.subprocess, "run") as process:
                self.assertEqual(
                    nvidia.get_gpu_name(),
                    "NVIDIA GeForce RTX 4070 Laptop GPU",
                )
        library.assert_not_called()
        process.assert_not_called()


class DaemonImportTests(unittest.TestCase):
    def test_sensor_modules_do_not_import_qt(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import victus_hub.backend.sensors, victus_hub.backend.temps, victus_hubd.daemon; "
                "import sys; assert 'PySide6' not in sys.modules",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
