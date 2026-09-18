"""Push-stream shortcuts and keyboard actions without real input/hardware writes."""

import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from victus_hub.features.keyboard.shortcut import (
    HARDWARE_SHORTCUTS_KEY, KEY_FN, KEY_LEFTCTRL, KEY_LEFTSHIFT,
    KEY_RIGHTCTRL, KEY_RIGHTSHIFT, KeybindSettings,
)
from victus_hub.features.keyboard.lighting import LightingSettings
from victus_hub.services.shortcut_controller import ShortcutController
from victus_hub.pages.keyboard_page import KeyboardPage
from victus_hub.app.main_window import MainWindow
from victus_hubd import daemon


class QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate())


class TestShortcutStream(QtTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = str(Path(self.directory.name) / "daemon.sock")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen()
        self.addCleanup(self.server.close)
        self.settings = QSettings(str(Path(self.directory.name) / "settings.ini"), QSettings.IniFormat)
        self.settings.setValue(HARDWARE_SHORTCUTS_KEY, True)
        for patcher in (
            patch("victus_hub.services.shortcut_controller.SOCKET_PATH", path),
            patch("victus_hub.services.shortcut_controller.QSettings", return_value=self.settings),
            patch("victus_hub.services.shortcut_controller.read_keybind_settings",
                  return_value=KeybindSettings(key=149)),
            patch.object(daemon, "_held_mods", set()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.worker = threading.Thread(target=self.serve, daemon=True)
        self.worker.start()
        self.controller = ShortcutController()
        self.addCleanup(self.close_client)
        self.brightness = []
        self.animation = []
        self.performance = Mock()
        self.triggered = Mock()
        self.controller.brightness_step.connect(self.brightness.append)
        self.controller.animation_step.connect(self.animation.append)
        self.controller.performance_cycle.connect(self.performance)
        self.controller.triggered.connect(self.triggered)
        self.wait_for(lambda: self.controller._subscribed)

    def serve(self):
        connection, _ = self.server.accept()
        self.connection = connection
        daemon.handle_client(connection, Mock())

    def close_client(self):
        self.controller.shutdown()
        self.worker.join(timeout=2)
        self.assertFalse(self.worker.is_alive())
        self.controller.deleteLater()

    def press(self, code):
        daemon._record_key_event(code, 1)
        daemon._record_key_event(code, 0)

    def test_burst_delivers_each_action_without_timers(self):
        daemon._record_key_event(KEY_LEFTCTRL, 1)
        daemon._record_key_event(KEY_LEFTSHIFT, 1)
        for code in (103, 103, 108, 105, 106, 50):
            self.press(code)
        daemon._record_key_event(KEY_LEFTSHIFT, 0)
        daemon._record_key_event(KEY_LEFTCTRL, 0)
        self.wait_for(lambda: self.performance.call_count == 1)
        self.assertEqual(self.brightness, [1, 1, -1])
        self.assertEqual(self.animation, [-1, 1])
        self.assertEqual(self.controller.findChildren(QTimer), [])

    def test_plain_keys_disabled_shortcuts_and_repeat_are_ignored(self):
        for code in (103, 108, 105, 106, 50):
            self.press(code)
        self.controller.set_hardware_enabled(False)
        daemon._record_key_event(KEY_LEFTCTRL, 1)
        daemon._record_key_event(KEY_LEFTSHIFT, 1)
        self.press(103)
        daemon._record_key_event(103, 2)
        daemon._record_key_event(KEY_LEFTSHIFT, 0)
        daemon._record_key_event(KEY_LEFTCTRL, 0)
        self.press(149)  # ordering barrier and existing program shortcut
        self.wait_for(lambda: self.triggered.call_count == 1)
        self.assertEqual(self.brightness, [])
        self.assertEqual(self.animation, [])
        self.performance.assert_not_called()
        self.assertFalse(self.settings.value(HARDWARE_SHORTCUTS_KEY, type=bool))

    def test_capture_takes_priority_and_preserves_left_modifiers(self):
        captured = Mock()
        self.controller.captured.connect(captured)
        self.controller.start_capture()
        daemon._record_key_event(KEY_LEFTCTRL, 1)
        daemon._record_key_event(KEY_LEFTSHIFT, 1)
        self.press(103)
        daemon._record_key_event(KEY_LEFTSHIFT, 0)
        daemon._record_key_event(KEY_LEFTCTRL, 0)
        self.wait_for(lambda: captured.call_count == 1)
        captured.assert_called_once_with(frozenset({KEY_LEFTCTRL, KEY_LEFTSHIFT}), 103)
        self.assertEqual(self.brightness, [])
        self.assertFalse(self.controller.is_capturing())

    def test_fn_incomplete_right_hand_and_extra_modifiers_are_ignored(self):
        for mods in (
            (KEY_FN,), (KEY_LEFTCTRL,), (KEY_LEFTSHIFT,),
            (KEY_RIGHTCTRL, KEY_LEFTSHIFT), (KEY_LEFTCTRL, KEY_RIGHTSHIFT),
            (KEY_RIGHTCTRL, KEY_RIGHTSHIFT),
            (KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_FN),
        ):
            for mod in mods:
                daemon._record_key_event(mod, 1)
            for code in (103, 108, 105, 106, 50):
                self.press(code)
            for mod in mods:
                daemon._record_key_event(mod, 0)
        self.press(149)
        self.wait_for(lambda: self.triggered.call_count == 1)
        self.assertEqual(self.brightness, [])
        self.assertEqual(self.animation, [])
        self.performance.assert_not_called()

    def test_stream_reconnects_after_disconnect_without_replaying_keys(self):
        self.press(149)
        self.wait_for(lambda: self.triggered.call_count == 1)
        old_connection = self.connection
        old_worker = self.worker
        old_connection.shutdown(socket.SHUT_RDWR)
        old_worker.join(timeout=2)
        self.assertFalse(old_worker.is_alive())
        self.worker = threading.Thread(target=self.serve, daemon=True)
        self.worker.start()
        self.wait_for(lambda: self.connection is not old_connection and self.controller._subscribed)
        self.press(149)
        self.wait_for(lambda: self.triggered.call_count == 2)


class TestKeyboardShortcutActions(QtTestCase):
    def setUp(self):
        for patcher in (
            patch("victus_hub.pages.keyboard_page.api.get_keyboard_zone_count", return_value=4),
            patch("victus_hub.pages.keyboard_page.keyboard_rgb_module", return_value=("test", "")),
            patch("victus_hub.pages.keyboard_page.read_lighting_settings",
                  return_value=LightingSettings(enabled=True, brightness=0)),
            patch("victus_hub.pages.keyboard_page.write_lighting_settings"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.page = KeyboardPage()
        self.addCleanup(self.page.deleteLater)

    def test_brightness_quarters_clamp_and_sync_slider(self):
        levels = []
        self.page.brightness_changed.connect(levels.append)
        for _ in range(6):
            self.page.step_brightness(1)
        self.assertEqual(levels, [64, 128, 191, 255])
        self.assertEqual(self.page._brightness_slider.value(), 255)
        for _ in range(6):
            self.page.step_brightness(-1)
        self.assertEqual(levels[4:], [191, 128, 64, 0])
        self.page._brightness_slider.setValue(100)
        self.page.step_brightness(1)
        self.assertEqual(self.page._settings.brightness, 128)

    def test_brightness_up_enables_disabled_lighting(self):
        self.page._on_effect_link(0)
        self.page.step_brightness(1)
        self.assertTrue(self.page._settings.enabled)
        self.assertEqual(self.page._settings.brightness, 64)

    def test_effects_cycle_through_off_in_both_directions(self):
        first = self.page._effect_items[1][0]
        last = self.page._effect_items[-1][0]
        self.page._on_effect_link(1)
        changed = Mock()
        self.page.effect_changed.connect(changed)
        enabled = Mock()
        self.page.enabled_changed.connect(enabled)
        self.page.step_animation(-1)
        self.assertFalse(self.page._settings.enabled)
        enabled.assert_called_with(False)
        changed.assert_not_called()
        self.page.step_animation(-1)
        self.assertTrue(self.page._settings.enabled)
        enabled.assert_called_with(True)
        changed.assert_called_with(last)
        self.page.step_animation(1)
        self.assertFalse(self.page._settings.enabled)
        enabled.assert_called_with(False)
        self.page.step_animation(1)
        self.assertTrue(self.page._settings.enabled)
        enabled.assert_called_with(True)
        changed.assert_called_with(first)

    def test_performance_uses_actual_profile_when_hidden_and_wraps(self):
        window = Mock(_selected_profile=0)
        with patch("victus_hub.app.main_window.api.get_current_profile", return_value=2):
            MainWindow._cycle_profile(window)
        window._on_profile_select.assert_called_once_with(0)

    def test_failed_profile_selection_preserves_mode_and_restores_controls(self):
        window = Mock(_selected_profile=1)
        with patch("victus_hub.app.main_window.api.set_system_profile",
                   side_effect=RuntimeError("authorization denied")), \
                self.assertLogs("victus_hub.app.main_window", level="ERROR"):
            MainWindow._on_profile_select(window, 2)
        self.assertEqual(window._selected_profile, 1)
        window._home_page.set_selected_profile.assert_called_once_with(1)
        window._sync_tray_checks.assert_called_once()
        window._apply_accent.assert_not_called()
        window._fans_page.set_edit_profile.assert_not_called()

    def test_profile_selection_updates_ui_after_backend_succeeds(self):
        window = Mock(_selected_profile=1)

        def apply(index):
            self.assertEqual(index, 2)
            self.assertEqual(window._selected_profile, 1)
            window._home_page.set_selected_profile.assert_not_called()

        with patch("victus_hub.app.main_window.api.set_system_profile", side_effect=apply):
            MainWindow._on_profile_select(window, 2)
        self.assertEqual(window._selected_profile, 2)
        window._home_page.set_selected_profile.assert_called_once_with(2)
        window._apply_accent.assert_called_once_with(2)
        window._sync_tray_checks.assert_called_once()
        window._fans_page.set_edit_profile.assert_called_once_with(2)
