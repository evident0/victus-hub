"""Top processes card — grouped instances, icon, CPU, RAM, and Stop."""

from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHeaderView, QVBoxLayout, QPushButton,
    QSizePolicy, QTreeWidget, QTreeWidgetItem,
)

from hp_helper.theme import COLORS

_TOP_GROUPS = 10
_ICON_PX = 20

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


# ── UI (QTreeWidget columns, same approach as Sensors page) ─────────────────

_STOP_BTN_STYLE = f"""
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
"""

# Columns: Process | CPU | RAM | Stop
_COL_PROCESS = 0
_COL_CPU = 1
_COL_RAM = 2
_COL_STOP = 3


def _make_stop_button(enabled: bool = True) -> QPushButton:
    btn = QPushButton("Stop")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(52, 24)
    btn.setStyleSheet(_STOP_BTN_STYLE)
    btn.setEnabled(enabled)
    return btn


class TopProcessesCard(QFrame):
    """Double-width card: grouped processes in an aligned tree table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("topProcessesCard")
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        surface = COLORS["surface"]
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
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["Process", "CPU", "RAM", "Stop"])
        self._tree.setRootIsDecorated(True)
        self._tree.setIndentation(16)
        self._tree.setAnimated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setSelectionMode(QAbstractItemView.NoSelection)
        self._tree.setFocusPolicy(Qt.NoFocus)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        # Flat fill matching the card (overrides global QTreeWidget / header colors)
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
            QTreeWidget#topProcessesTree::item:hover,
            QTreeWidget#topProcessesTree::item:selected {{
                background-color: {surface};
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

        # Right-align metric columns (header + cells)
        header = self._tree.header()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(_COL_PROCESS, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_CPU, QHeaderView.Fixed)
        header.setSectionResizeMode(_COL_RAM, QHeaderView.Fixed)
        header.setSectionResizeMode(_COL_STOP, QHeaderView.Fixed)
        self._tree.setColumnWidth(_COL_CPU, 64)
        self._tree.setColumnWidth(_COL_RAM, 72)
        self._tree.setColumnWidth(_COL_STOP, 64)

        # Center the Stop header label over the button column
        self._tree.headerItem().setTextAlignment(_COL_CPU, int(Qt.AlignRight | Qt.AlignVCenter))
        self._tree.headerItem().setTextAlignment(_COL_RAM, int(Qt.AlignRight | Qt.AlignVCenter))
        self._tree.headerItem().setTextAlignment(_COL_STOP, int(Qt.AlignHCenter | Qt.AlignVCenter))

        self._tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._tree)

        self._group_items: dict[str, QTreeWidgetItem] = {}
        self._groups_by_key: dict[str, ProcessGroup] = {}
        # pid → item for instance rows
        self._instance_items: dict[int, QTreeWidgetItem] = {}

    def refresh(self):
        self.update_groups(read_process_groups())

    def update_groups(self, groups: list[ProcessGroup]):
        self._groups_by_key = {g.key: g for g in groups}
        new_keys = [g.key for g in groups]
        old_keys = set(self._group_items.keys())

        # Preserve expansion + scroll when structure is similar
        scroll = self._tree.verticalScrollBar().value()
        expanded = {
            key for key, item in self._group_items.items() if item.isExpanded()
        }

        structure_changed = new_keys != list(self._group_items.keys())

        if not structure_changed and self._group_items:
            self._update_in_place(groups)
            return

        self._tree.clear()
        self._group_items.clear()
        self._instance_items.clear()

        if not groups:
            return

        for gi, group in enumerate(groups):
            gitem = QTreeWidgetItem(self._tree)
            self._fill_group_item(gitem, group)
            gitem.setExpanded(group.key in expanded)
            # Only show expand affordance when there are multiple instances
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

        QTimer.singleShot(0, lambda: self._tree.verticalScrollBar().setValue(scroll))

    def _update_in_place(self, groups: list[ProcessGroup]):
        """Refresh values without rebuilding (keeps expand state)."""
        live_pids: set[int] = set()
        for group in groups:
            gitem = self._group_items.get(group.key)
            if gitem is None:
                continue
            self._fill_group_item(gitem, group, reuse_button=True)

            if group.count <= 1:
                # Remove any leftover children
                while gitem.childCount():
                    gitem.removeChild(gitem.child(0))
                gitem.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
                continue

            gitem.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            # Map existing children by pid stored in UserRole
            existing: dict[int, QTreeWidgetItem] = {}
            for i in range(gitem.childCount()):
                ch = gitem.child(i)
                pid = ch.data(0, Qt.UserRole)
                if isinstance(pid, int):
                    existing[pid] = ch

            wanted_pids = [p.pid for p in group.instances]
            # Remove stale
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
                    # Reorder if needed
                    cur_index = gitem.indexOfChild(ch)
                    if cur_index != ii:
                        gitem.takeChild(cur_index)
                        gitem.insertChild(ii, ch)
                    self._fill_instance_item(ch, proc, reuse_button=True)

        # Drop instance map entries that are gone
        for pid in list(self._instance_items.keys()):
            if pid not in live_pids and pid not in {
                p.pid for g in groups for p in g.instances
            }:
                self._instance_items.pop(pid, None)

    def _fill_group_item(self, item: QTreeWidgetItem, group: ProcessGroup,
                         reuse_button: bool = False):
        label = f"{group.name}  ({group.count})" if group.count > 1 else group.name
        item.setText(_COL_PROCESS, label)
        item.setData(0, Qt.UserRole + 1, group.key)  # group key
        item.setData(0, Qt.UserRole + 2, "group")
        if not group.icon.isNull():
            item.setIcon(_COL_PROCESS, group.icon)
        item.setText(_COL_CPU, group.cpu_display)
        item.setText(_COL_RAM, group.ram_display)
        item.setTextAlignment(_COL_CPU, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setTextAlignment(_COL_RAM, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setToolTip(_COL_PROCESS, group.name)

        if reuse_button:
            btn = self._tree.itemWidget(item, _COL_STOP)
            if isinstance(btn, QPushButton):
                btn.setEnabled(any(p.pid > 1 for p in group.instances))
                return
        btn = _make_stop_button(enabled=any(p.pid > 1 for p in group.instances))
        key = group.key
        btn.clicked.connect(lambda checked=False, k=key: self._on_stop_group(k))
        self._tree.setItemWidget(item, _COL_STOP, btn)

    def _fill_instance_item(self, item: QTreeWidgetItem, proc: ProcessInfo,
                            reuse_button: bool = False):
        item.setText(_COL_PROCESS, proc.detail)
        item.setData(0, Qt.UserRole, proc.pid)
        item.setData(0, Qt.UserRole + 2, "instance")
        item.setText(_COL_CPU, proc.cpu_display)
        item.setText(_COL_RAM, proc.ram_display)
        item.setTextAlignment(_COL_CPU, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setTextAlignment(_COL_RAM, int(Qt.AlignRight | Qt.AlignVCenter))
        item.setToolTip(_COL_PROCESS, f"{proc.name}  (PID {proc.pid})")
        # No icon on children — indentation shows hierarchy
        item.setIcon(_COL_PROCESS, QIcon())

        if reuse_button:
            btn = self._tree.itemWidget(item, _COL_STOP)
            if isinstance(btn, QPushButton):
                btn.setEnabled(proc.pid > 1)
                return
        btn = _make_stop_button(enabled=proc.pid > 1)
        pid = proc.pid
        btn.clicked.connect(lambda checked=False, p=pid: self._on_stop_pid(p))
        self._tree.setItemWidget(item, _COL_STOP, btn)

    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int):
        """Toggle expand on group rows (same as Sensors page)."""
        if item.data(0, Qt.UserRole + 2) == "group" and item.childCount() > 0:
            item.setExpanded(not item.isExpanded())

    def _on_stop_group(self, key: str):
        group = self._groups_by_key.get(key)
        if group is None:
            return
        stop_processes([p.pid for p in group.instances])
        self.refresh()

    def _on_stop_pid(self, pid: int):
        stop_process(pid)
        self.refresh()
