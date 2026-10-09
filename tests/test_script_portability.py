"""Development helpers preview the Rust UI without touching installed services."""

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
        self.env["TEST_CARGO_EXIT"] = "1"
        result = self.run_script("ui-test")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("systemctl", self.log.read_text())

    def test_ui_uses_cargo_offline_and_preserves_explicit_gdk_backend(self):
        self.prepare_ui()
        for platform in (None, "x11"):
            with self.subTest(platform=platform):
                self.env.pop("GDK_BACKEND", None)
                if platform:
                    self.env["GDK_BACKEND"] = platform
                result = self.run_script("ui-test")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"backend={platform or ''}", result.stdout)
                self.assertIn("offline=1 no_dbus=1 zones=4", result.stdout)
                self.assertIn("cargo run --release -p victus-hub", self.log.read_text())
                self.assertNotIn("systemctl", self.log.read_text())

    def prepare_ui(self):
        for name in ("ui-test", "libadwaita-pkgconfig.sh"):
            text = (REPO / "scripts" / name).read_text()
            for prefix in ("/etc/", "/run/"):
                text = text.replace(prefix, str(self.root) + prefix)
            (self.scripts / name).write_text(text)
        self.command("cargo", 'printf "cargo %s\\n" "$*" >> "$TEST_LOG"\n'
                     'printf "backend=%s offline=%s no_dbus=%s zones=%s\\n" '
                     '"${GDK_BACKEND:-}" "$VICTUS_HUB_OFFLINE" "$VICTUS_HUB_NO_DBUS" "$VICTUS_HUB_EMULATE_ZONES"\n'
                     'exit "${TEST_CARGO_EXIT:-0}"')
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
