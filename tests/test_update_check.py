"""Settings update checks and confirmation are user-initiated."""

import json
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

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

    def test_checks_only_after_button_press_and_prompts_for_newer_release(self):
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
        with patch.object(QMessageBox, "exec", return_value=0) as dialog:
            self.finish({"tag_name": "v1.0.2"})
        dialog.assert_called_once()
        self.assertTrue(self.page._update_btn.isEnabled())
        self.assertEqual(
            self.page._update_status.text(),
            "Release found: v1.0.2",
        )
        self.assertIn(COLORS["warn"], self.page._update_status.styleSheet())
        self.reply.deleteLater.assert_called_once()

    def test_update_confirmation_and_cancel(self):
        requested = []
        self.page.update_requested.connect(requested.append)
        self.page._update_btn.click()
        with patch.object(QMessageBox, "exec", return_value=0), \
             patch.object(QMessageBox, "clickedButton", return_value=None):
            self.finish({"tag_name": "v1.0.2"})
        self.assertEqual(requested, [])
        self.page._update_btn.click()
        with patch.object(QMessageBox, "exec", return_value=0), \
             patch.object(QMessageBox, "clickedButton",
                          lambda box: next(button for button in box.buttons()
                                           if button.text() == "Update")):
            self.finish({"tag_name": "v1.0.2"})
        self.assertEqual(requested, ["v1.0.2"])

    def test_confirmed_update_starts_installer_before_quitting(self):
        from victus_hub.app.main_window import MainWindow

        calls = []

        class Window:
            def _quit_app(self):
                calls.append("quit")

        self.page.update_requested.connect(
            lambda tag: MainWindow._start_update(Window(), tag))
        self.page._update_btn.click()
        with patch("victus_hub.app.main_window.start_update",
                   side_effect=lambda tag: calls.append(tag)), \
             patch.object(QMessageBox, "exec", return_value=0), \
             patch.object(QMessageBox, "clickedButton",
                          lambda box: next(button for button in box.buttons()
                                           if button.text() == "Update")):
            self.finish({"tag_name": "v1.0.2"})
        self.assertEqual(calls, ["v1.0.2", "quit"])

    def test_failed_installer_launch_keeps_gui_open(self):
        from victus_hub.app.main_window import MainWindow

        class Window:
            def _quit_app(self):
                raise AssertionError("GUI should not quit when the installer cannot start")

        with patch("victus_hub.app.main_window.start_update", side_effect=OSError("no auth agent")), \
             patch.object(QMessageBox, "warning") as warning:
            MainWindow._start_update(Window(), "v1.0.2")
        warning.assert_called_once()

    def test_update_dialog_uses_main_text_for_width(self):
        self.page._update_btn.click()
        shown = []

        def capture(box):
            shown.append(box)
            return 0

        with patch.object(QMessageBox, "exec", capture):
            self.finish({"tag_name": "v1.0.2"})
        box = shown[0]
        self.assertEqual(box.windowTitle(), "Update available")
        self.assertEqual(box.informativeText(), "")
        self.assertIn("Install Victus Hub v1.0.2?", box.text())
        self.assertIn(
            "The app will close during installation and reopen when it finishes.",
            box.text(),
        )
        box.show()
        QApplication.processEvents()
        label = box.findChild(QLabel, "qt_msgbox_label")
        self.assertIsNone(box.findChild(QLabel, "qt_msgbox_informativelabel"))
        self.assertGreater(box.width(), 300)
        self.assertGreater(label.width(), 300)
        box.hide()

    def test_current_or_older_release_is_up_to_date(self):
        for tag in ("1.0.1", "v1.0.0"):
            with self.subTest(tag=tag):
                self.page._update_btn.click()
                with patch.object(QMessageBox, "exec", return_value=0) as dialog:
                    self.finish({"tag_name": tag})
                dialog.assert_not_called()
                self.assertEqual(self.page._update_status.text(), "Program is up to date.")
                self.assertIn(COLORS["ok"], self.page._update_status.styleSheet())
                self.assertTrue(self.page._update_btn.isEnabled())

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


class TestStartUpdate(unittest.TestCase):
    def launch(self, tools, tag="v1.0.2"):
        from victus_hub.app.main_window import start_update

        with patch("victus_hub.app.main_window.shutil.which", side_effect=tools.get), \
             patch("victus_hub.app.main_window.subprocess.Popen") as popen:
            start_update(tag)
        return popen

    def test_opens_github_installer_in_a_terminal(self):
        popen = self.launch({
            "curl": "/usr/bin/curl",
            "sudo": "/usr/bin/sudo",
            "gnome-terminal": "/usr/bin/gnome-terminal",
        })
        args, kwargs = popen.call_args
        self.assertEqual(args[0][:4], ["/usr/bin/gnome-terminal", "--", "/bin/bash", "-c"])
        script = args[0][4]
        self.assertIn(
            "curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/install.sh | sudo bash",
            script,
        )
        self.assertIn("/usr/local/bin/victus-hub", script)
        self.assertIn("Update finished (exit %s). You can close this window.", script)
        self.assertIn("sleep infinity", script)
        self.assertNotIn("read -r", script)
        self.assertNotIn("v1.0.2", script)
        self.assertNotIn("update-install", script)
        self.assertNotIn("pkexec", script)
        self.assertNotIn("update_worker", script)
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])

    def test_release_tag_is_not_passed_to_the_shell(self):
        popen = self.launch({
            "curl": "/usr/bin/curl",
            "sudo": "/usr/bin/sudo",
            "xterm": "/usr/bin/xterm",
        }, tag="v1.0.2; rm -rf /")
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/xterm", "-hold", "-T"])
        script = command[-1]
        self.assertIn(
            "curl -sL https://raw.githubusercontent.com/evident0/victus-hub/master/install.sh | sudo bash",
            script,
        )
        self.assertNotIn("rm -rf", script)
        self.assertNotIn("1.0.2", script)

    def test_konsole_fallback(self):
        popen = self.launch({
            "curl": "/usr/bin/curl",
            "sudo": "/usr/bin/sudo",
            "konsole": "/usr/bin/konsole",
        })
        self.assertEqual(
            popen.call_args.args[0][:5],
            ["/usr/bin/konsole", "--separate", "--hold", "-e", "/bin/bash"],
        )

    def test_missing_terminal_curl_or_sudo_does_not_launch(self):
        from victus_hub.app.main_window import start_update

        cases = (
            {"curl": "/usr/bin/curl", "sudo": "/usr/bin/sudo"},
            {"sudo": "/usr/bin/sudo", "gnome-terminal": "/usr/bin/gnome-terminal"},
            {"curl": "/usr/bin/curl", "gnome-terminal": "/usr/bin/gnome-terminal"},
        )
        for tools in cases:
            with self.subTest(tools=sorted(tools)):
                with patch("victus_hub.app.main_window.shutil.which", side_effect=tools.get), \
                     patch("victus_hub.app.main_window.subprocess.Popen") as popen:
                    with self.assertRaises(OSError):
                        start_update("v1.0.2")
                popen.assert_not_called()
