"""Profile event handling tests without changing the host power profile."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtDBus import QDBusMessage, QDBusVariant
from PySide6.QtWidgets import QApplication

from victus_hub.services.profile_watcher import ProfileWatcher


class TestProfileWatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.connection_patch = patch(
            "victus_hub.services.profile_watcher.QDBusConnection",
        )
        connection_type = self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.bus = connection_type.systemBus.return_value
        self.bus.isConnected.return_value = True
        self.watcher = ProfileWatcher()
        self.addCleanup(self.watcher.deleteLater)
        self.events = []
        self.watcher.profile_changed.connect(
            lambda *args: self.events.append(args),
        )

    @staticmethod
    def reply(value, *, error=False):
        message = Mock()
        message.type.return_value = (
            QDBusMessage.MessageType.ErrorMessage
            if error else QDBusMessage.MessageType.ReplyMessage
        )
        message.arguments.return_value = [value]
        message.errorMessage.return_value = "unavailable"
        return message

    def test_tuned_start_reads_once_and_subscribes_to_events(self):
        self.bus.connect.return_value = True
        self.bus.call.return_value = self.reply("throughput-performance")

        with patch(
            "victus_hub.services.profile_watcher.profiles.current_profile_reading",
        ) as cli_read:
            self.watcher.start()

        self.assertEqual(self.watcher._backend, "tuned")
        self.assertEqual(
            self.events,
            [(2, "throughput-performance", "tuned D-Bus")],
        )
        cli_read.assert_not_called()
        self.bus.call.assert_called_once()

    def test_tuned_event_publishes_only_successful_known_profiles(self):
        self.watcher._on_tuned_profile_changed(
            "powersave", True, "",
        )
        self.watcher._on_tuned_profile_changed(
            "powersave", False, "failed",
        )
        self.watcher._on_tuned_profile_changed(
            "unknown", True, "",
        )

        self.assertEqual(self.events, [(0, "powersave", "tuned D-Bus")])

    def test_power_profiles_daemon_properties_event_reads_active_profile(self):
        self.watcher._backend = "power-profiles-daemon"
        self.watcher._read_power_profiles_profile = Mock(return_value="balanced")
        message = Mock()
        message.arguments.return_value = [
            "org.freedesktop.PowerProfiles", {}, [],
        ]

        self.watcher._on_power_profiles_changed(message)

        self.assertEqual(self.events, [(1, "balanced", "power-profiles-daemon D-Bus")])
        self.watcher._read_power_profiles_profile.assert_called_once()

    def test_failed_event_backend_uses_cli_only_as_slow_fallback(self):
        self.bus.connect.return_value = True
        self.bus.call.side_effect = [
            self.reply(None, error=True),
            self.reply(QDBusVariant("balanced")),
        ]

        self.watcher.start()

        self.assertEqual(self.watcher._backend, "power-profiles-daemon")
        self.assertEqual(
            self.events,
            [(1, "balanced", "power-profiles-daemon D-Bus")],
        )
