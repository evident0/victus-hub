"""Top processes card — icon, name, CPU, RAM, and kill control."""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QWidget, QScrollArea,
)

from hp_helper.theme import COLORS

_TOP_N = 12
_ICON_PX = 20
_CPU_HZ = os.sysconf(os.sysconf_names["SC_CLK_TCK"]) if hasattr(os, "sysconf") else 100
try:
    _NCPU = max(1, os.cpu_count() or 1)
except Exception:
    _NCPU = 1

# Common process → icon theme / desktop name aliases
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

# Cached desktop Icon= lookups: exe basename / app id → icon name
_desktop_icon_cache: dict[str, str] | None = None
# Cached QIcons by theme/name key
_icon_cache: dict[str, QIcon] = {}


@dataclass
class ProcessInfo:
    pid: int
    name: str
    rss_kb: int
    cpu_pct: float = 0.0
    exe: str = ""
    icon: QIcon = field(default_factory=QIcon)

    @property
    def ram_display(self) -> str:
        mb = self.rss_kb / 1024.0
        if mb >= 1024.0:
            return f"{mb / 1024.0:.1f} GB"
        return f"{mb:.0f} MB"

    @property
    def cpu_display(self) -> str:
        if self.cpu_pct < 0.05:
            return "0%"
        if self.cpu_pct < 10:
            return f"{self.cpu_pct:.1f}%"
        return f"{self.cpu_pct:.0f}%"


