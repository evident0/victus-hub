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
        text = text.replace("/usr/bin/python3", str(self.bin / "python3"))
        for prefix in ("/run/systemd", "/lib/modules", "/sys/firmware", "/proc/cpuinfo"):
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

    def test_app_only_does_not_require_build_dependencies(self):
        self.command("gdbus", "exit 0")
        result = self.run_check(1)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_venv_is_explicit(self):
        self.command("python3", 'case "$*" in *ensurepip*) exit 1;; esac')
        result = self.run_check(1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("venv/ensurepip", result.stderr)

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
        (scripts / "secure-boot.sh").write_text("secure_boot_prepare() { SECURE_BOOT=0; MOK_PENDING=0; }\n")
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
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", TEST_ROOT=str(self.root), TEST_LOG=str(self.log))
        self.command("sudo", 'exec "$@"')
        self.command("uname", "printf 'test-kernel\\n'")
        self.command("modprobe", '[ "$1" != hp-kbd-rgb ] || { printf "No such device\\n" >&2; exit 1; }')
        dkms = self.bin / "dkms"
        dkms.write_text(f"#!{sys.executable}\n" + '''
import os, pathlib, sys
root = pathlib.Path(os.environ['TEST_ROOT'])
args = sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as log:
    log.write(' '.join(args) + '\\n')
action = args[0]
package = args[args.index('-m') + 1]
version = args[args.index('-v') + 1]
state = root / (package + '-state')
current = state.read_text() if state.exists() else ''
if action == 'status':
    if current:
        print(f'{package}/{version}, test-kernel, x86_64: {current}')
elif action == 'add':
    state.write_text('added')
elif action == 'build':
    if os.environ.get('TEST_BUILD_FAIL'):
        sys.exit(10)
    state.write_text('built')
elif action == 'install':
    module = package.removeprefix('victus-hub-')
    path = root / 'lib/modules/test-kernel/extra' / (module + '.ko')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    state.write_text('installed')
elif action == 'remove':
    state.unlink(missing_ok=True)
''')
        dkms.chmod(0o755)

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + body + "\n")
        path.chmod(0o755)

    def install(self):
        return subprocess.run(["/bin/bash", str(self.root / "scripts/dkms-install")], env=self.env, capture_output=True, text=True)

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
        before = self.log.read_text().count("build -m")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().count("build -m"), before)

    def test_build_failure_is_not_hidden_as_optional_rgb(self):
        self.env['TEST_BUILD_FAIL'] = '1'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("install -m", self.log.read_text())


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
        text = text.replace("/usr/bin/python3", str(self.bin / "python3"))
        (scripts / "install").write_text(text)
        (scripts / "kmod-prompts.sh").write_text("")
        (scripts / "preflight.sh").write_text('preflight() { return "${TEST_PREFLIGHT_EXIT:-0}"; }\n')
        (self.root / "data").mkdir()
        for name in ("victus-hubd.service", "victus-hub-sleep"):
            shutil.copyfile(REPO / "data" / name, self.root / "data" / name)
        (self.root / "usr/share/applications").mkdir(parents=True)
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
import pathlib, sys
args = sys.argv[1:]
assert args[:3] == ['-I', '-m', 'venv'], args
target = pathlib.Path(args[3]) / 'bin'
target.mkdir()
python = target / 'python'
python.write_text('#!/bin/bash\\nprintf "python %s\\n" "$*" >> "$TEST_LOG"\\ncase "$*" in *"-m pip"*) exit "${TEST_PIP_EXIT:-0}";; esac\\n')
python.chmod(0o755)
''')
        python.chmod(0o755)

    def install(self):
        return subprocess.run(["/bin/bash", str(self.root / "scripts/install"), "--app-only"], env=self.env, capture_output=True, text=True)

    def test_pip_failure_preserves_current_and_does_not_restart_service(self):
        app = self.root / "opt/victus-hub-app"
        previous = app / "releases/previous"
        previous.mkdir(parents=True)
        (app / "current").symlink_to(previous)
        self.env['TEST_PIP_EXIT'] = '1'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertNotIn("systemctl", self.log.read_text())
        self.assertEqual(list((app / "releases").iterdir()), [previous])

    def test_success_installs_isolated_service_and_launcher(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        app = self.root / "opt/victus-hub-app"
        self.assertTrue((app / "current/bin/python").is_file())
        log = self.log.read_text()
        self.assertIn("python -I -m pip install --upgrade", log)
        self.assertIn("--disable-pip-version-check", log)
        self.assertIn("systemctl restart victus-hubd.service", log)
        service = (self.root / "etc/systemd/system/victus-hubd.service").read_text()
        self.assertIn("/current/bin/python -I -m victus_hubd", service)
        self.assertIn("DeviceAllow=char-nvidia* rw", service)
        self.assertNotIn("PYTHONPATH", service)
        self.assertNotIn("@ROOT_DIR@", service)
        launcher = self.root / "usr/local/bin/victus-hub"
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertIn(" -I -m victus_hub", launcher.read_text())

    def test_preflight_failure_makes_no_installation_changes(self):
        self.env['TEST_PREFLIGHT_EXIT'] = '1'
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "opt").exists())
        self.assertFalse(self.log.exists())

    def test_icons_prefer_magick_and_support_legacy_convert(self):
        # Hide any host ImageMagick binaries so the legacy case is real.
        for name in ("dirname", "id", "install", "mktemp", "chmod", "rm", "ln",
                     "mv", "tee", "cp", "cmp", "seq", "readlink", "touch"):
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
