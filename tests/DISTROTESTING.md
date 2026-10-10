# Cross-distribution script testing

Keep this file uncommitted until the user explicitly requests otherwise.

Use this workflow when changing installers, uninstallers, DKMS hooks, desktop
launchers, dependency checks, or development scripts. Run the checks in rootless
Podman containers so package installation and simulated system changes stay
outside the host. The checkout is mounted read-only; build a writable copy
inside each container for the real Python package installation.

The current program is Rust/GTK; Python/Qt is the behavior reference. Run the
Rust workspace and display checks below as well as the Python/script checks.

## Latest run: 2026-10-09

After fixes, all three distributions passed:

| Distribution | Python / DKMS | GTK / libadwaita | Results |
| --- | --- | --- | --- |
| Ubuntu 24.04 | 3.12.3 / 3.0.11 | 4.14.5 / 1.5.0 | 102 Python tests, 133 ordinary Rust tests, and all 3 GTK display tests passed |
| Fedora 43 | 3.14.7 / 3.4.3 | 4.20.4 / 1.8.8 | 102 Python tests, 133 ordinary Rust tests, and all 3 GTK display tests passed |
| Arch latest | 3.14.7 / 3.4.4 | 4.24.1 / 1.10.0 | 102 Python tests, 133 ordinary Rust tests, and all 3 GTK display tests passed |

There were no Python skips. Cargo initially reports the three display tests as
ignored; each was then explicitly run and passed in a separate Xvfb process.
All three also passed shell syntax/ShellCheck, isolated installed Python imports,
Qt Wayland-to-X11 fallback, a fresh native Rust workspace build, and an actual
offline Rust/GTK window under Xvfb with `GDK_BACKEND=wayland,x11`.
Rust was 1.96.0, with one build job, debug information and incremental builds
disabled. Native smoke-test binaries used the development profile, not release.
Real dependency preflight passed with a simulated container-only systemd marker;
that does not verify a running systemd service.

Image digests used (architecture-specific where the image supplies a manifest list):

- Ubuntu: `sha256:f610ab94648195aa356059f5b41d6085c9d4d903c072430cdd1af7bdb646106b`
- Fedora: `sha256:960a01f0bee7738d852d25d36f48a3cf73b6a6d805eca4afabb14978df4dbc3e`
- Arch: `sha256:77b5aed28bc6b5535eefecc8afe7bcf23a64d591e99dc0ad793247c53eddd081`

The run fixed a GTK 4.14 shutdown loop: the sensor context-menu popover must be
unparented before its ListBox is destroyed. The UI regression test now checks
production cleanup instead of doing that cleanup itself. Python installer/UI
fixtures were updated for Cargo's temporary target directory and offline Rust
preview.

Disk space started at approximately 8.3 GiB free and stayed above 6.5 GiB on the
root filesystem. Containers/images were processed and removed one at a time;
all run-created container layers, package caches, Python environments, Cargo
artifacts, and temporary host runners/logs were removed. Existing host build
artifacts and Rust toolchains were preserved. Mint remains an Ubuntu-base proxy
check, without direct Cinnamon or laptop hardware validation.

## What was tested

The branch review used these images:

| Distribution | Image | Observed result |
| --- | --- | --- |
| Ubuntu 24.04 | `docker.io/library/ubuntu:24.04` | Package installation, isolated app/daemon imports, Qt X11 fallback, and expanded 100-test suite passed |
| Fedora 43 | `registry.fedoraproject.org/fedora:43` | Package installation, isolated imports, Qt X11 fallback, and 59-test suite passed; one session-bus test skipped |
| Arch | `docker.io/archlinux/archlinux:latest` | Package installation, isolated imports, Qt X11 fallback, and 59-test suite passed |
| Linux Mint 22 | No separate Mint image | Ubuntu/Python base and X11 compatibility checked through Ubuntu 24.04; no direct Mint desktop validation |

Shell syntax checks and ShellCheck also passed. The commands below run the
expanded suite on all three images; report the actual counts and skips from
each new run. Arch's `latest` tag and distro packages change over time, so
record image digests and Python/DKMS versions with results.

