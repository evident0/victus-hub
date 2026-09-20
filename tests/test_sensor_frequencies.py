"""CPU frequency telemetry and graph integration without hardware access."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from victus_hub.backend.sensors import SensorReader
from victus_hub.backend.types import ExtraSensor, SensorReading, SensorSnapshot
from victus_hub.features.sensors.definitions import sensor_definition_for_key
from victus_hub.features.sensors.stats import build_rows, next_stats
from victus_hub.pages.sensors_page import SensorsPage, _ROLE_GRAPHABLE
from victus_hub.windows.sensor_graph_window import SensorGraphWindow


class TestSensorFrequencies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def frequency(self, value=3200.0):
        return ExtraSensor(
            "cpu-frequency-0", "CPU", "CPU 0 Frequency", "MHz", 0, 5100,
            value, SensorReading(f"{value:.1f} MHz", "sysfs"),
        )

    def test_frequency_reads_units_fallback_and_numeric_order(self):
        values = {
            "cpu0/cpufreq/scaling_cur_freq": 3200500,
            "cpu0/cpufreq/cpuinfo_max_freq": 5100000,
            "cpu2/cpufreq/scaling_cur_freq": 0,
            "cpu2/cpufreq/cpuinfo_cur_freq": 2400000,
            "cpu10/cpufreq/scaling_cur_freq": 4100000,
        }
        with patch("victus_hub.backend.sensors.CPU_ROOT") as root, \
                patch("victus_hub.backend.sensors.read_int", side_effect=lambda p: values.get(str(p))):
            root.glob.return_value = [Path(f"cpu{i}") for i in (10, 2, 1, 0)]
            sensors = SensorReader._read_cpu_frequencies(None)
        self.assertEqual([s.key for s in sensors], [
            "cpu-frequency-0", "cpu-frequency-2", "cpu-frequency-10",
        ])
        self.assertEqual(sensors[0].numeric_value, 3200.5)
        self.assertEqual(sensors[0].reading.value, "3200.5 MHz")
        self.assertEqual(sensors[0].value_max, 5100)
        self.assertEqual(sensors[1].reading.source, "cpu2/cpufreq/cpuinfo_cur_freq")

    def test_table_order_graphability_and_frequency_stats(self):
        snap = SensorSnapshot(extra_sensors=[self.frequency()])
        stats = next_stats(snap, {})
        snap.extra_sensors = [self.frequency(4000)]
        stats = next_stats(snap, stats)
        page = SensorsPage()
        self.addCleanup(page.deleteLater)
        page.update_rows(build_rows(snap, stats))
        cpu = page._group_items["CPU"]
        self.assertEqual([cpu.child(i).text(0) for i in range(cpu.childCount())], [
            "CPU Temp", "CPU Usage", "CPU Power", "CPU 0 Frequency",
        ])
        for key in ("cpu-frequency-0", "cpu-usage", "gpu-usage"):
            self.assertTrue(page._sensor_items[key].data(0, _ROLE_GRAPHABLE))
        self.assertEqual(page._sensor_items["cpu-frequency-0"].text(4), "3600 MHz")

    def test_graph_uses_frequency_metadata_and_handles_missing_sample(self):
        snap = SensorSnapshot(extra_sensors=[self.frequency()], cpu_usage_pct=35, gpu_usage_pct=60)
        with patch("victus_hub.windows.sensor_graph_window.api.read_sensors", return_value=snap):
            graph = SensorGraphWindow("cpu-frequency-0")
            self.addCleanup(graph.deleteLater)
            self.addCleanup(graph.close)
            self.assertEqual(graph.windowTitle(), "CPU 0 Frequency Graph")
            self.assertEqual(float(graph._max_input.text()), 5100)
            self.assertEqual(graph._definition.unit, "MHz")
            self.assertEqual(graph._current_label.text(), "3200.0 MHz")
            for key, expected in (("cpu-usage", 35), ("gpu-usage", 60)):
                definition = sensor_definition_for_key(key, snap)
                self.assertTrue(definition.graphable)
                self.assertEqual(definition.numeric_value(snap), expected)
            snap.extra_sensors = []
            graph._poll()
            self.assertEqual(graph._definition.key, "cpu-frequency-0")
            self.assertEqual(graph._current_label.text(), "Unavailable")
            self.assertIsNone(graph._definition.numeric_value(snap))
