"""Home page — utilization cards, performance modes, fan mode, GPU MUX, and footer."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from PySide6.QtCore import Signal

from hp_helper.widgets.profile_section import ProfileSection
from hp_helper.widgets.footer import Footer
from hp_helper.widgets.utilization_card import UtilizationCard
from hp_helper.app.theme import COLORS

# Match storage_card space threshold; temps above this count as "not good".
_USAGE_WARN_PCT = 90.0
_TEMP_WARN_C = 85.0


def _status_accent(*, temp_c: float | None = None, usage_pct: float | None = None,
                   prefer_temp: bool = True) -> str:
    """Green when temps/space look good, red otherwise."""
    if prefer_temp and temp_c is not None:
        return COLORS["accent_red"] if temp_c >= _TEMP_WARN_C else COLORS["accent_green"]
    if usage_pct is not None:
        return COLORS["accent_red"] if usage_pct >= _USAGE_WARN_PCT else COLORS["accent_green"]
    # Unknown reading — treat as good (green)
    return COLORS["accent_green"]


class HomePage(QWidget):
    """Home tab: CPU/GPU/RAM cards, performance + fan + MUX, footer."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)
    fan_curves_popout_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── Utilization cards ──
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        self._cpu_card = UtilizationCard("CPU", "— °C", COLORS["accent_green"])
        self._gpu_card = UtilizationCard("GPU", "— °C", COLORS["accent_green"])
        self._ram_card = UtilizationCard("RAM", "— °C", COLORS["accent_green"])

        for card in (self._cpu_card, self._gpu_card, self._ram_card):
            # Cap height so controls below keep room
            card.setMaximumHeight(280)
            cards_row.addWidget(card)

        layout.addLayout(cards_row, 0)

        # Performance buttons + fan mode (segmented) + GPU MUX tiles
        self._profile_section = ProfileSection(hide_title=True)
        self._profile_section.profile_selected.connect(self.profile_selected.emit)
        self._profile_section.fan_mode_selected.connect(self.fan_mode_selected.emit)
        self._profile_section.fan_curves_popout_requested.connect(
            self.fan_curves_popout_requested.emit)
        layout.addWidget(self._profile_section, 0)

        layout.addStretch(1)

        self._footer = Footer()
        layout.addWidget(self._footer, 0)

    def update_sensor_data(self, snapshot):
        """Refresh CPU/GPU/RAM cards from a sensor snapshot."""
        # CPU: usage + temp left, fan RPM right; accent from temp
        cpu_pct = snapshot.cpu_usage_pct or 0.0
        cpu_temp_c = snapshot.cpu_temp_c
        cpu_temp = f"{cpu_temp_c:.0f}°C" if cpu_temp_c is not None else "— °C"
        cpu_fan = snapshot.cpu_fan.value
        cpu_accent = _status_accent(temp_c=cpu_temp_c, prefer_temp=True)
        self._cpu_card.update_data(
            cpu_pct, cpu_temp, cpu_accent, "CPU Utilization", cpu_fan)

        # GPU: usage + temp left, fan RPM right; accent from temp
        gpu_pct = snapshot.gpu_usage_pct or 0.0
        gpu_temp_c = snapshot.gpu_temp_c
        gpu_temp = f"{gpu_temp_c:.0f}°C" if gpu_temp_c is not None else "— °C"
        gpu_fan = snapshot.gpu_fan.value
        gpu_accent = _status_accent(temp_c=gpu_temp_c, prefer_temp=True)
        self._gpu_card.update_data(
            gpu_pct, gpu_temp, gpu_accent, "GPU Utilization", gpu_fan)

        # RAM: temp left (first Memory °C from extra_sensors), usage on right
        # Accent from space (usage %); red if temp is also hot when known
        ram_pct = snapshot.ram_usage_pct or 0.0
        ram_temp_c: float | None = None
        ram_temp = "— °C"
        for es in getattr(snapshot, "extra_sensors", []):
            if es.group == "Memory" and getattr(es, "unit", None) == "°C":
                val = getattr(es, "numeric_value", None)
                if val is not None:
                    ram_temp_c = float(val)
                    ram_temp = f"{ram_temp_c:.0f}°C"
                else:
                    ram_temp = getattr(getattr(es, "reading", None), "value", "— °C")
                break
        if snapshot.ram_used_gb is not None and snapshot.ram_total_gb is not None:
            ram_usage = f"{snapshot.ram_used_gb:.1f} / {snapshot.ram_total_gb:.1f} GB"
        else:
            ram_usage = "— GB"
        # Space is primary for RAM; still flag red on high temp if reported
        if ram_temp_c is not None and ram_temp_c >= _TEMP_WARN_C:
            ram_accent = COLORS["accent_red"]
        else:
            ram_accent = _status_accent(usage_pct=ram_pct, prefer_temp=False)
        self._ram_card.update_data(
            ram_pct, ram_temp, ram_accent, "RAM Utilization", ram_usage)

    def set_hardware_title(self, title: str):
        self._footer.set_hardware_title(title)

    def set_selected_profile(self, index: int):
        self._profile_section.set_selected_profile(index)

    def set_selected_fan_mode(self, mode: str):
        self._profile_section.set_selected_fan_mode(mode)