These checks exercise dependency availability, Python wheel installation,
installer behavior in temporary filesystems, real ELF build IDs/compression,
module indexing, and an actual Qt application under Xvfb. They do not prove
kernel-driver compilation/loading, booted systemd operation, physical hardware
control, Secure Boot/MOK enrollment, SELinux service behavior, or suspend/resume
on a laptop. Containers share the host kernel.

The current driver requires `devm_platform_profile_register`. Ubuntu 24.04 /
Mint 22's 6.8 base kernel lacks this API; a newer compatible kernel and matching
headers are required. Arch Secure Boot also needs a compatible key-enrollment
setup; the project workflow uses shim/MOK and DKMS 3 or newer.

## 1. Create disposable containers

Run these commands from the checkout in the same host shell. Podman, network
access, and enough disk space for packages/PySide6 are required. No host `sudo`
is needed; container root is mapped through rootless Podman.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUST_SYSROOT=$(rustc --print sysroot)
df -h "$REPO_ROOT" /tmp
podman ps -a --format '{{.Names}}'
podman images --format '{{.Repository}}:{{.Tag}} {{.ID}}'
```

Record existing images and containers so cleanup preserves other work. On a
space-constrained device, complete steps 1–4 for **one distribution at a time**.
Exclude `target/` from source copies (it was 8 GiB on this checkout), disable
pip caching, and clear distro package caches. Monitor both the container storage
filesystem and `/tmp`; stop if storage free space drops below 3 GiB. Bound test
logs (this run capped them at 2 MiB) and use timeouts for display tests.

Choose `ubuntu`, `fedora`, or `arch`, ensure its container name is unused, and
create only that container. Mount the existing Rust sysroot read-only to avoid
installing another toolchain in each image. Ubuntu 24.04's default Rust package
is too old for this project; the mounted toolchain must be Rust 1.90 or newer.

```bash
DISTRO=ubuntu
case "$DISTRO" in
  ubuntu) IMAGE=docker.io/library/ubuntu:24.04 ;;
  fedora) IMAGE=registry.fedoraproject.org/fedora:43 ;;
  arch) IMAGE=docker.io/archlinux/archlinux:latest ;;
  *) exit 2 ;;
esac
container="vh-review-$DISTRO"
podman pull "$IMAGE"
podman image inspect --format '{{.RepoDigests}}' "$IMAGE"
podman run -d --name "$container" --security-opt label=disable \
  --memory=2g --memory-swap=2g \
  -v "$REPO_ROOT:/src:ro" -v "$RUST_SYSROOT:/toolchain:ro" -w /src \
  --entrypoint /usr/bin/sleep "$IMAGE" infinity
```

`label=disable` allows the read-only bind mount on SELinux hosts without
relabeling the checkout. Do not add privileged mode or mount host `/sys`,
`/lib/modules`, `/etc`, `/opt`, or `/run` for these tests.

## 2. Install distribution dependencies

These commands change only the disposable containers. The Xvfb, Xauth, and
ShellCheck packages are test tools. Kernel headers are not needed for the
simulated module tests; real driver builds need a separate compatible-kernel
test environment.

### Ubuntu / Mint's Ubuntu base

```bash
podman exec vh-review-ubuntu bash -lc '
  apt-get update -qq &&
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    python3 python3-venv systemd libsystemd0 libglib2.0-bin \
    libdbus-1-3 libfontconfig1 libegl1 libgl1 \
    libwayland-client0 libwayland-cursor0 libwayland-egl1 libx11-xcb1 \
    libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
    libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 \
    libxcb-xinerama0 build-essential dkms mokutil openssl cmake \
    pkg-config libpci-dev kmod xz-utils gzip zstd git xvfb xauth shellcheck \
    libgtk-4-dev libadwaita-1-dev dbus-daemon x11-utils &&
  apt-get clean
