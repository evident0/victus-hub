"""Authorization and event privacy at the actual Unix socket boundary."""

import os
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from victus_hubd import auth, daemon
from victus_hub.backend.shortcut_policy import validate_shortcut


class TestSessionAuthorization(unittest.TestCase):
    def properties(self, **changes):
        values = dict(User="1000", Active="yes", Remote="no", LockedHint="no", Type="wayland", Seat="seat0")
        values.update(changes)
        return SimpleNamespace(returncode=0, stdout="\n".join(f"{k}={v}" for k, v in values.items()))

    def test_only_active_unlocked_local_desktop_is_allowed(self):
        peer = auth.Peer(1000, "session-1")
        for changes, expected in (
            ({}, True), ({"Type": "x11"}, True), ({"Remote": "yes"}, False),
            ({"Active": "no"}, False), ({"LockedHint": "yes"}, False),
            ({"LockedHint": ""}, False), ({"User": "1001"}, False),
            ({"Type": "tty"}, False), ({"Seat": "seat1"}, False),
        ):
            with self.subTest(changes=changes), patch.object(auth.subprocess, "run", return_value=self.properties(**changes)):
                self.assertEqual(peer.authorized(), expected)

    def test_logind_failure_denies_access_and_root_remains_available(self):
        with patch.object(auth.subprocess, "run", side_effect=OSError):
            self.assertFalse(auth.Peer(1000, "1").authorized())
            self.assertFalse(auth.Peer(1000).authorized())
            self.assertTrue(auth.Peer(0).authorized())

    def test_credentials_come_from_socket_not_request_data(self):
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        with patch.object(auth, "_session_for_peer", return_value="1") as session, \
                patch.object(auth.Peer, "authorized", return_value=True):
            peer = auth.authenticate(server)
        self.assertEqual(peer.uid, os.getuid())
        if os.getuid():
            session.assert_called_once_with(os.getpid(), os.getuid())


class TestDaemonBoundary(unittest.TestCase):
    def request(self, request, authorized=True):
        client, server = socket.socketpair()
        client.settimeout(2)
        self.addCleanup(client.close)
        peer = Mock(uid=1000, authorized=lambda: authorized)
        with patch.object(daemon, "authenticate", return_value=peer), \
                patch.object(daemon.sysfs, "write_pwm_max") as write:
            worker = threading.Thread(target=daemon.handle_client, args=(server, Mock()))
            worker.start()
            client.sendall(request)
            response = client.recv(4096)
            worker.join(2)
            self.assertFalse(worker.is_alive())
            return response, write.call_count

    def test_unauthorized_request_never_reaches_hardware(self):
        response, writes = self.request(b"fan-max\n", authorized=False)
        self.assertIn(b"access denied", response)
        self.assertEqual(writes, 0)

    def test_authorized_request_and_exact_command_matching(self):
        response, writes = self.request(b"fan-max\n")
        self.assertTrue(response.startswith(b"OK"))
        self.assertEqual(writes, 1)
        response, writes = self.request(b"fan-max-not-a-command\n")
        self.assertIn(b"unsupported", response)
        self.assertEqual(writes, 0)

    def test_raw_keyboard_interfaces_are_removed(self):
        for request in (b"keyboard-events\n", b"keyboard-last-event\n"):
            response, _ = self.request(request)
            self.assertIn(b"unsupported", response)

    def test_sleep_hooks_require_root(self):
        for command in (b"prepare-sleep\n", b"resume\n"):
            response, _ = self.request(command)
            self.assertIn(b"sleep hooks require root", response)

    def test_program_shortcuts_cannot_subscribe_to_lighting_stream(self):
        response, _ = self.request(b'shortcut-events\t{"mods": [], "key": 30}\n')
        self.assertIn(b"unsupported request", response)
        for mods in ([], [42], [464]):
            with self.assertRaises(RuntimeError):
                validate_shortcut(mods, 30)
        self.assertEqual(validate_shortcut([], 149), ((), 149))
        self.assertEqual(validate_shortcut([29, 42], 30), ((29, 42), 30))
        response, _ = self.request(b'shortcut-events\t{"mods": [], "key": 149}\n')
        self.assertIn(b'unsupported request', response)

    def test_oversized_and_multiline_requests_are_rejected(self):
        with patch.object(daemon, "MAX_REQUEST_BYTES", 100):
            response, _ = self.request(b"a" * 101 + b"\n")
            self.assertIn(b"too large", response)
        response, writes = self.request(b"fan-max\nfan-auto\n")
        self.assertIn(b"one request", response)
        self.assertEqual(writes, 0)

    def test_lighting_stream_rechecks_lock_state(self):
        client, server = socket.socketpair()
        client.settimeout(0.05)
        peer = Mock()
        peer.authorized.return_value = True
        from victus_hub.features.keyboard.lighting import LightingSettings

        with patch.object(daemon, "_kbd_subscribers", {server: peer}):
            try:
                daemon._publish_lighting(LightingSettings(enabled=True, brightness=64))
                self.assertTrue(client.recv(1024).startswith(b"LIGHTING\t"))
                peer.authorized.return_value = False
                daemon._publish_lighting(LightingSettings(enabled=True, brightness=64))
                with self.assertRaises(socket.timeout):
                    client.recv(1024)
                peer.authorized.return_value = True
                daemon._publish_lighting(LightingSettings(enabled=True, brightness=64))
                self.assertTrue(client.recv(1024).startswith(b"LIGHTING\t"))
            finally:
                client.close()
                server.close()
