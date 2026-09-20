"""Host profile and battery state without polling the fan loop.

Profile updates come from inotify on tuned's active-profile file and from a
long-lived ``busctl monitor`` of tuned / power-profiles-daemon / UPower
signals. Battery AC/DC uses sysfs, refreshed on those same events.
Subprocess profile CLI is used only for the one-shot startup snapshot.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import select
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from victus_hub.backend import profiles
from victus_hub.backend.sysfs_read import read_text
from victus_hub.backend.util import command_path

logger = logging.getLogger(__name__)

_TUNED_DIR = Path("/etc/tuned")
_TUNED_ACTIVE = _TUNED_DIR / "active_profile"
_POWER_SUPPLY = Path("/sys/class/power_supply")

_IN_CLOEXEC = 0x80000
_IN_MODIFY = 0x00000002
_IN_CLOSE_WRITE = 0x00000008
_IN_MOVED_TO = 0x00000080
_IN_CREATE = 0x00000100

_PPD_ENDPOINTS = (
    (
        "org.freedesktop.UPower.PowerProfiles",
        "/org/freedesktop/UPower/PowerProfiles",
    ),
    ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"),
)

_BUSCTL_MATCHES = (
    "type='signal',interface='com.redhat.tuned.control',member='profile_changed'",
    "type='signal',path='/org/freedesktop/UPower/PowerProfiles',"
    "interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
    "type='signal',path='/net/hadess/PowerProfiles',"
    "interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
    "type='signal',path='/org/freedesktop/UPower',"
    "interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
)


def _tuned_profile_name() -> str:
    try:
        return _TUNED_ACTIVE.read_text().strip()
    except OSError:
        return ""


def _parse_busctl_string(output: str | None) -> str:
    if not output:
        return ""
    text = output.strip()
    if text.startswith("s "):
        text = text[2:].strip()
    return text.strip().strip('"')


def _busctl_active_profile() -> int | None:
    busctl = command_path("busctl")
    if busctl is None:
        return None
    for service, path in _PPD_ENDPOINTS:
        try:
            result = subprocess.run(
                [
                    str(busctl), "--system", "get-property",
                    service, path, service, "ActiveProfile",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        index = profiles.profile_index_for_name(_parse_busctl_string(result.stdout))
        if index is not None:
            return index
    return None


def read_profile_index(*, allow_cli: bool = False) -> int | None:
    """Return the current power-saver/balanced/performance index.

    Prefers the tuned active-profile file, then a one-shot busctl property
    read. ``tuned-adm`` / ``powerprofilesctl`` run only when *allow_cli* is
    set (startup snapshot), never from the fan loop.
    """
    name = _tuned_profile_name()
    if name:
        index = profiles.profile_index_for_name(name)
        if index is not None:
            return index
    index = _busctl_active_profile()
    if index is not None:
        return index
    if allow_cli:
        return profiles.current_ui_profile_index()
    return None


def ac_online() -> bool | None:
    """True on AC, False on battery, None if power-supply nodes are missing."""
    if not _POWER_SUPPLY.is_dir():
        return None
    try:
        entries = list(_POWER_SUPPLY.iterdir())
    except OSError:
        return None
    saw_mains = False
    any_online = False
    for entry in entries:
        kind = (read_text(entry / "type") or "").strip()
        if kind not in {"Mains", "ADP", "USB"}:
            continue
        saw_mains = True
        if (read_text(entry / "online") or "").strip() == "1":
            any_online = True
    if saw_mains:
        return any_online
    for entry in entries:
        kind = (read_text(entry / "type") or "").strip()
        if kind != "Battery":
            continue
        status = (read_text(entry / "status") or "").strip().lower()
        if status == "discharging":
            return False
        if status in {"charging", "full", "not charging"}:
            return True
    return None


class HostWatch:
    """Event-driven profile and AC/battery notifications."""

    def __init__(
        self,
        on_change: Callable[[], None],
        stop: threading.Event,
    ) -> None:
        self._on_change = on_change
        self._stop = stop
        self._pending = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self._threads = [
            threading.Thread(target=self._dispatch_events, daemon=True, name="host-events"),
            threading.Thread(target=self._inotify_tuned, daemon=True, name="host-tuned"),
            threading.Thread(target=self._busctl_monitor, daemon=True, name="host-dbus"),
        ]
        for thread in self._threads:
            thread.start()

    def _notify(self) -> None:
        self._pending.set()

    def _dispatch_events(self) -> None:
        while not self._stop.is_set():
            if not self._pending.wait(1.0):
                continue
            # Tuned may report the same transition through both watchers.
            if self._stop.wait(0.05):
                return
            self._pending.clear()
            try:
                self._on_change()
            except Exception:
                logger.exception("host watch callback failed")

    def _inotify_tuned(self) -> None:
        if not _TUNED_DIR.is_dir():
            return
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            fd = libc.inotify_init1(_IN_CLOEXEC)
        except OSError:
            logger.warning("host watch: inotify unavailable")
            return
        if fd < 0:
            return
        mask = _IN_MODIFY | _IN_CLOSE_WRITE | _IN_MOVED_TO | _IN_CREATE
        watch = libc.inotify_add_watch(fd, str(_TUNED_DIR).encode(), mask)
        if watch < 0:
            os.close(fd)
            return
        logger.info("host watch: inotify on %s", _TUNED_DIR)
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([fd], [], [], 1.0)
                if not ready:
                    continue
                try:
                    os.read(fd, 4096)
                except OSError:
                    break
                self._notify()
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _busctl_monitor(self) -> None:
        busctl = command_path("busctl")
        if busctl is None:
            logger.info("host watch: busctl not found; using file/sysfs events only")
            return
        command = [str(busctl), "monitor", "--system", "--json=short"]
        for match in _BUSCTL_MATCHES:
            command.extend(["--match", match])
        while not self._stop.is_set():
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except OSError:
                logger.warning("host watch: busctl monitor failed to start")
                return
            logger.info("host watch: busctl monitor started")
            try:
                stdout = proc.stdout
                if stdout is None:
                    proc.kill()
                    return
                while not self._stop.is_set():
                    line = stdout.readline()
                    if line == "":
                        break
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(message, dict):
                        self._notify()
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            if self._stop.wait(2.0):
                return