'
```

### Fedora

```bash
podman exec vh-review-fedora bash -lc '
  dnf install -y -q --setopt=install_weak_deps=False \
    python3 python3-pip systemd systemd-libs glib2 dbus-libs fontconfig \
    mesa-libEGL libglvnd-glx libwayland-client libwayland-cursor libwayland-egl \
    libX11-xcb libxkbcommon libxkbcommon-x11 xcb-util-cursor xcb-util-wm \
    xcb-util-image xcb-util-keysyms xcb-util-renderutil libxcb \
    gcc gcc-c++ make binutils dkms mokutil openssl cmake pkgconf-pkg-config \
    pciutils-devel kmod xz gzip zstd git findutils diffutils \
    xorg-x11-server-Xvfb xorg-x11-xauth ShellCheck \
    gtk4-devel libadwaita-devel dbus-daemon xwininfo &&
  dnf clean all
'
```

### Arch

Use a full system upgrade with package installation to avoid an unsupported
partial upgrade of the rolling-release image.

```bash
podman exec vh-review-arch bash -lc '
  pacman -Syu --noconfirm --needed \
    python systemd glib2 dbus fontconfig mesa libglvnd wayland \
    libxkbcommon libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-image \
    xcb-util-keysyms xcb-util-renderutil libxcb base-devel git dkms mokutil \
    openssl cmake pciutils kmod xz gzip zstd \
    xorg-server-xvfb xorg-xauth shellcheck gtk4 libadwaita xorg-xwininfo &&
  pacman -Scc --noconfirm
