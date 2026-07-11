"""Top processes card — grouped instances, icon, CPU, RAM, and Stop."""

from __future__ import annotations

import os
import signal
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHeaderView, QMenu, QVBoxLayout,
    QSizePolicy, QTreeWidget, QTreeWidgetItem,
)

from hp_helper.theme import COLORS

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
    exe: str = ""
    # Stable group identity (script path, module, or binary path)
    group_key: str = ""
    # Short label for a child instance (comm / role)
    detail: str = ""
    icon: QIcon = field(default_factory=QIcon)

    @property
    def ram_display(self) -> str:
        return _fmt_ram(self.rss_kb)

    @property
    def cpu_display(self) -> str:
        return _fmt_cpu(self.cpu_pct)


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
        # Sum of per-process system shares; clamp for sampling noise
        return max(0.0, min(100.0, sum(p.cpu_pct for p in self.instances)))

    @property
    def ram_display(self) -> str:
        return _fmt_ram(self.rss_kb)

    @property
    def cpu_display(self) -> str:
        return _fmt_cpu(self.cpu_pct)

    @property
    def count(self) -> int:
        return len(self.instances)


def _fmt_ram(rss_kb: int) -> str:
    mb = rss_kb / 1024.0
    if mb >= 1024.0:
        return f"{mb / 1024.0:.1f} GB"
    return f"{mb:.0f} MB"


def _fmt_cpu(cpu_pct: float) -> str:
    # System-relative share (0–100% of all cores combined)
    if cpu_pct < 0.05:
        return "0%"
    if cpu_pct < 10:
        return f"{cpu_pct:.1f}%"
    return f"{cpu_pct:.0f}%"


def _parse_stat_jiffies(data: str) -> int | None:
    """Parse utime+stime from a /proc/.../stat payload."""
    rparen = data.rfind(")")
    if rparen < 0:
        return None
    fields = data[rparen + 2 :].split()
    try:
        return int(fields[11]) + int(fields[12])
    except (ValueError, IndexError):
        return None


def _read_proc_cpu_jiffies(pid: int) -> int | None:
    """Total user+system jiffies for the whole process (all threads)."""
    task_dir = f"/proc/{pid}/task"
    try:
        tids = os.listdir(task_dir)
    except OSError:
        tids = []
    if tids:
        total = 0
        any_ok = False
        for tid in tids:
            try:
                with open(
                    f"{task_dir}/{tid}/stat", encoding="utf-8", errors="replace"
                ) as f:
                    j = _parse_stat_jiffies(f.read())
            except OSError:
                continue
            if j is not None:
                total += j
                any_ok = True
        if any_ok:
            return total
    # Fallback: thread-group leader only
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            return _parse_stat_jiffies(f.read())
    except OSError:
        return None


def _read_system_jiffies() -> int | None:
    """Sum of all fields on the aggregate `cpu` line in /proc/stat."""
    try:
        with open("/proc/stat", encoding="utf-8", errors="replace") as f:
            line = f.readline()
    except OSError:
        return None
    if not line.startswith("cpu "):
        return None
    parts = line.split()
    try:
        # cpu user nice system idle iowait irq softirq steal guest guest_nice …
        return sum(int(x) for x in parts[1:])
    except ValueError:
        return None


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


class _CpuTracker:
    """Track per-pid and system jiffies to compute system-relative CPU %."""

    def __init__(self):
        # pid → process jiffies at last sample
        self._prev_proc: dict[int, int] = {}
        self._prev_sys: int | None = None

    def begin_sample(self) -> int | None:
        """Read system jiffies once per refresh; return system delta (or None)."""
        sys_now = _read_system_jiffies()
        if sys_now is None:
            self._prev_sys = None
            return None
        prev = self._prev_sys
        self._prev_sys = sys_now
        if prev is None or sys_now < prev:
            return None
        dsys = sys_now - prev
        return dsys if dsys > 0 else None

    def cpu_pct(self, pid: int, jiffies: int | None, system_delta: int | None) -> float:
        """Return % of total machine CPU used by *pid* since last sample (0–100)."""
        if jiffies is None:
            self._prev_proc.pop(pid, None)
            return 0.0
        prev_j = self._prev_proc.get(pid)
        self._prev_proc[pid] = jiffies
        if prev_j is None or system_delta is None:
            return 0.0
        dj = jiffies - prev_j
        if dj < 0:
            return 0.0
        # system_delta already counts all cores (sum of /proc/stat cpu fields)
        pct = 100.0 * dj / system_delta
        return max(0.0, min(pct, 100.0))

    def prune(self, live_pids: set[int]):
        for p in [p for p in self._prev_proc if p not in live_pids]:
            del self._prev_proc[p]


_cpu_tracker = _CpuTracker()


