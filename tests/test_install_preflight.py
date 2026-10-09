"""Installer dependency guidance and optional RGB, without system changes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class TestPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin))
        self.command("uname", "printf 'test-kernel\\n'")
        self.command("id", "printf '0\\n'")
        self.command("python3", "exit 0")
        self.command("systemctl", "exit 0")
        self.command("loginctl", "exit 0")
        (self.bin / "grep").symlink_to(shutil.which("grep"))
        (self.root / "run/systemd/system").mkdir(parents=True)
        (self.root / "proc").mkdir()
        (self.root / "proc/cpuinfo").write_text("GenuineIntel\n")
        text = (REPO / "scripts/preflight.sh").read_text()
        for prefix in (
            "/usr/lib/x86_64-linux-gnu",
            "/lib/x86_64-linux-gnu",
            "/usr/lib64",
            "/lib64",
            "/run/systemd",
            "/lib/modules",
            "/usr/src",
            "/sys/firmware",
            "/proc/cpuinfo",
        ):
            text = text.replace(prefix, str(self.root) + prefix)
        self.script = self.root / "preflight"
        self.script.write_text(text + '\npreflight "${1:-0}"\n')

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def run_check(self, app_only=0):
        return subprocess.run(["/bin/bash", str(self.script), str(app_only)], env=self.env, capture_output=True, text=True)

    def test_missing_packages_are_reported_without_installing(self):
        for manager, label in (("apt-get", "Ubuntu/Mint"), ("pacman", "Arch"), ("dnf", "Fedora")):
            with self.subTest(manager=manager):
                self.command(manager, 'printf "PACKAGE MANAGER WAS EXECUTED"; exit 99')
                result = self.run_check()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(label, result.stderr)
                self.assertIn("dkms", result.stderr)
                self.assertIn("headers/devel for running kernel test-kernel", result.stderr)
                self.assertNotIn("PACKAGE MANAGER WAS EXECUTED", result.stdout + result.stderr)
                (self.bin / manager).unlink()

    def plant_shared_libs(self):
        libdir = self.root / "usr/lib64"
        libdir.mkdir(parents=True, exist_ok=True)
        for name in (
            "libsystemd.so.0",
            "libgtk-4.so.1",
            "libadwaita-1.so.0",
        ):
            (libdir / name).write_text("")

    def test_app_only_does_not_require_build_dependencies(self):
        self.command("gdbus", "exit 0")
        self.command("cargo", "exit 0")
        self.command("rustc", "exit 0")
        self.plant_shared_libs()
        result = self.run_check(1)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_cargo_is_explicit(self):
        self.command("gdbus", "exit 0")
        self.command("rustc", "exit 0")
        self.plant_shared_libs()
        result = self.run_check(1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cargo", result.stderr)

    def test_split_ubuntu_mint_headers_reject_unsupported_kernel(self):
        self.command("uname", "printf '6.8.0-100-generic\\n'")
        header = self.root / "usr/src/linux-headers-6.8.0-100/include/linux/platform_profile.h"
        header.parent.mkdir(parents=True)
        header.write_text("/* Older platform_profile API */\n")
        result = self.run_check()
        self.assertIn("lacks devm_platform_profile_register", result.stderr)
        header.write_text("devm_platform_profile_register\n")
        self.assertNotIn("lacks devm_platform_profile_register", self.run_check().stderr)

    def test_missing_rgb_does_not_abort_installation(self):
        self.command("modprobe", 'printf "No such device\\n" >&2; exit 1')
        self.command("rm", "exit 0")
        result = subprocess.run(
            ["/bin/bash", "-c", 'set -e; SUDO=(); source "$1"; load_optional_rgb; printf "CONTINUED\\n"',
             "bash", str(REPO / "scripts/optional-rgb.sh")],
            env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CONTINUED", result.stdout)
        self.assertIn("continuing without RGB", result.stderr)


class TestDkmsHooks(unittest.TestCase):
    def test_new_kernel_gets_override_and_initramfs_refresh(self):
        for location in ("updates/dkms", "updates", "extra"):
            with self.subTest(location=location):
                self.check_hooks(default_priority=False, location=location)

    def test_normal_dkms_priority_removes_unnecessary_override(self):
        for location in ("updates/dkms", "updates", "extra"):
            with self.subTest(location=location):
                self.check_hooks(default_priority=True, location=location)

    def check_hooks(self, default_priority, location):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            log = root / "log"
            tool, args = {
                "updates/dkms": ("update-initramfs", "-u -k future-kernel"),
                "updates": ("mkinitcpio", "-P"),
                "extra": ("dracut", "--force --kver future-kernel"),
            }[location]
            for command in ("depmod", tool):
                path = binary / command
                path.write_text(f'#!/bin/bash\nprintf "{command} %s\\n" "$*" >> "$TEST_LOG"\n')
                path.chmod(0o755)
            for command in ("install", "rm", "readlink"):
                (binary / command).symlink_to(shutil.which(command))
            module = root / "lib/modules/future-kernel" / location / "hp-wmi.ko.zst"
            module.parent.mkdir(parents=True)
            module.touch()
            modinfo = binary / "modinfo"
            modinfo.write_text(f'#!/bin/bash\nprintf "%s\\n" "{module if default_priority else root / "stock/hp-wmi.ko"}"\n')
            modinfo.chmod(0o755)
            conf = root / "etc/depmod.d/victus-hub-hp-wmi-future-kernel.conf"
            conf.parent.mkdir(parents=True)
            conf.write_text("override hp-wmi future-kernel extra\n")
            env = dict(os.environ, PATH=str(binary), TEST_LOG=str(log))
            for hook in ("dkms-post-install", "dkms-post-remove"):
                text = (REPO / "kernel" / hook).read_text()
                for prefix in ("/lib/modules", "/etc/depmod.d"):
                    text = text.replace(prefix, str(root) + prefix)
                script = root / hook
                script.write_text(text)
                result = subprocess.run(["/bin/bash", str(script), "future-kernel", "hp-wmi"], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                conf = root / "etc/depmod.d/victus-hub-hp-wmi-future-kernel.conf"
                if hook.endswith("install") and not default_priority:
                    self.assertEqual(conf.read_text(), f"override hp-wmi future-kernel {location}\n")
                else:
                    self.assertFalse(conf.exists())
            expected = f"depmod -a future-kernel\n{tool} {args}\n" * 2
            if not default_priority:
                expected = "depmod -a future-kernel\n" + expected
            self.assertEqual(log.read_text(), expected)


class TestDkmsInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("dkms-install", "optional-rgb.sh"):
            text = (REPO / "scripts" / name).read_text()
            for prefix in ("/usr/src", "/etc/", "/lib/modules", "/sys/"):
                text = text.replace(prefix, str(self.root) + prefix)
            (scripts / name).write_text(text)
        (scripts / "secure-boot.sh").write_text(
            'secure_boot_prepare() { SECURE_BOOT=${TEST_SECURE_BOOT:-0}; MOK_PENDING=${TEST_MOK_PENDING:-0}; '
            'MOK_KEY=test-key; MOK_CERT=test-cert; }\n'
        )
        for module in ("hp-wmi", "hp-kbd-rgb"):
            directory = self.root / "kernel" / module
            directory.mkdir(parents=True)
            (directory / f"{module}.c").write_text("/* Test source */\n")
            shutil.copyfile(REPO / "kernel" / module / "Makefile", directory / "Makefile")
        for name in ("dkms-post-install", "dkms-post-remove"):
            shutil.copyfile(REPO / "kernel" / name, self.root / "kernel" / name)
        platform = self.root / "sys/devices/platform/hp-wmi"
        platform.mkdir(parents=True)
        (platform / "gpu_mux_supported_names").touch()
        self.log = self.root / "log"
        self.env = dict(os.environ, PATH=str(self.bin), TEST_ROOT=str(self.root), TEST_LOG=str(self.log))
        self.command("sudo", 'exec "$@"')
        self.command("id", "printf '0\\n'")
        self.command("uname", "printf 'test-kernel\\n'")
        self.command(
            "modprobe",
            'printf "modprobe %s\\n" "$*" >> "$TEST_LOG"\n'
            '[ "$1" != hp-kbd-rgb ] || { printf "No such device\\n" >&2; exit 1; }',
        )
        self.command(
            "modinfo",
            'printf "modinfo %s\\n" "$*" >> "$TEST_LOG"\n'
            'case " $* " in\n'
            '  *" -n "*)\n'
            '    module=${@: -1}\n'
            '    if [ "${TEST_MODINFO_KIND:-extra}" = stock ]; then\n'
            '      printf "%s/lib/modules/test-kernel/kernel/drivers/%s.ko\\n" "$TEST_ROOT" "$module"\n'
            '    else\n'
            '      printf "%s/%s/modules/test-kernel/extra/%s.ko%s\\n" "$TEST_ROOT" "${TEST_MODULE_LIB:-lib}" "$module" "${TEST_MODULE_SUFFIX:-}"\n'
            '    fi\n'
            '    ;;\n'
            '  *)\n'
            '    module=${@: -1}\n'
            '    if [ -f "$TEST_ROOT/$module-signed" ]; then printf "Victus-Hub\\n";\n'
            '    else printf "%s\\n" "${TEST_SIGNER:-Victus-Hub}"; fi ;;\n'
            'esac',
        )
        self.command(
            "objcopy",
            '[ -z "${TEST_OBJCOPY_FAIL:-}" ] || exit 1\n'
            'dest=\nprev=\n'
            'for arg in "$@"; do\n'
            '  if [ "$prev" = "--dump-section" ]; then dest=${arg#*=}; fi\n'
            '  prev=$arg\n'
            'done\n'
            '[ -n "$dest" ] || exit 1\n'
            'printf "%s" "${TEST_BUILD_NOTE:-same-build}" > "$dest"',
        )
        dkms = self.bin / "dkms"
        dkms.write_text(f"#!{sys.executable}\n" + '''
import os, pathlib, sys
root = pathlib.Path(os.environ['TEST_ROOT'])
args = sys.argv[1:]
if args == ['--version']:
    print('dkms-3.2.0')
    sys.exit(0)
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(' '.join(args) + '\\n')
action = args[0]
package = args[args.index('-m') + 1]
version = args[args.index('-v') + 1]
state = root / (package + '-state')
stored_version, current = '', ''
if state.exists():
    parts = state.read_text().splitlines()
    if len(parts) >= 2:
        stored_version, current = parts[0], parts[1]
if action == 'status':
    if current and stored_version == version:
        suffix = os.environ.get('TEST_STATUS_SUFFIX', '') if current == 'installed' else ''
        print(f'{package}/{version}, test-kernel, x86_64: {current}{suffix}')
elif action == 'add':
    state.write_text(f'{version}\\nadded\\n')
elif action == 'build':
    if os.environ.get('TEST_BUILD_FAIL'):
        sys.exit(10)
    state.write_text(f'{version}\\nbuilt\\n')
    if os.environ.get('TEST_SECURE_BOOT') == '1':
        (root / (package.removeprefix('victus-hub-') + '-signed')).touch()
elif action == 'install':
    module = package.removeprefix('victus-hub-')
    path = root / 'lib/modules/test-kernel/extra' / (module + '.ko')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    state.write_text(f'{version}\\ninstalled\\n')
elif action == 'remove':
    state.unlink(missing_ok=True)
''')
        dkms.chmod(0o755)
        for tool in ("sha256sum", "cut", "grep", "install", "tee", "mktemp", "cat", "cmp", "rm", "dirname", "readlink"):
            dest = self.bin / tool
            if not dest.exists():
                dest.symlink_to(shutil.which(tool))

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def install(self):
        # Rewritten copy under the temp root. PATH contains only stubs and the tools it calls.
        script = self.root / "scripts/dkms-install"
        self.assertIn(str(self.root), script.read_text())
        return subprocess.run(["/bin/bash", str(script)], env=self.env, capture_output=True, text=True)

    def test_registration_reinstall_and_unsupported_rgb(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("continuing without RGB", result.stderr)
        self.assertFalse((self.root / "etc/modules-load.d/hp-kbd-rgb.conf").exists())
        sources = list((self.root / "usr/src").iterdir())
        self.assertEqual(len(sources), 2)
        for source in sources:
            conf = source / "dkms.conf"
            result = subprocess.run(
                ["/bin/bash", "-c", 'kernelver=future-kernel; source "$1"; printf "%s\\n" "$AUTOINSTALL" "${MAKE[0]}" "$POST_INSTALL" "$POST_REMOVE"',
                 "bash", str(conf)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("yes\nmake KDIR=", result.stdout)
            self.assertIn("/future-kernel/build", result.stdout)
            self.assertIn("dkms-post-install future-kernel", result.stdout)
        before_build = self.log.read_text().count("build -m")
        before_install = self.log.read_text().count("install -m")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().count("build -m"), before_build)
        self.assertEqual(self.log.read_text().count("install -m"), before_install)
        self.assertIn("hp-wmi is already installed; loading it without rebuilding", result.stdout)
        self.assertIn("hp-kbd-rgb is unchanged and not in use; skipping rebuild and reload", result.stdout)

    def test_build_failure_is_not_hidden_as_optional_rgb(self):
        self.env['TEST_BUILD_FAIL'] = '1'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("install -m", self.log.read_text())

    def load_module(self, sys_name, build_note="same-build"):
        notes = self.root / "sys/module" / sys_name / "notes"
        notes.mkdir(parents=True)
        (notes / ".note.gnu.build-id").write_text(build_note)
        ko = self.root / "lib/modules/test-kernel/extra" / f"{sys_name.replace('_', '-')}.ko"
        ko.parent.mkdir(parents=True, exist_ok=True)
        ko.touch()

    def again(self, note="same-build", source=None, **env):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.load_module("hp_wmi", note)
        self.load_module("hp_kbd_rgb", note)
        if source is not None:
            (self.root / "kernel/hp-wmi/hp-wmi.c").write_text(source)
        self.env.update(env)
        self.log.write_text("")
        return self.install()

    def test_unchanged_loaded_module_skips_rebuild_and_reload(self):
        result = self.again()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Kernel modules unchanged; skipped rebuild and reload.", result.stdout)
        log = self.log.read_text()
        self.assertNotIn("build -m", log)
        self.assertNotIn("install -m", log)
        self.assertNotIn("modprobe", log)

    def test_loaded_module_reloads_without_rebuild_when_build_id_differs(self):
        result = self.again(note="old-build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("loaded module differs; reloading", result.stdout)
        log = self.log.read_text()
        self.assertNotIn("build -m", log)
        self.assertNotIn("install -m", log)
        self.assertIn("modprobe -r hp-wmi", log)
        self.assertIn("modprobe hp-wmi", log)
        self.assertIn("modprobe -r hp-kbd-rgb", log)

    def test_unreadable_build_id_reloads_without_rebuilding(self):
        result = self.again(TEST_OBJCOPY_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertNotIn("build -m", log)
        self.assertIn("modprobe -r hp-wmi", log)

    @unittest.skipUnless(all(shutil.which(tool) for tool in ("cc", "ld", "objcopy", "xz", "gzip", "zstd")),
                         "requires binutils, compiler, and module compression tools")
    def test_real_build_ids_match_for_plain_and_compressed_modules(self):
        self.assertEqual(self.install().returncode, 0)
        source = self.root / "module.c"
        source.write_text("int identity = 1;\n")
        obj, ko = self.root / "module.o", self.root / "module.ko"
        subprocess.run(["cc", "-c", str(source), "-o", str(obj)], check=True)
        subprocess.run(["ld", "-r", "--build-id", str(obj), "-o", str(ko)], check=True)
        note = self.root / "build-id"
        subprocess.run(["objcopy", "--dump-section", f".note.gnu.build-id={note}", str(ko), "/dev/null"], check=True)
        (self.bin / "objcopy").unlink()
        for tool in ("objcopy", "xz", "gzip", "zstd"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        for module in ("hp_wmi", "hp_kbd_rgb"):
            self.load_module(module)
            (self.root / "sys/module" / module / "notes/.note.gnu.build-id").write_bytes(note.read_bytes())
        for suffix, compressor in (("", None), (".xz", "xz"), (".gz", "gzip"), (".zst", "zstd")):
            with self.subTest(suffix=suffix):
                data = subprocess.check_output([compressor, "-c", str(ko)]) if compressor else ko.read_bytes()
                for module in ("hp-wmi", "hp-kbd-rgb"):
                    (self.root / "lib/modules/test-kernel/extra" / f"{module}.ko{suffix}").write_bytes(data)
                self.env["TEST_MODULE_SUFFIX"] = suffix
                self.log.write_text("")
                result = self.install()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Kernel modules unchanged", result.stdout)
                self.assertNotIn("modprobe", self.log.read_text())

    def test_missing_required_module_and_autoload_entry_are_restored(self):
        self.assertEqual(self.install().returncode, 0)
        autoload = self.root / "etc/modules-load.d/hp-wmi.conf"
        autoload.unlink()
        self.log.write_text("")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(autoload.read_text(), "hp-wmi\n")
        log = self.log.read_text()
        self.assertNotIn("build -m", log)
        self.assertIn("modprobe hp-wmi", log)
        self.assertNotIn("modprobe hp-kbd-rgb", log)

    def test_module_path_alias_skips_rebuild_and_reload(self):
        usr = self.root / "usr"
        usr.mkdir()
        (usr / "lib").symlink_to(self.root / "lib", target_is_directory=True)
        result = self.again(TEST_MODULE_LIB="usr/lib")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertNotIn("install -m", log)
        self.assertNotIn("modprobe", log)

    def test_secure_boot_rebuilds_cached_modules_with_wrong_signer(self):
        result = self.again(TEST_SECURE_BOOT="1", TEST_SIGNER="old-key")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        for module in ("hp-wmi", "hp-kbd-rgb"):
            self.assertRegex(log, rf"build -m victus-hub-{module} .* --force")
            self.assertIn(f"install -m victus-hub-{module}", log)

    def test_secure_boot_pending_enrollment_defers_loading(self):
        self.env.update(TEST_SECURE_BOOT="1", TEST_MOK_PENDING="1")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deferred until MOK enrollment", result.stdout)
        self.assertNotIn("modprobe", self.log.read_text())

    def test_changed_source_rebuilds_and_reloads_only_that_module(self):
        result = self.again(source="/* changed */\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Kernel module hp-kbd-rgb is unchanged; skipping rebuild and reload.", result.stdout)
        log = self.log.read_text()
        self.assertIn("build -m victus-hub-hp-wmi", log)
        self.assertIn("install -m victus-hub-hp-wmi", log)
        self.assertIn("modprobe -r hp-wmi", log)
        self.assertNotIn("build -m victus-hub-hp-kbd-rgb", log)
        self.assertNotIn("install -m victus-hub-hp-kbd-rgb", log)
        self.assertNotIn("modprobe hp-kbd-rgb", log)
        self.assertNotIn("modprobe -r hp-kbd-rgb", log)

    def test_archived_original_module_still_counts_as_installed(self):
        result = self.again(**{"TEST_STATUS_SUFFIX": " (Original modules exist)"})
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertNotIn("build -m", log)
        self.assertNotIn("install -m", log)
        self.assertNotIn("modprobe", log)

    def test_stock_module_selection_is_reinstalled(self):
        result = self.again(TEST_MODINFO_KIND="stock")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Custom hp-wmi is not the selected module; reinstalling.", result.stdout)
        log = self.log.read_text()
        self.assertIn("install -m victus-hub-hp-wmi", log)
        self.assertIn("install -m victus-hub-hp-kbd-rgb", log)

    def test_installed_module_mismatch_is_reinstalled(self):
        result = self.again(**{"TEST_STATUS_SUFFIX": " (Differences between built and installed modules)"})
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertNotIn("build -m", log)
        self.assertIn("install -m victus-hub-hp-wmi", log)
        self.assertIn("install -m victus-hub-hp-kbd-rgb", log)
        self.assertIn("modprobe -r hp-wmi", log)


class TestAppInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        scripts = self.root / "scripts"
        scripts.mkdir()
        text = (REPO / "scripts/install").read_text()
        for prefix in ("/opt/", "/etc/", "/usr/local/", "/usr/share/", "/usr/lib/", "/run/"):
            text = text.replace(prefix, str(self.root) + prefix)
        (scripts / "install").write_text(text)
        (scripts / "stop-gui").write_text("# test stub\n")
        (scripts / "kmod-prompts.sh").write_text("")
        (scripts / "preflight.sh").write_text('preflight() { return "${TEST_PREFLIGHT_EXIT:-0}"; }\n')
        (self.root / "data").mkdir()
        for name in ("victus-hubd.service", "victus-hub-sleep"):
            shutil.copyfile(REPO / "data" / name, self.root / "data" / name)
        self.log = self.root / "log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", TEST_LOG=str(self.log))
        for name, body in {
            "sudo": 'exec "$@"',
            "chown": "exit 0",
            "systemctl": 'printf "systemctl %s\\n" "$*" >> "$TEST_LOG"',
            "update-desktop-database": "exit 0",
        }.items():
            path = self.bin / name
            path.write_text("#!/bin/bash\n" + body + "\n")
            path.chmod(0o755)
        python = self.bin / "python3"
        python.write_text(f"#!{sys.executable}\n" + '''
import sys
args = sys.argv[1:]
if len(args) == 1 and args[0].endswith('/scripts/stop-gui'):
    with open(__import__('os').environ['TEST_LOG'], 'a') as log:
        log.write('stop-gui\\n')
    sys.exit(0)
sys.stderr.write('unexpected python3 call: %s\\n' % args)
sys.exit(1)
''')
        python.chmod(0o755)
        cargo = self.bin / "cargo"
        cargo.write_text(f"#!{sys.executable}\n" + '''
import os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write('cargo ' + ' '.join(args) + '\\n')
code = int(os.environ.get('TEST_CARGO_EXIT', '0'))
if code != 0:
    sys.exit(code)
manifest = None
for index, arg in enumerate(args):
    if arg == '--manifest-path' and index + 1 < len(args):
        manifest = pathlib.Path(args[index + 1])
if manifest is None:
    sys.exit('cargo stub expected --manifest-path')
release = manifest.parent / 'target' / 'release'
release.mkdir(parents=True, exist_ok=True)
for name in ('victus-hub', 'victus-hubd'):
    binary = release / name
    binary.write_text('#!/bin/sh\\nprintf "%s\\n" "$*" >> "$TEST_LOG"\\nexit 0\\n')
    binary.chmod(0o755)
''')
        cargo.chmod(0o755)

    def install(self):
        return subprocess.run(["/bin/bash", str(self.root / "scripts/install"), "--app-only"], env=self.env, capture_output=True, text=True)

    def test_cargo_failure_preserves_current_and_does_not_restart_service(self):
        app = self.root / "opt/victus-hub-app"
        previous = app / "releases/previous"
        previous.mkdir(parents=True)
        (app / "current").symlink_to(previous)
        self.env['TEST_CARGO_EXIT'] = '1'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertNotIn("systemctl", self.log.read_text())
        self.assertEqual(list((app / "releases").iterdir()), [previous])

    def test_success_installs_isolated_service_and_launcher(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.root / "opt/victus-hub-app"
        self.assertTrue((app / "current/bin/victus-hubd").is_file())
        self.assertTrue((app / "current/bin/victus-hub").is_file())
        log = self.log.read_text()
        self.assertIn("cargo build --release --manifest-path", log)
        self.assertLess(log.index("stop-gui"), log.index("cargo build --release"))
        self.assertIn("systemctl restart victus-hubd.service", log)
        self.assertIn('--socket', log)
        service = (self.root / "etc/systemd/system/victus-hubd.service").read_text()
        self.assertIn("/current/bin/victus-hubd", service)
        self.assertNotIn("python", service)
        self.assertIn("DeviceAllow=char-nvidia* rw", service)
        self.assertNotIn("PYTHONPATH", service)
        self.assertNotIn("@ROOT_DIR@", service)
        launcher = self.root / "usr/local/bin/victus-hub"
        self.assertTrue(os.access(launcher, os.X_OK))
        launcher_text = launcher.read_text()
        self.assertIn("exec env GSK_RENDERER=cairo ", launcher_text)
        self.assertIn("current/bin/victus-hub", launcher_text)
        self.assertNotIn("python", launcher_text)
        self.assertNotIn("GDK_BACKEND", launcher_text)
        for path in ("usr/share/applications/victus-hub.desktop",
                     "usr/share/dbus-1/services/io.github.evident0.VictusHub.service"):
            text = (self.root / path).read_text()
            self.assertIn("GSK_RENDERER=cairo", text)
            self.assertIn("VICTUS_HUB_DEBUG_LEVEL=0", text)
            self.assertNotIn("QT_QPA_PLATFORM", text)
            self.assertNotIn("GDK_BACKEND", text)

    def _seed_old_releases(self):
        releases = self.root / "opt/victus-hub-app/releases"
        older = releases / "older"
        old = releases / "old"
        older.mkdir(parents=True)
        old.mkdir()
        os.utime(older, (1, 1))
        os.utime(old, (2, 2))
        return releases

    def test_default_keeps_only_the_release_current_points_at(self):
        releases = self._seed_old_releases()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        current = (releases.parent / "current").resolve()
        self.assertEqual({path.name for path in releases.iterdir()}, {current.name})
        self.assertNotIn(current.name, {"old", "older"})

    def test_keep_releases_leaves_the_newest_previous_install(self):
        releases = self._seed_old_releases()
        self.env["VICTUS_HUB_KEEP_RELEASES"] = "2"
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        current = (releases.parent / "current").resolve()
        self.assertEqual({path.name for path in releases.iterdir()}, {current.name, "old"})

    def test_preflight_failure_makes_no_installation_changes(self):
        self.env['TEST_PREFLIGHT_EXIT'] = '1'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "opt").exists())
        self.assertFalse(self.log.exists())

    def test_icons_prefer_magick_and_support_legacy_convert(self):
        # Hide any host ImageMagick binaries so the legacy case is real.
        for name in ("dirname", "id", "install", "mktemp", "chmod", "rm", "ln",
                     "mv", "tee", "cp", "cmp", "seq", "readlink", "touch",
                     "find", "sort", "cut"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env["PATH"] = str(self.bin)
        icon = self.root / "victus_hub/resources/icons/logoV.png"
        icon.parent.mkdir(parents=True)
        icon.touch()
        for name in ("magick", "convert"):
            command = self.bin / name
            command.write_text(
                f'#!/bin/bash\nprintf "{name} %s\\n" "$*" >> "$TEST_LOG"\n'
                'output=${@: -1}; touch "${output#png32:}"\n'
            )
            command.chmod(0o755)
        cache = self.bin / "gtk-update-icon-cache"
        cache.write_text("#!/bin/bash\nexit 0\n")
        cache.chmod(0o755)
        for converter in ("magick", "convert"):
            with self.subTest(converter=converter):
                self.log.write_text("")
                result = self.install()
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = self.log.read_text().splitlines()
                self.assertEqual(sum(line.startswith(converter + " ") for line in lines), 10)
                if converter == "magick":
                    self.assertFalse(any(line.startswith("convert ") for line in lines))
                    (self.bin / "magick").unlink()
                for size in (16, 22, 24, 32, 48, 64, 128, 256, 512, 1024):
                    self.assertTrue((self.root / f"usr/share/icons/hicolor/{size}x{size}/apps/victus-hub.png").exists())
