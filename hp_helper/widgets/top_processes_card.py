"""Top processes card — grouped instances, icon, CPU, RAM, and Stop."""

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

_TOP_GROUPS = 10
_ICON_PX = 20
# Fixed column widths so header and every row share the same grid
_COL_GAP = 8
_COL_EXPAND = 18
_COL_ICON = 20
_COL_CPU = 52
_COL_RAM = 64
_COL_STOP = 56

_CPU_HZ = os.sysconf(os.sysconf_names["SC_CLK_TCK"]) if hasattr(os, "sysconf") else 100
try:
    _NCPU = max(1, os.cpu_count() or 1)
except Exception:
    _NCPU = 1

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
    # Short label for a child instance (comm / role)
    detail: str = ""
    icon: QIcon = field(default_factory=QIcon)

    @property
    def group_key(self) -> str:
        if self.exe:
            return self.exe
        return self.name.lower()

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
    icon: QIcon
    instances: list[ProcessInfo] = field(default_factory=list)

    @property
    def rss_kb(self) -> int:
        return sum(p.rss_kb for p in self.instances)

    @property
    def cpu_pct(self) -> float:
        return sum(p.cpu_pct for p in self.instances)

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
    if cpu_pct < 0.05:
        return "0%"
    if cpu_pct < 10:
        return f"{cpu_pct:.1f}%"
    return f"{cpu_pct:.0f}%"


def _read_proc_cpu_jiffies(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            data = f.read()
        rparen = data.rfind(")")
        if rparen < 0:
            return None
        fields = data[rparen + 2 :].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, ValueError, IndexError):
        return None


def _read_proc_meta(pid: int) -> tuple[str, str, int, str] | None:
    """Return (group_name, detail, rss_kb, exe) or None."""
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
        if exe.endswith(" (deleted)"):
            exe = exe[: -len(" (deleted)")]
    except OSError:
        pass

    detail = name
    group_name = name
    exe_base = Path(exe).name if exe else ""

    # Browser content helpers → group under browser binary
    content_names = (
        "MainThread", "Web Content", "Isolated Web Co", "RDD Process",
        "Privileged Cont", "Socket Process", "Utility Process",
    )
    if name in content_names or name.startswith("WebExtensions") or name.startswith("Isolated "):
        group_name = exe_base or name
    elif exe_base:
        # Prefer executable basename as the group label when it matches well
        group_name = exe_base
        # Keep original short name as instance detail when different
        if name.lower() != exe_base.lower():
            detail = name
        else:
            detail = f"PID {pid}"
    else:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
            if raw:
                args = [a.decode("utf-8", errors="replace") for a in raw.split(b"\x00") if a]
                if args:
                    base = Path(args[0]).name
                    if base:
                        group_name = base
        except OSError:
            pass
        detail = f"PID {pid}" if detail == group_name else detail

    if not detail or detail == group_name:
        detail = f"PID {pid}"

    return group_name, detail, rss_kb, exe


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
    def __init__(self):
        self._prev: dict[int, tuple[int, float]] = {}

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
        pct = (dj / _CPU_HZ) / dt * 100.0
        return max(0.0, min(pct, 100.0 * _NCPU))

    def prune(self, live_pids: set[int]):
        for p in [p for p in self._prev if p not in live_pids]:
            del self._prev[p]


_cpu_tracker = _CpuTracker()


def read_process_groups(limit: int = _TOP_GROUPS) -> list[ProcessGroup]:
    """Scan /proc, group by executable/name, return top groups by RAM."""
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
        group_name, detail, rss_kb, exe = meta
        jiffies = _read_proc_cpu_jiffies(pid)
        cpu_pct = _cpu_tracker.cpu_pct(pid, jiffies)
        icon = resolve_process_icon(group_name, exe)
        procs.append(ProcessInfo(
            pid=pid,
            name=group_name,
            rss_kb=rss_kb,
            cpu_pct=cpu_pct,
            exe=exe,
            detail=detail,
            icon=icon,
        ))

    _cpu_tracker.prune({p.pid for p in procs})

    groups: dict[str, ProcessGroup] = {}
    for p in procs:
        key = p.group_key
        g = groups.get(key)
        if g is None:
            groups[key] = ProcessGroup(key=key, name=p.name, icon=p.icon, instances=[p])
        else:
            g.instances.append(p)
            # Prefer non-null icon
            if g.icon.isNull() and not p.icon.isNull():
                g.icon = p.icon

    for g in groups.values():
        g.instances.sort(key=lambda p: p.rss_kb, reverse=True)

    ordered = sorted(groups.values(), key=lambda g: g.rss_kb, reverse=True)
    return ordered[:limit]


