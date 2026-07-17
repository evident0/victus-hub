"""Home page — performance modes, fan mode, GPU MUX, and footer."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Signal

from hp_helper.widgets.profile_section import ProfileSection
from hp_helper.widgets.footer import Footer


class HomePage(QWidget):
    """Home tab: performance + fan controls on top, GPU MUX tiles, footer."""

    profile_selected = Signal(int)
    fan_mode_selected = Signal(str)
    fan_curves_popout_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

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
        """Home no longer shows live cards; kept for main_window poll API."""
        return

    def set_hardware_title(self, title: str):
        self._footer.set_hardware_title(title)

    def set_selected_profile(self, index: int):
        self._profile_section.set_selected_profile(index)

    def set_selected_fan_mode(self, mode: str):
        self._profile_section.set_selected_fan_mode(mode)
