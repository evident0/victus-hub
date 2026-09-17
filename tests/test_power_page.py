"""Power slider editing and frequency application without hardware writes."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QAbstractSpinBox, QApplication

from victus_hub.backend.cpufreq import FrequencyPolicy
from victus_hub.pages.power_page import PowerPage, _SliderRow


class TestPowerPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.policies = [FrequencyPolicy(Path("policy0"), 1100980, 5137904, 1100980, 4600000)]
        reader = patch("victus_hub.pages.power_page.read_frequency_policies", return_value=self.policies)
        self.reader = reader.start()
        self.addCleanup(reader.stop)
        self.page = PowerPage()
        self.addCleanup(self.page.deleteLater)

    def test_numeric_edit_and_slider_stay_in_sync(self):
        row = self.page._stapm_spin
        row.value.setValue(40)
        self.assertEqual(row.slider.value(), 40)
        self.assertEqual(self.page._stapm_limit, 40000)
        row.slider.setValue(35)
        self.assertEqual(row.value.value(), 35)
        self.assertTrue(all(row.value.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.UpDownArrows
                            for row in self.page.findChildren(_SliderRow)))

    def test_frequency_units_precision_and_ordering(self):
        minimum, maximum = self.page._frequency_min, self.page._frequency_max
        self.assertEqual(minimum.value.value(), 1100.980)
        self.assertEqual(maximum.value.maximum(), 5137.904)
        minimum.value.setValue(4800)
        self.assertEqual(minimum.slider.value(), 4800000)
        self.assertEqual(maximum.value.value(), 4800)
        maximum.value.setValue(4000)
        self.assertEqual(minimum.value.value(), 4000)

    def test_reapply_changes_wait_for_apply(self):
        self.page._power_enabled = True
        self.page._update_apply_enabled()
        self.assertFalse(self.page._apply_btn.isEnabled())
        original = self.page._reapply_spin.slider.value()
        changed = 30 if original != 30 else 60
        with patch("victus_hub.pages.power_page.write_power_limit_settings") as write, \
                patch("victus_hub.pages.power_page.threading.Thread"):
            self.page._reapply_spin.value.setValue(changed)
            write.assert_not_called()
            self.assertTrue(self.page._apply_btn.isEnabled())
            self.page._reapply_spin.slider.setValue(original)
            self.assertFalse(self.page._apply_btn.isEnabled())
            self.page._reapply_spin.slider.setValue(changed)
            self.page._apply_btn.click()
            write.assert_called_once()
            self.assertEqual(write.call_args.args[0].reapply_seconds, changed)
            self.assertFalse(self.page._apply_btn.isEnabled())

    def test_sync_reapply_does_not_write_settings(self):
        settings = replace(self.page._make_power_settings(), reapply_seconds=45)
        with patch("victus_hub.pages.power_page.read_power_limit_settings", return_value=settings), \
                patch("victus_hub.pages.power_page.read_power_enabled", return_value=True), \
                patch("victus_hub.pages.power_page.write_power_limit_settings") as write:
            self.page.sync_power_from_settings()
            write.assert_not_called()
        self.assertEqual(self.page._reapply_spin.value.value(), 45)
        self.assertFalse(self.page._apply_btn.isEnabled())

    def test_apply_sends_khz_and_reads_back_kernel_limits(self):
        self.page._frequency_max.value.setValue(4200)

        def apply(minimum, maximum):
            self.policies[:] = [FrequencyPolicy(Path("policy0"), 1100980, 5137904, minimum, maximum - 1000)]
            return "applied"

        loop = QEventLoop()
        self.page._frequency_applied.connect(loop.quit)
        with patch("victus_hub.pages.power_page.request_cpu_frequency_limits", side_effect=apply) as request:
            self.page._on_apply_frequency()
            QTimer.singleShot(2000, loop.quit)
            loop.exec()
            request.assert_called_once_with(1100980, 4200000)
        self.assertTrue(self.page._frequency_btn.isEnabled())
        self.assertEqual(self.page._frequency_max.value.value(), 4199)

    def test_unavailable_hardware_and_apply_error(self):
        self.page._on_frequency_applied("daemon unavailable")
        self.assertIn("daemon unavailable", self.page._frequency_status.text())
        self.assertTrue(self.page._frequency_btn.isEnabled())
        self.reader.side_effect = RuntimeError("CPU frequency control is unavailable")
        self.page._load_frequency_limits()
        self.assertFalse(self.page._frequency_min.isEnabled())
        self.assertFalse(self.page._frequency_max.isEnabled())
        self.assertFalse(self.page._frequency_btn.isEnabled())
        self.assertIn("unavailable", self.page._frequency_status.text())


if __name__ == "__main__":
    unittest.main()
