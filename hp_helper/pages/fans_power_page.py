"""Fans & Power page — fan curve editor + power limit steppers."""

import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor

from hp_helper.widgets.fan_chart import FanChart
from hp_helper.app.theme import COLORS
from hp_helper.widgets.toggle_switch import ToggleSwitch
from hp_helper.pages.settings_page import make_spin
from hp_helper.features.power.limits import (
    POWER_MIN_MW, POWER_MAX_MW,
    TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C,
    PowerLimitSettings, clamp_power_limit, clamp_tctl_temp,
    read_power_enabled, write_power_enabled,
    read_power_limit_settings, write_power_limit_settings,
)
from hp_helper.api import (
    get_fan_config, save_fan_profile, apply_power_limits,
    FanPoint, FanProfileConfig,
)
from hp_helper.backend import fan_config
from hp_helper.widgets.profile_section import PROFILES as _PROFILE_NAMES_SRC



class FansPowerPage(QWidget):
    """Fans & Power tab: fan curve editor + power limit controls."""

    fan_curves_popout_requested = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)

        # ── State ──
        pwr = read_power_limit_settings()
        self._stapm_limit = pwr.stapm_limit
        self._fast_limit = pwr.fast_limit
        self._slow_limit = pwr.slow_limit
        self._tctl_temp = pwr.tctl_temp
        self._reapply_seconds = pwr.reapply_seconds
        self._power_enabled = read_power_enabled()
        # Last values sent via Apply (used to detect dirty controls)
        self._applied_stapm = pwr.stapm_limit
        self._applied_fast = pwr.fast_limit
        self._applied_slow = pwr.slow_limit
        self._applied_tctl = pwr.tctl_temp

        self._edit_profile = 1
        self._profiles: list[FanProfileConfig] = []
        self._config_loaded = False
        self._cpu_points = list(fan_config.default_cpu_points())
        self._gpu_points = list(fan_config.default_gpu_points())
        self._cpu_selected = -1
        self._gpu_selected = -1
        self._edit_custom_enabled = False
        self._dirty = False

        # ── Layout ──
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # Content row
        content_row = QHBoxLayout()
        content_row.setSpacing(12)

        # ── Left: Power panel ──
        power_panel = QWidget()
        power_panel.setMinimumWidth(240)
        power_layout = QVBoxLayout(power_panel)
        power_layout.setContentsMargins(14, 14, 14, 14)
        power_layout.setSpacing(14)

        # Power title
        power_title = QLabel("Power")
        power_title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
        power_layout.addWidget(power_title)

        # Power limit steppers (watts / °C)
        power_min_w = POWER_MIN_MW // 1000
        power_max_w = POWER_MAX_MW // 1000

        self._stapm_spin = make_spin(
            "STAPM Limit", "W",
            round(self._stapm_limit / 1000), power_min_w, power_max_w,
        )
        self._stapm_spin._spin.valueChanged.connect(self._on_stapm_changed)
        power_layout.addLayout(self._stapm_spin)

        self._fast_spin = make_spin(
            "Fast Limit", "W",
            round(self._fast_limit / 1000), power_min_w, power_max_w,
        )
        self._fast_spin._spin.valueChanged.connect(self._on_fast_changed)
        power_layout.addLayout(self._fast_spin)

        self._slow_spin = make_spin(
            "Slow Limit", "W",
            round(self._slow_limit / 1000), power_min_w, power_max_w,
        )
        self._slow_spin._spin.valueChanged.connect(self._on_slow_changed)
        power_layout.addLayout(self._slow_spin)

        self._tctl_spin = make_spin(
            "Tctl Temp", "°C",
            self._tctl_temp, TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C,
        )
        self._tctl_spin._spin.valueChanged.connect(self._on_tctl_changed)
        power_layout.addLayout(self._tctl_spin)

        # Reapply spinbox (minimum 1s)
        self._reapply_spin = make_spin(
            "Auto reapply interval", "s",
            self._reapply_seconds, 1, 3600,
        )
        self._reapply_spin._spin.valueChanged.connect(self._on_reapply_changed)
        power_layout.addLayout(self._reapply_spin)


        # Apply button + enable checkbox + status
        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setCursor(Qt.PointingHandCursor)
        self._apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['accent_blue']};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #4db8f2;
            }}
            QPushButton:pressed {{
                background-color: #2a9edf;
            }}
            QPushButton:disabled {{
                background-color: {COLORS['surface_raised']};
                color: {COLORS['text_secondary']};
            }}
        """)
        self._apply_btn.clicked.connect(self._on_apply_power)
        power_layout.addWidget(self._apply_btn)

        self._power_check = ToggleSwitch("Enable power limits")
        self._power_check.setChecked(self._power_enabled)
        self._power_check.toggled.connect(self._on_power_enabled_changed)
        power_layout.addWidget(self._power_check)

        self._power_status = QLabel()
        self._power_status.setWordWrap(True)
        self._update_power_status()
        self._update_apply_enabled()
        power_layout.addWidget(self._power_status)

        power_layout.addStretch()
        content_row.addWidget(power_panel, 1)

        # ── Right: Fan curves ──
        curves_panel = QWidget()
        curves_layout = QVBoxLayout(curves_panel)
        curves_layout.setContentsMargins(0, 0, 0, 0)
        curves_layout.setSpacing(8)

        # Header row
        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        fan_title = QLabel("Fan Curves")
        fan_title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
        header_row.addWidget(fan_title)
        self._profile_label = QLabel("")
        self._profile_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 13px; background: transparent;")
        header_row.addWidget(self._profile_label)
        header_row.addStretch()

        # Pop-out button
        self._popout_btn = QPushButton("\u2197")  # ↗
        self._popout_btn.setToolTip("Open fan curves in separate window")
        self._popout_btn.setFixedSize(28, 28)
        self._popout_btn.setCursor(Qt.PointingHandCursor)
        self._popout_btn.clicked.connect(self.fan_curves_popout_requested.emit)
        self._popout_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['surface_raised']};
                color: {COLORS['text_secondary']};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                padding: 0px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: #2a2a2a;
                color: {COLORS['text']};
            }}
        """)
        header_row.addWidget(self._popout_btn)
        curves_layout.addLayout(header_row)

        # Placeholder shown when fan curves are in a separate window
        self._popout_placeholder = QWidget()
        self._popout_placeholder.setStyleSheet(
            f"background-color: #181818; border-radius: 4px;")
        ph_layout = QVBoxLayout(self._popout_placeholder)
        ph_layout.setAlignment(Qt.AlignCenter)
        ph_icon = QLabel("\u2197")  # ↗
        ph_icon.setStyleSheet("color: #555555; font-size: 24px;")
        ph_icon.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(ph_icon)
        ph_text = QLabel("Fan curves are open in a separate window.")
        ph_text.setStyleSheet("color: #666666; font-size: 13px;")
        ph_text.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(ph_text)
        ph_sub = QLabel("Close it to resume editing here.")
        ph_sub.setStyleSheet("color: #555555; font-size: 11px;")
        ph_sub.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(ph_sub)
        self._popout_placeholder.hide()
        curves_layout.addWidget(self._popout_placeholder, 1)

        # Fan charts wrapper (hidden when pop-out is open)
        self._charts_wrapper = QWidget()
        charts_wrap_layout = QVBoxLayout(self._charts_wrapper)
        charts_wrap_layout.setContentsMargins(0, 0, 0, 0)
        charts_wrap_layout.setSpacing(8)

        self._cpu_chart = FanChart("CPU Fan Curve", QColor(COLORS["accent_blue"]), fan_config.CPU_TEMP_MAX_C)
        self._cpu_chart.point_added.connect(self._on_cpu_point_added)
        self._cpu_chart.point_moved.connect(self._on_cpu_point_moved)
        self._cpu_chart.point_deleted.connect(self._on_cpu_point_deleted)
        self._cpu_chart.point_selected.connect(self._on_cpu_point_selected)
        charts_wrap_layout.addWidget(self._cpu_chart, 1)

        self._gpu_chart = FanChart("GPU Fan Curve", QColor(COLORS["accent_red"]), fan_config.GPU_TEMP_MAX_C)
        self._gpu_chart.point_added.connect(self._on_gpu_point_added)
        self._gpu_chart.point_moved.connect(self._on_gpu_point_moved)
        self._gpu_chart.point_deleted.connect(self._on_gpu_point_deleted)
        self._gpu_chart.point_selected.connect(self._on_gpu_point_selected)
        charts_wrap_layout.addWidget(self._gpu_chart, 1)

        curves_layout.addWidget(self._charts_wrapper, 1)


        self._fan_curves_window_open = False
        content_row.addWidget(curves_panel, 2)
        layout.addLayout(content_row, 1)

        # Auto-save timer
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self._save_current_profile)

        # Load config
        self._load_config()
        self._update_profile_label()

    # ── Power callbacks ──

    def _on_stapm_changed(self, value_w: int):
        # Local only — daemon command updates only on Apply (value is watts)
        self._stapm_limit = clamp_power_limit(value_w * 1000)
        self._update_apply_enabled()

    def _on_fast_changed(self, value_w: int):
        self._fast_limit = clamp_power_limit(value_w * 1000)
        self._update_apply_enabled()

    def _on_slow_changed(self, value_w: int):
        self._slow_limit = clamp_power_limit(value_w * 1000)
        self._update_apply_enabled()

    def _on_tctl_changed(self, value: int):
        self._tctl_temp = clamp_tctl_temp(value)
        self._update_apply_enabled()

    def _on_reapply_changed(self, value: int):
        # Persist interval only; keep last-applied limit values until Apply
        self._reapply_seconds = max(1, value)
        applied = read_power_limit_settings()
        write_power_limit_settings(PowerLimitSettings(
            stapm_limit=applied.stapm_limit,
            fast_limit=applied.fast_limit,
            slow_limit=applied.slow_limit,
            tctl_temp=applied.tctl_temp,
            reapply_seconds=self._reapply_seconds,
        ))

    def _on_power_enabled_changed(self, checked: bool):
        self._power_enabled = checked
        write_power_enabled(checked)
        self._update_power_status()
        self._update_apply_enabled()

    def _power_values_dirty(self) -> bool:
        return (
            self._stapm_limit != self._applied_stapm
            or self._fast_limit != self._applied_fast
            or self._slow_limit != self._applied_slow
            or self._tctl_temp != self._applied_tctl
        )

    def _update_apply_enabled(self):
        # Clickable when inactive, or when active but values differ from last Apply
        can_apply = (not self._power_enabled) or self._power_values_dirty()
        self._apply_btn.setEnabled(can_apply)
        self._apply_btn.setCursor(
            Qt.PointingHandCursor if can_apply else Qt.ArrowCursor
        )

    def _on_apply_power(self):
        settings = self._make_power_settings()
        write_power_limit_settings(settings)
        self._applied_stapm = settings.stapm_limit
        self._applied_fast = settings.fast_limit
        self._applied_slow = settings.slow_limit
        self._applied_tctl = settings.tctl_temp
        self._update_apply_enabled()

        # Apply immediately so limits take effect without waiting for reapply tick
        def _apply():
            try:
                apply_power_limits(
                    settings.stapm_limit,
                    settings.fast_limit,
                    settings.slow_limit,
                    settings.tctl_temp,
                )
            except Exception:
                pass

        threading.Thread(target=_apply, daemon=True, name="power-apply").start()

    def _update_power_status(self):
        if self._power_enabled:
            self._power_status.setText("Power limits active")
            self._power_status.setStyleSheet(
                f"color: {COLORS['accent_green']}; font-size: 11px;"
            )
        else:
            self._power_status.setText("Power limits not active")
            self._power_status.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 11px;"
            )

    def _make_power_settings(self):
        return PowerLimitSettings(
            stapm_limit=self._stapm_limit,
            fast_limit=self._fast_limit,
            slow_limit=self._slow_limit,
            tctl_temp=self._tctl_temp,
            reapply_seconds=self._reapply_seconds,
        )

    # ── Config loading ──

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

    # ── Fan curve point handlers ──

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

    def _on_cpu_point_moved(self, index: int, temp: int, speed: int):
        if index < 0 or index >= len(self._cpu_points):
            return
        self._dirty = True
        pts = list(self._cpu_points)
        next_temp = max(fan_config.TEMP_MIN_C, min(fan_config.CPU_TEMP_MAX_C, temp))
        next_speed = max(0, min(100, speed))
        if index == 0:
            next_temp = fan_config.TEMP_MIN_C
            next_speed = max(0, min(next_speed, pts[1].speed if len(pts) > 1 else 100))
        elif index == len(pts) - 1:
            next_temp = fan_config.CPU_TEMP_MAX_C
            next_speed = max(pts[index - 1].speed, min(next_speed, 100))
        else:
            next_temp = max(pts[index - 1].temp + 1, min(next_temp, pts[index + 1].temp - 1))
            next_speed = max(pts[index - 1].speed, min(next_speed, pts[index + 1].speed))
        pts[index] = FanPoint(next_temp, next_speed)
        norm = fan_config.normalize_fan_points(pts, fan_config.CPU_TEMP_MAX_C)
        self._cpu_points = norm
        self._cpu_chart.points = norm
        self._schedule_save()

    def _on_gpu_point_moved(self, index: int, temp: int, speed: int):
        if index < 0 or index >= len(self._gpu_points):
            return
        self._dirty = True
        pts = list(self._gpu_points)
        next_temp = max(fan_config.TEMP_MIN_C, min(fan_config.GPU_TEMP_MAX_C, temp))
        next_speed = max(0, min(100, speed))
        if index == 0:
            next_temp = fan_config.TEMP_MIN_C
            next_speed = max(0, min(next_speed, pts[1].speed if len(pts) > 1 else 100))
        elif index == len(pts) - 1:
            next_temp = fan_config.GPU_TEMP_MAX_C
            next_speed = max(pts[index - 1].speed, min(next_speed, 100))
        else:
            next_temp = max(pts[index - 1].temp + 1, min(next_temp, pts[index + 1].temp - 1))
            next_speed = max(pts[index - 1].speed, min(next_speed, pts[index + 1].speed))
        pts[index] = FanPoint(next_temp, next_speed)
        norm = fan_config.normalize_fan_points(pts, fan_config.GPU_TEMP_MAX_C)
        self._gpu_points = norm
        self._gpu_chart.points = norm
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

    def _on_cpu_point_selected(self, index: int):
        self._cpu_selected = index
        self._gpu_chart.selected_point = -1
        self._gpu_selected = -1

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

    # ── Public API for timer sync ──
    def sync_power_from_settings(self):
        pwr = read_power_limit_settings()
        self._stapm_spin._spin.setValue(round(pwr.stapm_limit / 1000))
        self._fast_spin._spin.setValue(round(pwr.fast_limit / 1000))
        self._slow_spin._spin.setValue(round(pwr.slow_limit / 1000))
        self._tctl_spin._spin.setValue(pwr.tctl_temp)
        self._reapply_spin._spin.setValue(pwr.reapply_seconds)
        self._applied_stapm = pwr.stapm_limit
        self._applied_fast = pwr.fast_limit
        self._applied_slow = pwr.slow_limit
        self._applied_tctl = pwr.tctl_temp
        self._power_enabled = read_power_enabled()
        self._power_check.blockSignals(True)
        self._power_check.setChecked(self._power_enabled)
        self._power_check.blockSignals(False)
        self._update_power_status()
        self._update_apply_enabled()

    def refresh_fan_curves(self):
        """Reload fan config from disk and update the inline charts.

        Called when the pop-out fan curves window closes so any edits made
        there are reflected in the embedded charts.
        """
        self._load_config()

    # ── Pop-out window management ──

    def set_fan_curves_window_open(self, is_open: bool):
        """Toggle between inline charts and pop-out placeholder."""
        if is_open == self._fan_curves_window_open:
            return
        self._fan_curves_window_open = is_open
        self._charts_wrapper.setVisible(not is_open)
        self._popout_placeholder.setVisible(is_open)

    def is_fan_curves_window_open(self) -> bool:
        return self._fan_curves_window_open
