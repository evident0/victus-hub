"""Grouped process CPU, memory, network, and storage activity."""

from __future__ import annotations

import os
import signal
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHeaderView, QMenu, QVBoxLayout,
    QSizePolicy, QTreeWidget, QTreeWidgetItem,
)

from victus_hub.app.theme import COLORS

# Scrollable list — enough slots for mid-size apps, not only browsers.
_TOP_GROUPS = 30
_ICON_PX = 20


def _is_runtime_binary(name: str) -> bool:
    """True when *name* is an interpreter/sandbox, not the app itself.

    Flatpak/Python apps often show /usr/bin/python as /proc/pid/exe while
    the real identity is the script on the command line.
    """
    n = (name or "").lower()
    if not n:
        return False
    if n in (
        "python", "python2", "python3", "node", "nodejs", "ruby", "perl",
        "lua", "luajit", "php", "php-fpm", "java", "bash", "sh", "dash",
        "zsh", "fish", "mono", "dotnet", "wish", "tclsh", "bwrap",
        "flatpak-spawn", "electron", "busybox",
    ):
        return True
    for prefix in ("python", "ruby", "perl", "php", "node"):
        if not n.startswith(prefix):
            continue
        rest = n[len(prefix):]
        if not rest or rest[0] in ".-" or rest.replace(".", "").isdigit():
            return True
    return False


_ICON_ALIASES: dict[str, list[str]] = {
    "firefox": ["firefox", "org.mozilla.firefox", "firefox-esr"],
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "chromium": ["chromium", "chromium-browser", "google-chrome"],
    "code": ["code", "visual-studio-code", "com.visualstudio.code"],
    "discord": ["discord", "com.discordapp.Discord"],
    "slack": ["slack"],
    "spotify": ["spotify", "spotify-client"],
    "steam": ["steam", "steam-launcher"],
    "plasmashell": ["plasmashell", "start-here-kde", "kde"],
    "kwin_x11": ["kwin", "preferences-system-windows"],
    "kwin_wayland": ["kwin", "preferences-system-windows"],
    "dolphin": ["org.kde.dolphin", "dolphin", "system-file-manager"],
    "konsole": ["org.kde.konsole", "konsole", "utilities-terminal"],
    "gnome-shell": ["org.gnome.Shell", "gnome-shell"],
    "nautilus": ["org.gnome.Nautilus", "nautilus", "system-file-manager"],
    "telegram": ["telegram", "telegram-desktop", "org.telegram.desktop"],
    "signal": ["signal", "signal-desktop"],
    "zoom": ["Zoom", "zoom", "us.zoom.Zoom"],
    "obs": ["com.obsproject.Studio", "obs", "obs-studio"],
    "vlc": ["vlc", "org.videolan.VLC"],
}

_desktop_icon_cache: dict[str, str] | None = None
_icon_cache: dict[str, QIcon] = {}


@dataclass
class ProcessInfo:
    pid: int
    name: str
    rss_kb: int
    cpu_pct: float = 0.0
    download_bps: float | None = 0.0
    upload_bps: float | None = 0.0
    read_bps: float | None = 0.0
    write_bps: float | None = 0.0
    exe: str = ""
    # Stable group identity (script path, module, or binary path)
    group_key: str = ""
    # Short label for a child instance (comm / role)
    detail: str = ""
    icon: QIcon = field(default_factory=QIcon)
    socket_inodes: set[int] | None = field(default_factory=set, repr=False)

    @property
    def ram_display(self) -> str:
        return _fmt_ram(self.rss_kb)

    @property
    def cpu_display(self) -> str:
        return _fmt_cpu(self.cpu_pct)

    @property
    def download_display(self) -> str:
        return _fmt_rate(self.download_bps)

    @property
    def upload_display(self) -> str:
        return _fmt_rate(self.upload_bps)

    @property
    def read_display(self) -> str:
        return _fmt_rate(self.read_bps)

    @property
    def write_display(self) -> str:
        return _fmt_rate(self.write_bps)


@dataclass
class ProcessGroup:
    key: str
    name: str
    icon: QIcon = field(default_factory=QIcon)
    instances: list[ProcessInfo] = field(default_factory=list)

    @property
    def rss_kb(self) -> int:
        return sum(p.rss_kb for p in self.instances)

    @property
    def cpu_pct(self) -> float:
        return max(0.0, sum(p.cpu_pct for p in self.instances))

    @property
    def download_bps(self) -> float | None:
        return _sum_rates(p.download_bps for p in self.instances)

    @property
    def upload_bps(self) -> float | None:
        return _sum_rates(p.upload_bps for p in self.instances)

    @property
    def read_bps(self) -> float | None:
        return _sum_rates(p.read_bps for p in self.instances)

    @property
    def write_bps(self) -> float | None:
        return _sum_rates(p.write_bps for p in self.instances)

    @property
    def ram_display(self) -> str:
        return _fmt_ram(self.rss_kb)

    @property
    def cpu_display(self) -> str:
        return _fmt_cpu(self.cpu_pct)

    @property
    def download_display(self) -> str:
        return _fmt_rate(self.download_bps)

    @property
    def upload_display(self) -> str:
        return _fmt_rate(self.upload_bps)

    @property
    def read_display(self) -> str:
        return _fmt_rate(self.read_bps)

    @property
    def write_display(self) -> str:
        return _fmt_rate(self.write_bps)

    @property
    def count(self) -> int:
        return len(self.instances)


def sort_process_groups(
    groups: list[ProcessGroup], sort_key: str = "ram", ascending: bool = False,
) -> list[ProcessGroup]:
    """Order groups and their instances by a displayed process-table column."""
    attributes = {
        "cpu": "cpu_pct",
        "ram": "rss_kb",
        "download": "download_bps",
        "upload": "upload_bps",
        "read": "read_bps",
        "write": "write_bps",
    }
    attribute = attributes.get(sort_key)
    if attribute is None:
        groups = sorted(groups, key=lambda group: group.name.lower(), reverse=not ascending)
        for group in groups:
            group.instances.sort(key=lambda process: process.detail.lower(), reverse=not ascending)
        return groups

    # Keep unavailable rates at the end in either direction.
    def sort_entries(entries):
        available = [entry for entry in entries if getattr(entry, attribute) is not None]
        unavailable = [entry for entry in entries if getattr(entry, attribute) is None]
        return sorted(available, key=lambda entry: getattr(entry, attribute), reverse=not ascending) + unavailable

    groups = sort_entries(groups)
    for group in groups:
        group.instances[:] = sort_entries(group.instances)
    return groups


