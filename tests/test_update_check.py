"""Settings update checks are user-initiated and report release results inline."""

import json
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication

from victus_hub.app.theme import COLORS
from victus_hub.pages.settings_page import SettingsPage, release_is_newer


class TestReleaseVersions(unittest.TestCase):
    def test_numeric_comparison_with_optional_v_prefix(self):
        self.assertTrue(release_is_newer("v1.0.10", "1.0.9"))
        self.assertFalse(release_is_newer("1.0.1", "1.0.1"))
        self.assertFalse(release_is_newer("1.0.0", "1.0.1"))
        with self.assertRaises(ValueError):
            release_is_newer("latest", "1.0.1")


class TestUpdateCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.version_patch = patch("victus_hub.pages.settings_page.program_version", return_value="1.0.1")
        self.version_patch.start()
        self.addCleanup(self.version_patch.stop)
        self.network_patch = patch("victus_hub.pages.settings_page.QNetworkAccessManager")
        self.network = self.network_patch.start().return_value
        self.addCleanup(self.network_patch.stop)
        self.reply = Mock()
        self.reply.error.return_value = QNetworkReply.NetworkError.NoError
        self.network.get.return_value = self.reply
        self.page = SettingsPage()
        self.addCleanup(self.page.deleteLater)

    def finish(self, response):
        self.reply.readAll.return_value = json.dumps(response).encode()
        self.reply.finished.connect.call_args.args[0]()

    def test_checks_only_after_button_press_and_reports_newer_release(self):
        self.network.get.assert_not_called()
        self.assertTrue(self.page._update_status.isHidden())
        self.page._update_btn.click()
        request = self.network.get.call_args.args[0]
        self.assertEqual(
            request.url().toString(),
            "https://api.github.com/repos/evident0/victus-hub/releases/latest",
        )
        self.assertFalse(self.page._update_btn.isEnabled())
        self.assertEqual(self.page._update_status.text(), "Checking for updates…")
        self.assertFalse(self.page._update_status.isHidden())
        self.finish({"tag_name": "v1.0.2"})
        self.assertTrue(self.page._update_btn.isEnabled())
        self.assertEqual(
            self.page._update_status.text(),
            "Update available please uninstall and install the new version.",
        )
        self.assertIn(COLORS["warn"], self.page._update_status.styleSheet())
        self.reply.deleteLater.assert_called_once()

    def test_current_or_older_release_is_up_to_date(self):
        self.page._update_btn.click()
        self.finish({"tag_name": "1.0.0"})
        self.assertEqual(self.page._update_status.text(), "Program is up to date.")
        self.assertIn(COLORS["ok"], self.page._update_status.styleSheet())

    def test_network_and_invalid_release_failures_are_not_up_to_date(self):
        self.page._update_btn.click()
        self.reply.error.return_value = QNetworkReply.NetworkError.HostNotFoundError
        self.finish({"tag_name": "1.0.2"})
        self.assertEqual(self.page._update_status.text(), "Could not check for updates.")
        self.assertTrue(self.page._update_btn.isEnabled())
        self.reply.error.return_value = QNetworkReply.NetworkError.NoError
        self.page._update_btn.click()
        self.finish({"tag_name": "not-a-version"})
        self.assertEqual(self.page._update_status.text(), "Could not check for updates.")
