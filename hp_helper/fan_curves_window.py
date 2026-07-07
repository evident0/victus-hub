"""Standalone fan curves window — CPU + GPU chart editor with profile selector."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor

from hp_helper.widgets.fan_chart import FanChart
from hp_helper.theme import COLORS
from hp_helper.api import (
    get_fan_config, save_fan_profile,
    FanPoint, FanProfileConfig,
)
from hp_helper.backend import fan_config
from hp_helper.widgets.profile_section import PROFILES as _PROFILE_NAMES_SRC



class FanCurvesWindow(QMainWindow):
    """Standalone fan curves editor with CPU + GPU charts."""


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fan Curves")
        self.resize(860, 620)
        self.setMinimumSize(600, 300)
        # App icon (logoV) — same as main window
        from hp_helper.icon_utils import load_icon
        self.setWindowIcon(load_icon("logoV.png", color=None, size=48))

        self.setStyleSheet(f"background-color: {COLORS['bg']};")

        # ── State ──
        self._edit_profile = 1
        self._profiles: list[FanProfileConfig] = []
        self._config_loaded = False
        self._cpu_points = list(fan_config.default_cpu_points())
        self._gpu_points = list(fan_config.default_gpu_points())
        self._cpu_selected = -1
        self._gpu_selected = -1
        self._edit_custom_enabled = False
        self._dirty = False

        # ── Central widget ──
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header: title
        header = QHBoxLayout()
        title = QLabel("Fan Curves")
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
        header.addWidget(title)
        self._profile_label = QLabel("")
        self._profile_label.setStyleSheet("color: #9d9d9d; font-size: 13px; background: transparent;")
        header.addWidget(self._profile_label)
        header.addStretch()
        layout.addLayout(header)

        # Fan charts
        self._cpu_chart = FanChart("CPU Fan Curve", QColor(COLORS["accent_blue"]), fan_config.CPU_TEMP_MAX_C)
        self._cpu_chart.point_added.connect(self._on_cpu_point_added)
        self._cpu_chart.point_moved.connect(self._on_cpu_point_moved)
        self._cpu_chart.point_deleted.connect(self._on_cpu_point_deleted)
        self._cpu_chart.point_selected.connect(self._on_cpu_point_selected)
        layout.addWidget(self._cpu_chart, 1)

        self._gpu_chart = FanChart("GPU Fan Curve", QColor(COLORS["accent_red"]), fan_config.GPU_TEMP_MAX_C)
        self._gpu_chart.point_added.connect(self._on_gpu_point_added)
        self._gpu_chart.point_moved.connect(self._on_gpu_point_moved)
        self._gpu_chart.point_deleted.connect(self._on_gpu_point_deleted)
        self._gpu_chart.point_selected.connect(self._on_gpu_point_selected)
        layout.addWidget(self._gpu_chart, 1)


        # Auto-save timer
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_current_profile)

        # Load config
        self._load_config()
        self._update_profile_label()

    # ── Config ──

    def _load_config(self):
        config = get_fan_config()
        self._profiles = config.profiles
        self._config_loaded = True
        self._edit_custom_enabled = config.custom_enabled
        self._hydrate_editor()

    def _hydrate_editor(self):
        if not self._config_loaded:
            return
        profile = self._profiles[self._edit_profile] if self._edit_profile < len(self._profiles) else None
        if profile:
            self._dirty = False
            self._cpu_points = fan_config.normalize_fan_points(list(profile.cpu_points), fan_config.CPU_TEMP_MAX_C)
            self._gpu_points = fan_config.normalize_fan_points(list(profile.gpu_points), fan_config.GPU_TEMP_MAX_C)
            self._cpu_selected = -1
            self._gpu_selected = -1
            self._cpu_chart.points = self._cpu_points
            self._gpu_chart.points = self._gpu_points
            self._cpu_chart.selected_point = -1
            self._gpu_chart.selected_point = -1


    # ── Profile-driven editing ──
    def set_edit_profile(self, index: int):
        """Switch which profile's fan curves are shown (0=Power Saver, 1=Balanced, 2=Performance)."""
        if index == self._edit_profile:
            return
        self._edit_profile = index
        self._hydrate_editor()
        self._update_profile_label()

    _PROFILE_NAMES = [p[0] for p in _PROFILE_NAMES_SRC]

    def _update_profile_label(self):
        idx = min(max(self._edit_profile, 0), len(self._PROFILE_NAMES) - 1)
        self._profile_label.setText(f"({self._PROFILE_NAMES[idx]})")

    # ── CPU point handlers ──

    def _on_cpu_point_added(self, temp: int, speed: int):
        if temp <= fan_config.TEMP_MIN_C or temp >= fan_config.CPU_TEMP_MAX_C:
            return
        if any(p.temp == temp for p in self._cpu_points):
            return
        self._dirty = True
        new_speed = fan_config.interpolate_fan(self._cpu_points, temp)
        norm = fan_config.normalize_fan_points(self._cpu_points + [FanPoint(temp, new_speed)], fan_config.CPU_TEMP_MAX_C)
        self._cpu_points = norm
        self._cpu_chart.points = norm
        idx = next((i for i, p in enumerate(norm) if p.temp == temp), -1)
        self._cpu_chart.selected_point = idx
        self._cpu_selected = idx
        self._schedule_save()

    def _on_cpu_point_moved(self, index: int, temp: int, speed: int):
        if index < 0 or index >= len(self._cpu_points):
            return
        self._dirty = True
        pts = list(self._cpu_points)
        nt = max(fan_config.TEMP_MIN_C, min(fan_config.CPU_TEMP_MAX_C, temp))
        ns = max(0, min(100, speed))
        if index == 0:
            nt = fan_config.TEMP_MIN_C
            ns = max(0, min(ns, pts[1].speed if len(pts) > 1 else 100))
        elif index == len(pts) - 1:
            nt = fan_config.CPU_TEMP_MAX_C
            ns = max(pts[index - 1].speed, min(ns, 100))
        else:
            nt = max(pts[index - 1].temp + 1, min(nt, pts[index + 1].temp - 1))
            ns = max(pts[index - 1].speed, min(ns, pts[index + 1].speed))
        pts[index] = FanPoint(nt, ns)
        norm = fan_config.normalize_fan_points(pts, fan_config.CPU_TEMP_MAX_C)
        self._cpu_points = norm
        self._cpu_chart.points = norm
        self._schedule_save()

    def _on_cpu_point_deleted(self, index: int):
        if index <= 0 or index >= len(self._cpu_points) - 1:
            return
        self._dirty = True
        pts = [p for i, p in enumerate(self._cpu_points) if i != index]
        norm = fan_config.normalize_fan_points(pts, fan_config.CPU_TEMP_MAX_C)
        self._cpu_points = norm
        self._cpu_chart.points = norm
        self._cpu_chart.selected_point = -1
        self._cpu_selected = -1
        self._schedule_save()

    def _on_cpu_point_selected(self, index: int):
        self._cpu_selected = index
        self._gpu_chart.selected_point = -1
        self._gpu_selected = -1

    # ── GPU point handlers ──

    def _on_gpu_point_added(self, temp: int, speed: int):
        if temp <= fan_config.TEMP_MIN_C or temp >= fan_config.GPU_TEMP_MAX_C:
            return
        if any(p.temp == temp for p in self._gpu_points):
            return
        self._dirty = True
        new_speed = fan_config.interpolate_fan(self._gpu_points, temp)
        norm = fan_config.normalize_fan_points(self._gpu_points + [FanPoint(temp, new_speed)], fan_config.GPU_TEMP_MAX_C)
        self._gpu_points = norm
        self._gpu_chart.points = norm
        idx = next((i for i, p in enumerate(norm) if p.temp == temp), -1)
        self._gpu_chart.selected_point = idx
        self._gpu_selected = idx
        self._schedule_save()

    def _on_gpu_point_moved(self, index: int, temp: int, speed: int):
        if index < 0 or index >= len(self._gpu_points):
            return
        self._dirty = True
        pts = list(self._gpu_points)
        nt = max(fan_config.TEMP_MIN_C, min(fan_config.GPU_TEMP_MAX_C, temp))
        ns = max(0, min(100, speed))
        if index == 0:
            nt = fan_config.TEMP_MIN_C
            ns = max(0, min(ns, pts[1].speed if len(pts) > 1 else 100))
        elif index == len(pts) - 1:
            nt = fan_config.GPU_TEMP_MAX_C
            ns = max(pts[index - 1].speed, min(ns, 100))
        else:
            nt = max(pts[index - 1].temp + 1, min(nt, pts[index + 1].temp - 1))
            ns = max(pts[index - 1].speed, min(ns, pts[index + 1].speed))
        pts[index] = FanPoint(nt, ns)
        norm = fan_config.normalize_fan_points(pts, fan_config.GPU_TEMP_MAX_C)
        self._gpu_points = norm
        self._gpu_chart.points = norm
        self._schedule_save()

    def _on_gpu_point_deleted(self, index: int):
        if index <= 0 or index >= len(self._gpu_points) - 1:
            return
        self._dirty = True
        pts = [p for i, p in enumerate(self._gpu_points) if i != index]
        norm = fan_config.normalize_fan_points(pts, fan_config.GPU_TEMP_MAX_C)
        self._gpu_points = norm
        self._gpu_chart.points = norm
        self._gpu_chart.selected_point = -1
        self._gpu_selected = -1
        self._schedule_save()

    def _on_gpu_point_selected(self, index: int):
        self._gpu_selected = index
        self._cpu_chart.selected_point = -1
        self._cpu_selected = -1


    # ── Save ──

    def _schedule_save(self):
        if self._config_loaded and self._dirty:
            self._save_timer.start()

    def _save_current_profile(self):
        if not self._config_loaded or not self._dirty:
            return
        self._dirty = False
        cpu_pts = list(self._cpu_points)
        gpu_pts = list(self._gpu_points)
        config = save_fan_profile(self._edit_profile, cpu_pts, gpu_pts)
        self._profiles = config.profiles
