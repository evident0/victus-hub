"""Exercise installation with real kmod indexing and simulated module loading."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


TOOLS = ("cc", "ld", "objcopy", "depmod", "modinfo", "bash", "openssl")
REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "requires kmod and binutils")
class TestHpWmiInstallation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.release = os.uname().release
        self.module_dir = self.root / "kernel/hp-wmi"
        self.scripts = self.module_dir / "scripts"
        self.scripts.mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        modules = self.root / "lib/modules" / self.release
        (modules / "build").mkdir(parents=True)
        (modules / "modules.order").touch()
        (modules / "modules.builtin").touch()
        self.stock = modules / "kernel/drivers/platform/x86/hp/hp-wmi.ko"
        self.custom = self.module_dir / "hp-wmi.ko"
        self.stock.parent.mkdir(parents=True)
        # Different ELF build IDs, same module name. No actual kernel code runs.
        for number, target in enumerate((self.stock, self.custom)):
            source = self.root / f"module{number}.c"
            source.write_text(
                'const char name[] __attribute__((section(".modinfo"))) = "name=hp_wmi";\n'
                f"int identity = {number};\n"
            )
            obj = self.root / f"module{number}.o"
            subprocess.run(["cc", "-c", str(source), "-o", str(obj)], check=True)
            subprocess.run(["ld", "-r", "--build-id", str(obj), "-o", str(target)], check=True)
        self.stock_bytes = self.stock.read_bytes()
        config = self.root / "etc/depmod.d"
        config.mkdir(parents=True)
        # Reproduce a distro where kernel/ wins unless explicitly overridden.
        (config / "search.conf").write_text("search kernel extra\n")
        (self.root / "etc/modules-load.d").mkdir(parents=True)
        helpers = self.root / "scripts"
        helpers.mkdir()
        helper = (REPO / "scripts/secure-boot.sh").read_text()
        helper = re.sub(
            r"/var/lib/|/lib/modules|/usr/src/|/sys/|/dev/tty",
            lambda match: str(self.root) + match.group(),
            helper,
        )
        (helpers / "secure-boot.sh").write_text(helper)
        for name in ("install", "uninstall"):
            text = (REPO / "kernel/hp-wmi/scripts" / name).read_text()
            text = re.sub(
                r"/usr/lib/modules|/lib/modules|/etc/|/sys/",
                lambda match: str(self.root) + match.group(),
                text,
            )
            (self.scripts / name).write_text(text)

        self.write_command("sudo", 'exec "$@"')
        self.write_command("make", "exit 0")
        self.write_command(
            "mokutil",
            'case "$1" in\n'
            ' --sb-state) printf "SecureBoot %s\\n" "${TEST_SB_STATE:-disabled}";;\n'
            ' --test-key) [ "${TEST_ENROLLED:-0}" = 1 ];;\n'
            ' --list-new) if [ "${TEST_PENDING:-1}" = 1 ]; then openssl x509 -inform DER -in "$TEST_ROOT/var/lib/victus-hub/mok/MOK.der" -noout -fingerprint -sha1; fi;;\n'
            ' --import) printf "import\\n" >> "$TEST_ROOT/imports"; exit "${TEST_IMPORT_EXIT:-0}";;\n'
            ' *) exit 2;;\n'
            'esac',
        )
        sign_dir = modules / "build/scripts"
        sign_dir.mkdir()
        self.sign_file = sign_dir / "sign-file"
        self.sign_file.write_text(
            '#!/bin/bash\nset -eu\n'
            '[ "${TEST_SIGN_FAIL:-0}" = 0 ] || exit 1\n'
            'test -s "$2" && test -s "$3"\n'
            'printf "test signature" >> "$4"\n'
        )
        self.sign_file.chmod(0o755)
        self.write_command(
            "depmod",
            f'exec "{shutil.which("depmod")}" -b "$TEST_ROOT" -C "$TEST_ROOT/etc/depmod.d" "$@"',
        )
        self.write_command(
            "modinfo",
            'if [ "${FORCE_STOCK_LOOKUP:-0}" = 1 ]; then printf "%s\\n" "$TEST_STOCK"; exit; fi\n'
            f'exec "{shutil.which("modinfo")}" -b "$TEST_ROOT" "$@"',
        )
        self.write_command(
            "modprobe",
            'module="$TEST_ROOT/sys/module/hp_wmi"\n'
            'platform="$TEST_ROOT/sys/devices/platform/hp-wmi"\n'
            'if [ "${1:-}" = -r ]; then rm -rf "$module" "$platform"; exit; fi\n'
            'selected=$(modinfo -n hp-wmi)\n'
            'if [ "${FORCE_STOCK_LOAD:-0}" = 1 ]; then selected="$TEST_STOCK"; fi\n'
            'mkdir -p "$module/notes" "$platform"\n'
            'objcopy --dump-section ".note.gnu.build-id=$module/notes/.note.gnu.build-id" "$selected" /dev/null\n'
            'case "$selected" in */extra/*) touch "$platform/gpu_mux_mode" "$platform/gpu_mux_supported_names";; esac',
        )
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            TEST_ROOT=str(self.root),
            TEST_STOCK=str(self.stock),
        )
        self.run_command("depmod", "-a", self.release)
        self.run_command("modprobe", "hp-wmi")

    def write_command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        path.chmod(0o755)

    def run_command(self, *args, check=True):
        return subprocess.run(args, env=self.env, text=True, capture_output=True, check=check)

    def test_install_overrides_stock_and_uninstall_restores_it(self):
        self.assertEqual(self.run_command("modinfo", "-n", "hp-wmi").stdout.strip(), str(self.stock))
        result = self.run_command("bash", str(self.scripts / "install"))
        self.assertIn("Verified custom module build ID and MUX interfaces", result.stdout)
        self.assertIn("/extra/hp-wmi.ko", self.run_command("modinfo", "-n", "hp-wmi").stdout)
        result = self.run_command("bash", str(self.scripts / "uninstall"))
        self.assertIn("Restored in-tree hp-wmi", result.stdout)
        self.assertEqual(self.run_command("modinfo", "-n", "hp-wmi").stdout.strip(), str(self.stock))
        self.assertEqual(self.stock.read_bytes(), self.stock_bytes)
        self.assertFalse(list((self.root / "etc/depmod.d").glob("victus-hub-*.conf")))
        self.assertFalse((self.root / "etc/modules-load.d/hp-wmi.conf").exists())
        self.assertFalse((self.root / "sys/devices/platform/hp-wmi/gpu_mux_mode").exists())

    def test_wrong_lookup_fails_before_unloading_stock(self):
        self.env["FORCE_STOCK_LOOKUP"] = "1"
        result = self.run_command("bash", str(self.scripts / "install"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Module lookup selected", result.stderr)
        self.assertNotIn("Unloading currently loaded", result.stdout)
        self.assertTrue((self.root / "sys/module/hp_wmi").is_dir())

    def test_wrong_loaded_build_is_not_reported_as_success(self):
        self.env["FORCE_STOCK_LOAD"] = "1"
        result = self.run_command("bash", str(self.scripts / "install"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("build ID does not match", result.stderr)
        self.assertNotIn("Installed and loaded", result.stdout)

    def test_secure_boot_pending_preserves_loaded_driver_and_signs_copy(self):
        self.env["TEST_SB_STATE"] = "enabled"
        original = self.custom.read_bytes()
        loaded_note = self.root / "sys/module/hp_wmi/notes/.note.gnu.build-id"
        stock_note = loaded_note.read_bytes()
        result = self.run_command("bash", str(self.scripts / "install"))
        self.assertIn("deferred until MOK enrollment", result.stdout)
        self.assertNotIn("Unloading currently loaded", result.stdout)
        self.assertEqual(loaded_note.read_bytes(), stock_note)
        installed = Path(self.run_command("modinfo", "-n", "hp-wmi").stdout.strip())
        self.assertEqual(installed.read_bytes(), original + b"test signature")
        self.assertEqual(self.custom.read_bytes(), original)
        self.assertTrue((self.root / "etc/modules-load.d/hp-wmi.conf").exists())
        key = self.root / "var/lib/victus-hub/mok/MOK.priv"
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(key.parent.stat().st_mode & 0o777, 0o700)
        original_key = key.read_bytes()
        self.env["TEST_ENROLLED"] = "1"
        result = self.run_command("bash", str(self.scripts / "install"))
        self.assertIn("Verified custom module build ID", result.stdout)
        self.assertEqual(key.read_bytes(), original_key)
        self.assertEqual(installed.read_bytes(), original + b"test signature")

    def test_secure_boot_signing_failure_preserves_installed_driver(self):
        self.run_command("bash", str(self.scripts / "install"))
        installed = Path(self.run_command("modinfo", "-n", "hp-wmi").stdout.strip())
        original = installed.read_bytes()
        self.env.update(TEST_SB_STATE="enabled", TEST_ENROLLED="1", TEST_SIGN_FAIL="1")
        result = self.run_command("bash", str(self.scripts / "install"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(installed.read_bytes(), original)
        self.assertNotIn("Unloading currently loaded", result.stdout)

    def test_secure_boot_missing_signer_fails_before_install(self):
        self.env["TEST_SB_STATE"] = "enabled"
        self.sign_file.unlink()
        result = self.run_command("bash", str(self.scripts / "install"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires scripts/sign-file", result.stderr)
        self.assertEqual(self.run_command("modinfo", "-n", "hp-wmi").stdout.strip(), str(self.stock))

    def test_secure_boot_import_uses_terminal_with_piped_stdin(self):
        self.env.update(TEST_SB_STATE="enabled", TEST_PENDING="0")
        tty = self.root / "dev/tty"
        tty.parent.mkdir()
        tty.touch()
        result = subprocess.run(
            ["bash", str(self.scripts / "install")],
            env=self.env, input="", text=True, capture_output=True, check=True,
        )
        self.assertEqual((self.root / "imports").read_text(), "import\n")
        self.assertIn("one-time MOK enrollment password", result.stdout)
        self.assertIn("deferred until MOK enrollment", result.stdout)

    def test_secure_boot_import_failure_stops_before_install(self):
        self.env.update(TEST_SB_STATE="enabled", TEST_PENDING="0", TEST_IMPORT_EXIT="1")
        tty = self.root / "dev/tty"
        tty.parent.mkdir()
        tty.touch()
        result = self.run_command("bash", str(self.scripts / "install"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Installing", result.stdout)
        self.assertEqual(self.run_command("modinfo", "-n", "hp-wmi").stdout.strip(), str(self.stock))

    def test_secure_boot_without_terminal_prints_manual_import(self):
        self.env.update(TEST_SB_STATE="enabled", TEST_PENDING="0")
        result = self.run_command("bash", str(self.scripts / "install"))
        self.assertIn("No terminal available", result.stdout)
        self.assertIn("sudo mokutil --import", result.stdout)
        self.assertIn("deferred until MOK enrollment", result.stdout)
        self.assertFalse((self.root / "imports").exists())

    def test_unknown_secure_boot_state_is_not_treated_as_disabled(self):
        self.env["TEST_SB_STATE"] = "unknown"
        result = self.run_command("bash", str(self.scripts / "install"), check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unrecognized Secure Boot state", result.stderr)
        self.assertNotIn("Installing", result.stdout)

    def test_keyboard_secure_boot_pending_does_not_unload(self):
        self.env["TEST_SB_STATE"] = "enabled"
        module_dir = self.root / "kernel/hp-kbd-rgb"
        scripts = module_dir / "scripts"
        scripts.mkdir(parents=True)
        source = module_dir / "hp-kbd-rgb.ko"
        source.write_bytes(self.custom.read_bytes())
        loaded = self.root / "sys/module/hp_kbd_rgb"
        loaded.mkdir()
        script = (REPO / "kernel/hp-kbd-rgb/scripts/install").read_text()
        script = re.sub(
            r"/lib/modules|/etc/|/sys/",
            lambda match: str(self.root) + match.group(),
            script,
        )
        (scripts / "install").write_text(script)
        result = self.run_command("bash", str(scripts / "install"))
        self.assertIn("deferred until MOK enrollment", result.stdout)
        self.assertNotIn("Unloading currently loaded", result.stdout)
        installed = self.root / "lib/modules" / self.release / "extra/hp-kbd-rgb.ko"
        self.assertEqual(installed.read_bytes(), source.read_bytes() + b"test signature")
        self.assertEqual(
            (self.root / "etc/modules-load.d/hp-kbd-rgb.conf").read_text(),
            "led-class-multicolor\nhp-kbd-rgb\n",
        )


if __name__ == "__main__":
    unittest.main()
