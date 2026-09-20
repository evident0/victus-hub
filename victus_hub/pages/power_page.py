"""Power page — vendor-specific power limits, CPU frequency, and Intel undervolt."""

import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSizePolicy, QLabel, QSlider,
    QSpinBox, QDoubleSpinBox,
)
from PySide6.QtCore import Qt, Signal

from victus_hub.app.theme import COLORS, mono_font, ui_font
from victus_hub.widgets.toggle_switch import ToggleSwitch
from victus_hub.backend.types import SensorSnapshot
from victus_hub.backend.cpufreq import read_frequency_policies
from victus_hub.backend.cpu import is_intel_cpu
from victus_hub.backend.daemon_client import request_cpu_frequency_limits, request_intel_undervolt
from victus_hub.widgets.chrome import PageHead, hairline
from victus_hub.features.power.limits import (
    POWER_MIN_MW, POWER_MAX_MW,
    REAPPLY_MIN_S, REAPPLY_MAX_S,
    TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C,
    PowerLimitSettings, clamp_power_limit, clamp_reapply_seconds, clamp_tctl_temp,
    read_power_enabled, write_power_enabled,
    read_power_limit_settings, write_power_limit_settings,
    read_frequency_limits, write_frequency_limits,
    read_intel_undervolt, write_intel_undervolt,
)
from victus_hub.api import apply_power_limits, set_cpu_frequency_policy, set_power_policy


class _SliderRow(QWidget):
    """Label, slider, and synchronized numeric stepper."""

    def __init__(self, title: str, vmin: int, vmax: int, value: int, suffix: str,
                 parent=None, *, scale: int = 1):
        super().__init__(parent)
        self._scale = scale
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 14, 0, 14)
        row.setSpacing(12)
        lbl = QLabel(title)
        lbl.setFont(ui_font(14))
        lbl.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        lbl.setFixedWidth(90)
        row.addWidget(lbl)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(vmin, vmax)
        self.slider.setSingleStep(scale)
        self.slider.setPageStep(10 * scale)
        self.slider.setValue(value)
        row.addWidget(self.slider, 1)
        self.value = QDoubleSpinBox() if scale != 1 else QSpinBox()
        if scale != 1:
            self.value.setDecimals(3)
            self.value.setRange(vmin / scale, vmax / scale)
        else:
            self.value.setRange(vmin, vmax)
        self.value.setSingleStep(1)
        self.value.setSuffix(f" {suffix}")
        self.value.setKeyboardTracking(False)
        self.value.setValue(value / scale if scale != 1 else value)
        self.value.setFont(mono_font(13))
        self.value.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.value)
        self.slider.valueChanged.connect(self._on_slide)
        self.value.valueChanged.connect(lambda v: self.slider.setValue(round(v * scale)))

    def _on_slide(self, v: int) -> None:
        self.value.setValue(v / self._scale if self._scale != 1 else v)

    def setValue(self, v: int) -> None:
        self.slider.setValue(v)