def stop_process(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False


def stop_processes(pids: list[int]) -> None:
    for pid in pids:
        stop_process(pid)


# ── Shared styles / column helpers ──────────────────────────────────────────

_HDR_STYLE = f"""
    QLabel {{
        color: {COLORS['text_secondary']};
        font-size: 11px;
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
_DETAIL_STYLE = f"""
    QLabel {{
        color: {COLORS['text_secondary']};
        font-size: 11px;
        background: transparent;
    }}
"""
_METRIC_STYLE = f"""
    QLabel {{
        color: {COLORS['text_secondary']};
        font-size: 12px;
        background: transparent;
    }}
"""
_STOP_STYLE = f"""
    QPushButton {{
        background-color: {COLORS['surface_raised']};
        color: {COLORS['text']};
        border: 1px solid {COLORS['border']};
        border-radius: 4px;
        font-size: 11px;
        font-weight: 600;
        padding: 0 4px;
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
"""
_EXPAND_STYLE = f"""
    QPushButton {{
        background: transparent;
        color: {COLORS['text_secondary']};
        border: none;
        font-size: 11px;
        font-weight: bold;
        padding: 0;
    }}
    QPushButton:hover {{
        color: {COLORS['text']};
    }}
    QPushButton:disabled {{
        color: transparent;
    }}
"""


def _make_metric_label(width: int) -> QLabel:
    lbl = QLabel("—")
    lbl.setStyleSheet(_METRIC_STYLE)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lbl.setFixedWidth(width)
    return lbl


def _make_stop_button() -> QPushButton:
    btn = QPushButton("Stop")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(24)
    btn.setFixedWidth(_COL_STOP)
    btn.setStyleSheet(_STOP_STYLE)
    return btn


def _add_grid_columns(layout: QHBoxLayout, expand_w: QWidget, icon_w: QWidget,
                      name_w: QWidget, cpu_w: QWidget, ram_w: QWidget, stop_w: QWidget):
    """Add widgets in the shared column order with matching stretch/fixed widths."""
    layout.setSpacing(_COL_GAP)
    layout.addWidget(expand_w, 0)
    layout.addWidget(icon_w, 0)
    layout.addWidget(name_w, 1)
    layout.addWidget(cpu_w, 0)
    layout.addWidget(ram_w, 0)
    layout.addWidget(stop_w, 0)


class _InstanceRow(QWidget):
    """Child row under an expanded process group."""

    stop_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = 0
        self.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 1)

        expand_ph = QLabel("")
        expand_ph.setFixedWidth(_COL_EXPAND)
        expand_ph.setStyleSheet("background: transparent;")

        icon_ph = QLabel("")
        icon_ph.setFixedWidth(_COL_ICON)
        icon_ph.setStyleSheet("background: transparent;")

        self._name = QLabel("—")
        self._name.setStyleSheet(_DETAIL_STYLE)
        self._name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._cpu = _make_metric_label(_COL_CPU)
        self._ram = _make_metric_label(_COL_RAM)
        self._stop = _make_stop_button()
        self._stop.clicked.connect(lambda: self.stop_clicked.emit(self._pid))

        _add_grid_columns(row, expand_ph, icon_ph, self._name, self._cpu, self._ram, self._stop)

    def set_process(self, proc: ProcessInfo):
        self._pid = proc.pid
        self._name.setText(f"  {proc.detail}")
        self._name.setToolTip(f"{proc.name}  (PID {proc.pid})")
        self._cpu.setText(proc.cpu_display)
        self._ram.setText(proc.ram_display)
        self._stop.setEnabled(proc.pid > 1)


class _GroupRow(QWidget):
    """Parent row: icon · name · CPU · RAM · Stop, with optional instance dropdown."""

    stop_group = Signal(str)          # group key — stop all instances
    stop_pid = Signal(int)
    expand_toggled = Signal(str, bool)  # key, expanded

    def __init__(self, parent=None):
        super().__init__(parent)
        self._key = ""
        self._pids: list[int] = []
        self._expanded = False
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main (group) row
        main = QWidget()
        main.setStyleSheet("background: transparent;")
        row = QHBoxLayout(main)
        row.setContentsMargins(0, 2, 0, 2)

        self._expand = QPushButton("▸")
        self._expand.setFixedSize(_COL_EXPAND, 22)
        self._expand.setCursor(Qt.PointingHandCursor)
        self._expand.setStyleSheet(_EXPAND_STYLE)
        self._expand.clicked.connect(self._toggle)

        self._icon = QLabel()
        self._icon.setFixedSize(_COL_ICON, _COL_ICON)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("background: transparent;")

        self._name = QLabel("—")
        self._name.setStyleSheet(_NAME_STYLE)
        self._name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._cpu = _make_metric_label(_COL_CPU)
        self._ram = _make_metric_label(_COL_RAM)
        self._stop = _make_stop_button()
        self._stop.clicked.connect(lambda: self.stop_group.emit(self._key))

        _add_grid_columns(row, self._expand, self._icon, self._name, self._cpu, self._ram, self._stop)
        root.addWidget(main)

        # Dropdown container for instances
        self._children_host = QWidget()
        self._children_host.setStyleSheet(f"""
            background-color: {COLORS['surface_raised']};
            border-radius: 4px;
        """)
        self._children_layout = QVBoxLayout(self._children_host)
        self._children_layout.setContentsMargins(4, 4, 4, 4)
        self._children_layout.setSpacing(1)
        self._children_host.hide()
        root.addWidget(self._children_host)

        self._instance_rows: list[_InstanceRow] = []

    def set_group(self, group: ProcessGroup, expanded: bool):
        self._key = group.key
        self._pids = [p.pid for p in group.instances]
        self._expanded = expanded and group.count > 1

        if group.count > 1:
            self._name.setText(f"{group.name}  ({group.count})")
            self._expand.setEnabled(True)
            self._expand.setText("▾" if self._expanded else "▸")
        else:
            self._name.setText(group.name)
            self._expand.setEnabled(False)
            self._expand.setText(" ")
            self._expanded = False

        self._name.setToolTip(group.name)
        self._cpu.setText(group.cpu_display)
        self._ram.setText(group.ram_display)
        self._stop.setEnabled(any(pid > 1 for pid in self._pids))

        if not group.icon.isNull():
            self._icon.setPixmap(group.icon.pixmap(QSize(_ICON_PX, _ICON_PX)))
        else:
            self._icon.clear()

        # Instance rows
        if self._expanded:
            self._sync_instances(group.instances)
            self._children_host.show()
        else:
            self._children_host.hide()

    def _sync_instances(self, instances: list[ProcessInfo]):
        while len(self._instance_rows) < len(instances):
            row = _InstanceRow()
            row.stop_clicked.connect(self.stop_pid.emit)
            self._children_layout.addWidget(row)
            self._instance_rows.append(row)
        while len(self._instance_rows) > len(instances):
            row = self._instance_rows.pop()
            self._children_layout.removeWidget(row)
            row.deleteLater()
        for row, proc in zip(self._instance_rows, instances):
            row.set_process(proc)
            row.show()

    def _toggle(self):
        if not self._expand.isEnabled():
            return
        self._expanded = not self._expanded
        self.expand_toggled.emit(self._key, self._expanded)


class TopProcessesCard(QFrame):
    """Double-width card: grouped processes with instance dropdown."""

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
        layout.setSpacing(6)

        # Header — same column grid as rows
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)

        expand_ph = QLabel("")
        expand_ph.setFixedWidth(_COL_EXPAND)
        expand_ph.setStyleSheet("background: transparent;")

        icon_ph = QLabel("")
        icon_ph.setFixedWidth(_COL_ICON)
        icon_ph.setStyleSheet("background: transparent;")

        title = QLabel("Process")
        title.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }}
        """)
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        col_cpu = QLabel("CPU")
        col_cpu.setStyleSheet(_HDR_STYLE)
        col_cpu.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        col_cpu.setFixedWidth(_COL_CPU)

        col_ram = QLabel("RAM")
        col_ram.setStyleSheet(_HDR_STYLE)
        col_ram.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        col_ram.setFixedWidth(_COL_RAM)

        col_stop = QLabel("Stop")
        col_stop.setStyleSheet(_HDR_STYLE)
        col_stop.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        col_stop.setFixedWidth(_COL_STOP)

        _add_grid_columns(header_row, expand_ph, icon_ph, title, col_cpu, col_ram, col_stop)
        layout.addWidget(header)

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
        self._list_layout.setSpacing(4)
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

        self._rows: list[_GroupRow] = []
        self._expanded_keys: set[str] = set()
        self._groups_by_key: dict[str, ProcessGroup] = {}

    def refresh(self):
        self.update_groups(read_process_groups())

    def update_groups(self, groups: list[ProcessGroup]):
        self._groups_by_key = {g.key: g for g in groups}
        # Drop expanded state for groups that disappeared
        self._expanded_keys &= set(self._groups_by_key.keys())

        if not groups:
            for row in self._rows:
                row.hide()
            self._scroll.hide()
            self._empty.show()
            return

        self._empty.hide()
        self._scroll.show()

        while len(self._rows) < len(groups):
            row = _GroupRow()
            row.stop_group.connect(self._on_stop_group)
            row.stop_pid.connect(self._on_stop_pid)
            row.expand_toggled.connect(self._on_expand)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            self._rows.append(row)
        while len(self._rows) > len(groups):
            row = self._rows.pop()
            self._list_layout.removeWidget(row)
            row.deleteLater()

        for row, group in zip(self._rows, groups):
            row.set_group(group, group.key in self._expanded_keys)
            row.show()

    def _on_expand(self, key: str, expanded: bool):
        if expanded:
            self._expanded_keys.add(key)
        else:
            self._expanded_keys.discard(key)
        group = self._groups_by_key.get(key)
        if group is None:
            return
        for row in self._rows:
            if row._key == key:
                row.set_group(group, expanded)
                break

    def _on_stop_group(self, key: str):
        group = self._groups_by_key.get(key)
        if group is None:
            return
        stop_processes([p.pid for p in group.instances])
        self.refresh()

    def _on_stop_pid(self, pid: int):
        stop_process(pid)
        self.refresh()
