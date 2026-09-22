"""Exercise uninstall cleanup in a temporary filesystem, never on the host."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class TestUninstallCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        HOME=str(self.root / "home"), SUDO_USER="", TEST_LOG=str(self.log))
        self.command("id", "printf '0\\n'")
        self.command("sudo", 'exec "$@"')

    def command(self, name, body):
        command = self.bin / name
        command.write_text("#!/bin/bash\n" + body + "\n")
        command.chmod(0o755)

    def script(self, relative, prefixes):
        text = (REPO / relative).read_text()
        for prefix in prefixes:
            text = text.replace(prefix, str(self.root) + prefix)
        script = self.root / relative
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text)
        return script

    def run_script(self, script, *args):
        return subprocess.run(["/bin/bash", str(script), *args], env=self.env,
                              capture_output=True, text=True)

    def test_rgb_unloads_even_after_dkms_deleted_module_file(self):
        script = self.script("kernel/hp-kbd-rgb/scripts/uninstall",
                             ("/sys/", "/etc/", "/lib/modules"))
        (self.root / "sys/module/hp_kbd_rgb").mkdir(parents=True)
        self.command("modprobe", 'printf "Module not found\\n" >&2; exit 1')
        self.command("rmmod", 'printf "rmmod %s\\n" "$*" >> "$TEST_LOG"; exit "${TEST_RMMOD_EXIT:-0}"')
        self.command("depmod", "exit 0")
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.log.read_text(), "rmmod hp_kbd_rgb\n")
        # A genuine failure (e.g. a busy module) must still stop uninstall.
        self.env["TEST_RMMOD_EXIT"] = "1"
        result = self.run_script(script)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Uninstalled hp-kbd-rgb", result.stdout)

    def test_all_icon_sizes_removed_before_cache_refresh(self):
        script = self.script("scripts/uninstall", ("/usr/local", "/usr/lib", "/usr/share",
                                                    "/etc/", "/opt/", "/var/", "/run/"))
        icons = self.root / "usr/share/icons/hicolor"
        for size in (16, 22, 24, 32, 48, 64, 128, 256, 512, 1024):
            icon = icons / f"{size}x{size}/apps/victus-hub.png"
            icon.parent.mkdir(parents=True)
            icon.touch()
        other_icon = icons / "16x16/apps/another-app.png"
        other_icon.touch()
        self.command("systemctl", "exit 1")
        self.command("python3", "exit 1")
        self.command("gtk-update-icon-cache",
                     f'for icon in "{icons}"/*/apps/victus-hub.png; do\n'
                     '  [ ! -e "$icon" ] || { printf "stale icon\\n" >> "$TEST_LOG"; exit 1; }\n'
                     'done\nprintf "cache refreshed\\n" >> "$TEST_LOG"')
        result = self.run_script(script, "--app-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(icons.glob("*/apps/victus-hub.png"))), 0)
        self.assertTrue(other_icon.exists())
        self.assertEqual(result.stdout.count("Removing icon "), 10)
        self.assertEqual(self.log.read_text(), "cache refreshed\n")
        result = self.run_script(script, "--app-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), "cache refreshed\n")
