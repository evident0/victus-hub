"""Home page — profile selector, fan modes, and footer."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Signal

from hp_helper.widgets.profile_section import ProfileSection
from hp_helper.widgets.footer import Footer
from hp_helper.widgets.utilization_card import UtilizationCard
from hp_helper.theme import COLORS


class HomePage(QWidget):
    """Home tab with profile/fan controls and footer."""

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
        self._ram_card = UtilizationCard("RAM", "— GB", COLORS["accent_red"])

        cards_row.addWidget(self._cpu_card)
        cards_row.addWidget(self._gpu_card)
        cards_row.addWidget(self._ram_card)
        layout.addLayout(cards_row)
        layout.addStretch()


        # Profile section (centered)
        self._profile_section = ProfileSection(hide_title=True)
        self._profile_section.profile_selected.connect(self.profile_selected.emit)
        self._profile_section.fan_mode_selected.connect(self.fan_mode_selected.emit)
        self._profile_section.fan_curves_popout_requested.connect(
            self.fan_curves_popout_requested.emit)
        layout.addWidget(self._profile_section)
        layout.addStretch()

        # Footer
        self._footer = Footer()
        layout.addWidget(self._footer)

    def update_sensor_data(self, snapshot):
        """Refresh utilization cards from a sensor snapshot."""
        # CPU: usage % + temperature
        cpu_pct = snapshot.cpu_usage_pct or 0.0
        cpu_temp = f"{snapshot.cpu_temp_c:.0f}°C" if snapshot.cpu_temp_c is not None else "— °C"
        self._cpu_card.update_data(cpu_pct, cpu_temp, COLORS["accent_blue"], "CPU Utilization")

        # GPU: usage % + temperature
        gpu_pct = snapshot.gpu_usage_pct or 0.0
        gpu_temp = f"{snapshot.gpu_temp_c:.0f}°C" if snapshot.gpu_temp_c is not None else "— °C"
        self._gpu_card.update_data(gpu_pct, gpu_temp, COLORS["accent_green"], "GPU Utilization")

        # RAM: usage % + used/total GB
        ram_pct = snapshot.ram_usage_pct or 0.0
        if snapshot.ram_used_gb is not None and snapshot.ram_total_gb is not None:
            ram_sub = f"{snapshot.ram_used_gb:.1f} / {snapshot.ram_total_gb:.1f} GB"
        else:
            ram_sub = "— GB"
        self._ram_card.update_data(ram_pct, ram_sub, COLORS["accent_red"], "RAM Utilization")

    def set_hardware_title(self, title: str):
        self._footer.set_hardware_title(title)

    def set_selected_profile(self, index: int):
        self._profile_section.set_selected_profile(index)

    def set_selected_fan_mode(self, mode: str):
        self._profile_section.set_selected_fan_mode(mode)
