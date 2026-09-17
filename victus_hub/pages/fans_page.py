"""Fans page — CPU + GPU curve editor, formerly the fan-curves pop-out."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from victus_hub.api import FanPoint, FanProfileConfig, get_fan_config, save_fan_profile
from victus_hub.app.theme import COLORS
from victus_hub.backend import fan_config
from victus_hub.widgets.chrome import PageHead
from victus_hub.widgets.fan_chart import FanChart
from victus_hub.widgets.profile_section import FAN_MODES, PROFILES as _PROFILE_NAMES_SRC
from victus_hub.widgets.seg import LinkSeg, Seg


_CURVE_CPU = 0
_CURVE_GPU = 1

_FAN_TIPS = [
    "This model's own curve",
    "Built-in curve with faster smoothing",
    "Both fans at full speed",
    "Your own curve, remembered per profile",
]


class FansPage(QWidget):
    """Tab hosting fan-mode segment plus one CPU/GPU curve chart."""

    fan_mode_selected = Signal(str)

    _PROFILE_NAMES = [p[0] for p in _PROFILE_NAMES_SRC]
    _FAN_KEYS = [m[0] for m in FAN_MODES]
    _FAN_LABELS = [m[1] for m in FAN_MODES]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_profile = 1
        self._fan_mode = "auto"
        self._profiles: list[FanProfileConfig] = []
        self._config_loaded = False
        self._cpu_points = list(fan_config.default_cpu_points())
        self._gpu_points = list(fan_config.default_gpu_points())
        self._cpu_selected = -1
        self._gpu_selected = -1
        self._curve_idx = _CURVE_CPU
        self._dirty = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(0)

        self._head = PageHead("Fans")
        layout.addWidget(self._head)

        mode_col = QVBoxLayout()
        mode_col.setContentsMargins(0, 24, 0, 0)
        mode_col.setSpacing(0)
        self._mode_seg = Seg(self._FAN_LABELS, kind="page", tips=_FAN_TIPS)
        self._mode_seg.picked.connect(self._on_mode_seg)
        mode_col.addWidget(self._mode_seg)
        layout.addLayout(mode_col)
        self._mode_seg.select(0, False)

        self._curve_editor = QWidget()
        editor_layout = QVBoxLayout(self._curve_editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        links_col = QVBoxLayout()
        links_col.setContentsMargins(0, 24, 0, 0)
        links_col.setSpacing(0)
        self._curve_links = LinkSeg(["CPU", "GPU"], gap=16, size=14)
        self._curve_links.picked.connect(self._on_curve_link)
        links_col.addWidget(self._curve_links)
        editor_layout.addLayout(links_col)
        self._curve_links.select(_CURVE_CPU, False)

        self._chart = FanChart(
            "CPU curve", QColor(COLORS["accent"]), fan_config.CPU_TEMP_MAX_C,
        )
        self._chart.point_added.connect(self._on_point_added)
        self._chart.point_moved.connect(self._on_point_moved)
        self._chart.point_deleted.connect(self._on_point_deleted)
        self._chart.point_selected.connect(self._on_point_selected)
        self._chart.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        chart_wrap = QWidget()
        chart_l = QVBoxLayout(chart_wrap)
        chart_l.setContentsMargins(0, 22, 0, 0)
        chart_l.addWidget(self._chart)
        editor_layout.addWidget(chart_wrap, 1)
        layout.addWidget(self._curve_editor, 1)

        self._mode_info = QWidget()
        info_layout = QVBoxLayout(self._mode_info)
        info_layout.setContentsMargins(0, 24, 0, 0)
        self._mode_description = QLabel()
        self._mode_description.setWordWrap(True)
        self._mode_description.setStyleSheet(f"color: {COLORS['text']};")
        info_layout.addWidget(self._mode_description)
        info_layout.addStretch(1)
        layout.addWidget(self._mode_info, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_current_profile)

        self._load_config()
        self._update_profile_label()
        self._update_mode_content()

    def refresh_accent(self) -> None:
        self._mode_seg.refresh_accent()
        self._curve_links.refresh_accent()
        if self._curve_idx == _CURVE_CPU:
            self._chart.set_accent(QColor(COLORS["accent"]))

    def set_selected_fan_mode(self, mode: str) -> None:
        """Select a fan-mode segment without emitting a signal."""
        self._fan_mode = mode
        try:
            self._mode_seg.select(self._FAN_KEYS.index(mode), True)
        except ValueError:
            pass
        self._update_mode_content()

    def _update_mode_content(self) -> None:
        custom = self._fan_mode == "custom"
        self._curve_editor.setVisible(custom)
        self._mode_info.setVisible(not custom)
        self._mode_description.setText({
            "auto": "Automatic fan control follows the firmware's own fan curve.",
            "smart": "Smart fan control follows the built-in curve with faster smoothing.",
            "max": "Both fans run at full speed. Select another mode to return to automatic or curve-based control.",
        }.get(self._fan_mode, "Select Custom to edit this profile's CPU and GPU fan curves."))

    def set_edit_profile(self, index: int) -> None:
        """Switch which profile's fan curves are shown."""
        if index == self._edit_profile:
            return
        self._edit_profile = index
        self._hydrate_editor()
        self._update_profile_label()

    def _on_mode_seg(self, index: int) -> None:
        if index < 0 or index >= len(self._FAN_KEYS):
            return
        mode = self._FAN_KEYS[index]
        if mode == self._fan_mode:
            return
        self._fan_mode = mode
        self._update_mode_content()
        self.fan_mode_selected.emit(mode)

    def _is_cpu(self) -> bool:
        return self._curve_idx == _CURVE_CPU

    def _temp_max(self) -> int:
        return fan_config.CPU_TEMP_MAX_C if self._is_cpu() else fan_config.GPU_TEMP_MAX_C

    def _active_points(self) -> list[FanPoint]:
        return self._cpu_points if self._is_cpu() else self._gpu_points

    def _set_active_points(self, pts: list[FanPoint], selected: int | None = None) -> None:
        if self._is_cpu():
            self._cpu_points = pts
            if selected is not None:
                self._cpu_selected = selected
        else:
            self._gpu_points = pts
            if selected is not None:
                self._gpu_selected = selected
        self._chart.points = pts
        if selected is not None:
            self._chart.selected_point = selected

    def _apply_chart(self) -> None:
        cpu = self._is_cpu()
        self._chart.set_title("CPU curve" if cpu else "GPU curve")
        self._chart.set_temp_max(
            fan_config.CPU_TEMP_MAX_C if cpu else fan_config.GPU_TEMP_MAX_C,
        )
        self._chart.set_accent(QColor(COLORS["accent"] if cpu else COLORS["perf"]))
        self._chart.points = self._cpu_points if cpu else self._gpu_points
        self._chart.selected_point = self._cpu_selected if cpu else self._gpu_selected

    def _on_curve_link(self, index: int) -> None:
        if index not in (_CURVE_CPU, _CURVE_GPU) or index == self._curve_idx:
            return
        self._curve_idx = index
        self._apply_chart()

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
            self._apply_chart()

    def _on_point_added(self, temp: int, speed: int) -> None:
        temp_max = self._temp_max()
        pts = self._active_points()
        if temp <= fan_config.TEMP_MIN_C or temp >= temp_max:
            return
        if any(p.temp == temp for p in pts):
            return
        self._dirty = True
        new_speed = fan_config.interpolate_fan(pts, temp)
        norm = fan_config.normalize_fan_points(
            pts + [FanPoint(temp, new_speed)], temp_max,
        )
        idx = next((i for i, p in enumerate(norm) if p.temp == temp), -1)
        self._set_active_points(norm, idx)
        self._schedule_save()

    def _on_point_moved(self, index: int, temp: int, speed: int) -> None:
        pts = list(self._active_points())
        temp_max = self._temp_max()
        if index < 0 or index >= len(pts):
            return
        self._dirty = True
        nt = max(fan_config.TEMP_MIN_C, min(temp_max, temp))
        ns = max(0, min(100, speed))
        if index == 0:
            nt = fan_config.TEMP_MIN_C
            ns = max(0, min(ns, pts[1].speed if len(pts) > 1 else 100))
        elif index == len(pts) - 1:
            nt = temp_max
            ns = max(pts[index - 1].speed, min(ns, 100))
        else:
            nt = max(pts[index - 1].temp + 1, min(nt, pts[index + 1].temp - 1))
            ns = max(pts[index - 1].speed, min(ns, pts[index + 1].speed))
        pts[index] = FanPoint(nt, ns)
        self._set_active_points(fan_config.normalize_fan_points(pts, temp_max))
        self._schedule_save()

    def _on_point_deleted(self, index: int) -> None:
        pts = self._active_points()
        if index <= 0 or index >= len(pts) - 1:
            return
        self._dirty = True
        kept = [p for i, p in enumerate(pts) if i != index]
        self._set_active_points(
            fan_config.normalize_fan_points(kept, self._temp_max()), -1,
        )
        self._schedule_save()

    def _on_point_selected(self, index: int) -> None:
        if self._is_cpu():
            self._cpu_selected = index
        else:
            self._gpu_selected = index

    def _schedule_save(self) -> None:
        if self._config_loaded and self._dirty:
            self._save_timer.start()

    def _save_current_profile(self) -> None:
        if not self._config_loaded or not self._dirty:
            return
        self._dirty = False
        config = save_fan_profile(self._edit_profile, list(self._cpu_points), list(self._gpu_points))
        self._profiles = config.profiles
