"""Home page — profile selector, fan modes, live trends, and footer."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QSizePolicy,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor

from hp_helper.widgets.profile_section import ProfileSection
from hp_helper.widgets.footer import Footer
from hp_helper.widgets.utilization_card import UtilizationCard
from hp_helper.widgets.sensor_line_graph import SensorLineGraph
from hp_helper.sensor_stats import parse_reading_num
from hp_helper.theme import COLORS

# ~90s of history at 1 Hz sensor poll
_TREND_SAMPLES = 90


class _TrendPanel(QFrame):
    """Framed sparkline with title + live value header."""

    def __init__(
        self,
        title: str,
        unit: str,
        accent: str,
        value_min: float,
        value_max: float,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("trendPanel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(110)
        self.setStyleSheet(f"""
            #trendPanel {{
                background-color: {COLORS['surface']};
                border: 1px solid {COLORS['border']};
                border-radius: 10px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text']};
                font-size: 12px;
                font-weight: bold;
                background: transparent;
            }}
        """)
        header.addWidget(self._title_label)
        header.addStretch()

        self._value_label = QLabel("—")
        self._value_label.setStyleSheet(f"""
            QLabel {{
                color: {COLORS['text_secondary']};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
        """)
        header.addWidget(self._value_label)
        layout.addLayout(header)

        self._graph = SensorLineGraph(_TREND_SAMPLES, compact=True)
        self._graph.set_value_range(value_min, value_max)
        self._graph.set_unit(unit)
        self._graph.set_accent(QColor(accent))
        layout.addWidget(self._graph, 1)

        self._unit = unit

    def set_title(self, title: str):
        self._title_label.setText(title)

    def append_sample(self, value: float | None, display: str | None = None):
        """Append a numeric sample and update the header value label."""
        self._graph.append_sample(value)
        if display is not None:
            self._value_label.setText(display)
        elif value is None:
            self._value_label.setText("—")
        else:
            if value == int(value):
                text = f"{int(value)} {self._unit}".rstrip()
            else:
                text = f"{value:.1f} {self._unit}".rstrip()
            self._value_label.setText(text)


class HomePage(QWidget):
    """Home tab with profile/fan controls, live trends, and footer."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)
    fan_curves_popout_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Utilization cards ──
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        self._cpu_card = UtilizationCard("CPU", "— °C", COLORS["accent_blue"])
        self._gpu_card = UtilizationCard("GPU", "— °C", COLORS["accent_green"])
        self._ram_card = UtilizationCard("RAM", "— °C", COLORS["accent_red"])

        for card in (self._cpu_card, self._gpu_card, self._ram_card):
            # Cap height so live trends get room below
            card.setMaximumHeight(280)
            cards_row.addWidget(card)

        layout.addLayout(cards_row, 0)

        # ── Live trends (sparklines) ──
        trends_row = QHBoxLayout()
        trends_row.setSpacing(12)

        self._cpu_temp_trend = _TrendPanel(
            "CPU Temp", "\u00B0C", COLORS["accent_blue"], 0, 100,
        )
        self._gpu_temp_trend = _TrendPanel(
            "GPU Temp", "\u00B0C", COLORS["accent_green"], 0, 100,
        )
        self._power_trend = _TrendPanel(
            "Power", "W", COLORS["accent_red"], 0, 80,
        )

        trends_row.addWidget(self._cpu_temp_trend)
        trends_row.addWidget(self._gpu_temp_trend)
        trends_row.addWidget(self._power_trend)
        layout.addLayout(trends_row, 1)

        # Profile section
        self._profile_section = ProfileSection(hide_title=True)
        self._profile_section.profile_selected.connect(self.profile_selected.emit)
        self._profile_section.fan_mode_selected.connect(self.fan_mode_selected.emit)
        self._profile_section.fan_curves_popout_requested.connect(
            self.fan_curves_popout_requested.emit)
        layout.addWidget(self._profile_section, 0)

        # Footer
        self._footer = Footer()
        layout.addWidget(self._footer, 0)

    def update_sensor_data(self, snapshot):
        """Refresh utilization cards and trend sparklines from a sensor snapshot.
        Left: temp (CPU/GPU or RAM if lm-sensors reports it). Right: fan RPM (CPU/GPU) or RAM usage.
        """
        # CPU: usage + temp left, fan RPM right
        cpu_pct = snapshot.cpu_usage_pct or 0.0
        cpu_temp = f"{snapshot.cpu_temp_c:.0f}°C" if snapshot.cpu_temp_c is not None else "— °C"
        cpu_fan = snapshot.cpu_fan.value
        self._cpu_card.update_data(cpu_pct, cpu_temp, COLORS["accent_blue"], "CPU Utilization", cpu_fan)

        # GPU: usage + temp left, fan RPM right
        gpu_pct = snapshot.gpu_usage_pct or 0.0
        gpu_temp = f"{snapshot.gpu_temp_c:.0f}°C" if snapshot.gpu_temp_c is not None else "— °C"
        gpu_fan = snapshot.gpu_fan.value
        self._gpu_card.update_data(gpu_pct, gpu_temp, COLORS["accent_green"], "GPU Utilization", gpu_fan)

        # RAM: temp left (first Memory °C from extra_sensors), usage on right
        ram_pct = snapshot.ram_usage_pct or 0.0
        ram_temp = "— °C"
        for es in getattr(snapshot, "extra_sensors", []):
            if es.group == "Memory" and getattr(es, "unit", None) == "°C":
                val = getattr(es, "numeric_value", None)
                if val is not None:
                    ram_temp = f"{val:.0f}°C"
                else:
                    ram_temp = getattr(getattr(es, "reading", None), "value", "— °C")
                break
        if snapshot.ram_used_gb is not None and snapshot.ram_total_gb is not None:
            ram_usage = f"{snapshot.ram_used_gb:.1f} / {snapshot.ram_total_gb:.1f} GB"
        else:
            ram_usage = "— GB"
        self._ram_card.update_data(ram_pct, ram_temp, COLORS["accent_red"], "RAM Utilization", ram_usage)

        # Live trends
        cpu_temp_display = (
            f"{snapshot.cpu_temp_c:.0f} °C" if snapshot.cpu_temp_c is not None else "—"
        )
        self._cpu_temp_trend.append_sample(snapshot.cpu_temp_c, cpu_temp_display)

        gpu_temp_display = (
            f"{snapshot.gpu_temp_c:.0f} °C" if snapshot.gpu_temp_c is not None else "—"
        )
        self._gpu_temp_trend.append_sample(snapshot.gpu_temp_c, gpu_temp_display)

        power_w = parse_reading_num(snapshot.cpu_power)
        if power_w is not None:
            self._power_trend.set_title("CPU Power")
            power_display = snapshot.cpu_power.value
        else:
            power_w = parse_reading_num(snapshot.gpu_power)
            if power_w is not None:
                self._power_trend.set_title("GPU Power")
                power_display = snapshot.gpu_power.value
            else:
                power_display = "—"
        self._power_trend.append_sample(power_w, power_display)

    def set_hardware_title(self, title: str):
        self._footer.set_hardware_title(title)

    def set_selected_profile(self, index: int):
        self._profile_section.set_selected_profile(index)

    def set_selected_fan_mode(self, mode: str):
        self._profile_section.set_selected_fan_mode(mode)
