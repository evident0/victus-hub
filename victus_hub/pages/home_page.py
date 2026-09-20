"""Home page — Ohman big readings, modes, fans, lighting shortcut, footer."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal

from victus_hub.widgets.profile_section import ProfileSection
from victus_hub.widgets.chrome import PageHead, BigMetric, FooterBar, hairline
from victus_hub.app.theme import COLORS, mode_name, ui_font
from victus_hub.features.keyboard.lighting import (
    lighting_frames,
    normalize_lighting_settings,
    read_lighting_settings,
)
from victus_hub.features.power.limits import read_power_limit_settings, read_power_enabled


def _parse_rpm(text: str) -> str:
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    return digits if digits else "—"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}%"


class HomePage(QWidget):
    """Home tab: live temps/fans, performance + fan + MUX, lighting jump."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)
    fans_clicked = Signal()
    lighting_clicked = Signal()
    power_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(0)

        self._head = PageHead("Balanced")
        layout.addWidget(self._head)

        grid = QGridLayout()
        grid.setContentsMargins(0, 24, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._cpu = BigMetric("CPU", "°C")
        self._gpu = BigMetric("GPU", "°C")
        self._fan1 = BigMetric("CPU fan", " rpm")
        self._fan2 = BigMetric("GPU fan", " rpm")
        grid.addWidget(self._cpu, 0, 0)
        grid.addWidget(self._gpu, 0, 1)
        rule = QWidget()
        rule_l = QVBoxLayout(rule)
        rule_l.setContentsMargins(0, 18, 0, 18)
        rule_l.setSpacing(0)
        rule_l.addWidget(hairline())
        grid.addWidget(rule, 1, 0, 1, 2)
        grid.addWidget(self._fan1, 2, 0)
        grid.addWidget(self._fan2, 2, 1)
        layout.addLayout(grid)

        self._profile_section = ProfileSection()
        self._profile_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._profile_section.profile_selected.connect(self.profile_selected.emit)
        self._profile_section.fan_mode_selected.connect(self.fan_mode_selected.emit)
        self._profile_section.fan_curves_requested.connect(self.fans_clicked.emit)
        wrap = QWidget()
        wrap_l = QVBoxLayout(wrap)
        wrap_l.setContentsMargins(0, 24, 0, 0)
        wrap_l.addWidget(self._profile_section)
        layout.addWidget(wrap)

        power_row = QWidget()
        power_row.setCursor(Qt.PointingHandCursor)
        pr = QHBoxLayout(power_row)
        pr.setContentsMargins(0, 20, 0, 0)
        pr.setSpacing(12)
        p_lbl = QLabel("Power")
        p_lbl.setFont(ui_font(14))
        p_lbl.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        pr.addWidget(p_lbl)
        pr.addStretch()
        self._power_sub = QLabel("Limits off")
        self._power_sub.setFont(ui_font(12))
        self._power_sub.setStyleSheet(f"color: {COLORS['sub']}; background: transparent;")
        pr.addWidget(self._power_sub)
        arrow = QLabel("→")
        arrow.setFont(ui_font(15))
        arrow.setStyleSheet(f"color: {COLORS['accent']}; background: transparent;")
        self._power_arrow = arrow
        pr.addWidget(arrow)
        power_row.mousePressEvent = lambda e: self.power_clicked.emit()  # type: ignore[method-assign]
        layout.addWidget(power_row)

        light_row = QWidget()
        light_row.setCursor(Qt.PointingHandCursor)
        lr = QHBoxLayout(light_row)
        lr.setContentsMargins(0, 20, 0, 0)
        lr.setSpacing(12)
        light_text = QVBoxLayout()
        light_text.setContentsMargins(0, 0, 0, 0)
        light_text.setSpacing(3)
        l_lbl = QLabel("Lighting")
        l_lbl.setFont(ui_font(14))
        l_lbl.setStyleSheet(f"color: {COLORS['text']}; background: transparent;")
        light_text.addWidget(l_lbl)
        self._light_sub = QLabel("Static")
        self._light_sub.setFont(ui_font(12))
        self._light_sub.setStyleSheet(f"color: {COLORS['sub']}; background: transparent;")
        light_text.addWidget(self._light_sub)
        lr.addLayout(light_text, 1)

        from victus_hub import api as _api
        from victus_hub.pages.keyboard_page import KeyboardVisual
        self._zone_count = _api.get_keyboard_zone_count()
        self._mini = KeyboardVisual(zone_count=self._zone_count, compact=True)
        self._mini.setFixedSize(145, 64)
        lr.addWidget(self._mini, 0, Qt.AlignVCenter)
        l_arrow = QLabel("→")
        l_arrow.setFont(ui_font(15))
        l_arrow.setStyleSheet(f"color: {COLORS['accent']}; background: transparent;")
        self._light_arrow = l_arrow
        lr.addWidget(l_arrow)
        light_row.mousePressEvent = lambda e: self.lighting_clicked.emit()  # type: ignore[method-assign]
        layout.addWidget(light_row)

        layout.addStretch(1)

        self._footer = FooterBar()
        self._footer.set_left("HP Laptop")
        self._footer.set_right("v0.1.0")
        layout.addWidget(self._footer)

        self.refresh_power()
        self.refresh_lighting()

    def update_sensor_data(self, snapshot):
        """Refresh the four home readings from a sensor snapshot."""
        cpu_temp_c = snapshot.cpu_temp_c
        cpu_temp = f"{cpu_temp_c:.0f}" if cpu_temp_c is not None else "—"
        cpu_cap = f"CPU · {_fmt_pct(snapshot.cpu_usage_pct)}"
        power = getattr(snapshot.cpu_power, "value", "") or ""
        if power and power not in ("0 W", "0"):
            cpu_cap += f" · {power}"
        self._cpu.set_reading(cpu_temp, cpu_cap)

        gpu_temp_c = snapshot.gpu_temp_c
        gpu_temp = f"{gpu_temp_c:.0f}" if gpu_temp_c is not None else "—"
        gpu_cap = f"GPU · {_fmt_pct(snapshot.gpu_usage_pct)}"
        gpu_power = getattr(snapshot.gpu_power, "value", "") or ""
        if gpu_power and gpu_power not in ("0 W", "0"):
            gpu_cap += f" · {gpu_power}"
        self._gpu.set_reading(gpu_temp, gpu_cap)

        self._fan1.set_reading(_parse_rpm(snapshot.cpu_fan.value))
        self._fan2.set_reading(_parse_rpm(snapshot.gpu_fan.value))

        ram = ""
        if snapshot.ram_used_gb is not None and snapshot.ram_total_gb is not None:
            ram = f"RAM {snapshot.ram_used_gb:.1f}/{snapshot.ram_total_gb:.0f} GB"
        self._head.set_status(ram)

    def apply_frame(self, frame) -> None:
        """Drive the mini keyboard from the lighting controller."""
        from PySide6.QtGui import QColor
        if isinstance(frame, (list, tuple)):
            if not frame:
                self._mini.set_zone_state(False, [])
                return
            colors = [QColor(c.red, c.green, c.blue) for c in frame]
            is_off = all(max(c.red(), c.green(), c.blue()) == 0 for c in colors)
            self._mini.set_zone_state(not is_off, colors)
            return
        qc = QColor(frame.red, frame.green, frame.blue)
        is_off = max(frame.red, frame.green, frame.blue) == 0
        self._mini.set_key_state(not is_off, qc)

    def set_hardware_title(self, title: str):
        self._footer.set_left(title.split("(")[0].strip())

    def set_selected_profile(self, index: int):
        self._profile_section.set_selected_profile(index)
        self._head.set_title(mode_name(index))

    def set_selected_fan_mode(self, mode: str):
        self._profile_section.set_selected_fan_mode(mode)

    def refresh_accent(self) -> None:
        self._profile_section.refresh_accent()
        self._power_arrow.setStyleSheet(
            f"color: {COLORS['accent']}; background: transparent;"
        )
        self._light_arrow.setStyleSheet(
            f"color: {COLORS['accent']}; background: transparent;"
        )
        self.refresh_power()
        self.refresh_lighting()

    def refresh_power(self) -> None:
        from victus_hub.backend.cpu import is_intel_cpu

        if not read_power_enabled():
            self._power_sub.setText("Limits off")
            return
        pwr = read_power_limit_settings()
        if is_intel_cpu():
            self._power_sub.setText(f"PL1 {round(pwr.slow_limit / 1000)} W · PL2 {round(pwr.fast_limit / 1000)} W")
            return
        watts = round(pwr.stapm_limit / 1000)
        self._power_sub.setText(f"STAPM {watts} W")

    def refresh_lighting(self) -> None:
        """Sync the lighting caption and mini keyboard from saved settings."""
        s = read_lighting_settings()
        if not s.enabled:
            self._light_sub.setText("Off")
            self._mini.set_zone_state(False, [])
            return
        effect = (s.effect or "static").replace("_", " ").title()
        zones = len(s.zone_colors) if s.zone_colors else 1
        if zones > 1:
            self._light_sub.setText(f"{effect} · {zones} zones")
        else:
            self._light_sub.setText(effect)
        frames = lighting_frames(
            normalize_lighting_settings(s, self._zone_count),
            self._zone_count,
            0.0,
        )
        self.apply_frame(frames)
