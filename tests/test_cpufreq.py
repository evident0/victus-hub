"""CPU frequency reads, validation, write ordering, and daemon protocol."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from victus_hub.backend.cpufreq import read_frequency_policies
from victus_hub.backend.daemon_client import request_cpu_frequency_limits
from victus_hubd.cpufreq import apply_frequency_limits
from victus_hubd.daemon import _make_dispatch


class TestCpuFrequency(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("policy0", "policy1"):
            path = self.root / name
            path.mkdir()
            for field, value in {
                "cpuinfo_min_freq": 1100980,
                "cpuinfo_max_freq": 5137904,
                "scaling_min_freq": 1100980,
                "scaling_max_freq": 4600000,
            }.items():
                (path / field).write_text(str(value))

    def test_read_preserves_khz_precision(self):
        policies = read_frequency_policies(self.root)
        self.assertEqual(len(policies), 2)
        self.assertEqual(policies[0].minimum, 1100980)
        self.assertEqual(policies[0].hardware_max, 5137904)

    def test_apply_updates_all_policies(self):
        apply_frequency_limits(1200000, 4200000, self.root)
        for policy in read_frequency_policies(self.root):
            self.assertEqual((policy.minimum, policy.maximum), (1200000, 4200000))

    def test_amd_boost_ceiling_survives_power_save(self):
        for path in self.root.glob("policy*"):
            (path / "amd_pstate_max_freq").write_text("5137904")
            (path / "cpuinfo_max_freq").write_text("3801000")
            (path / "scaling_max_freq").write_text("3801000")
        self.assertTrue(all(p.hardware_max == 5137904 for p in read_frequency_policies(self.root)))
        apply_frequency_limits(1100980, 5137904, self.root)
        self.assertTrue(all(p.maximum == 5137904 for p in read_frequency_policies(self.root)))

    def test_invalid_optional_amd_ceiling_falls_back(self):
        (self.root / "policy0" / "amd_pstate_max_freq").write_text("invalid")
        self.assertEqual(read_frequency_policies(self.root)[0].hardware_max, 5137904)

    def test_validate_all_policies_before_writing(self):
        (self.root / "policy1" / "cpuinfo_max_freq").write_text("4700000")
        with self.assertRaisesRegex(RuntimeError, "hardware range"):
            apply_frequency_limits(1200000, 5000000, self.root)
        self.assertEqual(read_frequency_policies(self.root)[0].minimum, 1100980)

    def test_invalid_limits_do_not_write(self):
        for minimum, maximum in ((0, 4600000), (4700000, 4600000), (1100000, 4600000)):
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaises(RuntimeError):
                    apply_frequency_limits(minimum, maximum, self.root)
        self.assertEqual(read_frequency_policies(self.root)[0].maximum, 4600000)

    def test_write_order_keeps_minimum_below_maximum(self):
        original_write = Path.write_text

        def checked_write(path, text):
            value = int(text)
            if path.name == "scaling_min_freq":
                self.assertLessEqual(value, int((path.parent / "scaling_max_freq").read_text()))
            else:
                self.assertGreaterEqual(value, int((path.parent / "scaling_min_freq").read_text()))
            return original_write(path, text)

        with patch.object(Path, "write_text", checked_write):
            apply_frequency_limits(4800000, 5000000, self.root)
            apply_frequency_limits(1200000, 2000000, self.root)

    def test_missing_and_unreadable_policies(self):
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            read_frequency_policies(self.root / "missing")
        (self.root / "policy1" / "scaling_max_freq").unlink()
        with self.assertRaisesRegex(RuntimeError, "policy1"):
            apply_frequency_limits(1200000, 4200000, self.root)
        self.assertEqual((self.root / "policy0" / "scaling_max_freq").read_text(), "4600000")

    def test_write_failure_is_reported(self):
        with patch.object(Path, "write_text", side_effect=PermissionError("read-only")):
            with self.assertRaisesRegex(RuntimeError, "Some CPU limits may have changed"):
                apply_frequency_limits(1200000, 4200000, self.root)

    def test_daemon_dispatch_and_client(self):
        handler = dict(_make_dispatch(None, None))["cpu-frequency-limits\t"]
        with patch("victus_hubd.daemon.cpufreq.apply_frequency_limits", return_value="applied") as apply:
            self.assertEqual(handler("1100980\t4600000"), "OK\tapplied\n")
            apply.assert_called_once_with(1100980, 4600000)
            for body in ("1", "1\t2\t3", "a\t2"):
                with self.assertRaises(RuntimeError):
                    handler(body)
        with patch("victus_hub.backend.daemon_client._request_daemon", return_value="OK\tapplied") as request:
            self.assertEqual(request_cpu_frequency_limits(1100980, 4600000), "applied")
            request.assert_called_once_with("cpu-frequency-limits\t1100980\t4600000\n")
        with patch("victus_hub.backend.daemon_client._request_daemon", return_value="ERR\tunsupported request"):
            with self.assertRaises(RuntimeError):
                request_cpu_frequency_limits(1100980, 4600000)


if __name__ == "__main__":
    unittest.main()
