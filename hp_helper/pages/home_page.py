"""Home page — profile selector, fan modes, live cards, and footer."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy,
)
from PySide6.QtCore import Signal

from hp_helper.widgets.profile_section import ProfileSection
from hp_helper.widgets.footer import Footer
from hp_helper.widgets.utilization_card import UtilizationCard
from hp_helper.widgets.storage_card import StorageCard
from hp_helper.widgets.top_processes_card import TopProcessesCard
from hp_helper.theme import COLORS


class HomePage(QWidget):
    """Home tab with profile/fan controls, process/storage cards, and footer."""

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
            # Cap height so lower row gets room below
            card.setMaximumHeight(280)
            cards_row.addWidget(card)

        layout.addLayout(cards_row, 0)

        # ── Top processes (2 cols) + storage (1 col) ──
        lower_row = QHBoxLayout()
        lower_row.setSpacing(12)

        self._top_processes = TopProcessesCard()
        self._storage_card = StorageCard()
        self._storage_card.setMinimumHeight(110)
        self._storage_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Stretch 2 + 1 ≈ double-width top-processes next to storage
        lower_row.addWidget(self._top_processes, 2)
        lower_row.addWidget(self._storage_card, 1)
        layout.addLayout(lower_row, 1)

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
        """Refresh utilization/storage/process cards from a sensor snapshot."""
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

        # Top processes by RAM
        self._top_processes.refresh()

        # Storage
        self._storage_card.update_disks(getattr(snapshot, "disks", []) or [])

    def set_hardware_title(self, title: str):
        self._footer.set_hardware_title(title)

    def set_selected_profile(self, index: int):
        self._profile_section.set_selected_profile(index)

    def set_selected_fan_mode(self, mode: str):
        self._profile_section.set_selected_fan_mode(mode)
