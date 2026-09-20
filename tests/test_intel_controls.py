"""Intel power/voltage controls using fake sysfs and MSR interfaces."""

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings

from victus_hub import api
from victus_hub.backend.cpu import cpu_vendor
from victus_hub.backend.daemon_client import request_intel_power_limits, request_intel_undervolt
from victus_hub.features.power import limits
from victus_hubd import intel
from victus_hubd.daemon import _make_dispatch


class TestIntelPower(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        vendor = patch.object(intel, "is_intel_cpu", return_value=True)
        vendor.start()
        self.addCleanup(vendor.stop)
        for index in (0, 1):
            package = self.root / f"intel-rapl:{index}"
            package.mkdir()
            for name, value in {
                "name": f"package-{index}", "enabled": "1",
                # Intentionally reverse the indices: use names, not assumptions.
                "constraint_0_name": "short_term",
                "constraint_1_name": "long_term",
                "constraint_0_power_limit_uw": "65000000",
                "constraint_1_power_limit_uw": "45000000",
                "constraint_0_max_power_uw": "100000000",
                "constraint_1_time_window_us": "28000000",
            }.items():
                (package / name).write_text(value)

    def test_package_limits_use_named_constraints_and_preserve_windows(self):
        subdomain = self.root / "intel-rapl:0:0"
        subdomain.mkdir()
        (subdomain / "name").write_text("core")
        (self.root / "intel-rapl:0" / "enabled").write_text("0")
        intel.apply_power_limits(35000, 55000, self.root)
        for index in (0, 1):
            package = self.root / f"intel-rapl:{index}"
            self.assertEqual((package / "constraint_1_power_limit_uw").read_text(), "35000000")
            self.assertEqual((package / "constraint_0_power_limit_uw").read_text(), "55000000")
            self.assertEqual((package / "constraint_1_time_window_us").read_text(), "28000000")
            self.assertEqual((package / "enabled").read_text(), "1")

    def test_all_packages_validated_before_write(self):
        (self.root / "intel-rapl:1" / "constraint_0_max_power_uw").write_text("40000000")
        with self.assertRaisesRegex(RuntimeError, "hardware range"):
            intel.apply_power_limits(35000, 55000, self.root)
        self.assertEqual(
            (self.root / "intel-rapl:0" / "constraint_1_power_limit_uw").read_text(), "45000000",
        )

    def test_invalid_ranges_and_missing_hardware(self):
        for pl1, pl2 in ((14000, 25000), (45000, 35000), (25000, 121000)):
            with self.subTest(pl1=pl1, pl2=pl2), self.assertRaises(RuntimeError):
                intel.apply_power_limits(pl1, pl2, self.root)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            intel.apply_power_limits(25000, 35000, self.root / "missing")

    def test_locked_power_limit_and_write_failure_reported(self):
        with patch.object(Path, "write_text"):
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                intel.apply_power_limits(25000, 35000, self.root)
        with patch.object(Path, "write_text", side_effect=PermissionError("read-only")):
            with self.assertRaisesRegex(RuntimeError, "Some limits may have changed"):
                intel.apply_power_limits(25000, 35000, self.root)

    def test_non_intel_requests_rejected(self):
        with patch.object(intel, "is_intel_cpu", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "processor"):
                intel.apply_power_limits(25000, 35000, self.root)
            with self.assertRaisesRegex(RuntimeError, "processor"):
                intel.apply_undervolt(-50, -50)


class TestIntelUndervolt(unittest.TestCase):
    def setUp(self):
        self.offsets = {0: 0, 2: 0}
        self.response = 0
        self.locked = False
        self.commands = []
        for name, kwargs in (
            ("is_intel_cpu", {"return_value": True}),
            ("os.open", {"return_value": 123}),
            ("os.close", {}),
            ("os.pwrite", {"side_effect": self.write}),
            ("os.pread", {"side_effect": lambda *_: struct.pack("<Q", self.response)}),
        ):
            mock = patch(f"victus_hubd.intel.{name}", **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def write(self, fd, data, address):
        self.assertEqual((fd, address), (123, 0x150))
        command = struct.unpack("<Q", data)[0]
        self.commands.append(command)
        domain = (command >> 40) & 0xFF
        self.assertIn(domain, (0, 2))
        if (command >> 32) & 0xFF == 0x11 and not self.locked:
            self.offsets[domain] = (command >> 21) & 0x7FF
        self.response = self.offsets[domain] << 21
        return 8

    def test_offsets_encoded_verified_and_zero_resets(self):
        intel.apply_undervolt(-100, -50)
        self.assertEqual(self.offsets, {0: (-102 & 0x7FF), 2: (-51 & 0x7FF)})
        self.assertEqual([(cmd >> 32) & 0xFF for cmd in self.commands[:2]], [0x10, 0x10])
        intel.apply_undervolt(0, 0)
        self.assertEqual(self.offsets, {0: 0, 2: 0})

    def test_locked_firmware_is_not_reported_as_success(self):
        self.locked = True
        with self.assertRaisesRegex(RuntimeError, "not accepted"):
            intel.apply_undervolt(-100, -50)

    def test_mailbox_status_error_and_short_read(self):
        for data, message in ((struct.pack("<Q", 1 << 32), "locked"), (b"", "Short read")):
            with patch("victus_hubd.intel.os.pread", return_value=data):
                with self.assertRaisesRegex(RuntimeError, message):
                    intel.apply_undervolt(-100, -50)

    def test_invalid_offsets_and_missing_msr(self):
        for core, cache in ((1, 0), (0, -251)):
            with self.assertRaisesRegex(RuntimeError, "between"):
                intel.apply_undervolt(core, cache)
        self.assertFalse(self.commands)
        with patch("victus_hubd.intel.os.open", side_effect=FileNotFoundError("missing")):
            with self.assertRaisesRegex(RuntimeError, "msr kernel module"):
                intel.apply_undervolt(-50, -50)

    def test_mailbox_waits_for_completion_and_times_out(self):
        busy = struct.pack("<Q", 1 << 63)
        with patch("victus_hubd.intel.os.pread", side_effect=[busy, struct.pack("<Q", 0)]), \
                patch("victus_hubd.intel.time.sleep"):
            self.assertEqual(intel._mailbox(123, 0), 0)
        with patch("victus_hubd.intel.os.pread", return_value=busy), \
                patch("victus_hubd.intel.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                intel.apply_undervolt(-50, -50)


class TestIntelProtocol(unittest.TestCase):
    def test_daemon_and_client_units_validation_and_errors(self):
        handlers = dict(_make_dispatch(None, None))
        for prefix, target, client, values in (
            ("intel-power-limits", "apply_power_limits", request_intel_power_limits, (25000, 35000)),
            ("intel-undervolt", "apply_undervolt", request_intel_undervolt, (-100, -50)),
        ):
            body = "\t".join(map(str, values))
            with patch.object(intel, target, return_value="applied") as apply:
                self.assertEqual(handlers[prefix + "\t"](body), "OK\tapplied\n")
                apply.assert_called_once_with(*values)
                for invalid in ("1", "1\t2\t3", "x\t2"):
                    with self.assertRaises(RuntimeError):
                        handlers[prefix + "\t"](invalid)
            with patch("victus_hub.backend.daemon_client._request_daemon", return_value="OK\tapplied") as request:
                self.assertEqual(client(*values), "applied")
                request.assert_called_once_with(f"{prefix}\t{body}\n")
            with patch("victus_hub.backend.daemon_client._request_daemon", return_value="ERR\tlocked"):
                with self.assertRaisesRegex(RuntimeError, "locked"):
                    client(*values)

    def test_api_routes_intel_and_amd_including_reapply_entrypoint(self):
        for intel_cpu in (False, True):
            with patch("victus_hub.backend.cpu.is_intel_cpu", return_value=intel_cpu), \
                    patch.object(api.daemon_client, "request_power_limits") as amd, \
                    patch.object(api.daemon_client, "request_intel_power_limits") as intel_request:
                api.apply_power_limits(25000, 65000, 45000, 95)
                if intel_cpu:
                    intel_request.assert_called_once_with(45000, 65000)
                    amd.assert_not_called()
                else:
                    amd.assert_called_once_with(25000, 65000, 45000, 95)
                    intel_request.assert_not_called()

    def test_vendor_detection(self):
        with patch.object(Path, "read_text", return_value="processor : 0\nvendor_id : GenuineIntel\n"):
            self.assertEqual(cpu_vendor(), "GenuineIntel")
        with patch.object(Path, "read_text", side_effect=OSError):
            self.assertEqual(cpu_vendor(), "")


class TestIntelSettings(unittest.TestCase):
    def test_vendor_settings_are_independent_and_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "test.ini"), QSettings.IniFormat)
            with patch.object(limits, "QSettings", return_value=settings):
                with patch.object(limits, "is_intel_cpu", return_value=False):
                    amd = limits.PowerLimitSettings(stapm_limit=30000, fast_limit=60000, slow_limit=40000)
                    limits.write_power_limit_settings(amd)
                    limits.write_power_enabled(True)
                with patch.object(limits, "is_intel_cpu", return_value=True):
                    self.assertFalse(limits.read_power_enabled())
                    intel_settings = limits.PowerLimitSettings(fast_limit=55000, slow_limit=35000, reapply_seconds=10)
                    limits.write_power_limit_settings(intel_settings)
                    self.assertEqual(limits.read_power_limit_settings(), intel_settings)
                    limits.write_intel_undervolt(-100, -50)
                    self.assertEqual(limits.read_intel_undervolt(), (-100, -50))
                with patch.object(limits, "is_intel_cpu", return_value=False):
                    self.assertEqual(limits.read_power_limit_settings(), amd)
                    self.assertTrue(limits.read_power_enabled())
