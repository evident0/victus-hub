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

TEMP_MIN = 30
CPU_TEMP_MAX = 100
GPU_TEMP_MAX = 90

DEFAULT_CPU_POINTS: list[tuple[int, int]] = [(TEMP_MIN, 0), (CPU_TEMP_MAX, 100)]
DEFAULT_GPU_POINTS: list[tuple[int, int]] = [(TEMP_MIN, 0), (GPU_TEMP_MAX, 100)]



def _normalize_curve(points: list[tuple[int, int]], temp_max: int) -> list[tuple[int, int]]:
    if len(points) < 2:
        return list(DEFAULT_CPU_POINTS if temp_max == CPU_TEMP_MAX else DEFAULT_GPU_POINTS)
    result = sorted(points, key=lambda p: p[0])
    result[0] = (TEMP_MIN, result[0][1])
    result[-1] = (temp_max, result[-1][1])
    min_speed = 0
    for i in range(len(result)):
        result[i] = (result[i][0], max(min_speed, min(100, result[i][1])))
        min_speed = result[i][1]
    return result


def _interpolate(points: list[tuple[int, int]], temp: float) -> int:
    if not points:
        return 0
    if temp <= points[0][0]:
        return points[0][1]
    for i in range(1, len(points)):
        if temp <= points[i][0]:
            t0, s0 = points[i - 1]
            t1, s1 = points[i]
            return round(s0 + (s1 - s0) * (temp - t0) / (t1 - t0))
    return points[-1][1]


class FanCurvesWindow(QMainWindow):
    """Standalone fan curves editor with CPU + GPU charts."""

    closed = None  # placeholder — set by signal if needed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fan Curves")
        self.resize(860, 620)
        self.setMinimumSize(600, 300)
        self.setStyleSheet(f"background-color: {COLORS['bg']};")

        # ── State ──
        self._edit_profile = 1
        self._profiles: list[FanProfileConfig] = []
        self._config_loaded = False
        self._cpu_points = list(DEFAULT_CPU_POINTS)
        self._gpu_points = list(DEFAULT_GPU_POINTS)
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
        header.addStretch()
        layout.addLayout(header)

        # Fan charts
        self._cpu_chart = FanChart("CPU Fan Curve", QColor(COLORS["accent_blue"]), CPU_TEMP_MAX)
        self._cpu_chart.point_added.connect(self._on_cpu_point_added)
        self._cpu_chart.point_moved.connect(self._on_cpu_point_moved)
        self._cpu_chart.point_deleted.connect(self._on_cpu_point_deleted)
        self._cpu_chart.point_selected.connect(self._on_cpu_point_selected)
        layout.addWidget(self._cpu_chart, 1)

        self._gpu_chart = FanChart("GPU Fan Curve", QColor(COLORS["accent_red"]), GPU_TEMP_MAX)
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
            self._cpu_points = _normalize_curve(
                [(p.temp, p.speed) for p in profile.cpu_points], CPU_TEMP_MAX)
            self._gpu_points = _normalize_curve(
                [(p.temp, p.speed) for p in profile.gpu_points], GPU_TEMP_MAX)
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

    # ── CPU point handlers ──

    def _on_cpu_point_added(self, temp: int, speed: int):
        if temp <= TEMP_MIN or temp >= CPU_TEMP_MAX:
            return
        if any(p[0] == temp for p in self._cpu_points):
            return
        self._dirty = True
        new_speed = _interpolate(self._cpu_points, temp)
        norm = _normalize_curve(self._cpu_points + [(temp, new_speed)], CPU_TEMP_MAX)
        self._cpu_points = norm
        self._cpu_chart.points = norm
        idx = next((i for i, p in enumerate(norm) if p[0] == temp), -1)
        self._cpu_chart.selected_point = idx
        self._cpu_selected = idx
        self._schedule_save()

    def _on_cpu_point_moved(self, index: int, temp: int, speed: int):
        if index < 0 or index >= len(self._cpu_points):
            return
        self._dirty = True
        pts = list(self._cpu_points)
        nt = max(TEMP_MIN, min(CPU_TEMP_MAX, temp))
        ns = max(0, min(100, speed))
        if index == 0:
            nt = TEMP_MIN
            ns = max(0, min(ns, pts[1][1] if len(pts) > 1 else 100))
        elif index == len(pts) - 1:
            nt = CPU_TEMP_MAX
            ns = max(pts[index - 1][1], min(ns, 100))
        else:
            nt = max(pts[index - 1][0] + 1, min(nt, pts[index + 1][0] - 1))
            ns = max(pts[index - 1][1], min(ns, pts[index + 1][1]))
        pts[index] = (nt, ns)
        norm = _normalize_curve(pts, CPU_TEMP_MAX)
        self._cpu_points = norm
        self._cpu_chart.points = norm
        self._schedule_save()

    def _on_cpu_point_deleted(self, index: int):
        if index <= 0 or index >= len(self._cpu_points) - 1:
            return
        self._dirty = True
        pts = [p for i, p in enumerate(self._cpu_points) if i != index]
        norm = _normalize_curve(pts, CPU_TEMP_MAX)
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
        if temp <= TEMP_MIN or temp >= GPU_TEMP_MAX:
            return
        if any(p[0] == temp for p in self._gpu_points):
            return
        self._dirty = True
        new_speed = _interpolate(self._gpu_points, temp)
        norm = _normalize_curve(self._gpu_points + [(temp, new_speed)], GPU_TEMP_MAX)
        self._gpu_points = norm
        self._gpu_chart.points = norm
        idx = next((i for i, p in enumerate(norm) if p[0] == temp), -1)
        self._gpu_chart.selected_point = idx
        self._gpu_selected = idx
        self._schedule_save()

    def _on_gpu_point_moved(self, index: int, temp: int, speed: int):
        if index < 0 or index >= len(self._gpu_points):
            return
        self._dirty = True
        pts = list(self._gpu_points)
        nt = max(TEMP_MIN, min(GPU_TEMP_MAX, temp))
        ns = max(0, min(100, speed))
        if index == 0:
            nt = TEMP_MIN
            ns = max(0, min(ns, pts[1][1] if len(pts) > 1 else 100))
        elif index == len(pts) - 1:
            nt = GPU_TEMP_MAX
            ns = max(pts[index - 1][1], min(ns, 100))
        else:
            nt = max(pts[index - 1][0] + 1, min(nt, pts[index + 1][0] - 1))
            ns = max(pts[index - 1][1], min(ns, pts[index + 1][1]))
        pts[index] = (nt, ns)
        norm = _normalize_curve(pts, GPU_TEMP_MAX)
        self._gpu_points = norm
        self._gpu_chart.points = norm
        self._schedule_save()

    def _on_gpu_point_deleted(self, index: int):
        if index <= 0 or index >= len(self._gpu_points) - 1:
            return
        self._dirty = True
        pts = [p for i, p in enumerate(self._gpu_points) if i != index]
        norm = _normalize_curve(pts, GPU_TEMP_MAX)
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
        cpu_pts = [FanPoint(t, s) for t, s in self._cpu_points]
        gpu_pts = [FanPoint(t, s) for t, s in self._gpu_points]
        config = save_fan_profile(self._edit_profile, cpu_pts, gpu_pts)
        self._profiles = config.profiles