def _read_proc_cpu_jiffies(pid: int) -> int | None:
    """Return utime+stime jiffies for *pid*, or None."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            data = f.read()
        # comm may contain spaces/parens — split after last ')'
        rparen = data.rfind(")")
        if rparen < 0:
            return None
        fields = data[rparen + 2 :].split()
        # utime=12, stime=13 in fields after (comm) — 0-indexed from field 3 overall
        # After rparen+2, fields[0] is state, [11]=utime, [12]=stime
        utime = int(fields[11])
        stime = int(fields[12])
        return utime + stime
    except (OSError, ValueError, IndexError):
        return None


def _read_proc_meta(pid: int) -> tuple[str, int, str] | None:
    """Return (display_name, rss_kb, exe) or None."""
    name = None
    rss_kb = None
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        rss_kb = int(parts[1])
                if name is not None and rss_kb is not None:
                    break
    except (OSError, ValueError):
        return None
    if name is None or rss_kb is None or rss_kb <= 0:
        return None

    exe = ""
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
        # Kernel may append " (deleted)"
        if exe.endswith(" (deleted)"):
            exe = exe[: -len(" (deleted)")]
    except OSError:
        pass

    # Prefer a cleaner display name from cmdline / exe for known apps
    display = name
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
        if raw:
            args = [a.decode("utf-8", errors="replace") for a in raw.split(b"\x00") if a]
            if args:
                base = Path(args[0]).name
                if base and base not in (name,):
                    # Keep short content-process names (e.g. Isolated Web Co) as-is
                    # but use exe basename when status Name is a truncated binary
                    if len(name) >= 15 or name.endswith("…"):
                        display = base
    except OSError:
        pass

    if exe:
        exe_base = Path(exe).name
        # For generic kernel-style names, prefer exe basename
        if name in ("MainThread", "Web Content", "Isolated Web Co", "RDD Process",
                    "Privileged Cont", "Socket Process") or name.startswith("WebExtensions"):
            display = exe_base or name
        elif not display or display == name:
            # e.g. "firefox" stays firefox
            pass

    return display, rss_kb, exe


def _ensure_desktop_cache() -> dict[str, str]:
    """Map lowercased app keys → Icon= name from .desktop files."""
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
            # Key by desktop stem, Exec binary, and Name
            stem = desk.stem.lower()
            cache[stem] = icon
            if name:
                cache[name.lower()] = icon
            if exec_line:
                # strip field codes and args
                bin_part = exec_line.split()[0]
                bin_part = bin_part.replace("%u", "").replace("%U", "").replace("%f", "").replace("%F", "")
                base = Path(bin_part).name.lower()
                if base:
                    cache.setdefault(base, icon)
    _desktop_icon_cache = cache
    return cache


def _icon_from_filesystem(name: str) -> QIcon:
    """Load *name* from standard icon theme directories on disk."""
    if not name or name.startswith("/"):
        return QIcon()
    # Prefer larger sizes then scale down in the UI
    sizes = ("48x48", "32x32", "64x64", "24x24", "22x22", "16x16", "scalable", "256x256")
    themes = ("hicolor", "breeze", "breeze-dark", "Adwaita", "Papirus", "Papirus-Dark")
    exts = ("png", "svg", "svgz", "xpm")
    roots = [
        Path("/usr/share/icons"),
        Path("/usr/local/share/icons"),
        Path.home() / ".local/share/icons",
        Path("/usr/share/pixmaps"),
    ]
    # pixmaps often stores bare name.png
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


def resolve_process_icon(name: str, exe: str = "") -> QIcon:
    """Best-effort process icon from the icon theme / .desktop files / filesystem."""
    if not QIcon.themeName():
        # Offscreen / minimal sessions may not set a theme
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
        # strip common suffixes
        for suf in (".bin", "-bin", ".py", ".sh"):
            if low.endswith(suf):
                keys.append(low[: -len(suf)])

    desk = _ensure_desktop_cache()
    for k in list(keys):
        if k in desk:
            keys.append(desk[k])

    # de-dupe preserving order
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
        # Absolute path icons from desktop files
        if key.startswith("/"):
            pm = QPixmap(key)
            icon = QIcon(pm) if not pm.isNull() else QIcon()
        else:
            icon = QIcon.fromTheme(key)
            if icon.isNull():
                icon = _icon_from_filesystem(key)
        _icon_cache[key] = icon
        if not icon.isNull():
            return icon

    fallback = QIcon.fromTheme("application-x-executable")
    if fallback.isNull():
        fallback = _icon_from_filesystem("application-x-executable")
    if fallback.isNull():
        fallback = QIcon.fromTheme("application-default-icon")
    return fallback


class _CpuTracker:
    """Tracks per-pid CPU jiffies across refreshes to compute % usage."""

    def __init__(self):
        self._prev: dict[int, tuple[int, float]] = {}  # pid → (jiffies, monotonic_ts)

    def cpu_pct(self, pid: int, jiffies: int | None) -> float:
        now = time.monotonic()
        if jiffies is None:
            self._prev.pop(pid, None)
            return 0.0
        prev = self._prev.get(pid)
        self._prev[pid] = (jiffies, now)
        if prev is None:
            return 0.0
        prev_j, prev_t = prev
        dt = now - prev_t
        if dt <= 0:
            return 0.0
        dj = jiffies - prev_j
        if dj < 0:
            return 0.0
        # percent of one core; can exceed 100 on multi-threaded procs
        pct = (dj / _CPU_HZ) / dt * 100.0
        return max(0.0, min(pct, 100.0 * _NCPU))

    def prune(self, live_pids: set[int]):
        dead = [p for p in self._prev if p not in live_pids]
        for p in dead:
            del self._prev[p]


_cpu_tracker = _CpuTracker()


def read_top_processes(limit: int = _TOP_N) -> list[ProcessInfo]:
    """Return top processes by RAM, with CPU% and icons."""
    procs: list[ProcessInfo] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []

    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        meta = _read_proc_meta(pid)
        if meta is None:
            continue
        name, rss_kb, exe = meta
        jiffies = _read_proc_cpu_jiffies(pid)
        cpu_pct = _cpu_tracker.cpu_pct(pid, jiffies)
        icon = resolve_process_icon(name, exe)
        procs.append(ProcessInfo(
            pid=pid,
            name=name,
            rss_kb=rss_kb,
            cpu_pct=cpu_pct,
            exe=exe,
            icon=icon,
        ))

    _cpu_tracker.prune({p.pid for p in procs})
    procs.sort(key=lambda p: p.rss_kb, reverse=True)
    return procs[:limit]


def kill_process(pid: int) -> bool:
    """Send SIGTERM to *pid*. Returns True if the signal was delivered."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False


_LABEL_STYLE = f"""
    QLabel {{
        color: {COLORS['text_secondary']};
        font-size: 12px;
        background: transparent;
    }}
"""
_NAME_STYLE = f"""
    QLabel {{
        color: {COLORS['text']};
        font-size: 12px;
        font-weight: 600;
        background: transparent;
    }}
"""