def _fmt_ram(rss_kb: int) -> str:
    mb = rss_kb / 1024.0
    if mb >= 1024.0:
        return f"{mb / 1024.0:.1f} GB"
    return f"{mb:.0f} MB"


def _fmt_cpu(cpu_pct: float) -> str:
    if cpu_pct < 0.05:
        return "0%"
    if cpu_pct < 10:
        return f"{cpu_pct:.1f}%"
    return f"{cpu_pct:.0f}%"


def _sum_rates(values) -> float | None:
    rates = list(values)
    if not rates or any(value is None for value in rates):
        return None
    return sum(value for value in rates if value is not None)


def _fmt_rate(bytes_per_second: float | None) -> str:
    if bytes_per_second is None:
        return "—"
    value = max(0.0, bytes_per_second)
    if value < 1.0:
        return "0 B/s"
    if value < 1024.0:
        return f"{value:.0f} B/s"
    value /= 1024.0
    if value < 1024.0:
        return f"{value:.1f} KiB/s" if value < 10.0 else f"{value:.0f} KiB/s"
    value /= 1024.0
    if value < 1024.0:
        return f"{value:.1f} MiB/s" if value < 10.0 else f"{value:.0f} MiB/s"
    return f"{value / 1024.0:.1f} GiB/s"


def _parse_proc_stat(data: str) -> tuple[int, int] | None:
    """Return process CPU jiffies and start time from a proc stat payload."""
    rparen = data.rfind(")")
    if rparen < 0:
        return None
    fields = data[rparen + 2 :].split()
    try:
        return int(fields[11]) + int(fields[12]), int(fields[19])
    except (ValueError, IndexError):
        return None


def _read_proc_stat(pid: int) -> tuple[int, int] | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            return _parse_proc_stat(f.read())
    except OSError:
        return None


