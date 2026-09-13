"""Power page — ryzenadj power limit controls."""

import threading

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton
from PySide6.QtCore import Qt

from victus_hub.app.theme import COLORS
from victus_hub.widgets.toggle_switch import ToggleSwitch
from victus_hub.widgets.status_badge import StatusBadge
from victus_hub.backend.modules import ryzenadj_available
from victus_hub.pages.settings_page import (
    make_spin, make_settings_card, make_card_title,
)
from victus_hub.features.power.limits import (
    POWER_MIN_MW, POWER_MAX_MW,
    TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C,
    PowerLimitSettings, clamp_power_limit, clamp_tctl_temp,
    read_power_enabled, write_power_enabled,
    read_power_limit_settings, write_power_limit_settings,
)
from victus_hub.api import apply_power_limits


class PowerPage(QWidget):
    """Power tab: limit steppers, apply, and auto-reapply."""

    def __init__(self, parent=None):
        super().__init__(parent)

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        power_min_w = POWER_MIN_MW // 1000
        power_max_w = POWER_MAX_MW // 1000

        limits_card, limits_layout = make_settings_card()
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(make_card_title("Power Limits"))
        title_row.addStretch()
        ry_text, ry_color = ryzenadj_available()
        title_row.addWidget(StatusBadge(ry_text, ry_color))
        limits_layout.addLayout(title_row)

        self._stapm_spin = make_spin(
            "STAPM Limit", "W",
            round(self._stapm_limit / 1000), power_min_w, power_max_w,
        )
        self._stapm_spin._spin.valueChanged.connect(self._on_stapm_changed)
        limits_layout.addLayout(self._stapm_spin)

        self._fast_spin = make_spin(
            "Fast Limit", "W",
            round(self._fast_limit / 1000), power_min_w, power_max_w,
        )
        self._fast_spin._spin.valueChanged.connect(self._on_fast_changed)
        limits_layout.addLayout(self._fast_spin)

        self._slow_spin = make_spin(
            "Slow Limit", "W",
            round(self._slow_limit / 1000), power_min_w, power_max_w,
        )
        self._slow_spin._spin.valueChanged.connect(self._on_slow_changed)
        limits_layout.addLayout(self._slow_spin)

        self._tctl_spin = make_spin(
            "Tctl Temp", "°C",
            self._tctl_temp, TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C,
        )
        self._tctl_spin._spin.valueChanged.connect(self._on_tctl_changed)
        limits_layout.addLayout(self._tctl_spin)

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
        limits_layout.addWidget(self._apply_btn)

        self._power_check = ToggleSwitch("Enable power limits")
        self._power_check.setChecked(self._power_enabled)
        self._power_check.toggled.connect(self._on_power_enabled_changed)
        limits_layout.addWidget(self._power_check)

        layout.addWidget(limits_card)

        reapply_card, reapply_layout = make_settings_card()
        reapply_layout.addWidget(make_card_title("Auto reapply"))
        self._reapply_spin = make_spin(
            "Interval", "s",
            self._reapply_seconds, 1, 3600,
        )
        self._reapply_spin._spin.valueChanged.connect(self._on_reapply_changed)
        reapply_layout.addLayout(self._reapply_spin)
        layout.addWidget(reapply_card)

        self._update_apply_enabled()
        layout.addStretch()

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

    def _make_power_settings(self):
        return PowerLimitSettings(
            stapm_limit=self._stapm_limit,
            fast_limit=self._fast_limit,
            slow_limit=self._slow_limit,
            tctl_temp=self._tctl_temp,
            reapply_seconds=self._reapply_seconds,
        )

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
        self._update_apply_enabled()
