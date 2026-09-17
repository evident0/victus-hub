"""Battery transitions and asynchronous reply handling without hardware writes."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtDBus import QDBusMessage, QDBusVariant
from PySide6.QtWidgets import QApplication

from victus_hub.services.battery_power import BatteryPowerController, BATTERY_POWER_SAVE_KEY


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
        self.reply(False)  # plugging in leaves the profile alone
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
