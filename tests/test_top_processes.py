"""Tests for process CPU, disk, and network rate accounting."""

from __future__ import annotations

import unittest

from victus_hub.widgets.top_processes_card import (
    _CPU_COUNT,
    _CLK_TCK,
    _MetricTracker,
    _fmt_rate,
    _parse_proc_stat,
    ProcessGroup,
    ProcessInfo,
    sort_process_groups,
)


class TestProcStat(unittest.TestCase):
    def test_parses_cpu_and_start_time_with_spaces_in_name(self):
        fields = ["S"] + ["0"] * 19
        fields[11] = "120"
        fields[12] = "30"
        fields[19] = "98765"
        payload = "42 (process with spaces) " + " ".join(fields)
        self.assertEqual(_parse_proc_stat(payload), (150, 98765))


class TestMetricTracker(unittest.TestCase):
    def test_cpu_is_normalized_to_total_logical_capacity(self):
        tracker = _MetricTracker()
        elapsed, _ = tracker.begin_sample(10.0, {})
        tracker.process_rates(42, (100, 7), (1000, 2000), elapsed, 10.0)

        elapsed, _ = tracker.begin_sample(12.0, {})
        cpu, _read, _write = tracker.process_rates(
            42,
            (100 + 2 * _CLK_TCK, 7),
            (1000, 2000),
            elapsed,
            12.0,
        )
        self.assertEqual(cpu, 100.0 / _CPU_COUNT)

    def test_disk_rates_use_elapsed_time(self):
        tracker = _MetricTracker()
        elapsed, _ = tracker.begin_sample(1.0, {})
        tracker.process_rates(42, (10, 7), (1000, 2000), elapsed, 1.0)

        elapsed, _ = tracker.begin_sample(3.0, {})
        _cpu, read_bps, write_bps = tracker.process_rates(
            42,
            (10, 7),
            (5096, 10192),
            elapsed,
            3.0,
        )
        self.assertEqual(read_bps, 2048.0)
        self.assertEqual(write_bps, 4096.0)

    def test_pid_reuse_resets_rates(self):
        tracker = _MetricTracker()
        elapsed, _ = tracker.begin_sample(1.0, {})
        tracker.process_rates(42, (10, 7), (1000, 2000), elapsed, 1.0)

        elapsed, _ = tracker.begin_sample(2.0, {})
        rates = tracker.process_rates(
            42, (5000, 8), (9000, 9000), elapsed, 2.0,
        )
        self.assertEqual(rates, (0.0, 0.0, 0.0))

    def test_socket_rates_are_differenced(self):
        tracker = _MetricTracker()
        tracker.begin_sample(2.0, {11: ((1, 2), 1000, 2000)})
        elapsed, rates = tracker.begin_sample(
            4.0, {11: ((1, 2), 5096, 10192)},
        )
        self.assertEqual(elapsed, 2.0)
        self.assertEqual(rates[11], (2048.0, 4096.0))

    def test_socket_cookie_prevents_inode_reuse_spike(self):
        tracker = _MetricTracker()
        tracker.begin_sample(2.0, {11: ((1, 2), 1000, 2000)})
        _elapsed, rates = tracker.begin_sample(
            4.0, {11: ((3, 4), 9000, 10000)},
        )
        self.assertNotIn(11, rates)

    def test_transient_io_failure_preserves_valid_baseline(self):
        tracker = _MetricTracker()
        elapsed, _ = tracker.begin_sample(1.0, {})
        tracker.process_rates(42, (10, 7), (1000, 2000), elapsed, 1.0)

        elapsed, _ = tracker.begin_sample(2.0, {})
        _cpu, read_bps, write_bps = tracker.process_rates(
            42, (10, 7), None, elapsed, 2.0,
        )
        self.assertIsNone(read_bps)
        self.assertIsNone(write_bps)

        elapsed, _ = tracker.begin_sample(5.0, {})
        _cpu, read_bps, write_bps = tracker.process_rates(
            42, (10, 7), (5096, 10192), elapsed, 5.0,
        )
        self.assertEqual(read_bps, 1024.0)
        self.assertEqual(write_bps, 2048.0)


class TestRateFormatting(unittest.TestCase):
    def test_formats_binary_rates(self):
        self.assertEqual(_fmt_rate(0), "0 B/s")
        self.assertEqual(_fmt_rate(None), "—")
        self.assertEqual(_fmt_rate(2048), "2.0 KiB/s")
        self.assertEqual(_fmt_rate(2 * 1024 * 1024), "2.0 MiB/s")

    def test_group_marks_partial_rate_unavailable(self):
        group = ProcessGroup(
            key="app",
            name="App",
            instances=[
                ProcessInfo(1, "App", 100, read_bps=1024.0),
                ProcessInfo(2, "App", 100, read_bps=None),
            ],
        )
        self.assertIsNone(group.read_bps)
        self.assertEqual(group.read_display, "—")


class TestProcessSorting(unittest.TestCase):
    def setUp(self):
        self.alpha = ProcessGroup(
            key="alpha", name="Alpha",
            instances=[ProcessInfo(1, "Alpha", 200, cpu_pct=20.0, download_bps=5.0)],
        )
        self.beta = ProcessGroup(
            key="beta", name="Beta",
            instances=[ProcessInfo(2, "Beta", 100, cpu_pct=50.0, download_bps=None)],
        )

    def test_numeric_column_sorts_descending_then_ascending(self):
        self.assertEqual(
            [group.name for group in sort_process_groups([self.alpha, self.beta], "cpu")],
            ["Beta", "Alpha"],
        )
        self.assertEqual(
            [group.name for group in sort_process_groups([self.alpha, self.beta], "cpu", True)],
            ["Alpha", "Beta"],
        )

    def test_unavailable_rates_remain_last(self):
        self.assertEqual(
            [group.name for group in sort_process_groups([self.alpha, self.beta], "download", True)],
            ["Alpha", "Beta"],
        )

    def test_process_column_sorts_names(self):
        self.assertEqual(
            [group.name for group in sort_process_groups([self.beta, self.alpha], "process", True)],
            ["Alpha", "Beta"],
        )


if __name__ == "__main__":
    unittest.main()
