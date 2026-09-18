"""Battery transitions and asynchronous reply handling without hardware writes."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtDBus import QDBusMessage, QDBusVariant
from PySide6.QtWidgets import QApplication

from victus_hub.services.battery_power import BatteryPowerController, BATTERY_POWER_SAVE_KEY
from victus_hub.app.main_window import MainWindow


class TestBatteryPower(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        settings = patch("victus_hub.services.battery_power.QSettings")
        self.settings = settings.start().return_value
        self.settings.value.return_value = True
        self.addCleanup(settings.stop)
        with patch("victus_hub.services.battery_power.QDBusConnection"), \
                patch("victus_hub.services.battery_power.QDBusServiceWatcher"):
            self.controller = BatteryPowerController()
        self.addCleanup(self.controller.deleteLater)
        self.request = Mock()
        self.controller.power_save_requested.connect(self.request)
        self.restore = Mock()
        self.controller.restore_requested.connect(self.restore)

    def reply(self, value, generation=None, error=False):
        message = Mock()
        message.type.return_value = (QDBusMessage.MessageType.ErrorMessage if error
                                     else QDBusMessage.MessageType.ReplyMessage)
        message.arguments.return_value = [QDBusVariant(value)]
        watcher = Mock()
        watcher.reply.return_value = message
        self.controller._read_finished(
            watcher, self.controller._generation if generation is None else generation,
        )
        watcher.deleteLater.assert_called_once()

    def test_startup_and_transitions_only(self):
        self.reply(True)
        self.request.assert_called_once()
        self.reply(True)  # unrelated events must not override a manual choice
        self.request.assert_called_once()
        self.reply(False)
        self.restore.assert_called_once()
        self.reply(False)
        self.restore.assert_called_once()
        self.request.assert_called_once()
        self.reply(True)
        self.assertEqual(self.request.call_count, 2)

    def test_disabled_ignores_pending_reply_and_does_not_read(self):
        self.controller.set_enabled(False)
        self.settings.setValue.assert_called_with(BATTERY_POWER_SAVE_KEY, False)
        self.controller._bus.asyncCall.assert_not_called()
        self.reply(True)
        self.request.assert_not_called()

    def test_enabling_checks_current_source_and_persists(self):
        self.controller._on_battery = True
        with patch.object(self.controller, "refresh") as refresh:
            self.controller.set_enabled(True)
        refresh.assert_called_once()
        self.settings.setValue.assert_called_with(BATTERY_POWER_SAVE_KEY, True)
        self.reply(True)
        self.request.assert_called_once()

    def test_stale_failed_and_invalid_replies_do_not_switch(self):
        self.reply(True, generation=-1)
        with self.assertLogs("victus_hub.services.battery_power", level="WARNING"):
            self.reply(True, error=True)
        self.reply("false")
        self.reply(False)
        self.request.assert_not_called()

    def test_only_upower_properties_trigger_read(self):
        message = Mock()
        with patch.object(self.controller, "refresh") as refresh:
            message.arguments.return_value = ["unrelated.interface"]
            self.controller._properties_changed(message)
            refresh.assert_not_called()
            message.arguments.return_value = ["org.freedesktop.UPower"]
            self.controller._properties_changed(message)
            refresh.assert_called_once()

    def test_upower_restart_preserves_battery_transition(self):
        self.reply(True)
        with patch.object(self.controller, "refresh"):
            self.controller._owner_changed("org.freedesktop.UPower", "old", "")
            self.controller._owner_changed("org.freedesktop.UPower", "", "new")
        self.reply(True)
        self.request.assert_called_once()
        self.reply(False)
        self.restore.assert_called_once()

    def test_disabling_requests_restore(self):
        self.reply(True)
        self.controller.set_enabled(False)
        self.restore.assert_called_once()


class TestBatteryProfileRestoration(unittest.TestCase):
    def setUp(self):
        self.window = SimpleNamespace(
            _selected_profile=2, _profile_before_battery=None,
            _home_page=Mock(), _fans_page=Mock(),
            _apply_accent=Mock(), _sync_tray_checks=Mock(),
        )
        self.window._on_profile_select = lambda *args, **kwargs: MainWindow._on_profile_select(
            self.window, *args, **kwargs,
        )
        self.current = 2
        setter = patch("victus_hub.app.main_window.api.set_system_profile",
                       side_effect=self.set_profile)
        self.setter = setter.start()
        self.addCleanup(setter.stop)
        getter = patch("victus_hub.app.main_window.api.get_current_profile",
                       side_effect=lambda: self.current)
        getter.start()
        self.addCleanup(getter.stop)

    def set_profile(self, profile):
        self.current = profile

    def unplug(self):
        MainWindow._on_battery_power_save(self.window)

    def plug_in(self):
        MainWindow._restore_battery_profile(self.window)

    def test_restores_previous_mode_once(self):
        for previous in (1, 2):
            with self.subTest(previous=previous):
                self.current = previous
                self.unplug()
                self.assertEqual(self.current, 0)
                self.unplug()  # duplicate requests must not replace the saved mode
                self.plug_in()
                self.assertEqual(self.current, previous)
                self.setter.reset_mock()
                self.plug_in()
                self.setter.assert_not_called()

    def test_manual_selection_cancels_restoration_including_power_save(self):
        for manual in (0, 1, 2):
            with self.subTest(manual=manual):
                self.current = 2
                self.unplug()
                self.window._on_profile_select(manual)
                self.plug_in()
                self.assertEqual(self.current, manual)

    def test_external_change_then_power_save_cancels_restoration(self):
        self.unplug()
        for profile in (1, 0):
            self.current = profile
            MainWindow._on_profile_changed(self.window, profile, "", "external")
        self.plug_in()
        self.assertEqual(self.current, 0)

    def test_power_save_confirmation_keeps_saved_mode(self):
        self.unplug()
        MainWindow._on_profile_changed(self.window, 0, "", "external")
        self.plug_in()
        self.assertEqual(self.current, 2)

    def test_already_power_save_does_not_schedule_restore(self):
        self.current = 0
        self.unplug()
        self.plug_in()
        self.setter.assert_not_called()

    def test_failed_automatic_switch_does_not_schedule_restore(self):
        self.setter.side_effect = RuntimeError("failed")
        with self.assertLogs("victus_hub.app.main_window", level="ERROR"):
            self.unplug()
        self.assertIsNone(self.window._profile_before_battery)
        self.setter.reset_mock()
        self.plug_in()
        self.setter.assert_not_called()

    def test_failed_manual_switch_preserves_restore(self):
        self.unplug()
        self.setter.side_effect = RuntimeError("failed")
        with self.assertLogs("victus_hub.app.main_window", level="ERROR"):
            self.window._on_profile_select(1)
        self.setter.side_effect = self.set_profile
        self.plug_in()
        self.assertEqual(self.current, 2)
