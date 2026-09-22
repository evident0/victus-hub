"""Test reload orchestration without installing packages or restarting services."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class TestReloadScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("reload", "debug-level.sh"):
            shutil.copyfile(REPO / "scripts" / name, scripts / name)
        (scripts / "install").write_text(
            'printf "install %s\\n" "$*" >> "$TEST_LOG"\n'
            'exit "${TEST_INSTALL_EXIT:-0}"\n'
        )
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in {
            "id": 'printf "1000\\n"',
            "python3": (
                'if [ "$1" = - ]; then exec "$TEST_PYTHON" "$@"; fi\n'
                'printf "launch %s debug=%s\\n" "$*" "$VICTUS_HUB_DEBUG_LEVEL" >> "$TEST_LOG"'
            ),
        }.items():
            command = bin_dir / name
            command.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
            command.chmod(0o755)
        self.log = self.root / "calls"
        self.env = dict(
            os.environ,
            PATH=f"{bin_dir}:{os.environ['PATH']}",
            XDG_RUNTIME_DIR=str(self.root),
            TEST_LOG=str(self.log),
            TEST_PYTHON=sys.executable,
        )

    def reload(self, *args):
        return subprocess.run(
            ["bash", str(self.root / "scripts/reload"), *args],
            env=self.env, capture_output=True, text=True, timeout=15,
        )

    def test_starts_ui_when_not_running(self):
        result = self.reload("3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), "install --app-only\nlaunch -m victus_hub debug=3\n")

    def test_stops_socket_owner_before_launching_replacement(self):
        path = self.root / "victus-hub-single-instance.sock"
        # A separate process owns the listening socket, just like the Qt UI.
        process = subprocess.Popen(
            [sys.executable, "-c", """
import socket, sys, time
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(sys.argv[1])
sock.listen()
print('ready', flush=True)
time.sleep(30)
""", str(path)],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            result = self.reload()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(process.wait(timeout=2), -15)
            self.assertIn("launch -m victus_hub", self.log.read_text())
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()

    def test_failed_install_does_not_launch_ui(self):
        self.env["TEST_INSTALL_EXIT"] = "1"
        self.assertNotEqual(self.reload().returncode, 0)
        self.assertEqual(self.log.read_text(), "install --app-only\n")

    def test_invalid_debug_level_does_not_install(self):
        self.assertEqual(self.reload("4").returncode, 2)
        self.assertFalse(self.log.exists())
