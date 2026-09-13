"""Power page — ryzenadj power limit controls, Ohman rows."""

import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSizePolicy, QLabel, QSlider,
)
from PySide6.QtCore import Qt, Signal

from victus_hub.app.theme import COLORS, mono_font, ui_font
from victus_hub.widgets.toggle_switch import ToggleSwitch
from victus_hub.backend.modules import ryzenadj_available
from victus_hub.widgets.chrome import PageHead, hairline
from victus_hub.features.power.limits import (
    POWER_MIN_MW, POWER_MAX_MW,
    REAPPLY_MIN_S, REAPPLY_MAX_S,
    TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C,
    PowerLimitSettings, clamp_power_limit, clamp_reapply_seconds, clamp_tctl_temp,
    read_power_enabled, write_power_enabled,
    read_power_limit_settings, write_power_limit_settings,
)
from victus_hub.api import apply_power_limits


class _SliderRow(QWidget):
    """Label, slider, mono value — Ohman power-gain row."""

    def __init__(self, title: str, vmin: int, vmax: int, value: int, suffix: str,
                 parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 14, 0, 14)
        row.setSpacing(18)
        lbl = QLabel(title)
        lbl.setFont(ui_font(14))
        lbl.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        lbl.setFixedWidth(110)
        row.addWidget(lbl)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(vmin, vmax)
        self.slider.setValue(value)
        row.addWidget(self.slider, 1)
        self.value = QLabel(f"{value} {suffix}")
        self.value.setFont(mono_font(13))
        self.value.setFixedWidth(56)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        row.addWidget(self.value)
        self._suffix = suffix
        self.slider.valueChanged.connect(self._on_slide)

    def _on_slide(self, v: int) -> None:
        self.value.setText(f"{v} {self._suffix}")

    def setValue(self, v: int) -> None:
        self.slider.setValue(v)


class PowerPage(QWidget):
    """Power tab: limit sliders, apply, and auto-reapply."""

    limits_applied = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        pwr = read_power_limit_settings()
        self._stapm_limit = pwr.stapm_limit
        self._fast_limit = pwr.fast_limit
        self._slow_limit = pwr.slow_limit
        self._tctl_temp = pwr.tctl_temp
        self._reapply_seconds = pwr.reapply_seconds
        self._power_enabled = read_power_enabled()
        self._applied_stapm = pwr.stapm_limit
        self._applied_fast = pwr.fast_limit
        self._applied_slow = pwr.slow_limit
        self._applied_tctl = pwr.tctl_temp

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(0)

        ry_text, _ry_color = ryzenadj_available()
        self._head = PageHead("Power")
        self._head.set_status(ry_text)
        layout.addWidget(self._head)

        power_min_w = POWER_MIN_MW // 1000
        power_max_w = POWER_MAX_MW // 1000

        enable_row = QHBoxLayout()
        enable_row.setContentsMargins(0, 24, 0, 0)
        self._power_check = ToggleSwitch("Enable power limits")
        self._power_check.setChecked(self._power_enabled)
        self._power_check.toggled.connect(self._on_power_enabled_changed)
        enable_row.addWidget(self._power_check)
        enable_row.addStretch()
        layout.addLayout(enable_row)

        self._stapm_spin = _SliderRow(
            "STAPM", power_min_w, power_max_w,
            round(self._stapm_limit / 1000), "W",
        )
        self._stapm_spin.slider.valueChanged.connect(self._on_stapm_changed)
        layout.addWidget(self._stapm_spin)

        self._fast_spin = _SliderRow(
            "Fast", power_min_w, power_max_w,
            round(self._fast_limit / 1000), "W",
        )
        self._fast_spin.slider.valueChanged.connect(self._on_fast_changed)
        layout.addWidget(self._fast_spin)

        self._slow_spin = _SliderRow(
            "Slow", power_min_w, power_max_w,
            round(self._slow_limit / 1000), "W",
        )
        self._slow_spin.slider.valueChanged.connect(self._on_slow_changed)
        layout.addWidget(self._slow_spin)

        self._tctl_spin = _SliderRow(
            "Tctl", TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C,
            self._tctl_temp, "°C",
        )
        self._tctl_spin.slider.valueChanged.connect(self._on_tctl_changed)
        layout.addWidget(self._tctl_spin)

        layout.addWidget(hairline())

        self._reapply_spin = _SliderRow(
            "Reapply", REAPPLY_MIN_S, REAPPLY_MAX_S,
            clamp_reapply_seconds(self._reapply_seconds), "s",
        )
        self._reapply_spin.slider.valueChanged.connect(self._on_reapply_changed)
        layout.addWidget(self._reapply_spin)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setObjectName("accentBtn")
        self._apply_btn.setCursor(Qt.PointingHandCursor)
        self._apply_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._apply_btn.clicked.connect(self._on_apply_power)
        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(0, 16, 0, 0)
        apply_row.addWidget(self._apply_btn)
        apply_row.addStretch()
        layout.addLayout(apply_row)

        self._update_apply_enabled()
        layout.addStretch()

    def _on_stapm_changed(self, value_w: int):
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
        self._reapply_seconds = clamp_reapply_seconds(value)
        applied = read_power_limit_settings()
        write_power_limit_settings(PowerLimitSettings(
            stapm_limit=applied.stapm_limit,
            fast_limit=applied.fast_limit,
            slow_limit=applied.slow_limit,
            tctl_temp=applied.tctl_temp,
            reapply_seconds=clamp_reapply_seconds(self._reapply_seconds),
        ))

    def _on_power_enabled_changed(self, checked: bool):
        self._power_enabled = checked
        write_power_enabled(checked)
        self._update_apply_enabled()
        self.limits_applied.emit()

    def _power_values_dirty(self) -> bool:
        return (
            self._stapm_limit != self._applied_stapm
            or self._fast_limit != self._applied_fast
            or self._slow_limit != self._applied_slow
            or self._tctl_temp != self._applied_tctl
        )

    def _update_apply_enabled(self):
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
        self.limits_applied.emit()

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

    def _make_power_settings(self):
        return PowerLimitSettings(
            stapm_limit=self._stapm_limit,
            fast_limit=self._fast_limit,
            slow_limit=self._slow_limit,
            tctl_temp=self._tctl_temp,
            reapply_seconds=clamp_reapply_seconds(self._reapply_seconds),
        )

    def sync_power_from_settings(self):
        pwr = read_power_limit_settings()
        self._stapm_spin.setValue(round(pwr.stapm_limit / 1000))
        self._fast_spin.setValue(round(pwr.fast_limit / 1000))
        self._slow_spin.setValue(round(pwr.slow_limit / 1000))
        self._tctl_spin.setValue(pwr.tctl_temp)
        self._reapply_spin.setValue(pwr.reapply_seconds)
        self._applied_stapm = pwr.stapm_limit
        self._applied_fast = pwr.fast_limit
        self._applied_slow = pwr.slow_limit
        self._applied_tctl = pwr.tctl_temp
        self._power_enabled = read_power_enabled()
        self._power_check.blockSignals(True)
        self._power_check.setChecked(self._power_enabled)
        self._power_check.blockSignals(False)
        self._update_apply_enabled()
