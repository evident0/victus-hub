"""Fans page — CPU + GPU curve editor, formerly the fan-curves pop-out."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from victus_hub.api import FanPoint, FanProfileConfig, get_fan_config, save_fan_profile
from victus_hub.app.theme import COLORS
from victus_hub.backend import fan_config
from victus_hub.widgets.chrome import PageHead
from victus_hub.widgets.fan_chart import FanChart
from victus_hub.widgets.profile_section import PROFILES as _PROFILE_NAMES_SRC


class FansPage(QWidget):
    """Tab hosting the CPU and GPU fan-curve charts."""

    _PROFILE_NAMES = [p[0] for p in _PROFILE_NAMES_SRC]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_profile = 1
        self._profiles: list[FanProfileConfig] = []
        self._config_loaded = False
        self._cpu_points = list(fan_config.default_cpu_points())
        self._gpu_points = list(fan_config.default_gpu_points())
        self._cpu_selected = -1
        self._gpu_selected = -1
        self._dirty = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)

        self._head = PageHead("Fans")
        layout.addWidget(self._head)

        self._cpu_chart = FanChart(
            "CPU curve", QColor(COLORS["accent"]), fan_config.CPU_TEMP_MAX_C,
        )
        self._cpu_chart.point_added.connect(self._on_cpu_point_added)
        self._cpu_chart.point_moved.connect(self._on_cpu_point_moved)
        self._cpu_chart.point_deleted.connect(self._on_cpu_point_deleted)
        self._cpu_chart.point_selected.connect(self._on_cpu_point_selected)
        self._cpu_chart.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._cpu_chart, 1)

        self._gpu_chart = FanChart(
            "GPU curve", QColor(COLORS["perf"]), fan_config.GPU_TEMP_MAX_C,
        )
        self._gpu_chart.point_added.connect(self._on_gpu_point_added)
        self._gpu_chart.point_moved.connect(self._on_gpu_point_moved)
        self._gpu_chart.point_deleted.connect(self._on_gpu_point_deleted)
        self._gpu_chart.point_selected.connect(self._on_gpu_point_selected)
        self._gpu_chart.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._gpu_chart, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_current_profile)

        self._load_config()
        self._update_profile_label()

    def refresh_accent(self) -> None:
        self._cpu_chart.set_accent(QColor(COLORS["accent"]))

    def set_edit_profile(self, index: int) -> None:
        """Switch which profile's fan curves are shown."""
        if index == self._edit_profile:
            return
        self._edit_profile = index
        self._hydrate_editor()
        self._update_profile_label()

    def _update_profile_label(self) -> None:
        idx = min(max(self._edit_profile, 0), len(self._PROFILE_NAMES) - 1)
        self._head.set_status(self._PROFILE_NAMES[idx])

    def _load_config(self) -> None:
        try:
            config = get_fan_config()
        except Exception:
            return
        self._profiles = config.profiles
        self._config_loaded = True
        self._hydrate_editor()

    def _hydrate_editor(self) -> None:
        if not self._config_loaded:
            return
        profile = (
            self._profiles[self._edit_profile]
            if self._edit_profile < len(self._profiles) else None
        )
        if profile:
            self._dirty = False
            self._cpu_points = fan_config.normalize_fan_points(
                list(profile.cpu_points), fan_config.CPU_TEMP_MAX_C,
            )
            self._gpu_points = fan_config.normalize_fan_points(
                list(profile.gpu_points), fan_config.GPU_TEMP_MAX_C,
            )
            self._cpu_selected = -1
            self._gpu_selected = -1
            self._cpu_chart.points = self._cpu_points
            self._gpu_chart.points = self._gpu_points
            self._cpu_chart.selected_point = -1
            self._gpu_chart.selected_point = -1

    def _on_cpu_point_added(self, temp: int, speed: int) -> None:
        if temp <= fan_config.TEMP_MIN_C or temp >= fan_config.CPU_TEMP_MAX_C:
            return
        if any(p.temp == temp for p in self._cpu_points):
            return
        self._dirty = True
        new_speed = fan_config.interpolate_fan(self._cpu_points, temp)
        norm = fan_config.normalize_fan_points(
            self._cpu_points + [FanPoint(temp, new_speed)], fan_config.CPU_TEMP_MAX_C,
        )
        self._cpu_points = norm
        self._cpu_chart.points = norm
        idx = next((i for i, p in enumerate(norm) if p.temp == temp), -1)
        self._cpu_chart.selected_point = idx
        self._cpu_selected = idx
        self._schedule_save()

    def _on_cpu_point_moved(self, index: int, temp: int, speed: int) -> None:
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

    def _on_cpu_point_deleted(self, index: int) -> None:
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

    def _on_cpu_point_selected(self, index: int) -> None:
        self._cpu_selected = index
        self._gpu_chart.selected_point = -1
        self._gpu_selected = -1

    def _on_gpu_point_added(self, temp: int, speed: int) -> None:
        if temp <= fan_config.TEMP_MIN_C or temp >= fan_config.GPU_TEMP_MAX_C:
            return
        if any(p.temp == temp for p in self._gpu_points):
            return
        self._dirty = True
        new_speed = fan_config.interpolate_fan(self._gpu_points, temp)
        norm = fan_config.normalize_fan_points(
            self._gpu_points + [FanPoint(temp, new_speed)], fan_config.GPU_TEMP_MAX_C,
        )
        self._gpu_points = norm
        self._gpu_chart.points = norm
        idx = next((i for i, p in enumerate(norm) if p.temp == temp), -1)
        self._gpu_chart.selected_point = idx
        self._gpu_selected = idx
        self._schedule_save()

    def _on_gpu_point_moved(self, index: int, temp: int, speed: int) -> None:
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

    def _on_gpu_point_deleted(self, index: int) -> None:
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

    def _on_gpu_point_selected(self, index: int) -> None:
        self._gpu_selected = index
        self._cpu_chart.selected_point = -1
        self._cpu_selected = -1

    def _schedule_save(self) -> None:
        if self._config_loaded and self._dirty:
            self._save_timer.start()

    def _save_current_profile(self) -> None:
        if not self._config_loaded or not self._dirty:
            return
        self._dirty = False
        config = save_fan_profile(self._edit_profile, list(self._cpu_points), list(self._gpu_points))
        self._profiles = config.profiles