def read_process_groups(limit: int = _TOP_GROUPS) -> list[ProcessGroup]:
    """Scan /proc, group by app identity, return top groups by RAM.

    Thread-safe for a single concurrent scanner. Does **not** create QIcons
    (those are attached on the GUI thread via ``attach_group_icons``).
    """
    procs: list[ProcessInfo] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []

    system_delta = _cpu_tracker.begin_sample()

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        meta = _read_proc_meta(pid)
        if meta is None:
            continue
        group_name, detail, mem_kb, exe, group_key = meta
        jiffies = _read_proc_cpu_jiffies(pid)
        cpu_pct = _cpu_tracker.cpu_pct(pid, jiffies, system_delta)
        procs.append(ProcessInfo(
            pid=pid,
            name=group_name,
            rss_kb=mem_kb,  # PSS when available (field name kept for compatibility)
            cpu_pct=cpu_pct,
            exe=exe,
            group_key=group_key,
            detail=detail,
        ))

    _cpu_tracker.prune({p.pid for p in procs})

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

    for g in groups.values():
        g.instances.sort(key=lambda p: p.rss_kb, reverse=True)

    ordered = sorted(groups.values(), key=lambda g: g.rss_kb, reverse=True)
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

# Columns: Process | CPU | RAM
_COL_PROCESS = 0
_COL_CPU = 1
_COL_RAM = 2

_ROLE_PID = Qt.UserRole
_ROLE_GROUP_KEY = Qt.UserRole + 1
_ROLE_KIND = Qt.UserRole + 2  # "group" | "instance"


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
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Process", "CPU", "RAM"])
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
                padding: 4px 8px;
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
        header.setSectionResizeMode(_COL_PROCESS, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_CPU, QHeaderView.Fixed)
        header.setSectionResizeMode(_COL_RAM, QHeaderView.Fixed)
        self._tree.setColumnWidth(_COL_CPU, 64)
        self._tree.setColumnWidth(_COL_RAM, 72)

        self._tree.headerItem().setTextAlignment(_COL_CPU, int(Qt.AlignRight | Qt.AlignVCenter))
        self._tree.headerItem().setTextAlignment(_COL_RAM, int(Qt.AlignRight | Qt.AlignVCenter))

        layout.addWidget(self._tree)

        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._groups_by_key: dict[str, ProcessGroup] = {}
        self._instance_items: dict[int, QTreeWidgetItem] = {}
        # Selection restore across refresh: ("group", key) or ("instance", pid)
        self._selected_ref: tuple[str, object] | None = None
        self._tree.itemSelectionChanged.connect(self._remember_selection)

    def refresh(self):
        """Kick off a background /proc scan; UI updates when it completes."""
        with self._scan_lock:
            if self._scan_busy:
                # Coalesce: run one more scan after the in-flight one finishes
                self._scan_again = True
                return
            self._scan_busy = True
        threading.Thread(
            target=self._scan_worker, daemon=True, name="proc-scan",
        ).start()

    def _scan_worker(self):
        try:
            groups = read_process_groups()
        except Exception:
            groups = []
        self._scan_finished.emit(groups)

    def _on_scan_finished(self, groups: object):
        group_list: list[ProcessGroup] = groups if isinstance(groups, list) else []
        # QIcon / theme work stays on the GUI thread
        try:
            attach_group_icons(group_list)
        except Exception:
            pass
        self.update_groups(group_list)

        again = False
        with self._scan_lock:
            self._scan_busy = False
            again = self._scan_again
            self._scan_again = False
        if again:
            self.refresh()

    def update_groups(self, groups: list[ProcessGroup]):
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
        item.setTextAlignment(_COL_CPU, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setTextAlignment(_COL_RAM, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setToolTip(_COL_PROCESS, group.name)

    def _fill_instance_item(self, item: QTreeWidgetItem, proc: ProcessInfo):
        item.setText(_COL_PROCESS, proc.detail)
        item.setData(0, _ROLE_PID, proc.pid)
        item.setData(0, _ROLE_KIND, "instance")
        item.setData(0, _ROLE_GROUP_KEY, proc.group_key)
        item.setText(_COL_CPU, proc.cpu_display)
        item.setText(_COL_RAM, proc.ram_display)
        item.setTextAlignment(_COL_CPU, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setTextAlignment(_COL_RAM, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setToolTip(_COL_PROCESS, f"{proc.name}  (PID {proc.pid})")
        item.setIcon(_COL_PROCESS, QIcon())

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
        stop_label = "Stop all" if multi else "Stop"
        force_label = "Force stop all" if multi else "Force stop"
        act_stop = menu.addAction(stop_label)
        act_force = menu.addAction(force_label)

        chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        force = chosen is act_force
        if chosen is act_stop or chosen is act_force:
            stop_processes(pids, force=force)
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
