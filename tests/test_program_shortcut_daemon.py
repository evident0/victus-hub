"""Persist and activate per-user shortcuts without launching the installed UI."""

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from victus_hub.features.keyboard.shortcut import KeybindSettings
from victus_hubd import daemon, desktop_activation
from victus_hubd.program_shortcuts import ProgramShortcuts


class TestProgramShortcuts(unittest.TestCase):
    def test_saved_per_user_and_clear_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bindings.json"
            store = ProgramShortcuts(path)
            store.set(1000, (), 149)
            store.set(1001, (29, 42), 30)
            self.assertEqual(ProgramShortcuts(path).get(1000), ((), 149))
            self.assertEqual(ProgramShortcuts(path).get(1001), ((29, 42), 30))
            self.assertTrue(store.matches_any((), 149))
            store.set(1000, (), 0)
            self.assertFalse(ProgramShortcuts(path).matches_any((), 149))
            with self.assertRaises(RuntimeError):
                store.set(1000, (), 30)  # ordinary typing is never a shortcut
            self.assertFalse(store.matches_any((), 30))
            self.assertEqual(json.loads(path.read_text())["1000"]["key"], 0)

    def test_socket_uses_authenticated_uid(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProgramShortcuts(Path(directory) / "bindings.json")
            runtime = SimpleNamespace(program_shortcuts=store)

            def request(uid, line):
                client, server = socket.socketpair()
                client.settimeout(2)
                peer = SimpleNamespace(uid=uid, authorized=lambda: True)
                with client, patch.object(daemon, "authenticate", return_value=peer):
                    worker = threading.Thread(target=daemon.handle_client,
                                              args=(server, Mock(), None, runtime))
                    worker.start()
                    client.sendall(line)
                    result = client.recv(1024)
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    return result

            self.assertTrue(request(1000, b'program-shortcut\t{"mods":[],"key":149}\n').startswith(b'OK'))
            self.assertEqual(store.get(1000), ((), 149))
            self.assertIsNone(store.get(1001))
            self.assertIn(b'Ctrl, Alt or Super',
                          request(1001, b'program-shortcut\t{"mods":[],"key":30}\n'))
            self.assertIn(b'desktop user', request(0, b'program-shortcut\t{"mods":[],"key":149}\n'))

    def test_on_demand_activation_coalesces_and_respects_active_user(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProgramShortcuts(Path(directory) / "bindings.json")
            store.set(1000, (), 149)
            runtime = SimpleNamespace(program_shortcuts=store, handle_key=Mock())
            started = threading.Event()
            release = threading.Event()
            peer = SimpleNamespace(uid=1000)

            def activate(_peer):
                started.set()
                self.assertTrue(release.wait(2))

            with patch.object(daemon, "_runtime", runtime), \
                 patch.object(daemon, "_held_mods", set()), \
                 patch.object(daemon, "active_desktop_peer", return_value=peer) as active, \
                 patch.object(desktop_activation, "activate", side_effect=activate) as open_ui:
                daemon._record_key_event(148, 1)
                active.assert_not_called()  # no session lookup or worker on ordinary keys
                daemon._record_key_event(149, 1)
                self.assertTrue(started.wait(2))
                daemon._record_key_event(149, 1)
                daemon._record_key_event(149, 2)  # repeat is not a new press
                self.assertEqual(open_ui.call_count, 1)
                release.set()
                deadline = time.monotonic() + 2
                while daemon._activation_lock.locked() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertFalse(daemon._activation_lock.locked())
                active.return_value = SimpleNamespace(uid=1001)
                daemon._record_key_event(149, 1)
                deadline = time.monotonic() + 2
                while daemon._activation_lock.locked() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(open_ui.call_count, 1)
                self.assertFalse(daemon._activation_lock.locked())
                active.return_value = None  # locked/inactive session
                daemon._record_key_event(149, 1)
                deadline = time.monotonic() + 2
                while daemon._activation_lock.locked() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(open_ui.call_count, 1)
                self.assertFalse(daemon._activation_lock.locked())

    def test_dbus_call_drops_to_active_user(self):
        peer = SimpleNamespace(uid=1000, authorized=lambda: True)
        with patch("victus_hubd.desktop_activation.Path.is_socket", return_value=True), \
             patch("victus_hubd.desktop_activation.pwd.getpwuid",
                   return_value=SimpleNamespace(pw_gid=100)), \
             patch("victus_hubd.desktop_activation.subprocess.run",
                   return_value=SimpleNamespace(returncode=0)) as run:
            desktop_activation.activate(peer)
            args, kwargs = run.call_args
            self.assertEqual(args[0][0], "/usr/bin/gdbus")
            self.assertIn("org.freedesktop.Application.Activate", args[0])
            self.assertEqual(kwargs["user"], 1000)
            self.assertEqual(kwargs["group"], 100)
            self.assertEqual(kwargs["extra_groups"], [])
            run.reset_mock()
            desktop_activation.activate(SimpleNamespace(uid=1000, authorized=lambda: False))
            run.assert_not_called()


class TestProgramShortcutSync(unittest.TestCase):
    def test_syncs_existing_setting_on_startup(self):
        from victus_hub import api

        saved = KeybindSettings(mods=(29, 42), key=30)
        with patch.object(api.daemon_client, "request_program_shortcut") as push, \
              patch("victus_hub.features.keyboard.shortcut.read_keybind_settings",
                    return_value=saved), \
             patch("victus_hub.features.keyboard.shortcut.write_keybind_settings") as write:
            api.sync_program_shortcut()
            push.assert_called_once_with((29, 42), 30)
            write.assert_not_called()
            saved.enabled = False
            api.sync_program_shortcut()
            self.assertTrue(saved.enabled)
            write.assert_called_once_with(saved)
            saved.enabled = False
            push.side_effect = RuntimeError("unavailable")
            write.reset_mock()
            api.sync_program_shortcut()
            self.assertFalse(saved.enabled)
            write.assert_not_called()

    def test_clearing_saves_daemon_and_local_setting(self):
        from victus_hub import api

        with patch.object(api.daemon_client, "request_program_shortcut") as push, \
              patch("victus_hub.features.keyboard.shortcut.write_keybind_settings") as write:
            api.set_program_shortcut(KeybindSettings(enabled=False, key=0))
            push.assert_called_once_with((), 0)
            write.assert_called_once_with(KeybindSettings(enabled=False, key=0))

    def test_settings_keep_previous_binding_when_daemon_rejects_change(self):
        from victus_hub.pages.settings_page import SettingsPage

        previous = KeybindSettings(key=149)
        page = Mock(_kb=previous)
        with patch("victus_hub.pages.settings_page.api.set_program_shortcut",
                   side_effect=RuntimeError("unavailable")), \
             patch("victus_hub.pages.settings_page.QMessageBox.warning") as warning:
            self.assertFalse(SettingsPage._save_shortcut(page, KeybindSettings(key=148)))
        self.assertEqual(page._kb, previous)
        page._shortcut_ctrl.cancel_capture.assert_called_once()
        warning.assert_called_once()
