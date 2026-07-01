"""Home page — gauge dials, profile selector, fan modes, and footer."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Signal

from hp_helper.widgets.gauge_dial import GaugeDial
from hp_helper.widgets.profile_section import ProfileSection
from hp_helper.widgets.footer import Footer
from hp_helper.theme import COLORS


class HomePage(QWidget):
    """Home tab with two gauge dials, profile/fan buttons, and footer."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)
    fan_curves_popout_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Gauge panel: horizontal row of two dials
        gauge_row = QHBoxLayout()
        gauge_row.setSpacing(32)
        gauge_row.addStretch()

        self._cpu_dial = GaugeDial(
            "CPU", outer_max=100, outer_color="#ff2020",
            mid_max=100, mid_color="#3aaeef",
            inner_max=6000, inner_color="#06b48a",
        )
        gauge_row.addWidget(self._cpu_dial)

        self._gpu_dial = GaugeDial(
            "GPU", outer_max=100, outer_color="#ff2020",
            mid_max=100, mid_color="#3aaeef",
            inner_max=6000, inner_color="#06b48a",
        )
        gauge_row.addWidget(self._gpu_dial)
        gauge_row.addStretch()

        gauge_widget = QWidget()
        gauge_widget.setLayout(gauge_row)
        gauge_widget.setStyleSheet("background: transparent;")
        layout.addWidget(gauge_widget, 1)

        # Profile section
        self._profile_section = ProfileSection(hide_title=True)
        self._profile_section.profile_selected.connect(self.profile_selected.emit)
        self._profile_section.fan_mode_selected.connect(self.fan_mode_selected.emit)
        self._profile_section.fan_curves_popout_requested.connect(
            self.fan_curves_popout_requested.emit)
        layout.addWidget(self._profile_section)

        # Footer
        self._footer = Footer()
        layout.addWidget(self._footer)

    def update_sensor_data(self, snapshot):
        """Refresh gauge values and footer from a sensor snapshot."""
        cpu_temp = snapshot.cpu_temp_c
        cpu_usage = snapshot.cpu_usage_pct
        cpu_fan = _parse(snapshot.cpu_fan)
        gpu_temp = snapshot.gpu_temp_c
        gpu_usage = snapshot.gpu_usage_pct
        gpu_fan = _parse(snapshot.gpu_fan)

        self._cpu_dial.update_values(outer_value=cpu_temp, mid_value=cpu_usage,
                                      inner_value=cpu_fan)
        self._gpu_dial.update_values(outer_value=gpu_temp, mid_value=gpu_usage,
                                      inner_value=gpu_fan)

    def set_hardware_title(self, title: str):
        self._footer.set_hardware_title(title)

    def set_selected_profile(self, index: int):
        self._profile_section.set_selected_profile(index)


def _parse(reading) -> float:
    try:
        return float(reading.value.split()[0])
    except (ValueError, AttributeError):
        return 0.0