'
```

## 3. Validate installation, scripts, and desktop fallback

Run this block after the selected distribution's dependency command succeeds.
Each container uses a
fresh system-Python virtual environment, installs the actual project and its
PySide6 dependency, then runs the same checks. No pytest dependency is needed.
The assertions and `check=True` calls make a failed check exit nonzero; stop
and inspect the failure before claiming the distro passed.

```bash
podman exec -i "$container" python3 - <<'PY'
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path('/src')
project = Path('/tmp/victus-review-project')
shutil.copytree(
    root, project, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', '.pytest_cache', 'target', 'graphify-out'),
)
print(f'Python: {sys.version}', flush=True)
subprocess.run(['dkms', '--version'], check=True)
subprocess.run([sys.executable, '-I', '-m', 'venv', '/tmp/check-venv'], check=True)
python = '/tmp/check-venv/bin/python'
subprocess.run([
    python, '-I', '-m', 'pip', 'install', '-q', '--no-cache-dir', '--disable-pip-version-check', str(project),
], check=True)
subprocess.run([
    python, '-I', '-c',
    'from victus_hubd import daemon; from victus_hub.main import main; '
    'print("Installed app and daemon imports passed")',
], check=True)
subprocess.run([
    python, '-m', 'unittest',
    'tests.test_install_preflight', 'tests.test_hp_wmi_installation',
    'tests.test_uninstall_cleanup', 'tests.test_script_portability',
    'tests.test_update_check', 'tests.test_application_activation',
    'tests.test_sensor_request', 'tests.test_daemon_runtime',
], cwd=root, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', QT_QPA_PLATFORM='offscreen'), check=True)

runtime = Path('/tmp/victus-qt-runtime')
runtime.mkdir(mode=0o700, exist_ok=True)
subprocess.run([
    'xvfb-run', '-a', 'env', 'QT_QPA_PLATFORM=wayland;xcb',
    f'XDG_RUNTIME_DIR={runtime}', python, '-I', '-c',
    'from PySide6.QtWidgets import QApplication; app=QApplication([]); '
    'assert app.platformName() == "xcb", app.platformName(); '
    'print("Wayland-to-X11 fallback passed")',
], check=True)

scripts = [root / 'install.sh', root / 'uninstall.sh']
scripts += sorted(p for p in (root / 'scripts').iterdir() if p.is_file() and p.name != 'stop-gui')
scripts += sorted((root / 'kernel').glob('dkms-post-*'))
scripts += sorted((root / 'kernel').glob('*/scripts/*'))
for script in scripts:
    subprocess.run(['bash', '-n', str(script)], check=True)
subprocess.run([
    'shellcheck', '-S', 'warning', '-e', 'SC1091,SC2034', '-s', 'bash', *map(str, scripts),
], check=True)
subprocess.run(['sh', '-n', str(root / 'data/victus-hub-sleep')], check=True)
subprocess.run(['shellcheck', str(root / 'data/victus-hub-sleep')], check=True)
print('All distro checks passed', flush=True)
PY

git diff --check
git status --short
```

- Qt may print that Wayland could not connect before successfully selecting
  `xcb`; the assertion checks that fallback really happened.
- Session-bus integration tests can skip when the container lacks a session
  bus. Include skips in the reported result.
- `SC1091` is excluded for dynamically sourced helpers, and `SC2034` for
  variables consumed by callers of shared shell libraries. Review those uses
  manually. Other ShellCheck warnings must be investigated.
- `scripts/stop-gui` is Python, not shell; application-activation tests exercise
  it. Do not include it in the Bash/ShellCheck input list.
- Do not execute the actual top-level install/uninstall commands against the
  host. The installer tests rewrite system paths into temporary directories.

### Current Rust workspace and GTK display tests

Use the writable project copy created above. Run Cargo as an unprivileged
container user: hardware tests intentionally use read-only files to simulate
write failures, and container root bypasses those permissions. Keep caches and
build output inside the disposable container.

```bash
podman exec "$container" mkdir -p /tmp/cargo /tmp/rust-build /tmp/victus-test-home
podman exec "$container" chown -R 65534:65534 \
  /tmp/victus-review-project /tmp/cargo /tmp/rust-build /tmp/victus-test-home
RUST_EXEC=(podman exec --user 65534:65534 -w /tmp/victus-review-project
  -e HOME=/tmp/victus-test-home
  -e PATH=/toolchain/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  -e CARGO_HOME=/tmp/cargo -e CARGO_TARGET_DIR=/tmp/rust-build
  -e CARGO_BUILD_JOBS=1 -e CARGO_INCREMENTAL=0
  -e CARGO_PROFILE_DEV_DEBUG=0 -e CARGO_PROFILE_TEST_DEBUG=0)
"${RUST_EXEC[@]}" "$container" cargo test --locked --workspace -- --test-threads=1
"${RUST_EXEC[@]}" "$container" cargo build --locked --workspace

"${RUST_EXEC[@]}" -i "$container" python3 - <<'PY'
import os
from pathlib import Path
import subprocess

fonts = Path('/tmp/victus-test-fonts.conf')
fonts.write_text('<?xml version="1.0"?><fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>/src/victus_hub/resources/fonts</dir></fontconfig>')
env = dict(os.environ, FONTCONFIG_FILE=str(fonts), GTK_A11Y='none', GSK_RENDERER='cairo')
for name in (
    'fan_mode_explanation_uses_one_line',
    'frequency_sliders_show_kernel_limits_not_the_hardware_floor',
    'gtk_controls_preserve_pending_edits_and_wire_lighting_and_navigation',
):
    subprocess.run([
        'timeout', '--kill-after=5', '60', 'xvfb-run', '-a', 'dbus-run-session', '--',
        'cargo', 'test', '--locked', '-p', 'victus-hub', '--lib', '--',
        '--ignored', '--exact', f'ui::regression_tests::{name}',
    ], env=env, check=True)
PY
```

GTK binds initialization to one thread. Even `--test-threads=1` creates a new
thread per test, so run each GTK test in its own process as above. The fontconfig
file supplies the same bundled fonts as real program startup; omitting it can
cause layout checks to measure a distro fallback font instead.

## 4. Cleanup

Capture results before cleanup. Remove only the named containers created by
this run; their writable layers contain all packages, virtual environments,
build artifacts, and copied sources. The bind-mounted checkout is preserved.

```bash
podman rm -f -t 1 "$container"
podman ps -a --format '{{.Names}}'
podman images --format '{{.Repository}}:{{.Tag}} {{.ID}}'
git status --short
```

Only if these base images were absent before this run and are no longer needed:

```bash
podman rmi "$IMAGE"
```

The original review also used `podman commit` to retain installed dependencies
between checks. The workflow above keeps containers running instead, so those
extra images are unnecessary. If you create snapshots yourself, track and
remove their specific tags after removing containers that reference them.
Review-created tags were `localhost/vh-review-ubuntu`,
`localhost/vh-review-fedora`, `localhost/vh-review-arch`, and
`localhost/vh-verify-ubuntu`; containers also used the `vh-verify-*` names.
Those original review resources were removed after validation.

Do not use broad `podman system prune`, delete unrelated images/containers,
stage this file, or create commits as part of this procedure.