def _read_proc_io(pid: int) -> tuple[int, int] | None:
    """Return cumulative physical storage read/write bytes."""
    read_bytes = write_bytes = None
    try:
        with open(f"/proc/{pid}/io", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("read_bytes:"):
                    read_bytes = int(line.split()[1])
                elif line.startswith("write_bytes:"):
                    write_bytes = int(line.split()[1])
                if read_bytes is not None and write_bytes is not None:
                    return read_bytes, write_bytes
    except (OSError, ValueError, IndexError):
        pass
    return None


def _read_process_socket_inodes(pid: int, available: set[int]) -> set[int] | None:
    """Return this process's TCP socket inodes present in a diag snapshot."""
    if not available:
        return set()
    try:
        entries = os.scandir(f"/proc/{pid}/fd")
    except OSError:
        return None
    found: set[int] = set()
    with entries:
        for entry in entries:
            try:
                target = os.readlink(entry.path)
            except OSError:
                continue
            if not target.startswith("socket:[") or not target.endswith("]"):
                continue
            try:
                inode = int(target[8:-1])
            except ValueError:
                continue
            if inode in available:
                found.add(inode)
    return found


def _is_thread_group_leader(pid: int) -> bool:
    """True if *pid* is a process (TGID), not a non-leader thread.

    Listing every TID under /proc would multi-count RAM and clutter the list.
    """
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as f:
            tgid = None
            pid_field = None
            for line in f:
                if line.startswith("Tgid:"):
                    tgid = int(line.split()[1])
                elif line.startswith("Pid:"):
                    pid_field = int(line.split()[1])
                if tgid is not None and pid_field is not None:
                    return tgid == pid_field
    except (OSError, ValueError, IndexError):
        return True  # keep entry if status is unreadable
    return True


def _read_memory_kb(pid: int) -> int | None:
    """Best memory estimate in kB: PSS when available, else VmRSS.

    PSS (proportional set size) splits shared pages across processes, so
    summing PSS over a multi-process app (Firefox) is far more accurate than
    summing VmRSS (which multi-counts shared libraries).
    """
    # smaps_rollup is cheap and preferred (Linux 4.14+)
    try:
        with open(f"/proc/{pid}/smaps_rollup", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _read_cmdline(pid: int) -> list[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return []
    if not raw:
        return []
    return [a.decode("utf-8", errors="replace") for a in raw.split(b"\x00") if a]


def _app_identity_from_cmdline(args: list[str], runtime: str) -> str | None:
    """For interpreter/runtime processes, find the script/module that identifies the app.

    Examples:
      python /app/bin/protonvpn-app  → /app/bin/protonvpn-app
      python -m http.server          → http.server
      node /usr/bin/some-cli         → /usr/bin/some-cli
      bwrap … -- easyeffects …       → easyeffects
      java -jar /opt/app.jar         → /opt/app.jar
    """
    if not args:
        return None
    rt = (runtime or Path(args[0]).name).lower()

    # bubblewrap / flatpak helpers: payload after a bare "--"
    if rt in ("bwrap", "flatpak-spawn") or "bwrap" in rt:
        if "--" in args:
            i = args.index("--") + 1
            while i < len(args):
                a = args[i]
                if a.startswith("-"):
                    i += 1
                    continue
                return a
                # unreachable
            return None

    i = 1  # skip argv0
    is_python = rt.startswith("python")
    is_node = rt == "nodejs" or rt.startswith("node")
    is_java = rt == "java" or rt.endswith("java")
    is_ruby = rt.startswith("ruby")
    is_perl = rt.startswith("perl")
    is_php = rt.startswith("php")
    is_shell = rt in ("bash", "sh", "dash", "zsh", "fish", "busybox")

    while i < len(args):
        a = args[i]
        if not a:
            i += 1
            continue

        # Options that take a separate argument
        if is_python and a in ("-m", "-W", "-X", "-Q", "-c"):
            if a == "-m" and i + 1 < len(args):
                return args[i + 1]
            if a == "-c":
                return None  # anonymous -c snippet
            i += 2
            continue
        if is_node and a in ("-e", "--eval", "-p", "-r", "--require"):
            if a in ("-e", "--eval", "-p"):
                return None
            i += 2
            continue
        if is_java:
            if a == "-jar" and i + 1 < len(args):
                return args[i + 1]
            if a.startswith("-"):
                # -cp / -classpath take a value
                if a in ("-cp", "-classpath", "--class-path", "-D") or a.startswith("-D"):
                    if a in ("-cp", "-classpath", "--class-path") and i + 1 < len(args):
                        i += 2
                        continue
                i += 1
                continue
            # First non-option is main class
            return a
        if is_ruby and a in ("-e", "-I", "-r", "-c"):
            if a == "-e":
                return None
            i += 2 if a in ("-I", "-r") else 1
            continue
        if is_perl and a in ("-e", "-E", "-I"):
            if a in ("-e", "-E"):
                return None
            i += 2
            continue
        if is_php and a in ("-f", "-r"):
            if a == "-r":
                return None
            if a == "-f" and i + 1 < len(args):
                return args[i + 1]
            i += 1
            continue
        if is_shell and a in ("-c", "-lc", "-ic"):
            return None

        if a.startswith("-"):
            i += 1
            continue

        # First non-option argument: script / entrypoint
        return a

    return None


def _display_name_from_identity(identity: str) -> str:
    """Human label from a script path, module name, or bare command."""
    if not identity:
        return identity
    # module names like http.server stay as-is
    if "/" not in identity and not identity.startswith("."):
        return identity
    return Path(identity).name


def _read_proc_meta(pid: int) -> tuple[str, str, int, str, str] | None:
    """Return (group_name, detail, mem_kb, exe, group_key) or None."""
    if not _is_thread_group_leader(pid):
        return None

    name = None
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                    break
    except (OSError, ValueError):
        return None
    if not name:
        return None

    mem_kb = _read_memory_kb(pid)
    if mem_kb is None or mem_kb <= 0:
        return None

    exe = ""
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
        if exe.endswith(" (deleted)"):
            exe = exe[: -len(" (deleted)")]
    except OSError:
        pass

    args = _read_cmdline(pid)
    exe_base = Path(exe).name if exe else (Path(args[0]).name if args else "")
    detail = name
    group_name = name
    group_key = exe or name.lower()

    # Browser content helpers → group under browser binary
    content_names = (
        "MainThread", "Web Content", "Isolated Web Co", "RDD Process",
        "Privileged Cont", "Socket Process", "Utility Process",
    )
    is_content = (
        name in content_names
        or name.startswith("WebExtensions")
        or name.startswith("Isolated ")
    )

    if is_content and exe:
        group_name = exe_base or name
        group_key = exe
        detail = name
    elif _is_runtime_binary(exe_base) or _is_runtime_binary(name):
        # Interpreter / sandbox: identify the real app from cmdline
        runtime = exe_base or name
        identity = _app_identity_from_cmdline(args, runtime)
        if identity:
            group_name = _display_name_from_identity(identity)
            # Key by app label so Flatpak bwrap helpers merge with the
            # real process (e.g. bwrap … -- protonvpn-app + python script).
            group_key = group_name.lower()
            detail = name if name.lower() != group_name.lower() else f"PID {pid}"
        elif exe:
            # Fall back to exe (e.g. bare `python3` REPL)
            group_name = exe_base
            group_key = exe
            detail = name if name.lower() != exe_base.lower() else f"PID {pid}"
        else:
            group_name = name
            group_key = name.lower()
            detail = f"PID {pid}"
    elif exe:
        group_name = exe_base
        group_key = exe
        detail = name if name.lower() != exe_base.lower() else f"PID {pid}"
    else:
        # No exe (permission / kernel edge): try cmdline argv0
        if args:
            base = Path(args[0]).name
            if base:
                group_name = base
                group_key = args[0] if args[0].startswith("/") else base.lower()
        detail = f"PID {pid}" if detail == group_name else detail

    if not detail or detail == group_name:
        detail = f"PID {pid}"

    return group_name, detail, mem_kb, exe, group_key


def _ensure_desktop_cache() -> dict[str, str]:
    global _desktop_icon_cache
    if _desktop_icon_cache is not None:
        return _desktop_icon_cache

    cache: dict[str, str] = {}
    search_dirs = [
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path.home() / ".local/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
    ]
    for d in search_dirs:
        if not d.is_dir():
            continue
        try:
            entries = list(d.glob("*.desktop"))
        except OSError:
            continue
        for desk in entries:
            try:
                text = desk.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            icon = ""
            exec_line = ""
            name = ""
            for line in text.splitlines():
                if line.startswith("Icon="):
                    icon = line.split("=", 1)[1].strip()
                elif line.startswith("Exec="):
                    exec_line = line.split("=", 1)[1].strip()
                elif line.startswith("Name=") and not name:
                    name = line.split("=", 1)[1].strip()
            if not icon:
                continue
            cache[desk.stem.lower()] = icon
            if name:
                cache[name.lower()] = icon
            if exec_line:
                bin_part = exec_line.split()[0]
                for code in ("%u", "%U", "%f", "%F", "%i", "%c", "%k"):
                    bin_part = bin_part.replace(code, "")
                base = Path(bin_part).name.lower()
                if base:
                    cache.setdefault(base, icon)
    _desktop_icon_cache = cache
    return cache


def _icon_from_filesystem(name: str) -> QIcon:
    if not name or name.startswith("/"):
        return QIcon()
    sizes = ("48x48", "32x32", "64x64", "24x24", "22x22", "16x16", "scalable", "256x256")
    themes = ("hicolor", "breeze", "breeze-dark", "Adwaita", "Papirus", "Papirus-Dark")
    exts = ("png", "svg", "svgz", "xpm")
    roots = [
        Path("/usr/share/icons"),
        Path("/usr/local/share/icons"),
        Path.home() / ".local/share/icons",
        Path("/usr/share/pixmaps"),
    ]
    for root in (Path("/usr/share/pixmaps"), Path.home() / ".local/share/pixmaps"):
        for ext in exts:
            path = root / f"{name}.{ext}"
            if path.is_file():
                icon = QIcon(str(path))
                if not icon.isNull():
                    return icon
    for root in roots:
        if not root.is_dir():
            continue
        for theme in themes:
            base = root / theme
            if not base.is_dir():
                continue
            for size in sizes:
                for folder in ("apps", "categories", "devices", "status", "actions"):
                    for ext in exts:
                        path = base / size / folder / f"{name}.{ext}"
                        if path.is_file():
                            icon = QIcon(str(path))
                            if not icon.isNull():
                                return icon
    return QIcon()


def _selection_stable_icon(icon: QIcon, size: int = _ICON_PX) -> QIcon:
    """Return an icon that looks identical when the row is selected/active.

    QTreeWidget paints QIcon.Selected under highlight; many theme icons
    recolor or blank that mode, so bake the same pixmap into every mode.
    """
    if icon.isNull():
        return icon
    pm = icon.pixmap(QSize(size, size))
    if pm.isNull():
        return icon
    stable = QIcon()
    for mode in (
        QIcon.Mode.Normal,
        QIcon.Mode.Selected,
        QIcon.Mode.Active,
        QIcon.Mode.Disabled,
    ):
        for state in (QIcon.State.On, QIcon.State.Off):
            stable.addPixmap(pm, mode, state)
    return stable


def resolve_process_icon(name: str, exe: str = "") -> QIcon:
    if not QIcon.themeName():
        for candidate in ("breeze", "Adwaita", "hicolor"):
            QIcon.setThemeName(candidate)
            if QIcon.themeName() == candidate:
                break

    keys: list[str] = []
    exe_base = Path(exe).name if exe else ""
    for raw in (exe_base, name):
        if not raw:
            continue
        low = raw.lower()
        keys.append(low)
        keys.extend(_ICON_ALIASES.get(low, []))
        for suf in (".bin", "-bin", ".py", ".sh"):
            if low.endswith(suf):
                keys.append(low[: -len(suf)])

    desk = _ensure_desktop_cache()
    for k in list(keys):
        if k in desk:
            keys.append(desk[k])

    seen: set[str] = set()
    ordered: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)

    for key in ordered:
        if key in _icon_cache:
            icon = _icon_cache[key]
            if not icon.isNull():
                return icon
            continue
        if key.startswith("/"):
            pm = QPixmap(key)
            icon = QIcon(pm) if not pm.isNull() else QIcon()
        else:
            icon = QIcon.fromTheme(key)
            if icon.isNull():
                icon = _icon_from_filesystem(key)
        icon = _selection_stable_icon(icon)
        _icon_cache[key] = icon
        if not icon.isNull():
            return icon

    fallback = QIcon.fromTheme("application-x-executable")
    if fallback.isNull():
        fallback = _icon_from_filesystem("application-x-executable")
    if fallback.isNull():
        fallback = QIcon.fromTheme("application-default-icon")
    return _selection_stable_icon(fallback)


_NETLINK_SOCK_DIAG = 4
_SOCK_DIAG_BY_FAMILY = 20
_INET_DIAG_INFO = 2
_NLMSG_DONE = 3
_NLMSG_ERROR = 2
_NLM_F_DUMP_REQUEST = 0x301
_NLM_F_DUMP_INTR = 0x10
_TCP_INFO_BYTES_ACKED_OFFSET = 120
_TCP_INFO_BYTES_RECEIVED_OFFSET = 128
_CLK_TCK = int(os.sysconf("SC_CLK_TCK"))
_CPU_COUNT = max(os.cpu_count() or 1, 1)


def _aligned(length: int) -> int:
    return (length + 3) & ~3


def _read_tcp_socket_bytes(
    cancelled=None,
) -> dict[int, tuple[tuple[int, int], int, int]] | None:
    """Return inode -> (cookie, downloaded, uploaded) for live TCP.

    SOCK_DIAG exposes kernel TCP counters without packet capture. UDP and
    connections that open and close entirely between samples are intentionally
    not estimated because Linux exposes no equivalent reliable counters.
    """
    counters: dict[int, tuple[tuple[int, int], int, int]] = {}
    saw_socket = False
    saw_counters = False
    sequence = int(time.monotonic_ns() & 0xFFFFFFFF)
    for family in (socket.AF_INET, socket.AF_INET6):
        if cancelled is not None and cancelled():
            return None
        diag = None
        try:
            diag = socket.socket(
                socket.AF_NETLINK,
                socket.SOCK_RAW,
                _NETLINK_SOCK_DIAG,
            )
            diag.settimeout(0.25)
            diag.bind((0, 0))
            request = struct.pack(
                "=BBBBI",
                family,
                socket.IPPROTO_TCP,
                1 << (_INET_DIAG_INFO - 1),
                0,
                0x1FFF,
            )
            request += struct.pack("!HH", 0, 0)
            request += bytes(32)
            request += struct.pack("=III", 0, 0xFFFFFFFF, 0xFFFFFFFF)
            header = struct.pack(
                "=IHHII",
                16 + len(request),
                _SOCK_DIAG_BY_FAMILY,
                _NLM_F_DUMP_REQUEST,
                sequence,
                0,
            )
            diag.send(header + request)
        except OSError:
            if diag is not None:
                diag.close()
            return None

        done = False
        failed = False
        family_counters: dict[int, tuple[tuple[int, int], int, int]] = {}
        try:
            while not done:
                if cancelled is not None and cancelled():
                    return None
                payload, _ancillary, message_flags, _address = diag.recvmsg(
                    1024 * 1024,
                )
                if message_flags & socket.MSG_TRUNC:
                    failed = True
                    break
                offset = 0
                while offset + 16 <= len(payload):
                    length, kind, flags, seq, _pid = struct.unpack_from(
                        "=IHHII", payload, offset,
                    )
                    if length < 16 or offset + length > len(payload):
                        failed = True
                        break
                    message = payload[offset + 16 : offset + length]
                    offset += _aligned(length)
                    if seq != sequence:
                        continue
                    if flags & _NLM_F_DUMP_INTR:
                        failed = True
                        break
                    if kind == _NLMSG_ERROR:
                        failed = True
                        break
                    if kind == _NLMSG_DONE:
                        done = True
                        continue
                    if kind != _SOCK_DIAG_BY_FAMILY or len(message) < 72:
                        continue
                    saw_socket = True
                    inode = struct.unpack_from("=I", message, 68)[0]
                    cookie = struct.unpack_from("=II", message, 44)
                    attr_offset = 72
                    while attr_offset + 4 <= len(message):
                        attr_len, attr_type = struct.unpack_from(
                            "=HH", message, attr_offset,
                        )
                        if attr_len < 4 or attr_offset + attr_len > len(message):
                            break
                        info = message[attr_offset + 4 : attr_offset + attr_len]
                        if attr_type == _INET_DIAG_INFO and len(info) >= 136:
                            uploaded = struct.unpack_from(
                                "=Q", info, _TCP_INFO_BYTES_ACKED_OFFSET,
                            )[0]
                            downloaded = struct.unpack_from(
                                "=Q", info, _TCP_INFO_BYTES_RECEIVED_OFFSET,
                            )[0]
                            family_counters[inode] = cookie, downloaded, uploaded
                            saw_counters = True
                            break
                        attr_offset += _aligned(attr_len)
                if failed:
                    break
        except OSError:
            failed = True
        finally:
            if diag is not None:
                diag.close()
        if failed or not done:
            return None
        counters.update(family_counters)
    if saw_socket and not saw_counters:
        return None
    return counters


class _MetricTracker:
    """Difference cumulative process and socket counters between scans."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._previous_time: float | None = None
        self._previous_process: dict[
            int,
            tuple[int, int, int | None, int | None, float | None],
        ] = {}
        self._previous_socket_time: float | None = None
        self._previous_sockets: dict[tuple[int, int], tuple[int, int]] = {}

    def begin_sample(
        self,
        now: float,
        sockets: dict[int, tuple[tuple[int, int], int, int]] | None,
    ) -> tuple[float | None, dict[int, tuple[float, float]]]:
        previous_time = self._previous_time
        self._previous_time = now
        elapsed = None if previous_time is None else now - previous_time
        if elapsed is not None and elapsed <= 0.0:
            elapsed = None

        rates: dict[int, tuple[float, float]] = {}
        socket_elapsed = (
            None
            if self._previous_socket_time is None
            else now - self._previous_socket_time
        )
        if sockets is not None and socket_elapsed is not None and socket_elapsed > 0.0:
            for inode, (cookie, downloaded, uploaded) in sockets.items():
                previous = self._previous_sockets.get(cookie)
                if previous is None:
                    continue
                download_delta = downloaded - previous[0]
                upload_delta = uploaded - previous[1]
                if download_delta >= 0 and upload_delta >= 0:
                    rates[inode] = (
                        download_delta / socket_elapsed,
                        upload_delta / socket_elapsed,
                    )
        if sockets is not None:
            self._previous_socket_time = now
            self._previous_sockets = {
                cookie: (downloaded, uploaded)
                for cookie, downloaded, uploaded in sockets.values()
            }
        return elapsed, rates

    def process_rates(
        self,
        pid: int,
        stat: tuple[int, int] | None,
        io: tuple[int, int] | None,
        elapsed: float | None,
        now: float,
    ) -> tuple[float, float | None, float | None]:
        if stat is None:
            self._previous_process.pop(pid, None)
            return 0.0, None, None
        jiffies, start_time = stat
        previous = self._previous_process.get(pid)
        same_process = previous is not None and previous[0] == start_time
        if io is not None:
            read_bytes, write_bytes = io
            io_time = now
        elif same_process:
            read_bytes, write_bytes, io_time = previous[2], previous[3], previous[4]
        else:
            read_bytes = write_bytes = io_time = None
        self._previous_process[pid] = (
            start_time,
            jiffies,
            read_bytes,
            write_bytes,
            io_time,
        )
        if not same_process or elapsed is None:
            return 0.0, 0.0 if io is not None else None, 0.0 if io is not None else None

        cpu_delta = jiffies - previous[1]
        # Match KDE System Monitor: percentage of total logical CPU capacity,
        # rather than top's convention where one fully busy core is 100%.
        cpu_pct = max(
            0.0,
            100.0 * cpu_delta / (_CLK_TCK * elapsed * _CPU_COUNT),
        )
        if (
            io is None
            or previous[2] is None
            or previous[3] is None
            or previous[4] is None
        ):
            return cpu_pct, None, None
        io_elapsed = now - previous[4]
        if io_elapsed <= 0.0:
            return cpu_pct, 0.0, 0.0
        read_bps = max(0.0, (read_bytes - previous[2]) / io_elapsed)
        write_bps = max(0.0, (write_bytes - previous[3]) / io_elapsed)
        return cpu_pct, read_bps, write_bps

    def prune(self, live_pids: set[int]) -> None:
        for pid in [pid for pid in self._previous_process if pid not in live_pids]:
            del self._previous_process[pid]


_metric_tracker = _MetricTracker()


def read_process_groups(
    limit: int = _TOP_GROUPS,
    cancelled=None,
    reset_rates: bool = False,
    sort_key: str = "ram",
    ascending: bool = False,
) -> list[ProcessGroup]:
    """Scan /proc, group by app identity, and return the selected top groups.

    Thread-safe for a single concurrent scanner. Does **not** create QIcons
    (those are attached on the GUI thread via ``attach_group_icons``).
    """
    procs: list[ProcessInfo] = []
    if cancelled is not None and cancelled():
        return []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []

    if reset_rates:
        _metric_tracker.reset()
    socket_bytes = _read_tcp_socket_bytes(cancelled)
    if cancelled is not None and cancelled():
        return []
    sample_time = time.monotonic()
    elapsed, socket_rates = _metric_tracker.begin_sample(
        sample_time, socket_bytes,
    )
    network_available = socket_bytes is not None
    available_inodes = set(socket_bytes) if socket_bytes is not None else set()

    for entry in entries:
        if cancelled is not None and cancelled():
            return []
        if not entry.isdigit():
            continue
        pid = int(entry)
        meta = _read_proc_meta(pid)
        if meta is None:
            continue
        group_name, detail, mem_kb, exe, group_key = meta
        cpu_pct, read_bps, write_bps = _metric_tracker.process_rates(
            pid,
            _read_proc_stat(pid),
            _read_proc_io(pid),
            elapsed,
            sample_time,
        )
        socket_inodes = (
            _read_process_socket_inodes(pid, available_inodes)
            if network_available
            else None
        )
        process_network_available = network_available and socket_inodes is not None
        procs.append(ProcessInfo(
            pid=pid,
            name=group_name,
            rss_kb=mem_kb,  # PSS when available (field name kept for compatibility)
            cpu_pct=cpu_pct,
            download_bps=0.0 if process_network_available else None,
            upload_bps=0.0 if process_network_available else None,
            read_bps=read_bps,
            write_bps=write_bps,
            exe=exe,
            group_key=group_key,
            detail=detail,
            socket_inodes=socket_inodes,
        ))

    _metric_tracker.prune({p.pid for p in procs})

    socket_owners: dict[int, int] = {}
    for proc in procs:
        for inode in proc.socket_inodes or ():
            socket_owners[inode] = socket_owners.get(inode, 0) + 1
    for proc in procs:
        for inode in proc.socket_inodes or ():
            rate = socket_rates.get(inode)
            if rate is None:
                continue
            owners = socket_owners.get(inode, 1)
            if proc.download_bps is not None:
                proc.download_bps += rate[0] / owners
            if proc.upload_bps is not None:
                proc.upload_bps += rate[1] / owners

    groups: dict[str, ProcessGroup] = {}
    for p in procs:
        key = p.group_key or p.name.lower()
        g = groups.get(key)
        if g is None:
            groups[key] = ProcessGroup(key=key, name=p.name, instances=[p])
        else:
            g.instances.append(p)
            # Prefer a more specific display name over a bare runtime name
            if _is_runtime_binary(g.name) and not _is_runtime_binary(p.name):
                g.name = p.name

    ordered = sort_process_groups(list(groups.values()), sort_key, ascending)
    return ordered[:limit]


def attach_group_icons(groups: list[ProcessGroup]) -> None:
    """Resolve QIcons on the GUI thread (cheap after the first cache fill)."""
    for g in groups:
        exe = g.instances[0].exe if g.instances else ""
        exe_base = Path(exe).name if exe else ""
        icon = resolve_process_icon(
            g.name, exe if not _is_runtime_binary(exe_base) else "",
        )
        if icon.isNull() and exe:
            icon = resolve_process_icon(g.name, exe)
        g.icon = icon
        for p in g.instances:
            p.icon = icon


def stop_process(pid: int, force: bool = False) -> bool:
    """Send SIGTERM (or SIGKILL if *force*) to *pid*."""
    if pid <= 1:
        return False
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False


def stop_processes(pids: list[int], force: bool = False) -> None:
    for pid in pids:
        stop_process(pid, force=force)


# ── UI (QTreeWidget columns, same approach as Sensors page) ─────────────────

# Columns: Process | CPU | RAM | Download | Upload | Read | Write
_COL_PROCESS = 0
_COL_CPU = 1
_COL_RAM = 2
_COL_DOWNLOAD = 3
_COL_UPLOAD = 4
_COL_READ = 5
_COL_WRITE = 6
_VALUE_COLUMNS = (
    _COL_CPU,
    _COL_RAM,
    _COL_DOWNLOAD,
    _COL_UPLOAD,
    _COL_READ,
    _COL_WRITE,
)

_ROLE_PID = Qt.UserRole
_ROLE_GROUP_KEY = Qt.UserRole + 1
_ROLE_KIND = Qt.UserRole + 2  # "group" | "instance"
_SORT_KEYS = {
    _COL_PROCESS: "process",
    _COL_CPU: "cpu",
    _COL_RAM: "ram",
    _COL_DOWNLOAD: "download",
    _COL_UPLOAD: "upload",
    _COL_READ: "read",
    _COL_WRITE: "write",
}


class TopProcessesCard(QFrame):
    """Double-width card: grouped processes in an aligned tree table."""

    # Emitted from a worker thread with list[ProcessGroup] (no QIcons yet)
    _scan_finished = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("topProcessesCard")
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        surface = COLORS["surface"]
        raised = COLORS["surface_raised"]
        self.setStyleSheet(f"""
            #topProcessesCard {{
                background-color: {surface};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

        self._tree = QTreeWidget()
        self._tree.setObjectName("topProcessesTree")
        self._tree.setColumnCount(7)
        self._tree.setHeaderLabels([
            "Process", "CPU", "RAM", "Download", "Upload", "Read", "Write",
        ])
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        self._tree.setAnimated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tree.setFocusPolicy(Qt.StrongFocus)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        # Expand/collapse only via the branch arrow, not by clicking the row
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)

        # Background /proc scan — never block the GUI thread
        self._scan_lock = threading.Lock()
        self._scan_busy = False
        self._scan_again = False
        self._active = False
        self._scan_cancel = threading.Event()
        self._scan_cancel.set()
        self._reset_rates = True
        self._scan_finished.connect(self._on_scan_finished)

        # Flat fill matching the card; selection highlight on click
        self._tree.setStyleSheet(f"""
            QTreeWidget#topProcessesTree {{
                background-color: {surface};
                border: none;
                border-radius: 0;
                outline: none;
                color: {COLORS['text']};
            }}
            QTreeWidget#topProcessesTree::item {{
                padding: 3px 6px;
                background-color: {surface};
            }}
            QTreeWidget#topProcessesTree::item:hover {{
                background-color: {raised};
            }}
            QTreeWidget#topProcessesTree::item:selected {{
                background-color: {raised};
                color: {COLORS['text']};
            }}
            QTreeWidget#topProcessesTree::item:selected:active {{
                background-color: {raised};
                color: {COLORS['text']};
            }}
            QHeaderView::section {{
                background-color: {surface};
                color: {COLORS['text_secondary']};
                border: none;
                border-bottom: 1px solid {COLORS['border']};
                /* Keep text clear of Qt's native KDE sort indicator. */
                padding: 4px 24px 4px 8px;
                font-weight: bold;
                font-size: 11px;
            }}
            QHeaderView {{
                background-color: {surface};
            }}
        """)

        header = self._tree.header()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(_COL_RAM, Qt.DescendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setSectionResizeMode(_COL_PROCESS, QHeaderView.Stretch)
        for column in _VALUE_COLUMNS:
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        self._tree.setColumnWidth(_COL_CPU, 80)
        self._tree.setColumnWidth(_COL_RAM, 88)
        for column in (_COL_DOWNLOAD, _COL_UPLOAD, _COL_READ, _COL_WRITE):
            self._tree.setColumnWidth(column, 100)

        for column in _VALUE_COLUMNS:
            self._tree.headerItem().setTextAlignment(
                column, int(Qt.AlignRight | Qt.AlignVCenter),
            )
        self._tree.headerItem().setToolTip(
            _COL_CPU,
            "Share of total logical CPU capacity, matching KDE System Monitor.",
        )
        self._tree.headerItem().setToolTip(
            _COL_DOWNLOAD,
            "Live TCP receive rate; short-lived and non-TCP traffic is not estimated.",
        )
        self._tree.headerItem().setToolTip(
            _COL_UPLOAD,
            "Live acknowledged TCP send rate; short-lived and non-TCP traffic is not estimated.",
        )
        self._tree.headerItem().setToolTip(
            _COL_READ, "Physical storage read rate from /proc/<pid>/io.",
        )
        self._tree.headerItem().setToolTip(
            _COL_WRITE, "Physical storage write rate from /proc/<pid>/io.",
        )

        layout.addWidget(self._tree)

        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._groups_by_key: dict[str, ProcessGroup] = {}
        self._instance_items: dict[int, QTreeWidgetItem] = {}
        # Selection restore across refresh: ("group", key) or ("instance", pid)
        self._selected_ref: tuple[str, object] | None = None
        self._sort_column = _COL_RAM
        self._sort_ascending = False
        self._tree.itemSelectionChanged.connect(self._remember_selection)

    def set_active(self, active: bool) -> None:
        """Enable scans only while the Processes tab is actually visible."""
        with self._scan_lock:
            if active == self._active:
                return
            self._active = active
            self._scan_again = False
            self._scan_cancel.set()
            if active:
                self._scan_cancel = threading.Event()

    def refresh(self):
        """Kick off a background /proc scan; UI updates when it completes."""
        with self._scan_lock:
            if not self._active:
                return
            if self._scan_busy:
                # Coalesce: run one more scan after the in-flight one finishes
                self._scan_again = True
                return
            self._scan_busy = True
            cancel = self._scan_cancel
            reset_rates = self._reset_rates
            self._reset_rates = False
        threading.Thread(
            target=self._scan_worker,
            args=(cancel, reset_rates, self._sort_key(), self._sort_ascending),
            daemon=True,
            name="proc-scan",
        ).start()

    def _scan_worker(
        self, cancel: threading.Event, reset_rates: bool,
        sort_key: str, ascending: bool,
    ):
        try:
            groups = read_process_groups(
                cancelled=cancel.is_set,
                reset_rates=reset_rates,
                sort_key=sort_key,
                ascending=ascending,
            )
        except Exception:
            groups = []
        self._scan_finished.emit((cancel, groups))

    def _on_scan_finished(self, result: object):
        if not isinstance(result, tuple) or len(result) != 2:
            return
        cancel, groups = result
        group_list: list[ProcessGroup] = groups if isinstance(groups, list) else []
        with self._scan_lock:
            self._scan_busy = False
            current = (
                self._active
                and cancel is self._scan_cancel
                and not self._scan_cancel.is_set()
            )
            again = self._active and self._scan_again
            self._scan_again = False
        if not current:
            if again:
                self.refresh()
            return

        # QIcon / theme work stays on the GUI thread
        try:
            attach_group_icons(group_list)
        except Exception:
            pass
        self.update_groups(group_list)

        if again:
            self.refresh()

    def update_groups(self, groups: list[ProcessGroup]):
        groups = sort_process_groups(groups, self._sort_key(), self._sort_ascending)
        self._groups_by_key = {g.key: g for g in groups}
        new_keys = [g.key for g in groups]

        scroll = self._tree.verticalScrollBar().value()
        expanded = {
            key for key, item in self._group_items.items() if item.isExpanded()
        }
        # Capture selection before any rebuild
        self._remember_selection()

        structure_changed = new_keys != list(self._group_items.keys())

        self._tree.setUpdatesEnabled(False)
        try:
            if not structure_changed and self._group_items:
                self._update_in_place(groups)
                self._restore_selection()
                return

            self._tree.clear()
            self._group_items.clear()
            self._instance_items.clear()

            if not groups:
                return

            for group in groups:
                gitem = QTreeWidgetItem(self._tree)
                self._fill_group_item(gitem, group)
                gitem.setExpanded(group.key in expanded)
                gitem.setChildIndicatorPolicy(
                    QTreeWidgetItem.ShowIndicator if group.count > 1
                    else QTreeWidgetItem.DontShowIndicator
                )
                font = gitem.font(_COL_PROCESS)
                font.setBold(True)
                gitem.setFont(_COL_PROCESS, font)
                self._group_items[group.key] = gitem

                if group.count > 1:
                    for proc in group.instances:
                        child = QTreeWidgetItem(gitem)
                        self._fill_instance_item(child, proc)
                        self._instance_items[proc.pid] = child

            self._restore_selection()
        finally:
            self._tree.setUpdatesEnabled(True)
            QTimer.singleShot(0, lambda: self._tree.verticalScrollBar().setValue(scroll))

    def _update_in_place(self, groups: list[ProcessGroup]):
        """Refresh values without rebuilding (keeps expand + selection)."""
        live_pids: set[int] = set()
        for group in groups:
            gitem = self._group_items.get(group.key)
            if gitem is None:
                continue
            self._fill_group_item(gitem, group)

            if group.count <= 1:
                while gitem.childCount():
                    ch = gitem.child(0)
                    pid = ch.data(0, _ROLE_PID)
                    if isinstance(pid, int):
                        self._instance_items.pop(pid, None)
                    gitem.removeChild(ch)
                gitem.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
                continue

            gitem.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            existing: dict[int, QTreeWidgetItem] = {}
            for i in range(gitem.childCount()):
                ch = gitem.child(i)
                pid = ch.data(0, _ROLE_PID)
                if isinstance(pid, int):
                    existing[pid] = ch

            wanted_pids = [p.pid for p in group.instances]
            for pid, ch in list(existing.items()):
                if pid not in wanted_pids:
                    gitem.removeChild(ch)
                    self._instance_items.pop(pid, None)
                    existing.pop(pid, None)

            for ii, proc in enumerate(group.instances):
                live_pids.add(proc.pid)
                ch = existing.get(proc.pid)
                if ch is None:
                    ch = QTreeWidgetItem()
                    gitem.insertChild(ii, ch)
                    self._fill_instance_item(ch, proc)
                    self._instance_items[proc.pid] = ch
                else:
                    cur_index = gitem.indexOfChild(ch)
                    if cur_index != ii:
                        gitem.takeChild(cur_index)
                        gitem.insertChild(ii, ch)
                    self._fill_instance_item(ch, proc)

        for pid in list(self._instance_items.keys()):
            if pid not in live_pids:
                self._instance_items.pop(pid, None)

    def _fill_group_item(self, item: QTreeWidgetItem, group: ProcessGroup):
        label = f"{group.name}  ({group.count})" if group.count > 1 else group.name
        item.setText(_COL_PROCESS, label)
        item.setData(0, _ROLE_GROUP_KEY, group.key)
        item.setData(0, _ROLE_KIND, "group")
        item.setData(0, _ROLE_PID, None)
        if not group.icon.isNull():
            item.setIcon(_COL_PROCESS, group.icon)
        item.setText(_COL_CPU, group.cpu_display)
        item.setText(_COL_RAM, group.ram_display)
        item.setText(_COL_DOWNLOAD, group.download_display)
        item.setText(_COL_UPLOAD, group.upload_display)
        item.setText(_COL_READ, group.read_display)
        item.setText(_COL_WRITE, group.write_display)
        for column in _VALUE_COLUMNS:
            item.setTextAlignment(column, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setToolTip(_COL_PROCESS, group.name)

    def _fill_instance_item(self, item: QTreeWidgetItem, proc: ProcessInfo):
        item.setText(_COL_PROCESS, proc.detail)
        item.setData(0, _ROLE_PID, proc.pid)
        item.setData(0, _ROLE_KIND, "instance")
        item.setData(0, _ROLE_GROUP_KEY, proc.group_key)
        item.setText(_COL_CPU, proc.cpu_display)
        item.setText(_COL_RAM, proc.ram_display)
        item.setText(_COL_DOWNLOAD, proc.download_display)
        item.setText(_COL_UPLOAD, proc.upload_display)
        item.setText(_COL_READ, proc.read_display)
        item.setText(_COL_WRITE, proc.write_display)
        for column in _VALUE_COLUMNS:
            item.setTextAlignment(column, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setToolTip(_COL_PROCESS, f"{proc.name}  (PID {proc.pid})")
        item.setIcon(_COL_PROCESS, QIcon())

    def _sort_key(self) -> str:
        return _SORT_KEYS[self._sort_column]

    def _on_header_clicked(self, column: int) -> None:
        if column not in _SORT_KEYS:
            return
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = column == _COL_PROCESS
        order = Qt.AscendingOrder if self._sort_ascending else Qt.DescendingOrder
        self._tree.header().setSortIndicator(column, order)
        self.update_groups(list(self._groups_by_key.values()))
        self.refresh()

    def _remember_selection(self):
        item = self._tree.currentItem()
        if item is None:
            return
        kind = item.data(0, _ROLE_KIND)
        if kind == "group":
            key = item.data(0, _ROLE_GROUP_KEY)
            if key is not None:
                self._selected_ref = ("group", key)
        elif kind == "instance":
            pid = item.data(0, _ROLE_PID)
            if isinstance(pid, int):
                self._selected_ref = ("instance", pid)

    def _restore_selection(self):
        ref = self._selected_ref
        if ref is None:
            return
        kind, value = ref
        item = None
        if kind == "group":
            item = self._group_items.get(value)  # type: ignore[arg-type]
        elif kind == "instance":
            item = self._instance_items.get(value)  # type: ignore[arg-type]
            # If instance vanished, select parent group
            if item is None and isinstance(value, int):
                for g in self._groups_by_key.values():
                    if any(p.pid == value for p in g.instances):
                        break
                else:
                    # try group of last known
                    pass
        if item is not None:
            self._tree.setCurrentItem(item)
            item.setSelected(True)

    def _on_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        # Right-click selects / highlights the row
        self._tree.setCurrentItem(item)
        item.setSelected(True)
        self._remember_selection()

        pids = self._pids_for_item(item)
        if not pids:
            return

        menu = QMenu(self)
        multi = len(pids) > 1
        force_label = "Force stop all" if multi else "Force stop"
        act_force = menu.addAction(force_label)

        chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if chosen is act_force:
            stop_processes(pids, force=True)
            self.refresh()

    def _pids_for_item(self, item: QTreeWidgetItem) -> list[int]:
        kind = item.data(0, _ROLE_KIND)
        if kind == "instance":
            pid = item.data(0, _ROLE_PID)
            return [pid] if isinstance(pid, int) and pid > 1 else []
        if kind == "group":
            key = item.data(0, _ROLE_GROUP_KEY)
            group = self._groups_by_key.get(key) if key is not None else None
            if group is None:
                return []
            return [p.pid for p in group.instances if p.pid > 1]
        return []