class PowerPage(QWidget):
    """Power tab: limit sliders, apply, and auto-reapply."""

    limits_applied = Signal()
    _frequency_applied = Signal(str)
    _undervolt_applied = Signal(str)
    _power_result = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._intel = is_intel_cpu()
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
        self._applied_reapply = pwr.reapply_seconds
        self._applied_frequency = None
        self._frequency_busy = False
        self._intel_power_failed = False
        self._power_busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(0)

        self._head = PageHead("Power", status_width=360)
        self._head.set_status("CPU — W · Freq — MHz")
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

        self._power_disabled_note = QLabel(
            "To reset power limits to firmware defaults a reboot is required."
        )
        self._power_disabled_note.setWordWrap(True)
        self._power_disabled_note.setFont(ui_font(12))
        self._power_disabled_note.setStyleSheet(
            f"color: {COLORS['sub']}; padding-top: 8px;"
        )
        layout.addWidget(self._power_disabled_note)

        self._power_settings = QWidget()
        power_layout = QVBoxLayout(self._power_settings)
        power_layout.setContentsMargins(0, 0, 0, 0)
        power_layout.setSpacing(0)
        layout.addWidget(self._power_settings)

        self._stapm_spin = _SliderRow(
            "STAPM", power_min_w, power_max_w,
            round(self._stapm_limit / 1000), "W",
        )
        self._stapm_spin.slider.valueChanged.connect(self._on_stapm_changed)
        power_layout.addWidget(self._stapm_spin)

        self._fast_spin = _SliderRow(
            "PL2 (short)" if self._intel else "Fast", power_min_w, power_max_w,
            round(self._fast_limit / 1000), "W",
        )
        self._fast_spin.slider.valueChanged.connect(self._on_fast_changed)
        power_layout.addWidget(self._fast_spin)

        self._slow_spin = _SliderRow(
            "PL1 (long)" if self._intel else "Slow", power_min_w, power_max_w,
            round(self._slow_limit / 1000), "W",
        )
        self._slow_spin.slider.valueChanged.connect(self._on_slow_changed)
        power_layout.addWidget(self._slow_spin)

        self._tctl_spin = _SliderRow(
            "Tctl", TCTL_TEMP_MIN_C, TCTL_TEMP_MAX_C,
            self._tctl_temp, "°C",
        )
        self._tctl_spin.slider.valueChanged.connect(self._on_tctl_changed)
        power_layout.addWidget(self._tctl_spin)
        if self._intel:
            self._stapm_spin.hide()
            self._tctl_spin.hide()
            power_layout.removeWidget(self._slow_spin)
            power_layout.insertWidget(0, self._slow_spin)

        self._reapply_spin = _SliderRow(
            "Reapply", REAPPLY_MIN_S, REAPPLY_MAX_S,
            clamp_reapply_seconds(self._reapply_seconds), "s",
        )
        self._reapply_spin.slider.valueChanged.connect(self._on_reapply_changed)
        power_layout.addWidget(self._reapply_spin)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.setObjectName("accentBtn")
        self._apply_btn.setCursor(Qt.PointingHandCursor)
        self._apply_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._apply_btn.clicked.connect(self._on_apply_power)
        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(0, 16, 0, 0)
        apply_row.addWidget(self._apply_btn)
        apply_row.addStretch()
        power_layout.addLayout(apply_row)
        if self._intel:
            self._power_status = QLabel()
            self._power_status.setWordWrap(True)
            self._power_status.setFont(ui_font(12))
            self._power_status.setStyleSheet(f"color: {COLORS['sub']}; padding-top: 8px;")
            power_layout.addWidget(self._power_status)
            self._power_result.connect(self._on_power_result)

        layout.addSpacing(16)
        layout.addWidget(hairline())
        self._frequency_min = _SliderRow("CPU min", 0, 1, 0, "MHz", scale=1000)
        self._frequency_max = _SliderRow("CPU max", 0, 1, 0, "MHz", scale=1000)
        layout.addWidget(self._frequency_min)
        layout.addWidget(self._frequency_max)
        self._frequency_min.slider.valueChanged.connect(self._on_frequency_min_changed)
        self._frequency_max.slider.valueChanged.connect(self._on_frequency_max_changed)
        self._frequency_btn = QPushButton("Apply CPU frequency")
        self._frequency_btn.setObjectName("accentBtn")
        self._frequency_btn.setCursor(Qt.PointingHandCursor)
        self._frequency_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._frequency_btn.clicked.connect(self._on_apply_frequency)
        layout.addWidget(self._frequency_btn)
        self._frequency_status = QLabel()
        self._frequency_status.setWordWrap(True)
        self._frequency_status.setFont(ui_font(12))
        self._frequency_status.setStyleSheet(f"color: {COLORS['sub']}; padding-top: 8px;")
        layout.addWidget(self._frequency_status)
        self._frequency_applied.connect(self._on_frequency_applied)
        self._load_frequency_limits()
        saved_frequency = read_frequency_limits()
        if saved_frequency is not None and self._frequency_min.isEnabled():
            minimum, maximum = saved_frequency
            self._frequency_max.setValue(maximum)
            self._frequency_min.setValue(minimum)
            self._on_apply_frequency()

        if self._intel:
            self._add_undervolt_controls(layout)

        self._update_apply_enabled()
        layout.addStretch()

    def _add_undervolt_controls(self, layout: QVBoxLayout) -> None:
        layout.addSpacing(16)
        layout.addWidget(hairline())
        core, cache = read_intel_undervolt()
        self._undervolt_core = _SliderRow("Core offset", -250, 0, core, "mV")
        self._undervolt_cache = _SliderRow("Cache offset", -250, 0, cache, "mV")
        layout.addWidget(self._undervolt_core)
        layout.addWidget(self._undervolt_cache)
        self._undervolt_btn = QPushButton("Apply undervolt")
        self._undervolt_btn.setObjectName("accentBtn")
        self._undervolt_btn.setCursor(Qt.PointingHandCursor)
        self._undervolt_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._undervolt_btn.clicked.connect(self._on_apply_undervolt)
        layout.addWidget(self._undervolt_btn)
        self._undervolt_status = QLabel(
            "Intel core/cache offsets. Set both to 0 mV to reset. "
            "Requires the msr kernel module and firmware voltage-control support. "
            "Saved offsets are applied only when you click Apply undervolt."
        )
        self._undervolt_status.setWordWrap(True)
        self._undervolt_status.setFont(ui_font(12))
        self._undervolt_status.setStyleSheet(f"color: {COLORS['sub']}; padding-top: 8px;")
        layout.addWidget(self._undervolt_status)
        self._undervolt_applied.connect(self._on_undervolt_applied)

    def _on_apply_undervolt(self) -> None:
        self._pending_undervolt = (
            self._undervolt_core.slider.value(), self._undervolt_cache.slider.value(),
        )
        for widget in (self._undervolt_core, self._undervolt_cache, self._undervolt_btn):
            widget.setEnabled(False)
        self._undervolt_status.setText("Applying Intel undervolt…")
        offsets = self._pending_undervolt

        def _apply():
            error = ""
            try:
                request_intel_undervolt(*offsets)
            except Exception as exc:
                error = str(exc)
            self._undervolt_applied.emit(error)

        threading.Thread(target=_apply, daemon=True, name="undervolt-apply").start()

    def _on_undervolt_applied(self, error: str) -> None:
        for widget in (self._undervolt_core, self._undervolt_cache, self._undervolt_btn):
            widget.setEnabled(True)
        if error:
            self._undervolt_status.setText(f"Could not apply Intel undervolt: {error}")
        else:
            core, cache = self._pending_undervolt
            write_intel_undervolt(core, cache)
            self._undervolt_status.setText(f"Undervolt applied: core {core} mV · cache {cache} mV")

    def _on_power_result(self, error: str) -> None:
        self._power_busy = False
        self._intel_power_failed = bool(error)
        self._power_status.setText(
            f"Could not apply Intel power limits: {error}" if error else "Intel PL1/PL2 limits applied."
        )
        self._update_apply_enabled()

    def update_sensor_data(self, snapshot: SensorSnapshot) -> None:
        frequencies = [
            sensor.numeric_value for sensor in snapshot.extra_sensors
            if sensor.key.startswith("cpu-frequency-") and sensor.unit == "MHz"
        ]
        maximum = f"{max(frequencies):.1f} MHz" if frequencies else "— MHz"
        self._head.set_status(f"CPU {snapshot.cpu_power.value} · Freq {maximum}")
        if not self._frequency_busy:
            self._load_frequency_limits(refresh=True)

    def _load_frequency_limits(self, *, refresh=False):
        try:
            policies = read_frequency_policies()
            if refresh and policies == getattr(self, "_frequency_policies", None):
                return
            lower = max(p.hardware_min for p in policies)
            upper = min(p.hardware_max for p in policies)
            if lower > upper:
                raise RuntimeError("CPU policies have no common frequency range")
        except RuntimeError as exc:
            self._frequency_policies = None
            self._frequency_min.setEnabled(False)
            self._frequency_max.setEnabled(False)
            self._frequency_btn.setEnabled(False)
            self._frequency_status.setText(str(exc))
            return
        minimum = max(lower, min(upper, max(p.minimum for p in policies)))
        maximum = max(minimum, min(upper, min(p.maximum for p in policies)))
        current = (self._frequency_min.slider.value(), self._frequency_max.slider.value())
        if (refresh and self._frequency_min.isEnabled()
                and current != getattr(self, "_frequency_displayed", None)):
            # Profile changes can change cpuinfo limits. Keep pending edits,
            # clamped to the newly advertised range, rather than resetting them.
            minimum = max(lower, min(upper, self._frequency_min.slider.value()))
            maximum = max(minimum, min(upper, self._frequency_max.slider.value()))
        self._frequency_displayed = (
            max(lower, min(upper, max(p.minimum for p in policies))),
            max(lower, min(upper, min(p.maximum for p in policies))),
        )
        if refresh and self._applied_frequency is not None:
            self._applied_frequency = self._frequency_displayed
        self._frequency_policies = list(policies)
        for row, value in ((self._frequency_min, minimum), (self._frequency_max, maximum)):
            row.slider.blockSignals(True)
            row.value.blockSignals(True)
            row.slider.setRange(lower, upper)
            row.value.setRange(lower / 1000, upper / 1000)
            row.setValue(value)
            row.value.setValue(value / 1000)
            row.value.blockSignals(False)
            row.slider.blockSignals(False)
            row.setEnabled(True)
        self._update_frequency_apply_enabled()
        mixed = len({(p.minimum, p.maximum) for p in policies}) > 1
        self._frequency_status.setText(
            f"Applies to all {len(policies)} CPU policies. "
            + ("Current limits differ between policies. " if mixed else "")
            + "Hardware range: "
            f"{lower / 1000:.3f}–{upper / 1000:.3f} MHz."
        )

    def _on_frequency_min_changed(self, value: int):
        if value > self._frequency_max.slider.value():
            self._frequency_max.setValue(value)
        self._update_frequency_apply_enabled()

    def _on_frequency_max_changed(self, value: int):
        if value < self._frequency_min.slider.value():
            self._frequency_min.setValue(value)
        self._update_frequency_apply_enabled()

    def _update_frequency_apply_enabled(self):
        current = (self._frequency_min.slider.value(), self._frequency_max.slider.value())
        enabled = not self._frequency_busy and current != self._applied_frequency
        self._frequency_btn.setEnabled(enabled)
        self._frequency_btn.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)

    def _on_apply_frequency(self):
        minimum = self._frequency_min.slider.value()
        maximum = self._frequency_max.slider.value()
        self._pending_frequency = (minimum, maximum)
        self._frequency_busy = True
        self._frequency_btn.setEnabled(False)
        self._frequency_min.setEnabled(False)
        self._frequency_max.setEnabled(False)
        self._frequency_status.setText("Applying CPU frequency limits…")

        def _apply():
            error = ""
            try:
                request_cpu_frequency_limits(minimum, maximum)
            except Exception as exc:
                error = str(exc)
            self._frequency_applied.emit(error)

        threading.Thread(target=_apply, daemon=True, name="frequency-apply").start()

    def _on_frequency_applied(self, error: str):
        self._frequency_busy = False
        if not error:
            write_frequency_limits(*self._pending_frequency)
            set_cpu_frequency_policy(*self._pending_frequency)
        # Read back the kernel's accepted limits, including after a partial failure.
        self._load_frequency_limits()
        if error:
            self._applied_frequency = None
            self._frequency_status.setText(f"Could not apply CPU frequency: {error}")
        else:
            self._applied_frequency = (
                self._frequency_min.slider.value(), self._frequency_max.slider.value(),
            )
        if self._frequency_min.isEnabled():
            self._update_frequency_apply_enabled()

    def _on_stapm_changed(self, value_w: int):
        self._stapm_limit = clamp_power_limit(value_w * 1000)
        self._update_apply_enabled()

    def _on_fast_changed(self, value_w: int):
        self._fast_limit = clamp_power_limit(value_w * 1000)
        if self._intel and self._fast_limit < self._slow_limit:
            self._slow_spin.setValue(value_w)
        self._update_apply_enabled()

    def _on_slow_changed(self, value_w: int):
        self._slow_limit = clamp_power_limit(value_w * 1000)
        if self._intel and self._slow_limit > self._fast_limit:
            self._fast_spin.setValue(value_w)
        self._update_apply_enabled()

    def _on_tctl_changed(self, value: int):
        self._tctl_temp = clamp_tctl_temp(value)
        self._update_apply_enabled()

    def _on_reapply_changed(self, value: int):
        self._reapply_seconds = clamp_reapply_seconds(value)
        self._update_apply_enabled()

    def _on_power_enabled_changed(self, checked: bool):
        self._power_enabled = checked
        write_power_enabled(checked)
        set_power_policy(checked, self._make_power_settings())
        self._update_apply_enabled()
        self.limits_applied.emit()

    def _power_values_dirty(self) -> bool:
        return (
            self._stapm_limit != self._applied_stapm
            or self._fast_limit != self._applied_fast
            or self._slow_limit != self._applied_slow
            or self._tctl_temp != self._applied_tctl
            or self._reapply_seconds != self._applied_reapply
        )

    def _update_apply_enabled(self):
        self._power_settings.setVisible(self._power_enabled)
        self._power_disabled_note.setVisible(not self._power_enabled)
        can_apply = self._power_enabled and not self._power_busy and (
            self._power_values_dirty() or self._intel_power_failed
        )
        self._apply_btn.setEnabled(can_apply)
        self._apply_btn.setCursor(
            Qt.PointingHandCursor if can_apply else Qt.ArrowCursor
        )

    def _on_apply_power(self):
        settings = self._make_power_settings()
        write_power_limit_settings(settings)
        set_power_policy(True, settings)
        self._applied_stapm = settings.stapm_limit
        self._applied_fast = settings.fast_limit
        self._applied_slow = settings.slow_limit
        self._applied_tctl = settings.tctl_temp
        self._applied_reapply = settings.reapply_seconds
        self._update_apply_enabled()
        self.limits_applied.emit()

        if self._intel:
            self._power_busy = True
            self._update_apply_enabled()
            self._power_status.setText("Applying Intel power limits…")

        def _apply():
            error = ""
            try:
                apply_power_limits(
                    settings.stapm_limit,
                    settings.fast_limit,
                    settings.slow_limit,
                    settings.tctl_temp,
                )
            except Exception as exc:
                error = str(exc)
            if self._intel:
                self._power_result.emit(error)

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
        self._applied_reapply = pwr.reapply_seconds
        self._power_enabled = read_power_enabled()
        self._power_check.blockSignals(True)
        self._power_check.setChecked(self._power_enabled)
        self._power_check.blockSignals(False)
        self._update_apply_enabled()
