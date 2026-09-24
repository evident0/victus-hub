"""Power-source footer readings from sysfs nodes."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from victus_hub.backend import power_supply


class TestPowerSupply(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        root_patch = patch.object(power_supply, "_POWER_SUPPLY", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def supply(self, name: str, **fields: str) -> None:
        path = self.root / name
        path.mkdir()
        for field, value in fields.items():
            (path / field).write_text(value)

    def test_ac_and_battery_report_percentage(self):
        self.supply("AC", type="Mains", online="1")
        self.supply("BAT0", type="Battery", capacity="83", status="Charging")
        self.assertEqual(power_supply.power_status_text(), "AC · 83%")
        (self.root / "AC" / "online").write_text("0")
        (self.root / "BAT0" / "status").write_text("Discharging")
        self.assertEqual(power_supply.power_status_text(), "Battery · 83%")

    def test_battery_status_fallback_when_no_mains_node(self):
        self.supply("BAT0", type="Battery", capacity="45", status="Discharging")
        self.assertEqual(power_supply.power_status_text(), "Battery · 45%")
        (self.root / "BAT0" / "status").write_text("Full")
        self.assertEqual(power_supply.power_status_text(), "AC · 45%")

    def test_missing_or_invalid_capacity_and_supply(self):
        self.assertEqual(power_supply.power_status_text(), "—")
        self.supply("AC", type="Mains", online="1")
        self.assertEqual(power_supply.power_status_text(), "AC")
        self.supply("BAT0", type="Battery", capacity="unknown")
        self.assertEqual(power_supply.power_status_text(), "AC")
        with patch.object(power_supply, "_POWER_SUPPLY", self.root / "missing"):
            self.assertEqual(power_supply.power_status_text(), "—")