class _ProcessRow(QWidget):
    kill_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = 0
        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)

        # Icon
        self._icon = QLabel()
        self._icon.setFixedSize(_ICON_PX, _ICON_PX)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("background: transparent;")
        row.addWidget(self._icon, 0)

        # Name
        self._name = QLabel("—")
        self._name.setStyleSheet(_NAME_STYLE)
        self._name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self._name, 1)

        # CPU
        self._cpu = QLabel("—")
        self._cpu.setStyleSheet(_LABEL_STYLE)
        self._cpu.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._cpu.setMinimumWidth(48)
        self._cpu.setFixedWidth(52)
        row.addWidget(self._cpu, 0)

        # RAM
        self._ram = QLabel("—")
        self._ram.setStyleSheet(_LABEL_STYLE)
        self._ram.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._ram.setMinimumWidth(56)
        self._ram.setFixedWidth(64)
        row.addWidget(self._ram, 0)

        # Kill
        self._kill = QPushButton("Kill")
        self._kill.setCursor(Qt.PointingHandCursor)
        self._kill.setFixedHeight(24)
        self._kill.setFixedWidth(52)
        self._kill.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['surface_raised']};
                color: {COLORS['text']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
                padding: 0 6px;
            }}
            QPushButton:hover {{
                background-color: {COLORS['accent_red']};
                border-color: {COLORS['accent_red']};
                color: #ffffff;
            }}
            QPushButton:pressed {{
                background-color: #cc1a1a;
            }}
            QPushButton:disabled {{
                color: {COLORS['text_secondary']};
                background-color: {COLORS['surface']};
            }}
        """)
        self._kill.clicked.connect(lambda: self.kill_clicked.emit(self._pid))
        row.addWidget(self._kill, 0)

    def set_process(self, proc: ProcessInfo):
        self._pid = proc.pid
        self._name.setText(proc.name)
        self._name.setToolTip(f"{proc.name}  (PID {proc.pid})")
        self._cpu.setText(proc.cpu_display)
        self._ram.setText(proc.ram_display)
        self._kill.setEnabled(proc.pid > 1)

        if not proc.icon.isNull():
            self._icon.setPixmap(proc.icon.pixmap(QSize(_ICON_PX, _ICON_PX)))
        else:
            self._icon.clear()


class TopProcessesCard(QFrame):
    """Double-width card: icon · name · CPU · RAM · kill."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("topProcessesCard")
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(f"""
            #topProcessesCard {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        title = QLabel("Top Processes")
        title.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }}
        """)
        header.addWidget(title, 1)

        hdr_style = f"""
            QLabel {{
                color: {COLORS['text_secondary']};
                font-size: 11px;
                background: transparent;
            }}
        """
        col_cpu = QLabel("CPU")
        col_cpu.setStyleSheet(hdr_style)
        col_cpu.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        col_cpu.setFixedWidth(52)
        header.addWidget(col_cpu)

        col_ram = QLabel("RAM")
        col_ram.setStyleSheet(hdr_style)
        col_ram.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        col_ram.setFixedWidth(64)
        header.addWidget(col_ram)

        # Spacer matching kill button width
        spacer = QLabel("")
        spacer.setFixedWidth(52)
        header.addWidget(spacer)
        layout.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll.viewport().setStyleSheet("background: transparent;")

        self._list_host = QWidget()
        self._list_host.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch(1)

        self._scroll.setWidget(self._list_host)
        layout.addWidget(self._scroll, 1)

        self._empty = QLabel("No processes found")
        self._empty.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_secondary']};
                font-size: 12px;
                background: transparent;
            }}
        """)
        self._empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._empty, 1)
        self._empty.hide()

        self._rows: list[_ProcessRow] = []

    def refresh(self):
        """Re-scan /proc and update the list."""
        self.update_processes(read_top_processes())

    def update_processes(self, processes: list[ProcessInfo]):
        if not processes:
            for row in self._rows:
                row.hide()
            self._scroll.hide()
            self._empty.show()
            return

        self._empty.hide()
        self._scroll.show()

        while len(self._rows) < len(processes):
            row = _ProcessRow()
            row.kill_clicked.connect(self._on_kill)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._rows.append(row)
        while len(self._rows) > len(processes):
            row = self._rows.pop()
            self._list_layout.removeWidget(row)
            row.deleteLater()

        for row, proc in zip(self._rows, processes):
            row.set_process(proc)
            row.show()

    def _on_kill(self, pid: int):
        kill_process(pid)
        self.refresh()
