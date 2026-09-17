"""Fan settings persistence and UI integration without hardware writes."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from victus_hub import api
from victus_hub.backend import fan_config
from victus_hub.pages.fans_page import FansPage


class TestFanSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "config.json"
        config_path = patch.object(fan_config, "_config_path", return_value=self.path)
        config_path.start()
        self.addCleanup(config_path.stop)

    def make_page(self):
        page = FansPage()
        self.addCleanup(page.deleteLater)
        return page

    def test_legacy_and_invalid_response_default_to_smooth(self):
        for contents in ('{}', '{"fan_curve_response": "unknown"}',
                         '{"fan_curve_response": null}'):
            with self.subTest(contents=contents):
                self.path.write_text(contents)
                self.assertEqual(fan_config.load().curve_response, "smooth")
        api.set_fan_curve_response("unknown")
        self.assertEqual(fan_config.load().curve_response, "smooth")

    def test_ui_changes_persist_and_survive_curve_and_mode_saves(self):
        page = self.make_page()
        page.set_selected_fan_mode("custom")
        self.assertFalse(page._curve_editor.isHidden())
        page._curve_response.setCurrentIndex(page._curve_response.findData("aggressive"))
        page._min_fan_change._spin.setValue(4.5)
        config = fan_config.load()
        self.assertEqual(config.curve_response, "aggressive")
        self.assertEqual(config.min_fan_change_pct, 4.5)
        page._on_point_moved(1, page._cpu_points[1].temp, 45)
        page._save_timer.stop()
        page._save_current_profile()
        api.set_smart_fan_enabled(True)
        api.set_smart_fan_enabled(False)
        api.set_custom_fan_enabled(False)
        api.set_custom_fan_enabled(True)
        config = fan_config.load()
        self.assertEqual(config.curve_response, "aggressive")
        self.assertEqual(config.min_fan_change_pct, 4.5)
        restored = self.make_page()
        self.assertEqual(restored._curve_response.currentData(), "aggressive")
        self.assertEqual(restored._min_fan_change._spin.value(), 4.5)
        restored._curve_response.setCurrentIndex(0)
        self.assertEqual(fan_config.load().curve_response, "smooth")

    def test_loading_settings_does_not_write(self):
        api.set_fan_curve_response("aggressive")
        with patch.object(fan_config, "save_all") as save:
            page = self.make_page()
            page._load_config()
        save.assert_not_called()
        self.assertEqual(page._curve_response.currentData(), "aggressive")
