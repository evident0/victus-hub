"""Development helpers use isolated Python and check build tools before changes."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class TestDevelopmentScripts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        self.log = self.root / "log"
        self.env = dict(os.environ, PATH=str(self.bin), TEST_LOG=str(self.log))
        for tool in ("dirname", "env"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.command("id", "printf '0\\n'")

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def test_ui_dependency_failure_stops_before_service_changes(self):
        self.prepare_ui()
        self.env["TEST_PIP_EXIT"] = "1"
        result = self.run_script("ui-test")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("systemctl", self.log.read_text())

    def test_ui_uses_venv_and_preserves_explicit_qt_platform(self):
        self.prepare_ui()
        for platform in (None, "xcb"):
            with self.subTest(platform=platform):
                self.env.pop("QT_QPA_PLATFORM", None)
                if platform:
                    self.env["QT_QPA_PLATFORM"] = platform
                result = self.run_script("ui-test")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"platform={platform or 'wayland;xcb'}", result.stdout)
                self.assertIn(f"-m pip install -e {self.root}", self.log.read_text())

    def prepare_ui(self):
        for name in ("ui-test", "debug-level.sh"):
            text = (REPO / "scripts" / name).read_text()
            for prefix in ("/etc/", "/run/"):
                text = text.replace(prefix, str(self.root) + prefix)
            (self.scripts / name).write_text(text)
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_LOG"\n'
                          'if [ "$2" = pip ]; then exit "${TEST_PIP_EXIT:-0}"; fi\n'
                          'printf "platform=%s\\n" "$QT_QPA_PLATFORM"\n')
        python.chmod(0o755)
        self.command("python3", '[ "$1 $2" = "-m venv" ]')
        self.command("systemctl", 'printf "systemctl %s\\n" "$*" >> "$TEST_LOG"; exit 1')

    def test_ryzenadj_requires_pkg_config_before_cloning(self):
        (self.scripts / "ryzenadj-install").write_text((REPO / "scripts/ryzenadj-install").read_text())
        self.command("grep", "exit 0")  # Simulate an AMD CPU.
        for tool in ("cmake", "gcc", "g++", "make", "git"):
            self.command(tool, 'printf "unexpected build action\\n" >> "$TEST_LOG"; exit 1')
        result = self.run_script("ryzenadj-install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pkg-config", result.stderr)
        self.assertFalse(self.log.exists())

    def run_script(self, name):
        return subprocess.run(["/bin/bash", str(self.scripts / name)], env=self.env,
                              capture_output=True, text=True)
