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
from victus_hub.features.power.limits import PowerLimitSettings
from victus_hub.pages.power_page import PowerPage, _SliderRow


class TestPowerPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        vendor = patch("victus_hub.pages.power_page.is_intel_cpu", return_value=False)
        vendor.start()
        self.addCleanup(vendor.stop)
        saved = patch("victus_hub.pages.power_page.read_frequency_limits", return_value=None)
        self.saved = saved.start()
        self.addCleanup(saved.stop)
        writer = patch("victus_hub.pages.power_page.write_frequency_limits")
        self.writer = writer.start()
        self.addCleanup(writer.stop)
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

    def test_unsaved_frequency_defaults_to_full_hardware_range(self):
        self.assertEqual(self.page._frequency_min.slider.value(), 1100980)
        self.assertEqual(self.page._frequency_max.slider.value(), 5137904)
        self.writer.assert_not_called()

    def test_power_limits_default_to_disabled_for_both_vendors(self):
        from victus_hub.features.power.limits import read_power_enabled

        for intel in (False, True):
            with self.subTest(intel=intel), \
                    patch("victus_hub.features.power.limits.is_intel_cpu", return_value=intel), \
                    patch("victus_hub.features.power.limits.QSettings") as settings:
                settings.return_value.value.side_effect = lambda key, default, **kwargs: default
                self.assertFalse(read_power_enabled())
                group = "intelPowerLimits" if intel else "powerLimits"
                settings.return_value.value.assert_called_once_with(f"{group}/enabled", False, type=bool)

    def test_frequency_units_precision_and_ordering(self):
        minimum, maximum = self.page._frequency_min, self.page._frequency_max
        self.assertEqual(minimum.value.value(), 1100.980)
        self.assertEqual(maximum.value.maximum(), 5137.904)
        maximum.value.setValue(4600)
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

    def test_frequency_value_tracks_power_profile_changes(self):
        self.policies[:] = [replace(self.policies[0], hardware_max=3801000, maximum=3801000)]
        self.page._load_frequency_limits()
        self.policies[:] = [replace(self.policies[0], hardware_max=5137904, maximum=4600000)]
        self.page._load_frequency_limits(refresh=True)
        self.assertEqual(self.page._frequency_max.value.value(), 4600)
        self.assertEqual(self.page._frequency_max.value.maximum(), 5137.904)
        self.writer.assert_not_called()

    def test_frequency_range_tracks_power_profile_changes(self):
        self.policies[:] = [replace(self.policies[0], hardware_max=3801000, maximum=3801000)]
        self.page._load_frequency_limits()
        self.assertEqual(self.page._frequency_max.value.maximum(), 3801)
        self.page._frequency_min.value.setValue(1200)
        self.policies[:] = [replace(self.policies[0], hardware_max=5137904)]
        self.page._load_frequency_limits(refresh=True)
        self.assertEqual(self.page._frequency_max.value.maximum(), 5137.904)
        self.assertEqual(self.page._frequency_min.value.value(), 1200)
        self.page._frequency_max.value.setValue(5000)
        self.page._load_frequency_limits(refresh=True)
        self.assertEqual(self.page._frequency_max.value.value(), 5000)
        self.policies[:] = [replace(self.policies[0], hardware_max=3801000)]
        self.page._load_frequency_limits(refresh=True)
        self.assertEqual(self.page._frequency_max.value.value(), 3801)
        self.writer.assert_not_called()

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
        self.assertFalse(self.page._frequency_btn.isEnabled())
        self.assertEqual(self.page._frequency_max.value.value(), 4199)
        self.writer.assert_called_once_with(1100980, 4200000)
        self.page._frequency_max.value.setValue(4300)
        self.assertTrue(self.page._frequency_btn.isEnabled())
        self.page._frequency_max.value.setValue(4199)
        self.assertFalse(self.page._frequency_btn.isEnabled())

    def test_saved_frequency_is_applied_once_at_initialization(self):
        self.saved.return_value = (1200000, 4200000)
        with patch.object(PowerPage, "_on_apply_frequency") as apply:
            page = PowerPage()
            self.addCleanup(page.deleteLater)
            apply.assert_called_once_with()
            self.assertEqual(page._frequency_min.slider.value(), 1200000)
            self.assertEqual(page._frequency_max.slider.value(), 4200000)
            page._load_frequency_limits()
            apply.assert_called_once_with()

    def test_unavailable_hardware_and_apply_error(self):
        self.page._on_frequency_applied("daemon unavailable")
        self.assertIn("daemon unavailable", self.page._frequency_status.text())
        self.writer.assert_not_called()
        self.assertTrue(self.page._frequency_btn.isEnabled())
        self.reader.side_effect = RuntimeError("CPU frequency control is unavailable")
        self.page._load_frequency_limits()
        self.assertFalse(self.page._frequency_min.isEnabled())
        self.assertFalse(self.page._frequency_max.isEnabled())
        self.assertFalse(self.page._frequency_btn.isEnabled())
        self.assertIn("unavailable", self.page._frequency_status.text())

    def make_intel_page(self):
        with patch("victus_hub.pages.power_page.is_intel_cpu", return_value=True), \
                patch("victus_hub.pages.power_page.read_power_limit_settings", return_value=PowerLimitSettings(
                    fast_limit=65000, slow_limit=45000,
                )), \
                patch("victus_hub.pages.power_page.read_power_enabled", return_value=True), \
                patch("victus_hub.pages.power_page.read_intel_undervolt", return_value=(-50, -25)):
            page = PowerPage()
        self.addCleanup(page.deleteLater)
        return page

    def test_vendor_specific_controls_and_undervolt_placement(self):
        self.assertFalse(self.page._stapm_spin.isHidden())
        self.assertFalse(self.page._tctl_spin.isHidden())
        self.assertFalse(hasattr(self.page, "_undervolt_core"))
        page = self.make_intel_page()
        self.assertTrue(page._stapm_spin.isHidden())
        self.assertTrue(page._tctl_spin.isHidden())
        self.assertEqual(page._slow_spin.slider.value(), 45)
        self.assertEqual(page._fast_spin.slider.value(), 65)
        self.assertGreater(page.layout().indexOf(page._undervolt_core), page.layout().indexOf(page._frequency_status))
        self.assertEqual(page._frequency_min.value.value(), 1100.980)
        self.assertEqual(page._undervolt_core.slider.value(), -50)

    def test_intel_pl1_pl2_ordering(self):
        page = self.make_intel_page()
        page._slow_spin.setValue(70)
        self.assertEqual((page._slow_limit, page._fast_limit), (70000, 70000))
        page._fast_spin.setValue(40)
        self.assertEqual((page._slow_limit, page._fast_limit), (40000, 40000))

    def test_undervolt_applies_and_saves_only_on_success(self):
        page = self.make_intel_page()
        loop = QEventLoop()
        page._undervolt_applied.connect(loop.quit)
        for error in (None, RuntimeError("firmware locked")):
            with patch("victus_hub.pages.power_page.request_intel_undervolt", side_effect=error) as request, \
                    patch("victus_hub.pages.power_page.write_intel_undervolt") as write:
                page._on_apply_undervolt()
                self.assertFalse(page._undervolt_btn.isEnabled())
                QTimer.singleShot(2000, loop.quit)
                loop.exec()
                request.assert_called_once_with(-50, -25)
                self.assertTrue(page._undervolt_btn.isEnabled())
                if error:
                    write.assert_not_called()
                    self.assertIn("firmware locked", page._undervolt_status.text())
                else:
                    write.assert_called_once_with(-50, -25)
                    self.assertIn("Undervolt applied", page._undervolt_status.text())

    def test_intel_power_apply_error_is_visible_and_retryable(self):
        page = self.make_intel_page()
        page._slow_spin.setValue(40)
        loop = QEventLoop()
        page._power_result.connect(loop.quit)
        with patch("victus_hub.pages.power_page.apply_power_limits", side_effect=RuntimeError("RAPL locked")) as apply, \
                patch("victus_hub.pages.power_page.write_power_limit_settings"):
            page._on_apply_power()
            QTimer.singleShot(2000, loop.quit)
            loop.exec()
            self.assertEqual(apply.call_args.args[1:3], (65000, 40000))
        self.assertIn("RAPL locked", page._power_status.text())
        self.assertTrue(page._apply_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
